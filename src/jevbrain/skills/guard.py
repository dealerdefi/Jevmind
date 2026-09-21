"""
guard — should this command run?

Inspired by semdecide's reflex guard (sharziki/semdecide) and jev-mcp's
screening tools (jkudish/jev-mcp): before an agent executes a shell command,
ask a handful of typed questions about it, and let code — not the agent —
decide whether it runs.

    destructive     Noul    it would destroy data or access in a way that is hard to undo
    external        Noul    it would reach outside this machine: push, deploy, send, pay
    secrets         Noul    it would expose credentials or private data
    consequence     Score   low · recoverable · severe
    route           Choice  allow · ask · block

    jevbrain guard "rm -rf ./build"
    jevbrain guard --hook          # as a Claude Code PreToolUse hook, JSON on stdin

As a hook it answers in Claude Code's own format: `allow` runs silently, `ask`
puts the command in front of you, `block` refuses with the reason. An answer the
brain is unsure about is never `allow`.
"""

from __future__ import annotations

import re

from ..mind import ESCALATE, Mind
from ..questions import Answer, Choice, Noul, Score, choice_answer, noul_answer, score_answer

SKILL = "guard"
CONSEQUENCE = ("low: read-only or trivially reversible", "recoverable: changes state that can be restored",
               "severe: irreversible, external, financial or private")
ROUTE = {"allow": "run it without asking", "ask": "show it to the person first",
         "block": "refuse: clearly unsafe or outside what was asked"}

DESTRUCTIVE = [
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b(?!.*\b(node_modules|dist|build|__pycache__|\.cache|tmp)\b)", 0.9, "recursive force delete"),
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/|~|\$HOME|\*|\.)(\s|$)", 0.99, "deletes a root, home or cwd"),
    (r"\b(mkfs|fdisk|wipefs|shred)\b|\bdd\s+.*\bof=/dev/", 0.99, "wipes a disk"),
    (r"\bgit\s+(push\s+.*(--force|-f)\b|reset\s+--hard|clean\s+-[a-z]*f|branch\s+-D|checkout\s+--\s+\.)", 0.8, "discards git history or work"),
    (r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE|DELETE\s+FROM\s+\w+\s*(;|$))", 0.92, "destroys database data"),
    (r"\bchmod\s+(-R\s+)?[0-7]*777\b|\bchown\s+-R\b", 0.55, "opens or changes permissions broadly"),
    (r">\s*/dev/sd|:\(\)\s*\{\s*:\|:&\s*\};:", 0.99, "a fork bomb or raw device write"),
    (r"\bkubectl\s+delete\b|\bterraform\s+destroy\b|\bdocker\s+(system\s+prune|rm\s+-f)", 0.85, "destroys infrastructure"),
]
EXTERNAL = [
    (r"\bgit\s+push\b", 0.85, "pushes to a remote"),
    (r"\b(npm|yarn|pnpm)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgh\s+release\b", 0.95, "publishes a package"),
    (r"\b(curl|wget|http)\b.*\s-(X\s*(POST|PUT|DELETE|PATCH)|d\b|-data)", 0.8, "sends data to a server"),
    (r"\b(kubectl\s+apply|terraform\s+apply|helm\s+(install|upgrade)|fly\s+deploy|vercel\s+--prod|serverless\s+deploy)\b", 0.9, "deploys"),
    (r"\b(sendmail|mail\s+-s|ssh\s+\S+@|scp\s+|rsync\s+.*\S+@)", 0.8, "talks to another machine"),
    (r"\b(cast\s+send|forge\s+script.*--broadcast|stripe\s+)", 0.95, "moves money or on-chain value"),
]
SECRETS = [
    (r"(cat|less|more|head|tail|cp|scp|curl.*-T)\s+.*(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.env\b|\.npmrc|\.pypirc|\.netrc|credentials)", 0.9, "reads a credentials file"),
    (r"\b(printenv|env)\b(\s*$|\s*\|)|\becho\s+\$\w*(KEY|TOKEN|SECRET|PASS)\w*", 0.8, "prints secrets from the environment"),
    (r"(curl|wget)[^|]*\|\s*(sudo\s+)?(ba|z)?sh", 0.7, "pipes a remote script into a shell"),
    (r"\bgit\s+config\s+.*credential|\bsecurity\s+find-generic-password", 0.85, "reads a credential store"),
]
READ_ONLY = re.compile(r"^\s*(ls|pwd|cat|head|tail|less|grep|rg|find|wc|echo|git\s+(status|log|diff|show|branch)|"
                       r"python3?\s+-m\s+(pytest|unittest)|npm\s+(test|run\s+test)|pytest|go\s+test|cargo\s+test|"
                       r"which|type|whoami|date|uname|tree|du|df|jq)\b")


