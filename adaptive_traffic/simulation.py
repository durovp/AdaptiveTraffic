"""Дискретная симуляция. Один шаг обслуживает интервал [time, time + dt)."""

from collections import deque
from dataclasses import dataclass

from .config import Config
from .controllers import Action, Controller, calculate_priority
from .models import Direction, PHASE_DIRECTIONS, Phase, PhaseStats, TrafficSnapshot, Vehicle
from .traffic_demand import TrafficPlan
from .traffic_light import TrafficLight


@dataclass
class SimulationResult:
    plan: TrafficPlan
    controller: str
    vehicles: list[Vehicle]
    queue_samples: list[int]
    switches: list[dict]
    elapsed_time: float
    completed_successfully: bool


class Simulation:
    def __init__(self, config: Config, plan: TrafficPlan, controller: Controller):
        if plan.duration != config.simulation_duration:
            raise ValueError("Plan duration must match simulation_duration")
        self.config = config
        self.plan = plan
        self.controller = controller
        self.light = TrafficLight(config)
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
        self._service_green_started_at = None
        self._next_departure = {direction: 0.0 for direction in Direction}

    @property
    def time(self) -> float:
        return self.step_index * self.config.time_step

    @property
    def all_passed(self) -> bool:
        return self.arrival_index == len(self.vehicles) and not any(self.queues.values())

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
        time = self.time
        interval_end = time + self.config.time_step
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
            if decision.action == Action.REQUEST_SWITCH:
                self.light.request_switch(time)
                self.switches.append({
                    "simulation_time": time,
                    "old_phase": phase.value,
                    "new_phase": phase.other.value,
                    "reason": decision.reason.value,
                    "queue_NS": snapshot.ns.queue,
                    "queue_EW": snapshot.ew.queue,
                    "wait_NS": snapshot.ns.oldest_wait,
                    "wait_EW": snapshot.ew.oldest_wait,
                    "priority_NS": calculate_priority(
                        snapshot.ns.queue, snapshot.ns.oldest_wait, self.config.wait_equivalent
                    ),
                    "priority_EW": calculate_priority(
                        snapshot.ew.queue, snapshot.ew.oldest_wait, self.config.wait_equivalent
                    ),
                })

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
                    # Пустой подход не накапливает возможность выпустить пачку машин.
                    self._next_departure[direction] = interval_end + self.config.vehicle_headway
        self.step_index += 1

    def run(self) -> SimulationResult:
        deadline = self.plan.duration + self.config.drain_timeout
        maximum_steps = round(deadline / self.config.time_step)
        while self.step_index < maximum_steps:
            if self.time >= self.plan.duration and self.all_passed:
                break
            self.step()
        return SimulationResult(
            self.plan, self.controller.name, self.vehicles, self.queue_samples,
            self.switches, self.time, self.all_passed,
        )
