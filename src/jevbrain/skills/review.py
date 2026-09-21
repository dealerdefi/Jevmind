"""
review — triage a diff before anyone expensive reads it.

Inspired by jev-review (devagrawal09/jev-review): most of a diff is safe, and
the part that is not is where a senior reviewer — or a pricier model — should
spend their attention. Every hunk gets two questions in one batch:

    risk     Score   low · medium · high · critical
    route    Choice  skip · model (a bigger model reviews it) · human

    git diff main | jevbrain review
    jevbrain review --repo . --base main --html review.html

The local brain reads the hunk the way a careful reviewer skims: what the file
is (a migration, CI, auth, a lockfile), what the added lines touch (secrets,
shell, SQL built from strings, eval, permissions), and what was taken away
(tests, error handling, checks). A hunk it is unsure about goes to a human —
the gate never lets an unsure answer wave something through.
"""

from __future__ import annotations

import html
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..mind import ESCALATE, Mind, gate
from ..questions import Answer, Choice, Score, choice_answer, score_answer

SKILL = "review"
RISK = ("low: formatting, docs, tests added, local refactor",
        "medium: behaviour change with limited reach",
        "high: security, data, money, auth, concurrency or public API",
        "critical: could leak secrets, lose data, or run untrusted input")
ROUTE = {"skip": "safe to merge on a skim",
         "model": "worth a careful read by a stronger model",
         "human": "a human who owns this area must read it"}

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

