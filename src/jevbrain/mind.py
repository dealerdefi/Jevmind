"""
The mind: the one path every skill takes to a decision.

    skill builds state + questions
        → brain answers (local, jev or replay)
        → gate: is the answer that drives the action confident enough?
        → ledger: sealed, with what the brain saw, what it said, what code did
        → the skill acts, or does not

A skill never talks to a brain directly. That is what makes the dashboard
possible — every decision in the system, from every skill, goes through here
and lands in one record with one schema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .brain import Brain, LocalBrain, Reflex, Thought, open_brain
from .ledger import Ledger
from .questions import Answer, Question

ACT, HOLD, ESCALATE = "act", "hold", "escalate"


@dataclass
class Decision:
    id: str
    skill: str
    subject: str
    answers: dict[str, Answer]
    action: str
    reason: str
    thought: Thought

    def __getitem__(self, key: str) -> Answer:
        return self.answers[key]


@dataclass
class Mind:
    """Holds the brain choice, the gate, and the ledger for one run."""

    brain: str = "local"
    threshold: float = 0.5
    record: bool = True
    home: Path = field(default_factory=lambda: Path(os.environ.get("JEVBRAIN_HOME", "~/.jevbrain")).expanduser())
    decisions: list[Decision] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._brains: dict[str, Brain] = {}
        self._ledger: Ledger | None = Ledger(self.home / "ledger.jsonl") if self.record else None

    def _brain(self, skill: str, reflexes: dict[str, Reflex]) -> Brain:
        if skill not in self._brains:
            self._brains[skill] = open_brain(self.brain, reflexes, skill, self.home)
        b = self._brains[skill]
        if isinstance(b, LocalBrain) and reflexes:
            b.reflexes = reflexes              # the caller's reflexes, every call
        return b

    def ask(self, skill: str, subject: str, state: Any, questions: dict[str, Question],
            reflexes: dict[str, Reflex] | None = None, drive: str | None = None,
            act_when: Any = None, threshold: float | None = None,
            escalate_when: Any = None) -> Decision:
        """
        `drive` names the answer that decides the action. The action is:

          act        the drive answer is confident and says to act
          hold       the drive answer is confident and says not to
          escalate   the drive answer is not confident enough to trust either way

        `act_when` is the value that means "act" — an option name for a Choice,
        a callable for anything else. With no `drive`, the decision is only
        recorded and the action is `act`.
        """
        brain = self._brain(skill, reflexes or {})
        thought = brain.think(state, questions)
        th = self.threshold if threshold is None else threshold

        if drive is None:
            action, reason = ACT, "recorded"
        else:
            action, reason = gate(thought.answers[drive], th, act_when, drive, escalate_when)

        n = len(self._ledger) if self._ledger is not None else len(self.decisions)
        did = f"{skill}-{n:05d}-{thought.evidence[:6]}"
        subject = " ".join(subject.split())[:160]
        d = Decision(did, skill, subject, thought.answers, action, reason, thought)
        self.decisions.append(d)
        if self._ledger is not None:
            self._ledger.append("decision", {
                "id": did, "skill": skill, "subject": d.subject, "brain": thought.brain,
                "model": thought.model, "evidence": thought.evidence,
                "answers": {k: a.row() for k, a in thought.answers.items()},
                "action": action, "reason": reason, "drive": drive,
                "latency_ms": round(thought.latency_ms, 3),
                "input_tokens": thought.input_tokens, "tokens_estimated": thought.tokens_estimated,
                "cost_usd": round(thought.cost_usd, 9),
            })
        return d

    def label(self, decision_id: str, key: str, truth: Any) -> None:
        """Write what actually turned out to be true for one answer."""
        if self._ledger is not None:
            self._ledger.append("outcome", {"id": decision_id, "key": key, "truth": truth})

    @property
    def ledger(self) -> Ledger | None:
        return self._ledger


def gate(a: Answer, threshold: float, act_when: Any, name: str = "answer",
         escalate_when: Any = None) -> tuple[str, str]:
    """
    The whole gate, in one place: unsure escalates; sure acts or holds. An
    answer that itself says "a person should look" (`escalate_when`) escalates
    however sure it is.
    """
    yes = act_when(a) if callable(act_when) else (a.value == act_when)
    if a.confidence < threshold:
        return ESCALATE, f"{name} at confidence {a.confidence:.2f}, under {threshold:.2f}"
    if escalate_when is not None and (escalate_when(a) if callable(escalate_when) else a.value == escalate_when):
        return ESCALATE, f"{name} = {_show(a)} at {a.confidence:.2f}"
    if yes:
        return ACT, f"{name} = {_show(a)} at {a.confidence:.2f}"
    return HOLD, f"{name} = {_show(a)} at {a.confidence:.2f}"


def _show(a: Answer) -> str:
    if a.kind == "noul":
        return f"{a.p:.2f}"
    if a.kind == "score":
        return f"{float(a.value):.2f}"
    return str(a.value)
