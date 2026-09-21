"""
compact — context garbage collection.

Inspired by winnow (GhalebDweikat/winnow) and fast-jev-compaction
(tamaratran/fast-jev-compaction): when a tool spits out a wall of text, decide
which parts still matter to the task, and drop the rest **without rewriting
anything that is kept**. Summaries lie by omission and by paraphrase; a kept
block here is byte-for-byte what the tool printed.

    cat build.log | jevbrain compact "why does the auth test fail"

The text is cut into blocks — paragraphs, or runs of lines — and every block is
one Noul in a single batch: "this block is needed to do the task". Blocks the
brain is sure about are kept or dropped; blocks it is unsure about are **kept**,
because dropping something you needed costs more than reading something you
did not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import lexical
from ..mind import ESCALATE, HOLD, Mind, gate
from ..questions import Answer, Noul, noul_answer, sigmoid

SKILL = "compact"
MAX_BLOCK_LINES = 12

#: Lines that are nearly always worth keeping when something went wrong.
SIGNAL = re.compile(
    r"(traceback|exception|error|fail(ed|ure)?|assert|panic|fatal|segfault|denied|refused|"
    r"timeout|timed out|not found|undefined|cannot|can't|unexpected|expected .* got|✗|FAIL)",
    re.I)
NOISE = re.compile(r"^\s*(\[?\d{2}:\d{2}:\d{2}|\d+%|downloading|installing|collecting|"
                   r"requirement already|using cached|ok$|passed$|\.+$|-{3,}|={3,})", re.I)


@dataclass
class Block:
    i: int
    start: int
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def blocks(text: str, max_lines: int = MAX_BLOCK_LINES) -> list[Block]:
    """Paragraphs first; a paragraph longer than max_lines is cut into runs."""
    out: list[Block] = []
    cur: list[str] = []
    start = 0
    for n, line in enumerate(text.splitlines()):
        if not line.strip():
            if cur:
                out.extend(_split(cur, start, max_lines, len(out)))
                cur = []
            start = n + 1
            continue
        if not cur:
            start = n
        cur.append(line)
    if cur:
        out.extend(_split(cur, start, max_lines, len(out)))
    for i, b in enumerate(out):
        b.i = i
    return out


def _split(lines: list[str], start: int, max_lines: int, base: int) -> list[Block]:
    return [Block(base + k, start + j, lines[j:j + max_lines])
            for k, j in enumerate(range(0, len(lines), max_lines))]


def reflex_needed(state: dict, q: Noul, key: str) -> Answer:
    """Relevance to the task, error signal, and how much of the block is noise."""
    i = int(key.split(":")[1])
    blk = state["blocks"][i]
    b, task = blk["text"], state["task"]
    lines = b.splitlines() or [""]
    rel = blk["relevance"]                        # BM25 of the task vs this block, 0..1 of the max
    sig = sum(1 for ln in lines if SIGNAL.search(ln)) / len(lines)
    noise = sum(1 for ln in lines if NOISE.search(ln)) / len(lines)
    ov = lexical.overlap(task, b)
    x = -1.6 + 3.2 * rel + 2.2 * min(1.0, sig * 2) + 1.4 * ov - 1.8 * noise
    p = sigmoid(x)
    why = f"relevance {rel:.2f} · error lines {sig:.0%} · task terms {ov:.0%} · noise {noise:.0%}"
    return noul_answer(p, why, "local")


REFLEXES = {"needed": reflex_needed}


@dataclass
class Result:
    kept: list[Block]
    dropped: list[Block]
    unsure: list[Block]
    text: str
    before: int
    after: int
    decision_ids: list[str]


def compact(text: str, task: str, mind: Mind, keep_threshold: float = 0.5,
            batch: int = 40) -> Result:
    bs = blocks(text)
    if not bs:
        return Result([], [], [], "", 0, 0, [])
    bm = lexical.BM25([b.text for b in bs])
    rel = lexical.normalise(bm.scores(task))

    kept, dropped, unsure, ids = [], [], [], []
    for i in range(0, len(bs), batch):
        chunk = bs[i:i + batch]
        state = {"task": task, "blocks": [
            {"n": b.i, "text": b.text, "relevance": round(rel[b.i], 4)} for b in chunk]}
        # One call per batch, one Noul per block — keyed by its index in this batch.
        questions = {f"needed:{k}": Noul(
            f"Block {b.i} of the tool output (blocks[{k}]) contains information needed to "
            f"accomplish the task; dropping it would lose something the task depends on.")
            for k, b in enumerate(chunk)}
        d = mind.ask(SKILL, f"{len(chunk)} blocks · {task}", state, questions, REFLEXES)
        ids.append(d.id)
        for k, b in enumerate(chunk):
            action, _ = gate(d.answers[f"needed:{k}"], 0.3, lambda a: a.p >= keep_threshold)
            (dropped if action == HOLD else unsure if action == ESCALATE else kept).append(b)

    keep_ids = {b.i for b in kept} | {b.i for b in unsure}
    out: list[str] = []
    gap = 0
    for b in bs:
        if b.i in keep_ids:
            if gap:
                out.append(f"[… {gap} line{'s' if gap != 1 else ''} dropped by jevbrain compact …]")
                gap = 0
            out.append(b.text)
        else:
            gap += len(b.lines)
    if gap:
        out.append(f"[… {gap} line{'s' if gap != 1 else ''} dropped by jevbrain compact …]")
    result = "\n\n".join(out)
    return Result(kept, dropped, unsure, result, len(text), len(result), ids)
