"""
route — decide how much model a task deserves before spending it.

Inspired by jev-codex-router (0xNatoshi/jev-codex-router): a typo fix does not
need the biggest model at maximum reasoning, and a concurrency bug does not
belong on the smallest one. One batch, three answers:

    difficulty   Score    trivial · small · medium · hard · research
    tier         Choice   fast · standard · frontier
    effort       Choice   low · medium · high reasoning

    jevbrain route "rename getUser to fetchUser across the web app"
    jevbrain route --json "why does the websocket drop under load"   # for scripts

The output is a routing decision, not a model name: map tiers to whatever your
stack calls them with `--map fast=haiku,standard=sonnet,frontier=opus`.
"""

from __future__ import annotations

import re

from .. import lexical
from ..mind import Mind
from ..questions import Answer, Choice, Score, choice_answer, score_answer

SKILL = "route"
LEVELS = ("trivial: one obvious edit", "small: a contained change in one place",
          "medium: several files or some design", "hard: subtle, cross-cutting, or stateful",
          "research: unknown cause, needs investigation")
TIERS = {"fast": "the smallest, quickest model", "standard": "a capable general model",
         "frontier": "the strongest model available"}
EFFORT = {"low": "answer directly", "medium": "think it through", "high": "reason at length and verify"}

EASY = r"\b(typo|rename|format|lint|bump|comment|docstring|readme|spelling|copy|log line|print)\b"
HARD = (r"\b(race|deadlock|concurren\w*|thread\w*|memory leak|leak|flaky|intermittent|heisenbug|"
        r"perf\w*|latency|throughput|scal\w*|distributed|consisten\w*|migrat\w*|architect\w*|"
        r"security|vulnerab\w*|crypto\w*|auth\w*|refactor\w*|redesign|rewrite|protocol)\b")
UNKNOWN = r"\b(why|investigate|root cause|figure out|no idea|sometimes|randomly|only in prod|cannot reproduce)\b"


def features(task: str) -> dict:
    t = task.lower()
    return {
        "words": len(t.split()),
        "easy": len(re.findall(EASY, t)),
        "hard": len(re.findall(HARD, t)),
        "unknown": len(re.findall(UNKNOWN, t)),
        "files": len(re.findall(r"[\w/.-]+\.(py|ts|js|go|rs|java|rb|tsx|jsx|sql|yaml|yml)\b", t)),
        "code": task.count("```") // 2 + task.count("Traceback"),
        "across": 1 if re.search(r"\b(across|every|all (the )?(files|services|modules)|codebase)\b", t) else 0,
    }


def _x(f: dict) -> float:
    return (-1.0 * f["easy"] + 1.1 * f["hard"] + 1.3 * f["unknown"] + 0.35 * f["files"]
            + 0.5 * f["code"] + 0.6 * f["across"] + min(1.5, f["words"] / 40))


def reflex_difficulty(state: dict, q: Score, key: str) -> Answer:
    x = _x(state["features"])
    centres = (-0.6, 0.6, 1.8, 3.0, 4.2)
    w = [2.718 ** (-((x - c) ** 2) / 0.9) for c in centres]
    return score_answer(q, w, f"signal {x:.2f}", "local")


def reflex_tier(state: dict, q: Choice, key: str) -> Answer:
    x = _x(state["features"])
    return choice_answer(q, {"fast": 1.2 - 1.2 * x, "standard": 1.0 - abs(x - 1.5) * 0.9,
                             "frontier": -2.2 + 1.2 * x}, f"signal {x:.2f}", "local", 0.8)


def reflex_effort(state: dict, q: Choice, key: str) -> Answer:
    f = state["features"]
    x = _x(f) + 0.8 * f["unknown"]
    return choice_answer(q, {"low": 1.0 - 1.1 * x, "medium": 1.0 - abs(x - 1.6) * 0.8,
                             "high": -2.0 + 1.1 * x}, f"signal {x:.2f}", "local", 0.8)


REFLEXES = {"difficulty": reflex_difficulty, "tier": reflex_tier, "effort": reflex_effort}


def route(task: str, mind: Mind, threshold: float = 0.3) -> dict:
    f = features(task)
    d = mind.ask(SKILL, task, {"task": task, "features": f, "terms": lexical.tokens(task)[:40]}, {
        "difficulty": Score("How difficult is this programming task for a capable engineer?", LEVELS),
        "tier": Choice("Which model tier should handle this task?", TIERS),
        "effort": Choice("How much reasoning effort does this task need?", EFFORT),
    }, REFLEXES, drive="tier", act_when=lambda a: True, threshold=threshold)
    tier, effort = str(d["tier"].value), str(d["effort"].value)
    # Unsure about the tier? Go one up, not one down: under-powering a hard
    # task costs a failed attempt; over-powering an easy one costs cents.
    if d.action == "escalate":
        tier = {"fast": "standard", "standard": "frontier"}.get(tier, tier)
    return {
        "id": d.id, "task": task, "difficulty": LEVELS[d["difficulty"].level].split(":")[0],
        "difficulty_score": round(float(d["difficulty"].value), 3), "tier": tier, "effort": effort,
        "confidence": round(d["tier"].confidence, 3), "escalated": d.action == "escalate",
        "features": f,
    }
