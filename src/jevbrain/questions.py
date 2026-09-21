"""
The three shapes a decision can take, and the one shape an answer takes.

These mirror TypeSafe's System One wire format exactly, so a question built here
goes to Jev unchanged and a question answered locally comes back in the same
shape Jev would give:

    noul    {"type": "noul",   "instructions": "..."}
            → {"noul": 0.83}

    choice  {"type": "choice", "instructions": "...", "criteria": {"a": "...", "b": "..."}}
            → {"choice": "a", "probabilities": {"a": 0.9, "b": 0.1}, "confidence": 0.8}

    score   {"type": "score",  "instructions": "...", "criteria": ["low", "mid", "high"]}
            → {"score": 1.4, "probabilities": [0.1, 0.4, 0.5], "confidence": 0.6}

A Noul answer carries no confidence of its own on the wire. Here it gets one
derived from how far the probability sits from a coin, `|2p − 1|`, so the gate
can treat every answer the same way. That derivation is ours, not TypeSafe's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class Noul:
    """How likely is this statement to be true, 0 to 1."""

    instructions: str
    kind = "noul"

    def wire(self) -> dict:
        return {"type": "noul", "instructions": self.instructions}


@dataclass(frozen=True)
class Choice:
    """Pick exactly one of the options offered. Options are rebuilt every call."""

    instructions: str
    criteria: dict[str, str]
    kind = "choice"

    def __post_init__(self) -> None:
        if len(self.criteria) < 2:
            raise ValueError("a choice needs at least two options")

    def wire(self) -> dict:
        return {"type": "choice", "instructions": self.instructions,
                "criteria": dict(self.criteria)}


@dataclass(frozen=True)
class Score:
    """Place the state on an ordered scale, lowest first."""

    instructions: str
    criteria: tuple[str, ...]
    kind = "score"

    def __post_init__(self) -> None:
        if len(self.criteria) < 2:
            raise ValueError("a scale needs at least two levels")

    def wire(self) -> dict:
        return {"type": "score", "instructions": self.instructions,
                "criteria": list(self.criteria)}


Question = Union[Noul, Choice, Score]


@dataclass(frozen=True)
class Answer:
    kind: str
    #: noul → p in 0..1 · choice → option name · score → expected level, 0..n-1
    value: Union[float, str]
    confidence: float
    probabilities: Any = None
    why: str = ""
    by: str = ""
    #: for a calibrated noul, what the brain said before calibration
    raw: float | None = None

    @property
    def p(self) -> float:
        """For a noul, the probability. For anything else, a type error on purpose."""
        if self.kind != "noul":
            raise TypeError(f"a {self.kind} answer has no single probability")
        return float(self.value)

    @property
    def level(self) -> int:
        """For a score, the nearest level index."""
        if self.kind != "score":
            raise TypeError(f"a {self.kind} answer has no level")
        return int(round(float(self.value)))

    def row(self) -> dict:
        v = round(self.value, 6) if isinstance(self.value, float) else self.value
        probs = self.probabilities
        if isinstance(probs, dict):
            probs = {k: round(x, 6) for k, x in probs.items()}
        elif isinstance(probs, list):
            probs = [round(x, 6) for x in probs]
        row = {"kind": self.kind, "value": v, "confidence": round(self.confidence, 4),
               "probabilities": probs, "why": self.why, "by": self.by}
        if self.raw is not None:
            row["raw"] = round(self.raw, 6)
        return row


def noul_confidence(p: float) -> float:
    return abs(2.0 * p - 1.0)


def softmax(xs: list[float], temperature: float = 1.0) -> list[float]:
    t = max(temperature, 1e-6)
    m = max(xs)
    es = [math.exp((x - m) / t) for x in xs]
    s = sum(es)
    return [e / s for e in es]


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def choice_answer(q: Choice, weights: dict[str, float], why: str = "", by: str = "",
                  temperature: float = 1.0) -> Answer:
    """From raw per-option evidence to a well-formed answer."""
    names = list(q.criteria)
    probs = softmax([weights.get(n, 0.0) for n in names], temperature)
    ranked = sorted(zip(names, probs), key=lambda x: -x[1])
    top, second = ranked[0][1], ranked[1][1] if len(ranked) > 1 else 0.0
    return Answer("choice", ranked[0][0], top - second,
                  {n: p for n, p in zip(names, probs)}, why, by)


def score_answer(q: Score, probs: list[float], why: str = "", by: str = "") -> Answer:
    s = sum(probs) or 1.0
    probs = [x / s for x in probs]
    expected = sum(i * x for i, x in enumerate(probs))
    ranked = sorted(probs, reverse=True)
    conf = ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)
    return Answer("score", expected, conf, probs, why, by)


def noul_answer(p: float, why: str = "", by: str = "") -> Answer:
    p = min(max(p, 0.0), 1.0)
    return Answer("noul", p, noul_confidence(p), None, why, by)


@dataclass
class Batch:
    """Several questions about one state, asked in one call — the way Jev bills."""

    state: Any
    questions: dict[str, Question] = field(default_factory=dict)

    def wire(self) -> dict:
        return {k: q.wire() for k, q in self.questions.items()}
