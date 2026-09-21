"""
arena — a control loop you can watch.

Inspired by typesafe-mario (fhshaik/typesafe-mario), jev-drone
(RomanSlack/jev-drone) and OneVOneJev (emrickgarrett/OneVOneJev): no
screenshots, no pixels — the brain reads structured state every tick and
decides what the body does next. Low-level physics stays in code, the way a
drone's flight controller keeps it level while the model only says climb or
brake.

    jevbrain arena --watch             # live, in the terminal
    jevbrain arena --seed 7 --runs 20  # a benchmark

A side-scroller: pits, walkers, pipes. Every tick is one batch:

    move     Choice  run · jump · wait
    danger   Noul    running straight on would kill it within two ticks

`danger` has a ground truth — code simulates "just run" two ticks ahead — so
every answer is labelled the moment the tick is over. That makes arena the
fastest way to fill the ledger with graded decisions, and to watch `jevbrain
learn` recalibrate a brain on its own mistakes.
"""

from __future__ import annotations

import random
import sys
import time
from dataclasses import dataclass, field

from ..mind import Mind
from ..questions import Answer, Choice, Noul, choice_answer, noul_answer

SKILL = "arena"
MOVES = {"run": "move one step right", "jump": "jump now (only works from the ground)",
         "wait": "hold position this tick — on the ground stand still, in the air stop drifting forward"}
JUMP_V = 2
LOOK = 12


@dataclass
class Walker:
    x: int
    dx: int = -1


@dataclass
class Level:
    length: int
    pits: dict[int, int]          # x → width
    pipes: dict[int, int]         # x → height
    walkers: list[Walker]

    def pit_at(self, x: int) -> bool:
        return any(px <= x < px + w for px, w in self.pits.items())


def make_level(seed: int, length: int = 240) -> Level:
    rng = random.Random(seed)
    pits: dict[int, int] = {}
    pipes: dict[int, int] = {}
    walkers: list[Walker] = []
    x = 12
    while x < length - 12:
        kind = rng.random()
        if kind < 0.4:
            pits[x] = rng.choice((1, 2, 2, 3))
            x += pits[x] + rng.randint(5, 10)
        elif kind < 0.65:
            pipes[x] = rng.choice((1, 1, 2))
            x += rng.randint(6, 11)
        else:
            walkers.append(Walker(x + rng.randint(4, 8)))
            x += rng.randint(7, 12)
    return Level(length, pits, pipes, walkers)


@dataclass
class Body:
    x: int = 2
    y: int = 0
    vy: int = 0
    alive: bool = True
    won: bool = False

    @property
    def grounded(self) -> bool:
        return self.y == 0


def step(body: Body, level: Level, move: str) -> None:
    """The physics. The brain never touches this."""
    if not body.alive or body.won:
        return
    if move == "jump" and body.grounded:
        body.vy = JUMP_V
    dx = 0 if move == "wait" else 1
    nx = body.x + dx
    h = level.pipes.get(nx, 0)
    if h and body.y < h:
        nx = body.x                      # blocked by the pipe, not killed
    body.x = nx
    if not body.grounded or body.vy > 0:
        body.y = max(0, body.y + body.vy)
        body.vy -= 1
        if body.y == 0:
            body.vy = 0
    for w in level.walkers:
        nwx = w.x + w.dx
        if level.pit_at(nwx) or level.pipes.get(nwx):
            w.dx = -w.dx
        else:
            w.x = nwx
    if body.grounded and level.pit_at(body.x):
        body.alive = False
    if body.y == 0 and any(abs(w.x - body.x) == 0 for w in level.walkers):
        body.alive = False
    if body.x >= level.length:
        body.won = True


def ahead(body: Body, level: Level, reach: int = 8) -> dict:
    """Structured state: what is in front, how far, how big. No pixels."""
    gap = next((d for d in range(1, reach + 1) if level.pit_at(body.x + d)), None)
    gw = 0
    if gap is not None:
        gx = body.x + gap
        while level.pit_at(gx + gw):
            gw += 1
    pipe = next((d for d in range(1, reach + 1) if level.pipes.get(body.x + d)), None)
    walker = min((w.x - body.x for w in level.walkers if 0 <= w.x - body.x <= reach), default=None)
    wdx = next((w.dx for w in level.walkers if w.x - body.x == walker), 0) if walker is not None else 0
    window = {
        "pits": [[px - body.x, w] for px, w in sorted(level.pits.items()) if -3 <= px - body.x <= LOOK],
        "pipes": [[px - body.x, h] for px, h in sorted(level.pipes.items()) if -1 <= px - body.x <= LOOK],
        "walkers": [[w.x - body.x, w.dx] for w in level.walkers if -3 <= w.x - body.x <= LOOK],
    }
    return {"x": body.x, "y": body.y, "vy": body.vy, "grounded": body.grounded, "window": window,
            "gap_in": gap, "gap_width": gw,
            "pipe_in": pipe, "pipe_height": level.pipes.get(body.x + pipe, 0) if pipe else 0,
            "walker_in": walker, "walker_dx": wdx, "to_goal": level.length - body.x}


def would_die_running(body: Body, level: Level, ticks: int = 2) -> bool:
    """Ground truth for `danger`: copy the world, just run, see what happens."""
    b = Body(body.x, body.y, body.vy, body.alive, body.won)
    lv = Level(level.length, level.pits, level.pipes, [Walker(w.x, w.dx) for w in level.walkers])
    for _ in range(ticks):
        step(b, lv, "run")
        if not b.alive:
            return True
    return False


