"""
The record: every decision, before anyone knows how it went.

`~/.jevbrain/ledger.jsonl` — append-only, one JSON object per line, each hashed
over the one before it:

    hash_n = sha256( seq | at | kind | body | hash_{n-1} )

    decision    a skill asked the brain, the brain answered, the gate acted
    outcome     later: what actually turned out to be true, for one decision
    note        anything written by hand

An outcome never edits a decision. It is a second line that points at the
first. Change one confidence after the fact and every hash after it stops
matching — `jevbrain doctor` says where.

Tamper-evident, not tamper-proof: whoever holds the file can rewrite all of it.
What it catches is the single quiet edit, which is the one that happens.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

GENESIS = "0" * 64
KINDS = ("decision", "outcome", "note")


def home() -> Path:
    return Path(os.environ.get("JEVBRAIN_HOME", "~/.jevbrain")).expanduser()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def digest(seq: int, at: str, kind: str, body: dict, prev: str) -> str:
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256("|".join([str(seq), at, kind, payload, prev]).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Entry:
    seq: int
    at: str
    kind: str
    body: dict
    prev: str
    hash: str


class Ledger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or home() / "ledger.jsonl"
        self.entries: list[Entry] = list(read(self.path))

    @property
    def head(self) -> str:
        return self.entries[-1].hash if self.entries else GENESIS

    def __len__(self) -> int:
        return len(self.entries)

    def append(self, kind: str, body: dict[str, Any], at: str | None = None) -> Entry:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        at = at or now()
        seq, prev = len(self.entries), self.head
        e = Entry(seq, at, kind, body, prev, digest(seq, at, kind, body, prev))
        self.entries.append(e)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"seq": seq, "at": at, "kind": kind, "body": body,
                                 "prev": prev, "hash": e.hash},
                                sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        return e

    def decisions(self) -> list[Entry]:
        return [e for e in self.entries if e.kind == "decision"]

    def outcomes(self) -> dict[str, dict]:
        """decision id → {question key → truth}, latest label winning."""
        out: dict[str, dict] = {}
        for e in self.entries:
            if e.kind == "outcome":
                out.setdefault(e.body["id"], {})[e.body.get("key", "*")] = e.body["truth"]
        return out

    def find(self, prefix: str) -> Entry | None:
        hits = [e for e in self.decisions() if e.body.get("id", "").startswith(prefix)]
        return hits[0] if len(hits) == 1 else None


def read(path: Path) -> Iterator[Entry]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
            yield Entry(d["seq"], d["at"], d["kind"], d["body"], d["prev"], d["hash"])
        except (ValueError, KeyError):
            continue


@dataclass(frozen=True)
class Verdict:
    ok: bool
    lines: int
    head: str
    broke_at: int | None = None
    reason: str = ""


def verify(path: Path) -> Verdict:
    if not path.exists():
        return Verdict(True, 0, GENESIS, None, "no ledger yet")
    prev, n = GENESIS, 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            return Verdict(False, n, prev, n, "a line is not JSON")
        if d.get("seq") != n:
            return Verdict(False, n, prev, n, f"sequence jumped at {n}")
        if d.get("prev") != prev:
            return Verdict(False, n, prev, n, "prev hash does not match the line before")
        if d.get("hash") != digest(d["seq"], d["at"], d["kind"], d["body"], d["prev"]):
            return Verdict(False, n, prev, n, "this line was edited after it was written")
        prev, n = d["hash"], n + 1
    return Verdict(True, n, prev)
