"""
The brain: something that takes a state and typed questions and returns typed
answers. Three of them share one interface.

    local    offline, free, deterministic. Reflexes written for each skill, a
             lexical engine for everything else, and a calibration layer
             fitted from its own graded history (`jevmind learn`).
    jev      TypeSafe's Jev over HTTP: POST /v1/systemone, Bearer key,
             {state, model, questions} → {answers, usage, model}. The wire
             format is the one typesafe-mcp and semdecide speak.
    replay   answers from a tape recorded off either of the others, so a run
             can be repeated to the byte without a network or a bill.

Every call returns a `Thought`: the answers, how long it took, what it cost, and
a fingerprint of exactly what the brain was shown.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from . import lexical
from .questions import (Answer, Choice, Noul, Question, Score, choice_answer, logit,
                        noul_answer, score_answer, sigmoid)

#: (state, question, key) → answer. The key lets one reflex serve `needed:0` … `needed:39`.
Reflex = Callable[[Any, Question, str], Answer]

DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"

#: TypeSafe's published price: $0.042 per million input tokens, output free.
PRICE_PER_M_INPUT = 0.042


class BrainError(RuntimeError):
    pass


def fingerprint(state: Any, questions: dict[str, Question]) -> str:
    blob = json.dumps({"state": state, "questions": {k: q.wire() for k, q in questions.items()}},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def estimate_tokens(state: Any, questions: dict[str, Question]) -> int:
    """About four characters a token. An estimate, labelled as one everywhere it shows."""
    blob = json.dumps({"state": state, "questions": {k: q.wire() for k, q in questions.items()}},
                      separators=(",", ":"), ensure_ascii=False, default=str)
    return max(1, len(blob) // 4)


@dataclass
class Thought:
    brain: str
    evidence: str
    answers: dict[str, Answer]
    latency_ms: float
    input_tokens: int
    tokens_estimated: bool
    model: str = ""

    @property
    def cost_usd(self) -> float:
        return 0.0 if self.brain == "local" else self.input_tokens / 1e6 * PRICE_PER_M_INPUT


class Brain(Protocol):
    name: str

    def think(self, state: Any, questions: dict[str, Question]) -> Thought:
        ...


# ── local ────────────────────────────────────────────────────────────────────


def _state_text(state: Any) -> str:
    if isinstance(state, str):
        return state
    return json.dumps(state, ensure_ascii=False, default=str)


def lexical_answer(state: Any, q: Question) -> Answer:
    """
    The fallback for a question no reflex covers. Honest about what it is: word
    overlap between the question and the evidence. Its confidence is kept low on
    purpose, so a gate set sensibly will escalate rather than act on it.
    """
    text = _state_text(state)
    if isinstance(q, Choice):
        names = list(q.criteria)
        docs = [f"{n} {d}" for n, d in q.criteria.items()]
        bm = lexical.BM25(docs)
        weights = bm.scores(text)
        w = {n: x for n, x in zip(names, weights)}
        a = choice_answer(q, w, "lexical match between the state and each option", "local:lexical",
                          temperature=1.5)
        return Answer(a.kind, a.value, a.confidence * 0.6, a.probabilities, a.why, a.by)
    if isinstance(q, Score):
        docs = list(q.criteria)
        bm = lexical.BM25(docs)
        weights = bm.scores(text)
        probs = [x + 0.25 for x in weights]
        a = score_answer(q, probs, "lexical match between the state and each level", "local:lexical")
        return Answer(a.kind, a.value, a.confidence * 0.6, a.probabilities, a.why, a.by)
    # Word overlap can say a statement is *about* the state; it cannot say it is
    # true. So it moves p only a little, and only upward, and says so.
    ov = lexical.overlap(q.instructions, text)
    p = 0.5 + 0.2 * ov
    return noul_answer(p, f"no rule for this question; {ov:.0%} of its terms appear in the state "
                          f"(a floor, not a judgement — use --brain jev for open questions)", "local:lexical")


@dataclass
class Calibration:
    """
    Platt scaling per question key: p' = sigmoid(a · logit(p) + b), fitted from
    graded decisions. Identity until something has been learned.
    """

    params: dict[str, tuple[float, float]] = field(default_factory=dict)

    def apply(self, key: str, p: float) -> float:
        a, b = self.params.get(key, (1.0, 0.0))
        return sigmoid(a * logit(p) + b)

    @classmethod
    def load(cls, path: Path) -> "Calibration":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls({k: (float(v[0]), float(v[1])) for k, v in raw.items()})
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({k: list(v) for k, v in sorted(self.params.items())}, indent=1),
                        encoding="utf-8")


class LocalBrain:
    name = "local"

    def __init__(self, reflexes: dict[str, Reflex] | None = None, skill: str = "",
                 calibration: Calibration | None = None) -> None:
        self.reflexes = reflexes or {}
        self.skill = skill
        self.calibration = calibration or Calibration()

    def think(self, state: Any, questions: dict[str, Question]) -> Thought:
        t0 = time.perf_counter()
        out: dict[str, Answer] = {}
        for key, q in questions.items():
            reflex = self.reflexes.get(key) or self.reflexes.get(key.split(":")[0])
            a = reflex(state, q, key) if reflex else lexical_answer(state, q)
            if a.kind == "noul":
                cal_key = f"{self.skill}/{key.split(':')[0]}"
                if cal_key in self.calibration.params:
                    raw = a.p
                    c = noul_answer(self.calibration.apply(cal_key, raw), a.why + " · calibrated",
                                    a.by or "local")
                    a = Answer(c.kind, c.value, c.confidence, None, c.why, c.by, raw)
            if not a.by:
                a = Answer(a.kind, a.value, a.confidence, a.probabilities, a.why, "local")
            out[key] = a
        return Thought("local", fingerprint(state, questions), out,
                       (time.perf_counter() - t0) * 1000, estimate_tokens(state, questions), True)


# ── jev ──────────────────────────────────────────────────────────────────────


def load_key() -> str | None:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    path = Path(os.environ.get("TYPESAFE_CREDENTIALS_FILE", "~/.config/typesafe/credentials.env")).expanduser()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().removeprefix("export ").strip()
            if line.startswith("TYPESAFE_API_KEY="):
                v = line.split("=", 1)[1].strip().strip("'\"")
                if v:
                    return v
    except OSError:
        return None
    return None


def endpoint() -> str:
    base = os.environ.get("TYPESAFE_BASE_URL")
    if base:
        return base.rstrip("/") + "/v1/systemone"
    return DEFAULT_URL


def _num(v: Any, where: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise BrainError(f"jev answered {where} with {v!r}, not a number")
    x = float(v)
    if not (lo - 1e-9 <= x <= hi + 1e-9):
        raise BrainError(f"jev answered {where} = {x}, outside {lo}..{hi}")
    return x


def parse_answers(body: Any, questions: dict[str, Question]) -> tuple[dict[str, Answer], dict, str]:
    """
    Validate a System One response against the questions that were asked.

    Strict at the envelope, forgiving per answer: a malformed answer to one
    question becomes a zero-confidence answer to that question, which every
    gate refuses, instead of sinking the whole batch.
    """
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise BrainError("jev response has no answers object")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    model = body.get("model") if isinstance(body.get("model"), str) else ""
    out: dict[str, Answer] = {}
    for key, q in questions.items():
        raw = body["answers"].get(key)
        try:
            if not isinstance(raw, dict):
                raise BrainError(f"no answer for {key}")
            if isinstance(q, Noul):
                out[key] = noul_answer(_num(raw.get("noul"), f"{key}.noul", 0, 1), "jev", "jev")
            elif isinstance(q, Choice):
                c = raw.get("choice")
                if c not in q.criteria:
                    raise BrainError(f"{key}: chose {c!r}, which was not offered")
                probs = raw.get("probabilities") or {}
                probs = {n: _num(probs.get(n, 0.0), f"{key}.probabilities.{n}", 0, 1) for n in q.criteria}
                out[key] = Answer("choice", c, _num(raw.get("confidence"), f"{key}.confidence", 0, 1),
                                  probs, "jev", "jev")
            else:
                n = len(q.criteria)
                probs = raw.get("probabilities")
                if isinstance(probs, dict):
                    probs = [probs.get(str(i), 0.0) for i in range(n)]
                if not isinstance(probs, list) or len(probs) != n:
                    raise BrainError(f"{key}: probabilities must have {n} values")
                probs = [_num(x, f"{key}.probabilities", 0, 1) for x in probs]
                out[key] = Answer("score", _num(raw.get("score"), f"{key}.score", 0, n - 1),
                                  _num(raw.get("confidence"), f"{key}.confidence", 0, 1),
                                  probs, "jev", "jev")
        except BrainError as e:
            neutral: Any = 0.5 if isinstance(q, Noul) else (
                next(iter(q.criteria)) if isinstance(q, Choice) else (len(q.criteria) - 1) / 2)
            out[key] = Answer(q.kind, neutral, 0.0, None, f"unreadable: {e}", "jev")
    return out, usage, model


Transport = Callable[[urllib.request.Request, float], bytes]


def _urlopen(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class JevBrain:
    name = "jev"

    def __init__(self, key: str | None = None, url: str | None = None, model: str = DEFAULT_MODEL,
                 timeout: float = 15.0, retries: int = 2, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.key = key or load_key()
        if not self.key:
            raise BrainError("no TYPESAFE_API_KEY (env or ~/.config/typesafe/credentials.env)")
        self.url, self.model, self.timeout, self.retries = url or endpoint(), model, timeout, retries
        self.transport = transport or _urlopen
        self.sleep = sleep

    def think(self, state: Any, questions: dict[str, Question]) -> Thought:
        payload = json.dumps({"model": self.model, "state": state,
                              "questions": {k: q.wire() for k, q in questions.items()}},
                             ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        req = urllib.request.Request(self.url, data=payload, method="POST", headers={
            "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
            "User-Agent": "jevmind/0.1"})
        t0 = time.perf_counter()
        for attempt in range(self.retries + 1):
            try:
                body = json.loads(self.transport(req, self.timeout))
                answers, usage, model = parse_answers(body, questions)
                tok = usage.get("input_tokens")
                est = not isinstance(tok, int)
                return Thought("jev", fingerprint(state, questions), answers,
                               (time.perf_counter() - t0) * 1000,
                               estimate_tokens(state, questions) if est else tok, est, model)
            except urllib.error.HTTPError as e:
                if (e.code == 429 or 500 <= e.code <= 599) and attempt < self.retries:
                    self.sleep(min(2.0, 0.2 * 2 ** attempt) + random.random() * 0.05)
                    continue
                # Never echo the body: a reflecting proxy could hand the key back.
                raise BrainError(f"jev returned HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt < self.retries:
                    self.sleep(min(2.0, 0.2 * 2 ** attempt))
                    continue
                raise BrainError(f"could not reach {self.url}: {type(e).__name__}") from None
            except json.JSONDecodeError:
                raise BrainError("jev answered with something that is not JSON") from None
        raise BrainError("unreachable")


# ── replay ───────────────────────────────────────────────────────────────────


class Tape:
    """Record every thought by fingerprint; replay them without a network."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.rows[r["evidence"]] = r

    def record(self, t: Thought) -> None:
        if t.evidence in self.rows:
            return
        r = {"evidence": t.evidence, "brain": t.brain, "model": t.model,
             "answers": {k: a.row() for k, a in t.answers.items()},
             "latency_ms": round(t.latency_ms, 3), "input_tokens": t.input_tokens}
        self.rows[t.evidence] = r
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