def _scan(cmd: str, rules: list) -> tuple[float, list[str]]:
    hits = [(p, r) for pat, p, r in rules if re.search(pat, cmd, re.I)]
    if not hits:
        return 0.0, []
    return max(p for p, _ in hits), [r for _, r in hits]


def assess(cmd: str) -> dict:
    d, dr = _scan(cmd, DESTRUCTIVE)
    e, er = _scan(cmd, EXTERNAL)
    s, sr = _scan(cmd, SECRETS)
    ro = bool(READ_ONLY.match(cmd)) and not (d or e or s)
    if "sudo " in cmd:
        d = max(d, 0.35)
        dr.append("runs as root")
    return {"destructive": d, "external": e, "secrets": s, "read_only": ro,
            "reasons": dr + er + sr}


def _p(base: float, read_only: bool) -> float:
    return base if base else (0.02 if read_only else 0.08)


def reflex_destructive(state: dict, q: Noul, key: str) -> Answer:
    a = state["assessment"]
    return noul_answer(_p(a["destructive"], a["read_only"]), "; ".join(a["reasons"]) or "no destructive pattern", "local")


def reflex_external(state: dict, q: Noul, key: str) -> Answer:
    a = state["assessment"]
    return noul_answer(_p(a["external"], a["read_only"]), "; ".join(a["reasons"]) or "stays on this machine", "local")


def reflex_secrets(state: dict, q: Noul, key: str) -> Answer:
    a = state["assessment"]
    return noul_answer(_p(a["secrets"], a["read_only"]), "; ".join(a["reasons"]) or "touches no credentials", "local")


def reflex_consequence(state: dict, q: Score, key: str) -> Answer:
    a = state["assessment"]
    worst = max(a["destructive"], a["external"], a["secrets"])
    if a["read_only"]:
        return score_answer(q, [0.9, 0.08, 0.02], "read-only", "local")
    return score_answer(q, [max(0.02, 1 - worst * 1.4), 0.5 - abs(worst - 0.5), worst ** 2 * 1.5],
                        "; ".join(a["reasons"]) or "changes local state", "local")


def reflex_route(state: dict, q: Choice, key: str) -> Answer:
    a = state["assessment"]
    worst = max(a["destructive"], a["external"], a["secrets"])
    authorised = state.get("authorised", False)
    if a["read_only"]:
        return choice_answer(q, {"allow": 3.0, "ask": 0.0, "block": -2.0}, "read-only", "local")
    # Destroying or leaking is refused; reaching outside is put in front of a person —
    # a push or a publish is often exactly what was asked for.
    harm = max(a["destructive"], a["secrets"])
    w = {"allow": 1.6 - 4.0 * worst,
         "ask": 0.6 + 1.2 * worst + 0.8 * a["external"] - 5.0 * max(0, harm - 0.85),
         "block": -3.0 + 5.0 * harm}
    if authorised:
        w["allow"] += 1.5
        w["block"] -= 1.0
    return choice_answer(q, w, "; ".join(a["reasons"]) or "local change", "local", 0.8)


REFLEXES = {"destructive": reflex_destructive, "external": reflex_external, "secrets": reflex_secrets,
            "consequence": reflex_consequence, "route": reflex_route}


def guard(cmd: str, mind: Mind, context: str = "", threshold: float = 0.45) -> tuple[str, dict]:
    a = assess(cmd)
    authorised = bool(context) and bool(re.search(re.escape(cmd.strip()[:40]), context))
    d = mind.ask(SKILL, cmd, {"command": cmd, "context": context[-3000:], "assessment": a,
                              "authorised": authorised}, {
        "destructive": Noul("Running this command would destroy data, access, or infrastructure "
                            "in a way that is difficult to reverse."),
        "external": Noul("Running this command would change shared or remote state: push, publish, "
                         "deploy, send, or transact."),
        "secrets": Noul("Running this command would expose credentials or private data."),
        "consequence": Score("How bad would it be if this command did the wrong thing?", CONSEQUENCE),
        "route": Choice("Under a cautious policy for an autonomous coding agent, how should this "
                        "command be routed?", ROUTE),
    }, REFLEXES, drive="route", act_when="allow", escalate_when="ask", threshold=threshold)
    route = str(d["route"].value)
    if d.action == ESCALATE and route == "allow":
        route = "ask"          # unsure is never allow
    return route, {"id": d.id, "reasons": a["reasons"], "confidence": d["route"].confidence,
                   "destructive": d["destructive"].p, "external": d["external"].p,
                   "secrets": d["secrets"].p, "consequence": CONSEQUENCE[d["consequence"].level].split(":")[0]}


def hook_response(route: str, info: dict) -> dict:
    """Claude Code PreToolUse hook output."""
    decision = {"allow": "allow", "ask": "ask", "block": "deny"}[route]
    why = "; ".join(info["reasons"]) or "no risk found"
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": decision,
        "permissionDecisionReason": f"jevbrain guard: {route} — {why} (decision {info['id']})"}}
