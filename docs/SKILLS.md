# The skills, written out

Every skill asks its questions through `Mind.ask`, so every answer is gated the same
way and lands in the same ledger. Below: each skill's questions, the local reflex that
answers each one, and what the gate does with it. Everything a reflex uses is in the
state it is given — nothing is looked up behind the brain's back.

## compact

| key | shape | local reflex |
|---|---|---|
| `needed:<i>` | Noul | `σ(−1.6 + 3.2·rel + 2.2·min(1, 2·errors) + 1.4·overlap − 1.8·noise)` — `rel` is BM25 of the task over the block, scaled to the best block; `errors` the share of lines matching failure words; `noise` the share matching progress bars, pip, timestamps |

Gate at confidence 0.3 (`|2p−1|`, so p ≤ 0.35 or p ≥ 0.65 is sure). Sure-yes keeps, sure-no drops, unsure **keeps**. Blocks are paragraphs, cut to at most 12 lines. One call per 40 blocks.

## navigate

| key | shape | local reflex |
|---|---|---|
| `next` | Choice over a directory's children | each child scored by the best BM25 match of the issue **beneath it**, from one index over the whole repository (tests ×0.7, docs ×0.6), plus path-word overlap; softmax at temperature 0.6 |

Beam of 3, depth 6. A file's final score is the product of the choice probabilities on the way down.

## review

| key | shape | local reflex |
|---|---|---|
| `risk` | Score: low · medium · high · critical | a signal `x` summed from added-line patterns (credential literal 3.0, TLS off 2.6, `eval` 2.4, `curl\|sh` 2.4, SQL from strings 2.0, …), removed-line patterns (assertions 1.6, error handling 1.6, checks 1.5), and the path (migration 1.7, CI 1.6, auth/payments 1.5, tests −0.8, docs −1.5); prose files count added/removed patterns at a quarter; Gaussian bumps at 0 · 1.5 · 3.2 · 5 |
| `route` | Choice: skip · model · human | `skip 2.2 − 1.3x`, `model 1 − 0.8·|x−2|`, `human −2.4 + 1.1x` |

`human` escalates however sure; unsure goes to `human`. Exit 2 if anything did.

## route

| key | shape | local reflex |
|---|---|---|
| `difficulty` | Score: trivial → research | `x = −easy + 1.1·hard + 1.3·unknown + 0.35·files + 0.5·code + 0.6·across + min(1.5, words/40)` over keyword families |
| `tier` | Choice: fast · standard · frontier | linear in `x` |
| `effort` | Choice: low · medium · high | linear in `x + 0.8·unknown` |

Unsure about the tier → one tier up.

## canny

| key | shape | local reflex |
|---|---|---|
| `supported` | Noul | `σ(0.4 + 0.9·Σ findings)`: failures −3.0, weakened checks −2.0, stubs −1.8, contradicted claim −1.5, no tests ran −2.5, no output −1.2, more asserts removed than added −1.2, skipped −0.6, passing output +2.2 |

TRUST ≥ 0.75 > DOUBT ≥ 0.35 > REJECT.

## curate

| key | shape | local reflex |
|---|---|---|
| `quality` | Score: junk · weak · usable · good | length, distinct-word ratio, truncation, repeated lines, non-ASCII share |
| `risky` | Noul | 0.97 for a key or private key, 0.88 for an email/phone/card/SSN, else 0.04 |
| `keep` | Choice: keep · drop · review | from quality minus 4 × risk |

Near-duplicates (5-word shingles, Jaccard ≥ 0.85 against records already kept) are dropped before the brain is asked.

## walk

| key | shape | local reflex |
|---|---|---|
| `answer_here` | Noul | `σ(−2.2 + 4.2·overlap)` of the question with the page |
| `next` | Choice over the page's unvisited links + `stop` | BM25 of the question over each linked page; `stop` weighted by how much of the question is already on this page |

Stops on `stop`, or when `answer_here` ≥ 0.85 with confidence ≥ 0.7.

## guard

| key | shape | local reflex |
|---|---|---|
| `destructive` · `external` · `secrets` | Noul | the highest-weight matching pattern in each family (e.g. `rm -rf ~` 0.99, `git push` 0.85, `cat ~/.ssh/…` 0.9); read-only commands 0.02 |
| `consequence` | Score: low · recoverable · severe | from the worst of the three |
| `route` | Choice: allow · ask · block | `block` grows with destroy/leak; `ask` grows with reaching outside; read-only is `allow` |

`ask` escalates; unsure `allow` becomes `ask`. Exit 0 · 1 · 2.

## arena

| key | shape | local reflex |
|---|---|---|
| `move` | Choice: run · jump · wait | a planner: every 3-move prefix (then run) simulated for 6 ticks on the visible window; alive +6, distance +0.35/cell, a needless wait −0.3 or jump −0.15 |
| `danger` | Noul | two ticks of running simulated on the window: 0.9 if it dies, 0.3 if it lives but a hazard is within 3, else 0.08 |

`danger` is labelled after every tick by simulating the real world, which is what makes it gradeable and learnable.
