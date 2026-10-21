"""Данные и часы демонстрации. Здесь нет pygame и правил движения автомобилей."""

from .config import Config
from .controllers import Action, CONTROLLERS, phase_priority
from .models import Direction
from .simulation import Simulation
from .traffic_demand import TrafficPlan
from .metrics import percentile


REASON_EXPLANATIONS = {
    "FIXED_TIMER": "Истекло заданное время фиксированного зелёного.",
    "EMPTY_CURRENT": "На текущем зелёном больше нет машин, а другая сторона ждёт.",
    "MAX_WAIT": "Машины другой стороны ждут слишком долго.",
    "MAX_GREEN": "Достигнут максимум зелёного, на другой стороне есть машины.",
    "HIGHER_PRIORITY": "Другая сторона получила заметно больший приоритет.",
    "PED_MAX_WAIT": "Пешеход ждёт предельно долго: начинается безопасное переключение.",
}


def explain_decision(simulation: Simulation) -> str:
    if simulation.last_decision.reason:
        return REASON_EXPLANATIONS[simulation.last_decision.reason.value]
    if simulation.light.green_phase is None:
        return "Автомат выдерживает защитный или пешеходный интервал. Автомобили не обслуживаются."
    debug = simulation.last_controller_debug
    if debug and debug["balanced_load_detected"] and debug["green_elapsed"] < debug["effective_min_green"]:
        return "Обе стороны сильно и примерно одинаково загружены. Зелёный удерживается дольше, чтобы уменьшить потери на переходах."
    if simulation.light.elapsed(simulation.time) < simulation.config.min_green:
        return "Минимальное время зелёного ещё не истекло."
    return "Условия переключения не выполнены: сохраняем зелёный."


def get_view_state(simulation: Simulation) -> dict:
    """Снимок читается без изменения Simulation и без повторного вызова decide."""
    snapshot = simulation.snapshot()
    priorities = {"NS": None, "EW": None}
    if simulation.controller.name != "FIXED":
        for name, stats in (("NS", snapshot.ns), ("EW", snapshot.ew)):
            priorities[name] = phase_priority(simulation.controller.name, stats, simulation.config)
    passed = [vehicle for vehicle in simulation.vehicles if vehicle.crossing_time is not None]
    return {
        "controller": simulation.controller.name,
        "scenario": simulation.plan.scenario,
        "seed": simulation.plan.seed,
        "time": simulation.time,
        "phase": simulation.light.state.value,
        "phase_elapsed": simulation.light.elapsed(simulation.time),
        "queues": {direction: len(simulation.queues[direction]) for direction in Direction},
        "queue_vehicles": {direction: tuple(vehicle.id for vehicle in simulation.queues[direction]) for direction in Direction},
        "signals": {direction: simulation.light.signal_for(direction) for direction in Direction},
        "queue_NS": snapshot.ns.queue,
        "queue_EW": snapshot.ew.queue,
        "wait_NS": snapshot.ns.oldest_wait,
        "wait_EW": snapshot.ew.oldest_wait,
        "priorities": priorities,
        "decision": "SWITCH" if simulation.last_decision.action == Action.REQUEST_SWITCH else "KEEP",
        "last_switch_reason": simulation.switches[-1]["reason"] if simulation.switches else "N/A",
        "mean_wait": sum(vehicle.waiting_time for vehicle in passed) / len(passed) if passed else 0.0,
        "p95_wait": percentile([vehicle.waiting_time for vehicle in passed], 95),
        "number_of_switches": len(simulation.switches),
        "explanation": explain_decision(simulation),
        "controller_debug": simulation.last_controller_debug,
        "departures": [{"id": v.id, "direction": v.direction.value, "time": v.crossing_time} for v in simulation.last_departures],
        "cars_generated": simulation.arrival_index + simulation.manual_count,
        "manual_count": simulation.manual_count,
        "cars_planned": len(simulation.plan.arrivals),
        "cars_passed": len(passed),
        "finished": simulation.finished,
        "completed_successfully": simulation.finished and simulation.all_passed and simulation.all_pedestrians_served,
        "pedestrian_request": simulation.light.pedestrian_request,
        "pedestrian_wait": simulation.light.pedestrian_waiting_time(simulation.time),
        "pedestrian_signal": simulation.light.pedestrian_signal,
    }


class GuiSession:
    """Меняет только темп вызова step; при любой скорости шаг модели одинаков."""

    def __init__(self, config: Config, plan: TrafficPlan, controller_name: str, pedestrian_times: tuple[float, ...] = ()):
        self.config = config
        self.plan = plan
        self.pedestrian_times = tuple(pedestrian_times)
        self.speed = 1
        self.restart(controller_name)

    def restart(self, controller_name: str | None = None):
        if controller_name is None:
            controller_name = self.simulation.controller.name
        self.simulation = Simulation(self.config, self.plan, CONTROLLERS[controller_name.upper()](self.config), self.pedestrian_times)
        self.paused = False
        self.accumulator = 0.0
        self.animation_time = 0.0
        self.crossings = []

    def advance_wall_time(self, seconds: float):
        if self.paused:
            return
        elapsed = max(0.0, seconds) * self.speed
        self.animation_time += elapsed
        self.accumulator += elapsed
        while self.accumulator >= self.config.time_step and not self.simulation.finished:
            self.simulation.step()
            # Только уже обслуженные симулятором машины получают анимацию.
            self.crossings.extend(self.simulation.last_departures)
            self.accumulator -= self.config.time_step
        if self.simulation.finished:
            self.accumulator = 0.0
        self.crossings = [vehicle for vehicle in self.crossings if self.animation_time - vehicle.crossing_time < 1.0]