def _mini(state: dict) -> tuple[Body, Level]:
    """Rebuild the visible window as a tiny world, relative to the runner."""
    s, w = state["ahead"], state["ahead"]["window"]
    lv = Level(10_000, {px: pw for px, pw in w["pits"]}, {px: h for px, h in w["pipes"]},
               [Walker(wx, dx) for wx, dx in w["walkers"]])
    return Body(0, s["y"], s["vy"]), lv


def _survives(state: dict, plan: tuple[str, ...]) -> tuple[bool, int]:
    b, lv = _mini(state)
    lv = Level(lv.length, lv.pits, lv.pipes, [Walker(x.x, x.dx) for x in lv.walkers])
    for mv in plan:
        step(b, lv, mv)
        if not b.alive:
            return False, b.x
    return True, b.x


PLANS: list[tuple[str, ...]] = []
for a in ("run", "jump", "wait"):
    for b_ in ("run", "jump", "wait"):
        for c in ("run", "jump", "wait"):
            PLANS.append((a, b_, c, "run", "run", "run"))


def reflex_move(state: dict, q: Choice, key: str) -> Answer:
    """A tiny planner over the structured window: which first move keeps it alive and moving?"""
    best: dict[str, float] = {"run": -9.0, "jump": -9.0, "wait": -9.0}
    for plan in PLANS:
        ok, dist = _survives(state, plan)
        # Alive beats dead, further beats nearer, and a needless jump or wait
        # costs a little — so on open ground the brain says "run" and means it.
        v = (6.0 if ok else 0.0) + 0.35 * dist - {"wait": 0.3, "jump": 0.15}.get(plan[0], 0.0)
        best[plan[0]] = max(best[plan[0]], v)
    return choice_answer(q, best, f"best 6-tick plan per first move: " +
                         " ".join(f"{k} {v:.1f}" for k, v in best.items()), "local", 0.15)


def reflex_danger(state: dict, q: Noul, key: str) -> Answer:
    ok, _ = _survives(state, ("run", "run"))
    near = any(0 < px <= 3 for px, _ in state["ahead"]["window"]["pits"]) or \
        any(abs(wx) <= 3 for wx, _ in state["ahead"]["window"]["walkers"])
    p = 0.08 if ok else 0.9
    if ok and near:
        p = 0.3
    return noul_answer(p, "simulated two ticks of running on the visible window", "local")


REFLEXES = {"move": reflex_move, "danger": reflex_danger}


@dataclass
class Run:
    seed: int
    ticks: int = 0
    distance: int = 0
    won: bool = False
    died_at: int | None = None
    decisions: int = 0
    ms: list[float] = field(default_factory=list)


def render(body: Body, level: Level, width: int = 64, colour: bool = True) -> list[str]:
    pink = "\033[38;5;205m\033[1m" if colour else ""
    dim = "\033[38;5;89m" if colour else ""
    red = "\033[38;5;110m" if colour else ""
    off = "\033[0m" if colour else ""
    left = max(0, body.x - 10)
    rows = []
    for yy in range(4, -1, -1):
        line = []
        for x in range(left, left + width):
            ch = " "
            if yy == 0 and not level.pit_at(x):
                ch = f"{dim}▀{off}"
            h = level.pipes.get(x, 0)
            if h and yy >= 1 and yy <= h:
                ch = f"{dim}█{off}"
            if yy == 1 and any(w.x == x for w in level.walkers):
                ch = f"{red}◆{off}"
            if x == body.x and yy == body.y + 1:
                ch = f"{pink}{'@' if body.alive else 'x'}{off}"
            if x == level.length and yy >= 1:
                ch = f"{pink}▌{off}"
            line.append(ch)
        rows.append("".join(line))
    return rows


def play(seed: int, mind: Mind, watch: bool = False, fps: float = 18.0, max_ticks: int = 600,
         colour: bool = True, threshold: float = 0.3) -> Run:
    level = make_level(seed)
    body = Body()
    run = Run(seed)
    if watch:
        sys.stdout.write("\033[?25l")
    try:
        while body.alive and not body.won and run.ticks < max_ticks:
            s = ahead(body, level)
            d = mind.ask(SKILL, f"seed {seed} tick {run.ticks}", {"ahead": s}, {
                "move": Choice("What should the runner do this tick?", MOVES),
                "danger": Noul("If the runner keeps running straight on, it dies within two ticks."),
            }, REFLEXES, drive="move", act_when=lambda a: True, threshold=threshold)
            truth = would_die_running(body, level)
            mind.label(d.id, "danger", truth)
            run.decisions += 1
            run.ms.append(d.thought.latency_ms)
            move = str(d["move"].value)
            step(body, level, move)
            run.ticks += 1
            if watch:
                frame = render(body, level, colour=colour)
                status = (f"  tick {run.ticks:>4}  x {body.x:>4}/{level.length}  move {move:<5} "
                          f"danger {d['danger'].p:.2f}  {d.thought.brain} {d.thought.latency_ms:.2f}ms")
                sys.stdout.write("\033[H\033[2J" + "\n".join(frame) + "\n" + status + "\n")
                sys.stdout.flush()
                time.sleep(1 / fps)
    finally:
        if watch:
            sys.stdout.write("\033[?25h")
    run.distance, run.won = body.x, body.won
    if not body.alive:
        run.died_at = body.x
    return run
