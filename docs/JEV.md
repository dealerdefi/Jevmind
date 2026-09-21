# Talking to Jev

`src/jevmind/brain.py` → `TypeSafeBrain`. Standard library only (`urllib`).

## Where the format comes from

TypeSafe's public docs were not reachable from where this was written. The request
and response shapes below were read from the source of two MIT-licensed clients that
talk to the live service — [typesafe-mcp](https://github.com/itsmostafa/typesafe-mcp)
(Go) and [semdecide](https://github.com/sharziki/semdecide) (Python) — and jevmind's
adapter is tested against a stand-in transport that answers in that shape. **It has not
been run against the live API from this repository.** If TypeSafe changes a field name,
`parse_answers()` is the one function to change, and `tests/test_core.py` shows every
shape it accepts.

## Request

```
POST https://api.typesafe.ai/v1/systemone          ($TYPESAFE_BASE_URL + /v1/systemone)
Authorization: Bearer $TYPESAFE_API_KEY             (or ~/.config/typesafe/credentials.env)
Content-Type: application/json

{
  "model": "jev-latest",
  "state": <any JSON — the evidence>,
  "questions": {
    "urgent": {"type": "noul",   "instructions": "Does this convey urgency?"},
    "team":   {"type": "choice", "instructions": "Which team?",
               "criteria": {"billing": "payments, refunds", "infra": "outages"}},
    "sev":    {"type": "score",  "instructions": "How severe?",
               "criteria": ["minor", "major", "critical"]}
  }
}
```

Every question for one state goes in **one** request. That is how Jev is priced and
how jevmind always calls it — `compact` sends forty blocks as forty Nouls in one call,
not forty calls.

## Response

```
{
  "answers": {
    "urgent": {"noul": 0.91},
    "team":   {"choice": "billing", "probabilities": {"billing": 0.8, "infra": 0.2}, "confidence": 0.7},
    "sev":    {"score": 1.6, "probabilities": [0.1, 0.2, 0.7], "confidence": 0.5}
  },
  "usage": {"input_tokens": 120, "output_tokens": 0},
  "model": "jev-latest"
}
```

## What the adapter checks

| | |
|---|---|
| no `answers` object | the whole call fails with `BrainError` |
| a Noul outside 0..1, or not a number | that answer: confidence 0 |
| a Choice naming an option that was not offered | that answer: confidence 0, "not offered" |
| a Score with the wrong number of probabilities, or off the scale | that answer: confidence 0 |
| a missing answer | that answer: confidence 0 |
| HTTP 429 or 5xx | retried twice with backoff and jitter |
| any other HTTP error | `BrainError("jev returned HTTP 401")` — the body is never echoed, because a reflecting proxy could put your key in it |

A confidence-0 answer is refused by every gate, so a malformed response turns into an
escalation, never into an action.

A Noul carries no confidence on the wire. jevmind derives one, `|2p − 1|`, so the gate
can treat every answer alike. That is jevmind's convention, not TypeSafe's.

## Cost

TypeSafe's published price is $0.042 per million input tokens, output free. jevmind
uses `usage.input_tokens` when the response has it and a four-characters-a-token
estimate otherwise, and marks the estimate as one everywhere it is shown. `jevmind top`
also shows what the local brain's decisions *would* have cost on Jev.

## Tapes

With `--brain jev`, every thought is appended to `~/.jevmind/tape.jsonl`, keyed by a
SHA-256 of the exact state and questions. `--brain replay` answers from it and refuses
anything that is not on it.
