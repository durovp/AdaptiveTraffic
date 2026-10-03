"""Простой pygame-вид. Импорт pygame происходит только при явном запуске GUI."""

from pathlib import Path

from .models import Direction
from .presentation import GuiSession, get_view_state


WINDOW_SIZE = (1200, 800)
ROAD_COLOR = (54, 61, 70)
TEXT_COLOR = (223, 231, 239)
SIGNAL_COLORS = {"RED": (240, 77, 83), "YELLOW": (250, 194, 70), "GREEN": (64, 218, 146)}
CAR_COLORS = ((84, 169, 241), (240, 164, 77), (156, 133, 231), (87, 197, 179))
# Координаты служат только рисунку: они не попадают в расчёт очередей/проезда.
STOP_POSITIONS = {
    Direction.NORTH: (376, 282), Direction.SOUTH: (424, 518),
    Direction.EAST: (518, 376), Direction.WEST: (282, 424),
}
MOVEMENT = {
    Direction.NORTH: (0, 1), Direction.SOUTH: (0, -1),
    Direction.EAST: (-1, 0), Direction.WEST: (1, 0),
}
LABEL_POSITIONS = {
    Direction.NORTH: (520, 70), Direction.SOUTH: (170, 710),
    Direction.WEST: (40, 235), Direction.EAST: (630, 515),
}


def draw_frame(pygame, screen, session: GuiSession, fonts):
    regular, small, title = fonts
    view = get_view_state(session.simulation)
    screen.fill((30, 48, 45))
    pygame.draw.rect(screen, ROAD_COLOR, (310, 0, 180, 800))
    pygame.draw.rect(screen, ROAD_COLOR, (0, 310, 800, 180))
    for position in range(0, 800, 38):
        if not 280 <= position <= 490:
            pygame.draw.line(screen, (186, 194, 198), (400, position), (400, position + 20), 2)
            pygame.draw.line(screen, (186, 194, 198), (position, 400), (position + 20, 400), 2)
    for position in range(316, 486, 18):
        pygame.draw.rect(screen, (230, 227, 202), (position, 465, 10, 26))
    for start, end in (((312, 300), (396, 300)), ((404, 500), (488, 500)),
                       ((300, 404), (300, 488)), ((500, 312), (500, 396))):
        pygame.draw.line(screen, (241, 240, 219), start, end, 5)
    for direction, (x, y) in STOP_POSITIONS.items():
        dx, dy = MOVEMENT[direction]
        x, y = x + dx * 42, y + dy * 42
        pygame.draw.line(screen, (174, 184, 191), (x, y), (x + dx * 25, y + dy * 25), 3)
        pygame.draw.polygon(screen, (174, 184, 191), [
            (x + dx * 25, y + dy * 25),
            (x + dx * 14 - dy * 6, y + dy * 14 + dx * 6),
            (x + dx * 14 + dy * 6, y + dy * 14 - dx * 6),
        ])

    def text(message, position, font=regular, color=TEXT_COLOR):
        screen.blit(font.render(str(message), True, color), position)

    for direction, position in LABEL_POSITIONS.items():
        text(direction.value, position)
        text(f"Queue: {view['queues'][direction]}", (position[0], position[1] + 25), small)
    # Три лампы каждого автомобильного светофора; состояние берём из модели.
    light_positions = {Direction.NORTH: (264, 256), Direction.SOUTH: (508, 500),
                       Direction.WEST: (250, 504), Direction.EAST: (518, 254)}
    for direction, (x, y) in light_positions.items():
        pygame.draw.rect(screen, (18, 23, 29), (x, y, 25, 70), border_radius=5)
        for index, signal in enumerate(("RED", "YELLOW", "GREEN")):
            color = SIGNAL_COLORS[signal] if view["signals"][direction] == signal else (62, 67, 73)
            pygame.draw.circle(screen, color, (x + 12, y + 12 + index * 23), 8)
    pygame.draw.rect(screen, (18, 23, 29), (549, 564, 34, 34), border_radius=5)
    pygame.draw.circle(screen, SIGNAL_COLORS[view["pedestrian_signal"]], (566, 581), 11)
    text("PED / P", (544, 608), small)

    def car(vehicle_id, direction, x, y):
        vertical = direction in (Direction.NORTH, Direction.SOUTH)
        width, height = (18, 28) if vertical else (28, 18)
        rect = pygame.Rect(0, 0, width, height)
        rect.center = (round(x), round(y))
        pygame.draw.rect(screen, CAR_COLORS[vehicle_id % len(CAR_COLORS)], rect, border_radius=4)
        pygame.draw.rect(screen, (31, 52, 67), rect.inflate(-8, -8), border_radius=2)

    for direction, vehicle_ids in view["queue_vehicles"].items():
        x, y = STOP_POSITIONS[direction]
        dx, dy = MOVEMENT[direction]
        for index, vehicle_id in enumerate(vehicle_ids[:8]):
            car(vehicle_id, direction, x - dx * 34 * index, y - dy * 34 * index)
        if len(vehicle_ids) > 8:
            label_x, label_y = LABEL_POSITIONS[direction]
            text(f"+{len(vehicle_ids) - 8} offscreen", (label_x, label_y + 47), small)
    for vehicle in session.crossings:
        fraction = min(1.0, max(0.0, session.animation_time - vehicle.crossing_time))
        x, y = STOP_POSITIONS[vehicle.direction]
        dx, dy = MOVEMENT[vehicle.direction]
        car(vehicle.id, vehicle.direction, x + dx * 315 * fraction, y + dy * 315 * fraction)

    pygame.draw.rect(screen, (20, 27, 37), (800, 0, 400, 800))
    text("AdaptiveTraffic", (824, 22), title)
    status = "PAUSED" if session.paused else "RUNNING"
    if view["finished"]:
        status = "COMPLETE" if view["completed_successfully"] else "INCOMPLETE"
    text(f"{status}   x{session.speed}", (824, 59), color=SIGNAL_COLORS["GREEN"])
    priority = lambda name: "N/A" if view["priorities"][name] is None else f"{view['priorities'][name]:.2f}"
    lines = [
        f"Controller: {view['controller']}", f"Scenario: {view['scenario']}", f"Seed: {view['seed']}",
        f"Simulation time: {view['time']:.0f} s", f"Phase: {view['phase']}",
        f"Phase elapsed: {view['phase_elapsed']:.0f} s", "",
        "N / S queues: " + " / ".join(str(view['queues'][d]) for d in (Direction.NORTH, Direction.SOUTH)),
        "E / W queues: " + " / ".join(str(view['queues'][d]) for d in (Direction.EAST, Direction.WEST)),
        f"NS / EW queues: {view['queue_NS']} / {view['queue_EW']}",
        f"NS / EW oldest: {view['wait_NS']:.1f} / {view['wait_EW']:.1f} s",
        f"NS / EW priority: {priority('NS')} / {priority('EW')}", "",
        f"Last step decision: {view['decision']}", f"Last switch: {view['last_switch_reason']}",
        f"Mean wait (passed): {view['mean_wait']:.2f} s",
        f"Cars generated: {view['cars_generated']} / {view['cars_planned']}",
        f"Cars passed: {view['cars_passed']}",
        "", f"Pedestrian request: {'YES' if view['pedestrian_request'] else 'NO'}",
        f"Pedestrian waiting: {view['pedestrian_wait']:.1f} s",
        f"Pedestrian signal: {view['pedestrian_signal']}",
    ]
    for index, line in enumerate(lines):
        text(line, (824, 100 + index * 25), small)
    text("SPACE pause/resume   R restart", (824, 686), small)
    text("1 x1   2 x5   3 x20   P pedestrian", (824, 713), small)
    text("F fixed   Q queue   A adaptive   ESC exit", (824, 740), small)
    text("Queues are capped at 8 drawn cars; counts are exact.", (18, 772), small)