#: (pattern over added lines, weight, reason)
ADDED = [
    (r"\b(api[_-]?key|secret|password|passwd|private[_-]?key|access[_-]?token|auth[_-]?token)\w*\s*[:=]\s*['\"][A-Za-z0-9_\-/+=.]{12,}['\"]", 3.0, "a credential literal"),
    (r"(curl|wget)[^|\n]*\|\s*(sudo\s+)?(ba|z)?sh\b", 2.4, "a remote script piped to a shell"),
    (r"\beval\(|\bexec\(|new Function\(|pickle\.loads|yaml\.load\((?!.*SafeLoader)", 2.4, "code built at run time"),
    (r"subprocess\.|os\.system|shell\s*=\s*True|child_process|Runtime\.getRuntime", 1.8, "a shell call"),
    (r"(SELECT|INSERT|UPDATE|DELETE)\b.*(\+|%s|\{|f\")", 2.0, "SQL built from strings"),
    (r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false|InsecureSkipVerify", 2.6, "TLS verification off"),
    (r"chmod\s+777|0o?777|AllowAll|\*\s*:\s*\*|Access-Control-Allow-Origin.*\*", 1.8, "permissions opened wide"),
    (r"\b(auth|jwt|session|csrf|oauth|login|permission|role|admin|acl)\w*", 1.2, "auth code"),
    (r"\b(balance|amount|price|payment|refund|charge|invoice|transfer)\w*", 1.1, "money"),
    (r"\b(lock|mutex|thread|async|await|goroutine|atomic|race)\b", 0.7, "concurrency"),
    (r"\bDROP\s+(TABLE|COLUMN)|\bTRUNCATE\b|ALTER\s+TABLE", 2.2, "a destructive schema change"),
    (r"TODO|FIXME|HACK|XXX", 0.4, "a TODO left in"),
]
#: (pattern over removed lines, weight, reason)
REMOVED = [
    (r"\bassert\b|\bexpect\(|\bshould\b|def test_|it\(['\"]|test\(['\"]", 1.6, "a test or assertion removed"),
    (r"\bexcept\b|\bcatch\b|if err != nil|\.catch\(|\braise\b|\bthrow\b", 1.6, "error handling removed"),
    (r"\b(validate|sanitize|escape|check|verify|require|authorize)\w*", 1.5, "a check removed"),
]
#: (pattern over the path, weight, reason)
PATHS = [
    (r"(^|/)(\.github/workflows|\.gitlab-ci|Jenkinsfile|\.circleci)", 1.6, "CI"),
    (r"(^|/)(migrations?|alembic|schema)\b|\.sql$", 1.7, "a migration"),
    (r"(^|/)(Dockerfile|docker-compose|terraform|k8s|helm|\.tf$)", 1.3, "infrastructure"),
    (r"(package\.json|requirements.*\.txt|pyproject\.toml|go\.mod|Cargo\.toml|Gemfile)$", 1.0, "dependencies"),
    (r"(lock|\.lock|-lock\.json)$", 0.3, "a lockfile"),
    (r"(^|/)(auth|security|crypto|payments?|billing)(/|\.)", 1.5, "a sensitive area"),
    (r"(^|/)(tests?|spec|__tests__)/|_test\.|\.test\.|\.spec\.|test_[^/]*\.py$", -0.8, "tests"),
    (r"\.(md|rst|txt)$|(^|/)docs/", -1.5, "docs"),
]


@dataclass
class Hunk:
    path: str
    header: str
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    new_file: bool = False
    deleted_file: bool = False

    @property
    def size(self) -> int:
        return len(self.added) + len(self.removed)

    def text(self, limit: int = 60) -> str:
        lines = [f"-{x}" for x in self.removed[:limit]] + [f"+{x}" for x in self.added[:limit]]
        return "\n".join(lines)


def parse(diff: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    path, new, gone = "", False, False
    cur: Hunk | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            cur, new, gone = None, False, False
            m = re.search(r" b/(.+)$", line)
            path = m.group(1) if m else line
        elif line.startswith("new file mode"):
            new = True
        elif line.startswith("deleted file mode"):
            gone = True
        elif line.startswith("+++ "):
            p = line[4:].strip()
            if p != "/dev/null":
                path = p[2:] if p.startswith("b/") else p
        elif line.startswith("--- "):
            continue
        elif HUNK.match(line):
            cur = Hunk(path, line, new_file=new, deleted_file=gone)
            hunks.append(cur)
        elif cur is not None:
            if line.startswith("+"):
                cur.added.append(line[1:])
            elif line.startswith("-"):
                cur.removed.append(line[1:])
    return hunks


def signals(h: Hunk) -> tuple[float, list[str]]:
    x, why = 0.0, []
    add, rem = "\n".join(h.added), "\n".join(h.removed)
    # Prose that mentions a password is not a password. Docs keep a fraction.
    prose = 0.25 if re.search(r"\.(md|rst|txt)$|(^|/)docs/", h.path, re.I) else 1.0
    for pat, w, r in ADDED:
        if re.search(pat, add, re.I):
            x += w * prose
            why.append(r)
    for pat, w, r in REMOVED:
        if re.search(pat, rem, re.I) and not re.search(pat, add, re.I):
            x += w * prose
            why.append(r)
    for pat, w, r in PATHS:
        if re.search(pat, h.path, re.I):
            x += w
            why.append(r)
    if h.deleted_file and re.search(r"test", h.path, re.I):
        x += 1.5
        why.append("a test file deleted")
    x += min(1.5, h.size / 120)
    return x, why


def reflex_risk(state: dict, q: Score, key: str) -> Answer:
    x = state["signal"]
    centres = (0.0, 1.5, 3.2, 5.0)
    w = [2.718 ** (-((x - c) ** 2) / 1.6) for c in centres]
    return score_answer(q, w, " · ".join(state["why"]) or "nothing notable", "local")


def reflex_route(state: dict, q: Choice, key: str) -> Answer:
    x = state["signal"]
    w = {"skip": 2.2 - 1.3 * x, "model": 1.0 - abs(x - 2.0) * 0.8, "human": -2.4 + 1.1 * x}
    return choice_answer(q, w, " · ".join(state["why"]) or "nothing notable", "local", 0.8)


REFLEXES = {"risk": reflex_risk, "route": reflex_route}


@dataclass
class Verdict:
    hunk: Hunk
    risk: float
    risk_label: str
    route: str
    action: str
    why: list[str]
    decision: str


def review(diff: str, mind: Mind, threshold: float = 0.35) -> list[Verdict]:
    out: list[Verdict] = []
    for h in parse(diff):
        x, why = signals(h)
        state = {"path": h.path, "header": h.header, "diff": h.text(), "signal": round(x, 3),
                 "why": why, "added": len(h.added), "removed": len(h.removed)}
        d = mind.ask(SKILL, f"{h.path} {h.header[:60]}", state, {
            "risk": Score("How risky is this change to merge without a careful review?", RISK),
            "route": Choice("Who should review this hunk before it is merged?", ROUTE),
        }, REFLEXES, drive="route", act_when="skip", escalate_when="human", threshold=threshold)
        route = d["route"].value
        # Unsure never waves anything through: it goes to a human.
        action, _ = gate(d["route"], threshold, "skip")
        if action == ESCALATE:
            route = "human"
        lvl = d["risk"].level
        out.append(Verdict(h, float(d["risk"].value), RISK[lvl].split(":")[0], str(route),
                           d.action, why, d.id))
    return out


def git_diff(repo: Path, base: str | None) -> str:
    args = ["git", "-C", str(repo), "diff", "--no-color", "-U2"]
    if base:
        args.append(base)
    r = subprocess.run(args, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git diff failed")
    return r.stdout


def to_html(verdicts: list[Verdict], title: str = "jevbrain review") -> str:
    """A local dashboard: one static page, no server, sorted by risk."""
    rows = []
    for v in sorted(verdicts, key=lambda v: -v.risk):
        colour = {"low": "#6e5264", "medium": "#ff8cc6", "high": "#ff4fa3", "critical": "#ffd24a"}[v.risk_label]
        body = html.escape(v.hunk.text(40))
        rows.append(f"""<details class="h"><summary><span class="r" style="color:{colour}">{v.risk_label}</span>
<span class="route {v.route}">{v.route}</span><code>{html.escape(v.hunk.path)}</code>
<span class="why">{html.escape(' · '.join(v.why) or 'nothing notable')}</span></summary>
<pre>{body}</pre><div class="id">{v.decision}</div></details>""")
    counts = {r: sum(1 for v in verdicts if v.route == r) for r in ROUTE}
    return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title><style>
body{{background:#050305;color:#f3dcea;font:13px/1.5 ui-monospace,Menlo,monospace;margin:0;padding:28px}}
h1{{color:#ff4fa3;letter-spacing:6px;margin:0 0 4px}} .sub{{color:#b0708f;margin-bottom:18px}}
.h{{border:1px solid #2a1424;border-radius:8px;margin:8px 0;background:#0c070b}}
summary{{padding:10px 14px;cursor:pointer;display:flex;gap:14px;align-items:center}}
.r{{width:70px;font-weight:700;text-transform:uppercase;font-size:11px;letter-spacing:1.5px}}
.route{{font-size:11px;padding:1px 8px;border-radius:20px;border:1px solid #3a2233;color:#b0708f}}
.route.human{{background:#ff4fa3;color:#16040d;border-color:#ff4fa3;font-weight:700}}
.route.model{{color:#ff8cc6;border-color:#ff8cc6}} code{{color:#fff}} .why{{color:#8a6a7c;margin-left:auto}}
pre{{margin:0;padding:12px 16px;border-top:1px solid #1a0c17;color:#d9bccb;overflow:auto;max-height:360px}}
.id{{color:#5e4656;padding:0 16px 10px;font-size:11px}}</style>
<h1>REVIEW</h1><div class="sub">{len(verdicts)} hunks · human {counts['human']} · model {counts['model']} · skip {counts['skip']} · every row is a decision in the jevbrain ledger</div>
{''.join(rows)}"""
