"""
walk — follow a question through a linked vault, one edge at a time.

Inspired by neo4jev (jexp/neo4jev): at every node, judge which edge is most
worth taking next, and keep going until the brain says the answer is here.
The graph is any folder of markdown with `[[wikilinks]]` — an Obsidian vault,
a Zettelkasten, a docs site.

    jevbrain walk "how do refresh tokens rotate" --vault ~/notes --start index

Each step is one batch: a Choice over the outgoing links (plus `stop`), and a
Noul — *the answer is on this page*. The walk never revisits a page, never
invents a link, and stops the moment the brain is sure it has arrived.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import lexical
from ..mind import Mind
from ..questions import Answer, Choice, Noul, choice_answer, noul_answer, sigmoid

SKILL = "walk"
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


class Vault:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pages: dict[str, Path] = {}
        for p in root.rglob("*.md"):
            if any(part.startswith(".") for part in p.relative_to(root).parts):
                continue
            self.pages.setdefault(p.stem.lower(), p)
            self.pages.setdefault(str(p.relative_to(root).with_suffix("")).lower(), p)

    def find(self, name: str) -> Path | None:
        n = name.strip().lower().removesuffix(".md")
        return self.pages.get(n) or self.pages.get(n.split("/")[-1])

    def text(self, p: Path) -> str:
        return p.read_text(encoding="utf-8", errors="ignore")

    def links(self, p: Path) -> list[Path]:
        out, seen = [], set()
        for m in LINK.finditer(self.text(p)):
            t = self.find(m.group(1))
            if t and t != p and t not in seen:
                seen.add(t)
                out.append(t)
        return out


def reflex_next(state: dict, q: Choice, key: str) -> Answer:
    names = [n for n in q.criteria if n != "stop"]
    bm = lexical.BM25([q.criteria[n] for n in names] or [""])
    s = bm.scores(state["question"])
    here = state["here_relevance"]
    w = {n: x + 1.2 * lexical.overlap(state["question"], n) for n, x in zip(names, s)}
    w["stop"] = 3.2 * here - 0.8
    return choice_answer(q, w, "bm25 of the question over each linked page", "local", 0.9)


def reflex_here(state: dict, q: Noul, key: str) -> Answer:
    here = state["here_relevance"]
    return noul_answer(sigmoid(-2.2 + 4.2 * here), f"{here:.0%} of the question's terms are on this page",
                       "local")


REFLEXES = {"next": reflex_next, "answer_here": reflex_here}


@dataclass
class Step:
    page: str
    p_here: float
    took: str
    why: str
    decision: str


def walk(question: str, vault: Vault, start: str, mind: Mind, max_steps: int = 8) -> list[Step]:
    cur = vault.find(start)
    if cur is None:
        raise FileNotFoundError(f"no page called {start!r} in {vault.root}")
    visited: set[Path] = set()
    steps: list[Step] = []
    for _ in range(max_steps):
        visited.add(cur)
        text = vault.text(cur)
        links = [p for p in vault.links(cur) if p not in visited][:30]
        crit = {p.stem: vault.text(p)[:1500] for p in links}
        crit["stop"] = "the answer is on the current page; stop walking"
        if len(crit) < 2:
            crit["stop"] = "no unvisited links; stop"
            crit["(nowhere)"] = "there is nowhere else to go"
        here = lexical.overlap(question, text)
        d = mind.ask(SKILL, f"{cur.stem} · {question}", {
            "question": question, "page": cur.stem, "text": text[:4000],
            "here_relevance": round(here, 4)}, {
            "answer_here": Noul(f"The page '{cur.stem}' answers the question."),
            "next": Choice("Which linked page should the walk follow next to answer the "
                           "question — or stop here?", crit),
        }, REFLEXES)
        nxt = str(d["next"].value)
        steps.append(Step(cur.stem, d["answer_here"].p, nxt, d["next"].why, d.id))
        if nxt in ("stop", "(nowhere)") or (d["answer_here"].p >= 0.85 and d["answer_here"].confidence >= 0.7):
            break
        target = next((p for p in links if p.stem == nxt), None)
        if target is None:
            break
        cur = target
    return steps
