"""
canny — should you believe an agent that says it is done?

Inspired by Canny (qkal/Canny): coding agents announce "all tests pass" with
the same confidence whether or not they ran them. This reads the evidence the
agent left behind — test output, the diff, the claim itself — and asks one
Noul: *the completion claim is supported by this evidence*.

    jevmind canny --claim "fixed, all tests pass" --tests out.txt --diff change.diff

It then names what it found, because a verdict with no reasons is just another
claim:

    ✗ 1 test failed                         tests/test_auth.py::test_refresh…
    ✗ the diff adds a stub                   raise NotImplementedError
    ! the claim says "all tests pass"; the output shows a failure

TRUST · DOUBT · REJECT, and an exit code a hook can branch on (0 · 1 · 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..mind import Mind
from ..questions import Answer, Noul, noul_answer, sigmoid

SKILL = "canny"

FAIL = re.compile(r"(\b\d+ failed\b|\bFAILED\b|\bFAIL\b|✗|\bnot ok\b|\bERROR\b|Traceback|"
                  r"AssertionError|panic:|error\[E\d+\]|npm ERR!|Tests?:\s+\d+ failed)", re.M)
PASS = re.compile(r"(\b\d+ passed\b|\bOK\b$|Tests?:\s+\d+ passed|\bok\s+\S+\s+[\d.]+s\b|"
                  r"All \d+ tests passed|✓)", re.M)
NONE_RAN = re.compile(r"(\bno tests ran\b|collected 0 items|Ran 0 tests|0 passing|No tests found)", re.I)
SKIPPED = re.compile(r"(\b\d+ skipped\b|\bSKIP(PED)?\b|xfail)", re.I)
STUB = re.compile(r"(raise NotImplementedError|todo!\(\)|unimplemented!\(\)|throw new Error\(['\"]not implemented|"
                  r"^\s*pass\s*$|TODO|FIXME|return None\s*#|console\.log\(['\"]TODO)", re.M | re.I)
WEAKEN = re.compile(r"(@pytest\.mark\.skip|\.skip\(|xit\(|it\.skip|@Disabled|@unittest\.skip|assert True|"
                    r"expect\(true\)|# noqa|@ts-ignore|eslint-disable)", re.I)
CLAIMS_TESTS = re.compile(r"(all|every)\s+(the\s+)?tests?\s+(now\s+)?pass|tests? (are )?(green|passing)", re.I)


@dataclass
class Finding:
    mark: str   # ✗ against, ! suspicious, ✓ for
    text: str
    detail: str = ""


@dataclass
class Evidence:
    findings: list[Finding] = field(default_factory=list)
    x: float = 0.0

    def add(self, mark: str, text: str, detail: str, w: float) -> None:
        self.findings.append(Finding(mark, text, detail))
        self.x += w


def examine(claim: str, tests: str, diff: str) -> Evidence:
    ev = Evidence()
    fails = [ln.strip() for ln in tests.splitlines() if FAIL.search(ln)]
    passes = PASS.findall(tests)
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    removed = [ln[1:] for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]

    if not tests.strip():
        ev.add("!", "no test output was given", "a claim with nothing to check it against", -1.2)
    elif NONE_RAN.search(tests):
        ev.add("✗", "no tests actually ran", NONE_RAN.search(tests).group(0), -2.5)
    if fails:
        n = re.search(r"(\d+) failed", tests)
        text = (f"{n.group(1)} test{'s' if n.group(1) != '1' else ''} failed" if n
                else f"{len(fails)} failure line{'s' if len(fails) != 1 else ''} in the test output")
        ev.add("✗", text, fails[-1][:90], -3.0)
    elif passes:
        ev.add("✓", "the test output shows passing tests", str(passes[0])[:60], +2.2)
    if SKIPPED.search(tests):
        ev.add("!", "some tests were skipped", SKIPPED.search(tests).group(0), -0.6)

    stubs = [ln.strip() for ln in added if STUB.search(ln)]
    if stubs:
        ev.add("✗", "the diff adds a stub or a TODO", stubs[0][:90], -1.8)
    weak = [ln.strip() for ln in added if WEAKEN.search(ln)]
    if weak:
        ev.add("✗", "the diff weakens a check", weak[0][:90], -2.0)
    gone = [ln.strip() for ln in removed if re.search(r"\bassert\b|expect\(|def test_|it\(['\"]", ln)]
    if len(gone) > len([a for a in added if re.search(r"\bassert\b|expect\(|def test_|it\(['\"]", a)]):
        ev.add("!", "the diff removes more assertions than it adds", gone[0][:90], -1.2)
    if diff.strip() and not added:
        ev.add("!", "the diff only deletes code", "", -0.4)
    if not diff.strip():
        ev.add("!", "no diff was given", "nothing shows what was changed", -0.6)

    if CLAIMS_TESTS.search(claim) and fails:
        ev.add("!", "the claim says the tests pass; the output shows a failure",
               CLAIMS_TESTS.search(claim).group(0), -1.5)
    return ev


def reflex_supported(state: dict, q: Noul, key: str) -> Answer:
    x = state["signal"]
    return noul_answer(sigmoid(0.4 + 0.9 * x), f"{len(state['findings'])} findings, signal {x:+.1f}", "local")


REFLEXES = {"supported": reflex_supported}


def canny(claim: str, tests: str, diff: str, mind: Mind) -> tuple[str, Answer, Evidence, str]:
    ev = examine(claim, tests, diff)
    state = {"claim": claim, "test_output": tests[-6000:], "diff": diff[:8000],
             "findings": [f"{f.mark} {f.text}: {f.detail}" for f in ev.findings],
             "signal": round(ev.x, 3)}
    d = mind.ask(SKILL, claim, state, {"supported": Noul(
        "The agent's claim that the work is complete is supported by the test output and "
        "the diff — tests ran, they passed, and nothing was stubbed out or weakened.")},
        REFLEXES, drive="supported", act_when=lambda a: a.p >= 0.5, threshold=0.4)
    p = d["supported"].p
    verdict = "TRUST" if p >= 0.75 else "REJECT" if p < 0.35 else "DOUBT"
    return verdict, d["supported"], ev, d.id
