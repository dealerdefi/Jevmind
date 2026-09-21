"""
navigate — find the files an issue is about, one directory at a time.

Inspired by Blink (ellipsis-dev/blink): instead of stuffing a whole repository
into a context window, walk it. At each directory the brain gets one Choice —
which child is most likely to hold what this issue is about — and the walk
follows the best three branches down until it reaches files.

    jevbrain navigate "refresh tokens can be reused" --repo .

Each option is described by evidence, not by its name alone: a directory by the
names inside it, a file by its definitions and first lines. The brain never
sees a file it was not offered, and every step is a decision in the ledger.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import lexical
from ..mind import Mind
from ..questions import Answer, Choice, choice_answer

SKILL = "navigate"

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache",
        ".pytest_cache", ".tox", ".idea", ".vscode", "target", ".next", "coverage", ".ruff_cache"}
TEXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c",
        ".h", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".sh", ".md", ".toml", ".yaml", ".yml",
        ".json", ".sql", ".html", ".css", ".vue", ".svelte", ".txt", ".cfg", ".ini"}
DEFS = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn|interface|type|"
                  r"struct|enum|const|let|var|pub fn|impl)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def describe_file(p: Path, limit: int = 3000) -> str:
    try:
        head = p.read_text(encoding="utf-8", errors="ignore")[:limit * 4]
    except OSError:
        return p.name
    names = DEFS.findall(head)
    first = " ".join(head.splitlines()[:12])
    return f"{p.name} · defines {' '.join(names[:40])} · {first[:limit]}"


def describe_dir(p: Path, depth: int = 3) -> str:
    names: list[str] = []
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        rel = Path(root).relative_to(p)
        if len(rel.parts) >= depth:
            dirs[:] = []
        names.extend(str(rel / f) for f in files if Path(f).suffix in TEXT)
        if len(names) > 300:
            break
    sample = []
    for n in names[:60]:
        fp = p / n
        if fp.suffix in {".py", ".ts", ".js", ".go", ".rs"}:
            try:
                sample.extend(DEFS.findall(fp.read_text(encoding="utf-8", errors="ignore")[:4000])[:6])
            except OSError:
                pass
    return f"{p.name}/ · contains {' '.join(names[:80])} · defines {' '.join(sample[:120])}"


def children(d: Path) -> list[Path]:
    out = []
    for c in sorted(d.iterdir()):
        if c.name in SKIP or c.name.startswith("."):
            continue
        if c.is_dir() or c.suffix in TEXT:
            out.append(c)
    return out


def reflex_next(state: dict, q: Choice, key: str) -> Answer:
    """
    Each option is scored by the best-matching file beneath it, from one BM25
    index over the whole repository — so a big directory is not punished for
    being big, and a directory is only as good as the best thing inside it.
    """
    names = list(q.criteria)
    best = state.get("subtree_best", {})
    s = [best.get(n, 0.0) + 0.8 * lexical.overlap(state["issue"], n.replace("/", " ").replace("_", " "))
         for n in names]
    return choice_answer(q, dict(zip(names, s)), "best BM25 match of the issue beneath each option",
                         "local", temperature=0.6)


def index(repo: Path) -> dict[str, float]:
    """Every text file in the repository, relative path → description."""
    docs: dict[str, str] = {}
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
        for f in files:
            fp = Path(root) / f
            if fp.suffix in TEXT and len(docs) < 5000:
                docs[str(fp.relative_to(repo))] = str(fp.relative_to(repo)) + " " + describe_file(fp)
    return docs


REFLEXES = {"next": reflex_next}


@dataclass
class Hit:
    path: str
    p: float
    trail: list[str] = field(default_factory=list)


def navigate(issue: str, repo: Path, mind: Mind, beam: int = 3, depth: int = 6,
             top: int = 8) -> tuple[list[Hit], list[str]]:
    repo = repo.resolve()
    docs = index(repo)
    paths = list(docs)
    scores = dict(zip(paths, lexical.BM25(list(docs.values())).scores(issue))) if paths else {}
    # Tests and docs describe the code in the same words; the code is the target.
    for pth in paths:
        if re.search(r"(^|/)(tests?|spec|__tests__)/|(^|/)test_|_test\.|\.test\.|\.spec\.", pth):
            scores[pth] *= 0.7
        elif re.search(r"\.(md|rst|txt)$|(^|/)docs/", pth):
            scores[pth] *= 0.6
    frontier: list[tuple[Path, float, list[str]]] = [(repo, 1.0, [])]
    files: list[Hit] = []
    ids: list[str] = []
    for _ in range(depth):
        nxt: list[tuple[Path, float, list[str]]] = []
        for d, prob, trail in frontier:
            kids = children(d)
            if not kids:
                continue
            if len(kids) == 1:
                only = kids[0]
                (files.append(Hit(str(only.relative_to(repo)), prob, trail + [only.name]))
                 if only.is_file() else nxt.append((only, prob, trail + [only.name])))
                continue
            crit = {}
            for k in kids[:40]:
                label = k.name + ("/" if k.is_dir() else "")
                crit[label] = describe_dir(k) if k.is_dir() else describe_file(k)
            best = {}
            for k in kids[:40]:
                label = k.name + ("/" if k.is_dir() else "")
                rel = str(k.relative_to(repo))
                best[label] = round(max((v for pth, v in scores.items()
                                         if pth == rel or pth.startswith(rel + "/")), default=0.0), 4)
            dec = mind.ask(SKILL, f"{d.relative_to(repo) or '.'} · {issue}",
                           {"issue": issue, "at": str(d.relative_to(repo)) or ".", "subtree_best": best},
                           {"next": Choice(f"Which of these, inside {d.relative_to(repo) or 'the repository root'}, "
                                           f"is most likely to contain the code this issue is about?", crit)},
                           REFLEXES)
            ids.append(dec.id)
            probs = dec["next"].probabilities or {}
            ranked = sorted(probs.items(), key=lambda kv: -kv[1])
            for label, pr in ranked[:beam]:
                child = d / label.rstrip("/")
                if child.is_dir():
                    nxt.append((child, prob * pr, trail + [label]))
                else:
                    files.append(Hit(str(child.relative_to(repo)), prob * pr, trail + [label]))
        if not nxt:
            break
        nxt.sort(key=lambda x: -x[1])
        frontier = nxt[: beam * 2]
    files.sort(key=lambda h: -h.p)
    seen, out = set(), []
    for h in files:
        if h.path not in seen:
            seen.add(h.path)
            out.append(h)
    return out[:top], ids
