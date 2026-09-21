"""
curate — decide which records deserve to be trained on.

Inspired by jev-curate (AkashPriyadarshii/jev-curate): before a JSONL file goes
into the next training round, every record is judged on three things in one
batch, and code keeps or drops it:

    quality    Score   junk · weak · usable · good
    risky      Noul    it contains personal data, a secret, or something that
                       should not be learned
    keep       Choice  keep · drop · review

    jevbrain curate data.jsonl --out kept.jsonl --dropped dropped.jsonl

Near-duplicates are caught before the brain is asked (a shingle fingerprint),
because a record that is 95% the same as one already kept teaches nothing new
and costs a call to find that out.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from ..mind import ESCALATE, Mind
from ..questions import Answer, Choice, Noul, Score, choice_answer, noul_answer, score_answer, sigmoid

SKILL = "curate"
QUALITY = ("junk: empty, broken, or meaningless", "weak: thin, truncated, or off-topic",
           "usable: correct but plain", "good: clear, complete, worth learning from")
KEEP = {"keep": "include it in the next training round", "drop": "leave it out",
        "review": "a person should look before it is used"}

PII = [
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "an email address"),
    (r"(?<![\d\w])(\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)", "a phone number"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "an SSN-shaped number"),
    (r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b", "a card-shaped number"),
    (r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[bp]-[A-Za-z0-9-]{10,})", "an API key"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "a private key"),
    (r"\b0x[a-fA-F0-9]{64}\b", "a 32-byte hex secret"),
]
JUNK = re.compile(r"^(\W*|lorem ipsum.*|test|asdf+|n/?a|null|none|todo)$", re.I)


def text_of(rec: dict) -> str:
    for k in ("text", "content", "completion", "output", "response", "answer"):
        if isinstance(rec.get(k), str):
            prompt = rec.get("prompt") or rec.get("instruction") or rec.get("input") or ""
            return f"{prompt}\n{rec[k]}".strip() if isinstance(prompt, str) else rec[k]
    if isinstance(rec.get("messages"), list):
        return "\n".join(str(m.get("content", "")) for m in rec["messages"] if isinstance(m, dict))
    return json.dumps(rec, ensure_ascii=False)


def shingles(text: str, k: int = 5) -> set[int]:
    w = re.findall(r"\w+", text.lower())
    return {int(hashlib.blake2b(" ".join(w[i:i + k]).encode(), digest_size=6).hexdigest(), 16)
            for i in range(max(1, len(w) - k + 1))}


def jaccard(a: set[int], b: set[int]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def features(text: str) -> dict:
    words = re.findall(r"\w+", text)
    lines = text.splitlines() or [""]
    uniq = len(set(w.lower() for w in words)) / len(words) if words else 0.0
    return {
        "chars": len(text), "words": len(words), "unique_ratio": round(uniq, 3),
        "junk": bool(JUNK.match(text.strip())),
        "truncated": bool(re.search(r"(\.\.\.|…|[,;:(\-]|\b(and|or|but|the|a|an|to|of|with|because))\s*$",
                                    text.strip(), re.I)) and len(words) > 3,
        "repeated_line_ratio": round(1 - len(set(lines)) / len(lines), 3),
        "pii": [label for pat, label in PII if re.search(pat, text)],
        "non_ascii_ratio": round(sum(1 for c in text if ord(c) > 127) / max(1, len(text)), 3),
    }


def reflex_quality(state: dict, q: Score, key: str) -> Answer:
    f = state["features"]
    if f["junk"] or f["words"] < 3:
        return score_answer(q, [0.85, 0.1, 0.04, 0.01], "empty or placeholder", "local")
    x = (min(2.0, f["words"] / 40) + 1.5 * f["unique_ratio"] - 1.2 * f["truncated"]
         - 2.0 * f["repeated_line_ratio"] - 0.8 * (f["non_ascii_ratio"] > 0.4))
    centres = (-0.5, 0.8, 1.9, 3.0)
    w = [2.718 ** (-((x - c) ** 2) / 0.7) for c in centres]
    return score_answer(q, w, f"{f['words']} words, {f['unique_ratio']:.0%} distinct"
                        + (", looks cut off" if f["truncated"] else ""), "local")


def reflex_risky(state: dict, q: Noul, key: str) -> Answer:
    pii = state["features"]["pii"]
    p = 0.97 if any(k in pii for k in ("an API key", "a private key", "a 32-byte hex secret")) else \
        0.88 if pii else 0.04
    return noul_answer(p, "contains " + ", ".join(pii) if pii else "nothing sensitive found", "local")


def reflex_keep(state: dict, q: Choice, key: str) -> Answer:
    ql = reflex_quality(state, Score("", QUALITY), key)
    rk = reflex_risky(state, Noul(""), key)
    x = float(ql.value) - 4.0 * rk.p
    return choice_answer(q, {"keep": -1.5 + 1.4 * x, "drop": 1.0 - 1.3 * x - (0 if rk.p < 0.5 else -1.0),
                             "review": 0.4 - abs(x - 1.2) * 0.9 + (1.2 if 0.3 < rk.p < 0.95 else 0)},
                         f"quality {float(ql.value):.1f}, risk {rk.p:.2f}", "local", 0.7)


REFLEXES = {"quality": reflex_quality, "risky": reflex_risky, "keep": reflex_keep}


@dataclass
class Report:
    kept: list[dict] = field(default_factory=list)
    dropped: list[tuple[dict, str]] = field(default_factory=list)
    review: list[tuple[dict, str]] = field(default_factory=list)
    duplicates: int = 0
    broken: int = 0


def curate(lines: list[str], mind: Mind, dup_threshold: float = 0.85,
           threshold: float = 0.35) -> Report:
    rep = Report()
    seen: list[set[int]] = []
    for n, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
            if not isinstance(rec, dict):
                raise ValueError
        except ValueError:
            rep.broken += 1
            rep.dropped.append(({"line": n, "raw": raw[:200]}, "not a JSON object"))
            continue
        text = text_of(rec)
        sh = shingles(text)
        if any(jaccard(sh, s) >= dup_threshold for s in seen[-2000:]):
            rep.duplicates += 1
            rep.dropped.append((rec, "near-duplicate of a record already kept"))
            continue
        f = features(text)
        d = mind.ask(SKILL, f"line {n}: {text[:80]}", {"record": text[:4000], "features": f}, {
            "quality": Score("How useful is this record as training data?", QUALITY),
            "risky": Noul("This record contains personal data, a credential, or anything else "
                          "that a model should not learn and later repeat."),
            "keep": Choice("Should this record go into the next training round?", KEEP),
        }, REFLEXES, drive="keep", act_when="keep", escalate_when="review", threshold=threshold)
        choice = str(d["keep"].value)
        why = d["keep"].why
        if d.action == ESCALATE or choice == "review":
            rep.review.append((rec, why))
        elif choice == "keep":
            rep.kept.append(rec)
            seen.append(sh)
        else:
            rep.dropped.append((rec, why))
    return rep
