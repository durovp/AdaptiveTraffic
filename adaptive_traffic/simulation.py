"""Дискретная симуляция. Один шаг обслуживает интервал [time, time + dt)."""

from collections import deque
from dataclasses import dataclass, field
import math

from .config import Config
from .controllers import Action, Controller, Decision, Reason, phase_priority
from .models import Direction, PHASE_DIRECTIONS, Phase, PhaseStats, TrafficSnapshot, Vehicle
from .traffic_demand import TrafficPlan
from .traffic_light import State, TrafficLight


@dataclass
class SimulationResult:
    plan: TrafficPlan
    controller: str
    vehicles: list[Vehicle]
    queue_samples: list[int]
    switches: list[dict]
    elapsed_time: float
    completed_successfully: bool
    state_durations: dict[str, float] = field(default_factory=dict)
    green_durations: list[float] = field(default_factory=list)


class Simulation:
    def __init__(self, config: Config, plan: TrafficPlan, controller: Controller, pedestrian_times: tuple[float, ...] = ()):
        if plan.duration != config.simulation_duration:
            raise ValueError("Plan duration must match simulation_duration")
        self.config = config
        self.plan = plan
        self.controller = controller
        self.light = TrafficLight(config)
        self.pedestrian_times = tuple(pedestrian_times)
        if any(not math.isfinite(time) or not 0 <= time < plan.duration for time in self.pedestrian_times):
            raise ValueError("Pedestrian request times must lie in [0, generation duration)")
        if tuple(sorted(self.pedestrian_times)) != self.pedestrian_times:
            raise ValueError("Pedestrian request times must be sorted")
        self.pedestrian_index = 0
        self.queues = {direction: deque() for direction in Direction}
        # Создаём собственные Vehicle. Неизменяемый план остаётся общим.
        self.vehicles = [
            Vehicle(arrival.id, arrival.direction, arrival.arrival_time)
            for arrival in plan.arrivals
        ]
        self.arrival_index = 0
        self.step_index = 0
        self.queue_samples = []
        self.switches = []
        self.last_decision = Decision(Action.KEEP)
        self.last_departures: list[Vehicle] = []
        self.state_durations = {state.value: 0.0 for state in State}
        self.green_durations = []
        self._green_segment_started_at: float | None = None
        self._service_green_started_at = None
        self._next_departure = {direction: 0.0 for direction in Direction}

    @property
    def time(self) -> float:
        return self.step_index * self.config.time_step

    @property
    def all_passed(self) -> bool:
        return self.arrival_index == len(self.vehicles) and not any(self.queues.values())

    @property
    def finished(self) -> bool:
        maximum_steps = round((self.plan.duration + self.config.drain_timeout) / self.config.time_step)
        return self.step_index >= maximum_steps or (
            self.time >= self.plan.duration and self.all_passed and self.all_pedestrians_served
        )

    @property
    def all_pedestrians_served(self) -> bool:
        return self.pedestrian_index == len(self.pedestrian_times) and not self.light.pedestrian_request

    def request_pedestrian(self) -> bool:
        if self.finished:
            return False
        return self.light.request_pedestrian(self.time)

    def phase_after_pedestrian_timeout(self, current: Phase, snapshot: TrafficSnapshot) -> Phase:
        if self.controller.name == "FIXED":
            return current.other
        current_priority = phase_priority(self.controller.name, snapshot.for_phase(current), self.config)
        other_priority = phase_priority(self.controller.name, snapshot.for_phase(current.other), self.config)
        # При равенстве продолжаем ожидаемое чередование фаз.
        return current if current_priority > other_priority else current.other

    def snapshot(self) -> TrafficSnapshot:
        phase_stats = {}
        for phase, directions in PHASE_DIRECTIONS.items():
            count = sum(len(self.queues[direction]) for direction in directions)
            oldest_wait = max(
                self.time - self.queues[direction][0].arrival_time
                if self.queues[direction] else 0.0
                for direction in directions
            )
            phase_stats[phase] = PhaseStats(count, oldest_wait)
        return TrafficSnapshot(phase_stats[Phase.NS], phase_stats[Phase.EW])

    def step(self):
        if self.finished:
            return
        time = self.time
        interval_end = time + self.config.time_step
        self.last_departures = []
        self.last_decision = Decision(Action.KEEP)
        while self.pedestrian_index < len(self.pedestrian_times) and self.pedestrian_times[self.pedestrian_index] <= time:
            self.light.request_pedestrian(self.pedestrian_times[self.pedestrian_index])
            self.pedestrian_index += 1
        # Дробный момент появления виден на ближайшей следующей границе шага.
        while self.arrival_index < len(self.vehicles):
            vehicle = self.vehicles[self.arrival_index]
            if vehicle.arrival_time > time:
                break
            self.queues[vehicle.direction].append(vehicle)
            self.arrival_index += 1

        self.light.advance(time)
        phase = self.light.green_phase
        snapshot = self.snapshot()
        if phase is not None:
            decision = self.controller.decide(phase, self.light.elapsed(time), snapshot)
            next_phase = phase.other
            # Пешеходная обвязка не меняет алгоритм автомобильного контроллера.
            # Обычное переключение вставляет PED и сохраняет его исходную цель.
            if self.light.pedestrian_request:
                if self.light.elapsed(time) < self.config.min_green:
                    decision = Decision(Action.KEEP)
                elif decision.action == Action.KEEP and self.light.pedestrian_waiting_time(time) >= self.config.ped_max_wait:
                    decision = Decision(Action.REQUEST_SWITCH, Reason.PED_MAX_WAIT)
                    next_phase = self.phase_after_pedestrian_timeout(phase, snapshot)
            self.last_decision = decision
            if decision.action == Action.REQUEST_SWITCH:
                self.light.request_switch(time, next_phase)
                self.switches.append({
                    "simulation_time": time,
                    "old_phase": phase.value,
                    "new_phase": next_phase.value,
                    "reason": decision.reason.value,
                    "queue_NS": snapshot.ns.queue,
                    "queue_EW": snapshot.ew.queue,
                    "wait_NS": snapshot.ns.oldest_wait,
                    "wait_EW": snapshot.ew.oldest_wait,
                    "priority_NS": phase_priority(
                        self.controller.name, snapshot.ns, self.config
                    ),
                    "priority_EW": phase_priority(
                        self.controller.name, snapshot.ew, self.config
                    ),
                })

        # Диагностика наблюдает реально выполненный интервал и ничего не решает.
        self.state_durations[self.light.state.value] += self.config.time_step
        if self.light.green_phase is not None:
            if self._green_segment_started_at is None:
                self._green_segment_started_at = time
        elif self._green_segment_started_at is not None:
            self.green_durations.append(time - self._green_segment_started_at)
            self._green_segment_started_at = None

        self.queue_samples.append(sum(len(queue) for queue in self.queues.values()))
        if self.light.green_phase is not None:
            green_started_at = self.light.state_started_at
            if green_started_at != self._service_green_started_at:
                self._service_green_started_at = green_started_at
                for direction in self.light.allowed_directions:
                    self._next_departure[direction] = (
                        green_started_at + self.config.vehicle_headway
                    )
            for direction in self.light.allowed_directions:
                if self.queues[direction] and interval_end >= self._next_departure[direction]:
                    vehicle = self.queues[direction].popleft()
                    vehicle.crossing_time = interval_end
                    self.last_departures.append(vehicle)
                    # Пустой подход не накапливает возможность выпустить пачку машин.
                    self._next_departure[direction] = interval_end + self.config.vehicle_headway
        self.step_index += 1

    def run(self) -> SimulationResult:
        while not self.finished:
            self.step()
        return self.result()

    def result(self) -> SimulationResult:
        # Последний зелёный может быть усечён концом опыта или тайм-аутом.
        green_durations = list(self.green_durations)
        if self._green_segment_started_at is not None:
            green_durations.append(self.time - self._green_segment_started_at)
        return SimulationResult(
            self.plan, self.controller.name, self.vehicles, self.queue_samples,
            self.switches, self.time, self.all_passed and self.all_pedestrians_served,
            dict(self.state_durations), green_durations,
        )