def run_gui(session: GuiSession, max_frames: int | None = None, screenshot: Path | None = None) -> int:
    # Headless-запуск и pytest не импортируют и не инициализируют pygame.
    try:
        import pygame
    except ImportError as error:
        raise RuntimeError("GUI requires pygame: python -m pip install -r requirements.txt") from error
    pygame.display.init()
    pygame.font.init()
    try:
        screen = pygame.display.set_mode(WINDOW_SIZE)
        pygame.display.set_caption("AdaptiveTraffic")
        fonts = (pygame.font.Font(None, 25), pygame.font.Font(None, 22), pygame.font.Font(None, 34))
        clock = pygame.time.Clock()
        frame_count = 0
        running = True
        while running:
            elapsed = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_SPACE:
                        session.paused = not session.paused
                    elif event.key == pygame.K_r:
                        session.restart()
                    elif event.key == pygame.K_p:
                        session.simulation.request_pedestrian()
                    elif event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                        session.speed = {pygame.K_1: 1, pygame.K_2: 5, pygame.K_3: 20}[event.key]
                    elif event.key in (pygame.K_f, pygame.K_q, pygame.K_a):
                        session.restart({pygame.K_f: "FIXED", pygame.K_q: "QUEUE_ONLY", pygame.K_a: "ADAPTIVE"}[event.key])
            session.advance_wall_time(elapsed)
            draw_frame(pygame, screen, session, fonts)
            pygame.display.flip()
            frame_count += 1
            if max_frames is not None and frame_count >= max_frames:
                running = False
        if screenshot is not None:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            pygame.image.save(screen, str(screenshot))
    finally:
        pygame.quit()
    return 0
