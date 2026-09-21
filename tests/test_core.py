"""
The core: questions, brains, the gate, the ledger, grading and learning.

The failure modes that matter here are quiet ones — an unsure answer acted on,
a malformed answer from the network treated as a real one, a key echoed into a
log, an edit to the record nobody notices, a calibration that only helps on the
data it was fitted to. Each has a test.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from jevmind.brain import (BrainError, Calibration, TypeSafeBrain, LocalBrain, ReplayBrain, Recorder, Tape,
                            fingerprint, parse_answers)
from jevmind.grade import brier, fit_platt, learn, stats
from jevmind.ledger import Ledger, verify
from jevmind.mind import ACT, ESCALATE, HOLD, Mind, gate
from jevmind.questions import Answer, Choice, Noul, Score, choice_answer, noul_answer, score_answer

Q = {"urgent": Noul("Does this convey urgency?"),
     "team": Choice("Which team?", {"billing": "payments", "tech": "outages"}),
     "sev": Score("How severe?", ("low", "mid", "high"))}


class Wire(unittest.TestCase):
    def test_questions_serialise_to_the_system_one_shape(self):
        self.assertEqual(Q["urgent"].wire(), {"type": "noul", "instructions": "Does this convey urgency?"})
        self.assertEqual(Q["team"].wire()["criteria"], {"billing": "payments", "tech": "outages"})
        self.assertEqual(Q["sev"].wire()["criteria"], ["low", "mid", "high"])

    def test_a_choice_needs_two_options_and_a_scale_two_levels(self):
        with self.assertRaises(ValueError):
            Choice("x", {"only": "one"})
        with self.assertRaises(ValueError):
            Score("x", ("one",))

    def test_answers_are_well_formed(self):
        a = choice_answer(Q["team"], {"billing": 2.0, "tech": 0.0})
        self.assertEqual(a.value, "billing")
        self.assertAlmostEqual(sum(a.probabilities.values()), 1.0)
        s = score_answer(Q["sev"], [0.1, 0.2, 0.7])
        self.assertEqual(s.level, 2)
        n = noul_answer(0.9)
        self.assertAlmostEqual(n.confidence, 0.8)
        with self.assertRaises(TypeError):
            s.p


class ParsingJev(unittest.TestCase):
    def good(self):
        return {"answers": {"urgent": {"noul": 0.91},
                            "team": {"choice": "billing", "probabilities": {"billing": 0.8, "tech": 0.2},
                                     "confidence": 0.7},
                            "sev": {"score": 1.6, "probabilities": [0.1, 0.2, 0.7], "confidence": 0.5}},
                "usage": {"input_tokens": 120, "output_tokens": 0}, "model": "jev-latest"}

    def test_a_well_formed_response(self):
        ans, usage, model = parse_answers(self.good(), Q)
        self.assertEqual(ans["urgent"].p, 0.91)
        self.assertEqual(ans["team"].value, "billing")
        self.assertEqual(ans["sev"].level, 2)
        self.assertEqual(usage["input_tokens"], 120)
        self.assertEqual(model, "jev-latest")

    def test_an_option_that_was_not_offered_is_confidence_zero(self):
        b = self.good()
        b["answers"]["team"]["choice"] = "sales"
        ans, _, _ = parse_answers(b, Q)
        self.assertEqual(ans["team"].confidence, 0.0)
        self.assertIn("not offered", ans["team"].why)

    def test_one_bad_answer_does_not_sink_the_batch(self):
        b = self.good()
        b["answers"]["urgent"] = {"noul": "very"}
        del b["answers"]["sev"]
        ans, _, _ = parse_answers(b, Q)
        self.assertEqual(ans["urgent"].confidence, 0.0)
        self.assertEqual(ans["sev"].confidence, 0.0)
        self.assertEqual(ans["team"].confidence, 0.7)

    def test_no_answers_object_is_an_error(self):
        with self.assertRaises(BrainError):
            parse_answers({"oops": 1}, Q)


class TheJevModelOverHttp(unittest.TestCase):
    def test_it_posts_the_system_one_request(self):
        seen = {}

        def transport(req, timeout):
            seen["url"], seen["auth"] = req.full_url, req.headers["Authorization"]
            seen["body"] = json.loads(req.data)
            return json.dumps(ParsingJev().good()).encode()

        t = TypeSafeBrain(key="k-123", url="https://example.invalid/v1/systemone", transport=transport).think("s", Q)
        self.assertEqual(seen["url"], "https://example.invalid/v1/systemone")
        self.assertEqual(seen["auth"], "Bearer k-123")
        self.assertEqual(seen["body"]["model"], "jev-latest")
        self.assertEqual(set(seen["body"]["questions"]), set(Q))
        self.assertEqual(t.input_tokens, 120)
        self.assertFalse(t.tokens_estimated)
        self.assertAlmostEqual(t.cost_usd, 120 / 1e6 * 0.042)

    def test_it_retries_a_429_and_then_succeeds(self):
        calls = []

        def transport(req, timeout):
            calls.append(1)
            if len(calls) < 2:
                raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, io.BytesIO(b""))
            return json.dumps(ParsingJev().good()).encode()

        TypeSafeBrain(key="k", transport=transport, sleep=lambda s: None).think("s", Q)
        self.assertEqual(len(calls), 2)

    def test_an_error_never_echoes_the_body_or_the_key(self):
        def transport(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"your key k-SECRET is bad"))

        with self.assertRaises(BrainError) as cm:
            TypeSafeBrain(key="k-SECRET", transport=transport, retries=0).think("s", Q)
        self.assertNotIn("SECRET", str(cm.exception))

    def test_no_key_means_no_jev(self):
        import os
        old = os.environ.pop("TYPESAFE_API_KEY", None)
        os.environ["TYPESAFE_CREDENTIALS_FILE"] = "/nonexistent"
        try:
            with self.assertRaises(BrainError):
                TypeSafeBrain()
        finally:
            os.environ.pop("TYPESAFE_CREDENTIALS_FILE", None)
            if old:
                os.environ["TYPESAFE_API_KEY"] = old


class Replay(unittest.TestCase):
    def test_a_recorded_thought_replays_identically_and_offline(self):
        tape = Tape(Path(tempfile.mkdtemp()) / "tape.jsonl")
        live = Recorder(LocalBrain(), tape).think("payouts failing", Q)
        again = ReplayBrain(Tape(tape.path)).think("payouts failing", Q)
        self.assertEqual({k: a.value for k, a in live.answers.items()},
                         {k: a.value for k, a in again.answers.items()})
        with self.assertRaises(BrainError):
            ReplayBrain(Tape(tape.path)).think("something else", Q)

    def test_the_fingerprint_changes_with_the_state_or_the_questions(self):
        self.assertNotEqual(fingerprint("a", Q), fingerprint("b", Q))
        self.assertNotEqual(fingerprint("a", Q), fingerprint("a", {"urgent": Q["urgent"]}))


class TheGate(unittest.TestCase):
    def test_unsure_escalates_whatever_the_answer_says(self):
        a = Answer("choice", "allow", 0.2)
        self.assertEqual(gate(a, 0.5, "allow")[0], ESCALATE)

    def test_sure_acts_or_holds(self):
        self.assertEqual(gate(Answer("choice", "allow", 0.9), 0.5, "allow")[0], ACT)
        self.assertEqual(gate(Answer("choice", "block", 0.9), 0.5, "allow")[0], HOLD)

    def test_the_lexical_floor_never_clears_a_sensible_gate_on_an_open_question(self):
        t = LocalBrain().think("Help! My payouts have been failing for 3 days", Q)
        self.assertLess(t.answers["team"].confidence, 0.5)
        self.assertLess(t.answers["urgent"].confidence, 0.5)


class TheLedger(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_every_decision_is_written_and_the_chain_verifies(self):
        m = Mind(home=self.home)
        for s in ("a", "b", "c"):
            m.ask("t", s, s, {"x": Noul("x")})
        v = verify(self.home / "ledger.jsonl")
        self.assertTrue(v.ok)
        self.assertEqual(v.lines, 3)

    def test_editing_one_confidence_is_caught(self):
        m = Mind(home=self.home)
        for s in ("a", "b", "c"):
            m.ask("t", s, s, {"x": Noul("x")})
        p = self.home / "ledger.jsonl"
        lines = p.read_text().splitlines()
        d = json.loads(lines[1])
        d["body"]["answers"]["x"]["value"] = 0.99
        lines[1] = json.dumps(d, sort_keys=True, separators=(",", ":"))
        p.write_text("\n".join(lines) + "\n")
        v = verify(p)
        self.assertFalse(v.ok)
        self.assertEqual(v.broke_at, 1)

    def test_an_outcome_is_a_new_line_not_an_edit(self):
        m = Mind(home=self.home)
        d = m.ask("t", "a", "a", {"x": Noul("x")})
        before = (self.home / "ledger.jsonl").read_text()
        m.label(d.id, "x", True)
        after = (self.home / "ledger.jsonl").read_text()
        self.assertTrue(after.startswith(before))
        self.assertEqual(Ledger(self.home / "ledger.jsonl").outcomes()[d.id], {"x": True})

    def test_no_record_means_nothing_on_disk(self):
        Mind(home=self.home, record=False).ask("t", "a", "a", {"x": Noul("x")})
        self.assertFalse((self.home / "ledger.jsonl").exists())


class Learning(unittest.TestCase):
    def test_brier_at_its_anchors(self):
        self.assertEqual(brier(1.0, True), 0.0)
        self.assertEqual(brier(0.5, False), 0.25)
        self.assertEqual(brier(1.0, False), 1.0)

    def test_platt_fixes_a_brain_that_is_systematically_too_timid(self):
        # Says 0.6 when it is right 95% of the time, 0.4 when it is right 5%.
        data = [(0.6, i % 20 != 0) for i in range(200)] + [(0.4, i % 20 == 0) for i in range(200)]
        a, b = fit_platt(data)
        from jevmind.questions import logit, sigmoid
        self.assertGreater(sigmoid(a * logit(0.6) + b), 0.85)
        self.assertLess(sigmoid(a * logit(0.4) + b), 0.15)

    def _ledger_with(self, pairs):
        home = Path(tempfile.mkdtemp())
        m = Mind(home=home)
        for i, (p, y) in enumerate(pairs):
            d = m.ask("sk", f"s{i}", f"s{i}", {"q": Noul("q")},
                      {"q": lambda s, q, k, p=p: noul_answer(p, "", "local")})
            m.label(d.id, "q", y)
        return Ledger(home / "ledger.jsonl")

    def test_a_calibration_that_helps_on_unseen_data_is_kept(self):
        pairs = [(0.6, i % 20 != 0) if i % 2 else (0.4, i % 20 == 1) for i in range(120)]
        new, lessons = learn(self._ledger_with(pairs), Calibration())
        self.assertTrue(lessons[0].kept)
        self.assertIn("sk/q", new.params)

    def test_a_calibration_that_does_not_help_is_thrown_away(self):
        # Already perfectly calibrated coin flips: nothing to learn.
        pairs = [(0.5, i % 2 == 0) for i in range(100)]
        new, lessons = learn(self._ledger_with(pairs), Calibration())
        self.assertFalse(lessons[0].kept)
        self.assertNotIn("sk/q", new.params)

    def test_the_local_brain_uses_what_was_learned_and_keeps_the_raw_answer(self):
        cal = Calibration({"sk/q": (3.0, 0.0)})
        b = LocalBrain({"q": lambda s, q, k: noul_answer(0.6)}, "sk", cal)
        a = b.think("x", {"q": Noul("q")}).answers["q"]
        self.assertGreater(a.p, 0.6)
        self.assertEqual(a.raw, 0.6)

    def test_stats_count_every_action(self):
        home = Path(tempfile.mkdtemp())
        m = Mind(home=home, threshold=0.5)
        m.ask("s", "a", "a", {"c": Choice("c", {"x": "", "y": ""})},
              {"c": lambda s, q, k: Answer("choice", "x", 0.9)}, drive="c", act_when="x")
        m.ask("s", "b", "b", {"c": Choice("c", {"x": "", "y": ""})},
              {"c": lambda s, q, k: Answer("choice", "y", 0.9)}, drive="c", act_when="x")
        m.ask("s", "c", "c", {"c": Choice("c", {"x": "", "y": ""})},
              {"c": lambda s, q, k: Answer("choice", "x", 0.1)}, drive="c", act_when="x")
        s = stats(Ledger(home / "ledger.jsonl"))["s"]
        self.assertEqual((s.act, s.hold, s.escalate), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
