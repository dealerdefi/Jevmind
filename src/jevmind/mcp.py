"""
Every skill, over MCP.

Inspired by typesafe-mcp (itsmostafa/typesafe-mcp) and jev-mcp
(jkudish/jev-mcp): plug the brain into Claude Code, Claude Desktop, Codex — any
MCP client — and let the agent call typed decisions instead of asking itself.

    claude mcp add jevmind -- jevmind mcp
    claude mcp add jevmind -e TYPESAFE_API_KEY=… -- jevmind mcp --brain jev

JSON-RPC 2.0 over stdio, one message per line, no dependencies. Tools:

    evaluate    raw System One: state + typed questions → typed answers
    compact     drop what a task does not need from tool output, verbatim
    navigate    the files an issue is about
    review      which hunks of a diff need a human
    route       how much model a task deserves
    canny       is a "done" claim supported by the evidence
    guard       should this shell command run
    walk        follow a question through a markdown vault

Every call made through here lands in the same ledger as the command line, so
`jevmind top` shows what your agent decided, too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .brain import BrainError
from .mind import Mind
from .questions import Choice, Noul, Score

PROTOCOL = "2025-06-18"


def _s(**props: Any) -> dict:
    req = [k for k, v in props.items() if not v.pop("optional", False)]
    return {"type": "object", "properties": props, "required": req}


def _str(desc: str, optional: bool = False) -> dict:
    return {"type": "string", "description": desc, "optional": optional}


TOOLS = [
    {"name": "evaluate", "description": "Ask the brain typed questions about a state. questions maps an id to "
     "{type: noul|choice|score, instructions, criteria}. choice criteria is {option: description}; score "
     "criteria is a list of levels, lowest first. Returns each answer with probabilities and confidence.",
     "inputSchema": {"type": "object", "properties": {
         "state": {"description": "the evidence: text or any JSON"},
         "questions": {"type": "object", "description": "id → question"}}, "required": ["state", "questions"]}},
    {"name": "compact", "description": "Given a task and a large tool output, drop the blocks the task does not "
     "need and return the rest byte-for-byte, with markers where lines were dropped.",
     "inputSchema": _s(task=_str("what you are trying to do"), text=_str("the tool output"))},
    {"name": "navigate", "description": "Walk a repository directory by directory to the files most likely "
     "related to an issue.",
     "inputSchema": _s(issue=_str("the issue or question"), repo=_str("path to the repository", True))},
    {"name": "review", "description": "Triage a unified diff: risk per hunk, and whether a human, a stronger "
     "model, or nobody needs to read it.", "inputSchema": _s(diff=_str("unified diff text"))},
    {"name": "route", "description": "Decide difficulty, model tier and reasoning effort for a programming task.",
     "inputSchema": _s(task=_str("the task"))},
    {"name": "canny", "description": "Judge whether a claim that work is complete is supported by test output "
     "and a diff. Returns TRUST, DOUBT or REJECT with findings.",
     "inputSchema": _s(claim=_str("what the agent claims"), tests=_str("test output", True), diff=_str("diff", True))},
    {"name": "guard", "description": "Before running a shell command: allow, ask, or block, with reasons.",
     "inputSchema": _s(command=_str("the shell command"), context=_str("what the user asked for", True))},
    {"name": "walk", "description": "Follow a question through a folder of markdown notes linked with "
     "[[wikilinks]], one link at a time, until the answer is found.",
     "inputSchema": _s(question=_str("the question"), vault=_str("path to the vault"), start=_str("start page", True))},
]


def _wire_question(q: dict):
    t = q.get("type")
    if t == "noul":
        return Noul(str(q["instructions"]))
    if t == "choice":
        return Choice(str(q["instructions"]), {str(k): str(v) for k, v in dict(q["criteria"]).items()})
    if t == "score":
        return Score(str(q["instructions"]), tuple(str(x) for x in q["criteria"]))
    raise ValueError(f"unknown question type {t!r}")


def call(mind: Mind, name: str, a: dict) -> Any:
    if name == "evaluate":
        qs = {k: _wire_question(v) for k, v in dict(a["questions"]).items()}
        d = mind.ask("evaluate", str(a.get("state"))[:120], a["state"], qs)
        return {"id": d.id, "brain": d.thought.brain, "answers": {k: x.row() for k, x in d.answers.items()}}
    if name == "compact":
        from .skills.compact import compact
        r = compact(a["text"], a["task"], mind)
        return {"text": r.text, "kept_blocks": len(r.kept) + len(r.unsure), "dropped_blocks": len(r.dropped),
                "chars_before": r.before, "chars_after": r.after}
    if name == "navigate":
        from .skills.navigate import navigate
        hits, _ = navigate(a["issue"], Path(a.get("repo") or "."), mind)
        return [{"path": h.path, "p": round(h.p, 4)} for h in hits]
    if name == "review":
        from .skills.review import review
        return [{"path": v.hunk.path, "hunk": v.hunk.header, "risk": v.risk_label, "route": v.route,
                 "why": v.why} for v in review(a["diff"], mind)]
    if name == "route":
        from .skills.route import route
        return route(a["task"], mind)
    if name == "canny":
        from .skills.canny import canny
        verdict, ans, ev, did = canny(a["claim"], a.get("tests", ""), a.get("diff", ""), mind)
        return {"verdict": verdict, "p": round(ans.p, 4), "id": did,
                "findings": [f"{f.mark} {f.text} {f.detail}".strip() for f in ev.findings]}
    if name == "guard":
        from .skills.guard import guard
        route_, info = guard(a["command"], mind, context=a.get("context", ""))
        return {"route": route_, **info}
    if name == "walk":
        from .skills.walk import Vault, walk
        steps = walk(a["question"], Vault(Path(a["vault"]).expanduser()), a.get("start") or "index", mind)
        return [{"page": s.page, "answer_here": round(s.p_here, 4), "next": s.took} for s in steps]
    raise KeyError(name)


def handle(mind: Mind, msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:
        return None                                   # a notification
    try:
        if method == "initialize":
            result: Any = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "jevmind", "version": "0.1.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{**t, "inputSchema": _clean(t["inputSchema"])} for t in TOOLS]}
        elif method == "tools/call":
            p = msg.get("params", {})
            try:
                out = call(mind, p.get("name", ""), p.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}
            except (KeyError, ValueError, TypeError, FileNotFoundError, BrainError) as e:
                result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except Exception as e:  # never let one bad call kill the server
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


def _clean(schema: dict) -> dict:
    s = json.loads(json.dumps(schema))
    for v in s.get("properties", {}).values():
        v.pop("optional", None)
    return s


def serve(args, stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    import os
    home = Path(args.home or os.environ.get("JEVMIND_HOME", "~/.jevmind")).expanduser()
    mind = Mind(brain=args.brain, threshold=args.gate, record=not args.no_record, home=home)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        out = handle(mind, msg)
        if out is not None:
            stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
