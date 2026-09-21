"""The command line, end to end: the demo builds a ledger that verifies."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from jevmind.cli import main
from jevmind.ledger import Ledger, verify


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        try:
            code = main(list(argv))
        except SystemExit as e:
            code = int(e.code or 0)
    return code, out.getvalue()


class Demo(unittest.TestCase):
    def test_every_skill_runs_and_the_ledger_verifies(self):
        home = tempfile.mkdtemp()
        code, out = run("demo", "--home", home)
        self.assertEqual(code, 0, out)
        for skill in ("compact", "navigate", "review", "route", "canny", "curate", "walk", "guard", "arena"):
            self.assertIn(skill, out)
        v = verify(Path(home) / "ledger.jsonl")
        self.assertTrue(v.ok)
        skills = {e.body["skill"] for e in Ledger(Path(home) / "ledger.jsonl").decisions()}
        self.assertEqual(len(skills), 9)
        self.assertTrue((Path(home) / "review.html").exists())


class Commands(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def test_guard_exit_codes_follow_the_route(self):
        self.assertEqual(run("guard", "ls", "--home", self.home)[0], 0)
        self.assertEqual(run("guard", "rm -rf ~", "--home", self.home)[0], 2)

    def test_ask_jev_without_a_key_fails_loudly(self):
        import os
        old = os.environ.pop("TYPESAFE_API_KEY", None)
        os.environ["TYPESAFE_CREDENTIALS_FILE"] = "/nonexistent"
        try:
            code, out = run("ask", "noul", "is it?", "--state", "x", "--brain", "jev", "--home", self.home)
        finally:
            os.environ.pop("TYPESAFE_CREDENTIALS_FILE", None)
            if old:
                os.environ["TYPESAFE_API_KEY"] = old
        self.assertEqual(code, 1)
        self.assertIn("TYPESAFE_API_KEY", out)

    def test_label_then_grade(self):
        run("ask", "noul", "is it?", "--state", "x", "--home", self.home)
        did = Ledger(Path(self.home) / "ledger.jsonl").decisions()[0].body["id"]
        self.assertEqual(run("label", did, "answer", "yes", "--home", self.home)[0], 0)
        code, out = run("grade", "--home", self.home)
        self.assertIn("ask", out)

    def test_doctor_names_every_skill(self):
        code, out = run("doctor", "--home", self.home)
        for skill in ("compact", "navigate", "review", "route", "canny", "curate", "walk", "guard", "arena"):
            self.assertIn(skill, out)


if __name__ == "__main__":
    unittest.main()