class Recorder:
    """Wraps a brain and writes everything it says to a tape."""

    def __init__(self, inner: Brain, tape: Tape) -> None:
        self.inner, self.tape, self.name = inner, tape, inner.name

    def think(self, state: Any, questions: dict[str, Question]) -> Thought:
        t = self.inner.think(state, questions)
        self.tape.record(t)
        return t


class ReplayBrain:
    name = "replay"

    def __init__(self, tape: Tape) -> None:
        self.tape = tape

    def think(self, state: Any, questions: dict[str, Question]) -> Thought:
        fp = fingerprint(state, questions)
        r = self.tape.rows.get(fp)
        if r is None:
            raise BrainError("this exact state and set of questions is not on the tape")
        answers = {k: Answer(a["kind"], a["value"], a["confidence"], a.get("probabilities"),
                             a.get("why", ""), a.get("by", "replay"))
                   for k, a in r["answers"].items()}
        return Thought(r["brain"], fp, answers, 0.0, r.get("input_tokens", 0), False,
                       r.get("model", ""))


def open_brain(name: str, reflexes: dict[str, Reflex] | None = None, skill: str = "",
               home: Path | None = None) -> Brain:
    home = home or Path(os.environ.get("JEVMIND_HOME", "~/.jevmind")).expanduser()
    if name == "local":
        return LocalBrain(reflexes, skill, Calibration.load(home / "calibration.json"))
    if name == "jev":
        return Recorder(JevBrain(), Tape(home / "tape.jsonl"))
    if name == "replay":
        return ReplayBrain(Tape(home / "tape.jsonl"))
    raise BrainError(f"unknown brain {name!r}: local, jev or replay")
