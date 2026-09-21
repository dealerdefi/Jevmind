"""
The local brain's eyes: tokens and BM25.

Not a language model and not pretending to be one. It is the floor every local
answer stands on — which words of the question appear in the evidence, how
rare they are, and how much of the evidence they fill. It is fast, it is the
same on every machine, and when it is wrong you can see why.

Tokens split the way code is named: `parseHTTPResponse`, `parse_http_response`
and `src/http/parse.py` all yield `parse`, `http`, `response`.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+(?:\.\d+)?")

STOP = frozenset("""
a an and are as at be been but by can could did do does for from had has have
how i if in into is it its just may me more most my no not of on or our should
so some such than that the their them then there these they this those to too
up us was we were what when where which while who why will with would you your
""".split())


def tokens(text: str, keep_stop: bool = False) -> list[str]:
    out: list[str] = []
    for raw in _WORD.findall(text or ""):
        for part in _CAMEL.sub(" ", raw).split():
            w = part.lower()
            if len(w) < 2 and not w.isdigit():
                continue
            if not keep_stop and w in STOP:
                continue
            out.append(stem(w))
    return out


def stem(w: str) -> str:
    """A light suffix strip — enough that `failing`, `failed` and `fails` meet."""
    for suf in ("ations", "ation", "ings", "ing", "edly", "ed", "ies", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            if suf == "ies":
                return w[: -3] + "y"
            return w[: -len(suf)]
    return w


class BM25:
    """Okapi BM25 over a fixed set of documents."""

    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.72) -> None:
        self.k1, self.b = k1, b
        self.docs = [Counter(tokens(d)) for d in docs]
        self.lens = [sum(c.values()) for c in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter = Counter()
        for c in self.docs:
            df.update(c.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str, i: int) -> float:
        doc, dl = self.docs[i], self.lens[i]
        if not dl:
            return 0.0
        s = 0.0
        for t in set(tokens(query)):
            f = doc.get(t, 0)
            if not f:
                continue
            idf = self.idf.get(t, 0.0)
            s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
        return s

    def scores(self, query: str) -> list[float]:
        return [self.score(query, i) for i in range(len(self.docs))]


def overlap(query: str, text: str) -> float:
    """Share of the query's distinct terms that appear in the text, 0..1."""
    q = set(tokens(query))
    if not q:
        return 0.0
    t = set(tokens(text))
    return len(q & t) / len(q)


def normalise(xs: list[float]) -> list[float]:
    """Scale a list to 0..1 by its max. All-zero stays all-zero."""
    m = max(xs) if xs else 0.0
    return [x / m for x in xs] if m > 0 else [0.0 for _ in xs]
