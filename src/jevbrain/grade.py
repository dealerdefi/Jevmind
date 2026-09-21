"""
Grading the brain, and teaching it.

A decision is only worth something if it can be checked. When an outcome is
known — a test run that passed, a hunk a human flagged, a game tick survived —
it goes into the ledger as a second line, and this module scores every
probability the brain gave against it:

    brier   (p − y)²      0 perfect · 0.25 a coin · 1 certain and wrong
    log     −ln p(y)      punishes a confident miss much harder

`learn` fits Platt scaling — p' = σ(a·logit p + b) — per skill and question,
on the older 70% of labelled answers, and keeps it **only if it beats the
uncalibrated brain on the newer 30%**. A calibration that only helps on the data
it was fitted to is not learning; it is memorising, and it is thrown away.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median

from .brain import Calibration
from .ledger import Ledger
from .questions import logit, sigmoid


def brier(p: float, y: bool) -> float:
    return (p - (1.0 if y else 0.0)) ** 2


def logloss(p: float, y: bool) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -math.log(p if y else 1 - p)


@dataclass
class SkillStats:
    skill: str
    decisions: int = 0
    act: int = 0
    hold: int = 0
    escalate: int = 0
    latencies: list[float] = field(default_factory=list)
    tokens: int = 0
    cost: float = 0.0
    brains: dict[str, int] = field(default_factory=dict)
    graded: list[tuple[float, bool]] = field(default_factory=list)

    @property
    def p50(self) -> float:
        return median(self.latencies) if self.latencies else 0.0

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]

    @property
    def brier(self) -> float | None:
        g = self.graded
        return sum(brier(p, y) for p, y in g) / len(g) if g else None

    @property
    def hit(self) -> float | None:
        g = self.graded
        return sum(1 for p, y in g if (p >= 0.5) == y) / len(g) if g else None


def pairs(led: Ledger, raw: bool = True) -> list[tuple[str, str, float, bool, int]]:
    """
    (skill, key, p, truth, seq) for every noul answer that has an outcome.

    `raw` gives what the brain said before calibration — what `learn` fits on,
    so a new calibration replaces the old one instead of stacking on it. The
    scoreboard uses raw=False: what was actually said is what gets graded.
    """
    labels = led.outcomes()
    out = []
    for e in led.decisions():
        lab = labels.get(e.body["id"])
        if not lab:
            continue
        for key, a in e.body["answers"].items():
            if a["kind"] != "noul":
                continue
            truth = lab.get(key, lab.get("*"))
            if isinstance(truth, bool):
                p = float(a.get("raw", a["value"]) if raw else a["value"])
                out.append((e.body["skill"], key.split(":")[0], p, truth, e.seq))
    return out


def stats(led: Ledger) -> dict[str, SkillStats]:
    by: dict[str, SkillStats] = {}
    for e in led.decisions():
        b = e.body
        s = by.setdefault(b["skill"], SkillStats(b["skill"]))
        s.decisions += 1
        setattr(s, b["action"], getattr(s, b["action"]) + 1)
        s.latencies.append(float(b.get("latency_ms", 0.0)))
        s.tokens += int(b.get("input_tokens", 0))
        s.cost += float(b.get("cost_usd", 0.0))
        s.brains[b["brain"]] = s.brains.get(b["brain"], 0) + 1
    for skill, _key, p, y, _seq in pairs(led, raw=False):
        by.setdefault(skill, SkillStats(skill)).graded.append((p, y))
    return by


def buckets(graded: list[tuple[float, bool]], n: int = 5) -> list[dict]:
    out = []
    for i in range(n):
        lo, hi = i / n, (i + 1) / n
        g = [(p, y) for p, y in graded if lo <= p < hi or (i == n - 1 and p == 1.0)]
        if g:
            out.append({"lo": lo, "hi": hi, "n": len(g),
                        "said": sum(p for p, _ in g) / len(g),
                        "happened": sum(1 for _, y in g if y) / len(g)})
    return out


# ── learning ─────────────────────────────────────────────────────────────────


def fit_platt(data: list[tuple[float, bool]], steps: int = 50, l2: float = 1e-3) -> tuple[float, float]:
    """
    Logistic regression of the outcome on logit(p), by Newton's method with a
    light ridge pulling toward the identity (a=1, b=0). Converges in a handful
    of steps; the ridge keeps a tiny or one-sided sample from going wild.
    """
    xs = [logit(p) for p, _ in data]
    ys = [1.0 if y else 0.0 for _, y in data]
    n = len(xs)
    a, b = 1.0, 0.0
    for _ in range(steps):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            q = sigmoid(a * x + b)
            e, w = q - y, q * (1 - q)
            ga += e * x
            gb += e
            haa += w * x * x
            hab += w * x
            hbb += w
        ga, gb = ga / n + l2 * (a - 1.0), gb / n + l2 * b
        haa, hab, hbb = haa / n + l2, hab / n, hbb / n + l2
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


@dataclass
class Lesson:
    key: str
    n: int
    before: float
    after: float
    kept: bool
    params: tuple[float, float]


def learn(led: Ledger, current: Calibration, min_n: int = 20) -> tuple[Calibration, list[Lesson]]:
    groups: dict[str, list[tuple[float, bool, int]]] = {}
    for skill, key, p, y, seq in pairs(led):
        groups.setdefault(f"{skill}/{key}", []).append((p, y, seq))

    new = Calibration(dict(current.params))
    lessons: list[Lesson] = []
    for key, rows in sorted(groups.items()):
        rows.sort(key=lambda r: r[2])
        if len(rows) < min_n or len({y for _, y, _ in rows}) < 2:
            continue
        cut = int(len(rows) * 0.7)
        train = [(p, y) for p, y, _ in rows[:cut]]
        test = [(p, y) for p, y, _ in rows[cut:]]
        a, b = fit_platt(train)
        before = sum(brier(p, y) for p, y in test) / len(test)
        after = sum(brier(sigmoid(a * logit(p) + b), y) for p, y in test) / len(test)
        kept = after < before
        if kept:
            new.params[key] = (round(a, 6), round(b, 6))
        lessons.append(Lesson(key, len(rows), before, after, kept, (a, b)))
    return new, lessons
