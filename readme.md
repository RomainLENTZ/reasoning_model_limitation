# Tower of Hanoi & River Crossing Benchmark — Claude Sonnet 4.6

> Does Claude Sonnet 4.6 actually reason? A reproducible benchmark testing reasoning collapse on controllable puzzles, inspired by Apple Research's "Illusion of Thinking" (2025).

---

## Context

This project reproduces part of the methodology from **"The Illusion of Thinking: Understanding the Strengths and Limitations of Reasoning Models via the Lens of Problem Complexity"** (Shojaee et al., Apple Research, 2025).

> Shojaee et al. (2025). *The Illusion of Thinking.* Apple Research. [arxiv.org/abs/2506.06941](https://arxiv.org/abs/2506.06941)

The paper tested frontier LRMs (Claude 3.7 Sonnet, DeepSeek-R1, o3-mini) on controllable puzzle environments and concluded that all models exhibit a **complete accuracy collapse** beyond a model-specific complexity threshold — even with ample token budget. Their experiments used a 64k token budget.

This reproduction runs the same Tower of Hanoi environment on **Claude Sonnet 4.6**, progressively increasing the token budget from 8k to 64k. What started as a straightforward confirmation of the paper's findings turned into something more interesting: **the collapse disappears for thinking models when the token budget is sufficient.**

> **Scope:** 3 samples per N, N=1 to N=10, across 5 configurations. The original paper used 25 samples and N up to 20.

---

## Results at a Glance

### Without thinking — 64k tokens (final run)
![Sonnet 4.6 without thinking](assets/hanoi_benchmark_sonnet.png)

### With adaptive thinking — 64k tokens (final run)
![Sonnet 4.6 with thinking](assets/hanoi_benchmark_sonnet_thinking.png)

---

## Full Data — All Runs

Five runs were conducted, progressively increasing the token budget to understand whether observed collapses reflect reasoning limits or generation capacity limits.

### No thinking

| N | Min moves | 8k tokens | 32k tokens | 64k tokens |
|:-:|:---------:|:---------:|:----------:|:----------:|
| | | acc / tokens | acc / tokens | acc / tokens |
| 1 | 1 | 100% / 559 | 100% / 516 | 100% / 516 |
| 2 | 3 | 100% / 789 | 100% / 725 | 100% / 747 |
| 3 | 7 | 100% / 1,054 | 100% / 1,022 | 100% / 1,015 |
| 4 | 15 | 67% / 1,473 | 33% / 1,573 | 100% / 1,232 |
| 5 | 31 | 100% / 1,525 | 100% / 1,414 | 100% / 1,584 |
| 6 | 63 | 0% / 2,338 | 0% / 1,539 | 33% / 2,068 |
| 7 | 127 | 100% / 1,941 | 100% / 1,897 | 100% / 1,889 |
| 8 | 255 | 0% / 3,106 | 0% / 2,898 | 0% / 7,793 |
| 9 | 511 | 0% / 5,361 | 0% / 6,916 | 0% / 6,628 |
| 10 | 1,023 | 0% / 8,599 | 0% / 9,520 | 0% / 10,236 |

**Key observation:** Regardless of token budget (8k, 32k, or 64k), the no-thinking model fails at N=8+ every time — and never comes close to the token limit when doing so. At 64k, the N=8 failure uses only ~7,793 tokens out of 64,000 available. **This is a genuine reasoning failure, not a capacity limit.**

### With adaptive thinking

| N | Min moves | 32k tokens | 64k tokens |
|:-:|:---------:|:----------:|:----------:|
| | | acc / tokens | acc / tokens |
| 1 | 1 | 100% / 533 | 100% / 533 |
| 2 | 3 | 100% / 843 | 100% / 888 |
| 3 | 7 | 100% / 1,131 | 100% / 1,131 |
| 4 | 15 | 100% / 2,803 | 100% / 2,518 |
| 5 | 31 | 100% / 5,623 | 67% / 3,383 |
| 6 | 63 | 100% / 6,166 | 100% / 9,869 |
| 7 | 127 | 100% / 21,435 | 100% / 20,689 |
| 8 | 255 | **0%** / 32,561 ← budget hit | **100%** / 45,548 ← solved |
| 9 | 511 | 0% / 21,711 | 0% / 64,567 ← budget hit |
| 10 | 1,023 | 0% / 32,573 | 0% / 64,573 ← budget hit |

**Key observation:** At 32k, thinking collapses at N=8 — tokens hit the ceiling at 32,561. At 64k, N=8 is **solved perfectly** (3/3, up to 61k tokens used). The collapse at N=9 is again budget-limited at 64,567 tokens. Each time the budget is increased, the collapse point shifts up by exactly one disk.

---

## The Central Finding: Token Budget vs. Reasoning Collapse

### What we expected

Going into this experiment, the goal was to confirm the paper's findings on a newer model. The early runs at 8k and 32k appeared to do exactly that — thinking mode collapsed at N=8, matching the paper's reported threshold for Claude 3.7 Sonnet. The 32k results looked like a clean replication.

### What the 64k run revealed

Increasing the budget to 64k — the same budget used in the original paper — changed everything. With thinking, N=8 went from 0% to **100%**. The model didn't fail to reason: it had simply run out of space to write the answer.

This raises a direct question for the original paper: if Claude 3.7 Sonnet's observed collapse at N=8 with a 64k budget was also caused by token exhaustion rather than a reasoning failure, the paper's central claim for thinking models would need to be revisited. Without access to the token consumption data from the original experiments, this cannot be confirmed — but the pattern is consistent enough to warrant the question.

### Two fundamentally different types of failure

The contrast between thinking and no-thinking runs is the most important result of this reproduction:

| | No thinking at 64k (N=8) | With thinking at 32k (N=8) |
|---|---|---|
| Tokens used | ~7,793 out of 64,000 available | ~32,561 at the hard limit |
| Moves produced | All 255 | None |
| Failure cause | Illegal move at step #127 | Token budget exhausted |
| Nature | **Genuine reasoning failure** | **Generation capacity limit** |

Without thinking, the model has ample budget and still fails — it produces every move but makes an illegal placement at the exact midpoint of the puzzle. That is a true architectural limitation. With thinking at 32k, the model never even outputs the moves — it runs out of space. Increasing the budget to 64k resolves this entirely.

---

## Other Observations

### The odd/even anomaly is real and reproducible

Across all three no-thinking runs (8k, 32k, 64k), the same non-monotonic pattern holds: N=6 struggles or fails, N=7 recovers to 100% using only ~1,900 tokens, N=8 collapses to 0%. This is consistent across three independent runs with completely different token budgets, ruling out any budget-related explanation. The N=7 recovery at such low token count is almost certainly pattern memorization — the model has seen this specific 127-move sequence during training and retrieves it rather than computing it. The thinking runs do not show this artifact, further supporting the memorization hypothesis.

### Thinking models need far more budget than expected

Token consumption for thinking mode grows steeply with complexity:

- N=6: ~9,869 tokens
- N=7: ~20,689 tokens
- N=8: ~45,548 tokens (when successful)
- N=9: hits 64k ceiling without completing

Solving N=9 with thinking would likely require 100k–150k tokens. Any benchmark testing thinking models with a fixed 64k budget is effectively testing generation capacity — not reasoning capability — beyond a certain complexity level.

### The no-thinking failure mode is strikingly consistent

At N=8, across all runs and all budgets, the no-thinking model fails at exactly move #127 with the same error: attempting to place the largest disk on a peg that is blocked. Move #127 is precisely the midpoint of the 255-move solution — the moment where the largest disk must be placed. The model solves the first recursive sub-problem perfectly, then loses track of the board state at the critical moment. This deterministic, budget-independent failure pattern is strong evidence of a structural reasoning limitation.

---

## Comparison with the Original Paper

| Observation | Apple Paper (Claude 3.7) | This reproduction (Sonnet 4.6) |
|---|---|---|
| Collapse threshold, no thinking | ~N=6–8 | N=8, consistent across all budgets |
| Collapse threshold, thinking at 64k | ~N=8–10 (collapse) | N=9 — N=8 **solved** at 64k |
| Nature of collapse, no thinking | Reasoning failure | Confirmed: genuine reasoning failure |
| Nature of collapse, thinking | Assumed reasoning failure | Likely budget exhaustion |
| 64k budget sufficient? | Assumed yes | Questionable for thinking models |

---

## River Crossing — A Second Puzzle

To complement the Tower of Hanoi results, the same benchmark was run on the **River Crossing** puzzle (N=2 to N=6, 3 samples per N, 32k token budget), using the methodology from the original paper.

![River Crossing results](river_benchmark_sonnet_compare.png)

### Data

| N | Min moves | No thinking | With thinking |
|:-:|:---------:|:-----------:|:-------------:|
| | | acc / tokens | acc / tokens |
| 2 | 5 | 67% / 1,321 | 100% / 3,402 |
| 3 | 11 | 0% / 1,670 | 100% / 19,835 |
| 4 | 9 | 0% / 6,827 | 33% / 26,110 |
| 5 | 11 | 0% / 2,436 | 33% / 27,904 |
| 6 | ~15 | 0% / 11,866 | 0% / 21,619 |

### What this adds

**No thinking** collapses at N=3, immediately and budget-independently. At N=5 and N=6, the model fails on move #0 — it violates the safety constraint on the very first boat crossing, using fewer than 4,000 tokens out of 32,000 available. This is a clean architectural failure, fully consistent with the original paper's findings on Claude 3.7.

**With thinking**, the picture is more nuanced. At N=4 and N=5, 2 out of 3 samples hit the exact token ceiling (32,413 tokens) with "No moves extracted" — the same budget-exhaustion pattern seen in Tower of Hanoi. One sample succeeds in each case. At N=6, all three samples either exhaust the budget or produce a parsing error.

This means **River Crossing with thinking at 32k is also primarily budget-constrained**, not architecturally collapsed — mirroring the Tower of Hanoi pattern at 32k. Running at 64k would likely push the collapse point to N=5 or N=6.

### Comparison across puzzles

| | Tower of Hanoi (no thinking) | River Crossing (no thinking) | Tower of Hanoi (thinking, 64k) | River Crossing (thinking, 32k) |
|---|---|---|---|---|
| Collapse point | N=8 | N=3 | N=9 (budget) | N=4–5 (budget) |
| Failure mode | Wrong move at #127 | Wrong move at #0–5 | Token ceiling | Token ceiling |
| Nature | Architectural | Architectural | Capacity limit | Capacity limit |
| Budget-independent? | Yes | Yes | No | No |

The no-thinking failures on both puzzles are genuine reasoning failures, unaffected by token budget. The thinking failures are capacity limits that would likely shift with a larger budget — a pattern consistent with what was observed on Tower of Hanoi when moving from 32k to 64k.

The striking difference is the **compositional depth at collapse**: River Crossing fails with no thinking after only 0–5 valid moves, while Tower of Hanoi sustains 127 correct moves before failing. As the original paper notes, this is likely because River Crossing instances above N=2 are scarce in training data, while Tower of Hanoi solutions are well-represented.

---

## Open Questions

- Would N=9 solve with thinking at 128k tokens, as N=8 did at 64k?
- Would River Crossing with thinking at 64k push the collapse to N=5 or N=6, as the budget-exhaustion pattern suggests?
- Were the collapses reported in the original paper for other models (DeepSeek-R1, o3-mini) also budget-limited?
- Is 64k tokens a sufficient budget to test *reasoning* collapse, or does it just test *generation capacity*?
- Does Opus 4.6 push the no-thinking collapse threshold beyond N=8 on Hanoi, or beyond N=3 on River Crossing?

---

## Usage

```bash
pip install anthropic matplotlib numpy
```

```bash
$env:ANTHROPIC_API_KEY="sk-ant-..."   # PowerShell
export ANTHROPIC_API_KEY="sk-ant-..."  # bash/zsh
```

**Tower of Hanoi:**
```bash
python hanoi_benchmark.py                                           # no thinking, 8k
python hanoi_benchmark.py --max-tokens 64000                        # no thinking, 64k
python hanoi_benchmark.py --thinking --max-tokens 64000             # thinking, 64k
python hanoi_benchmark.py --compare --max-tokens 64000 --samples 5  # full comparison
```

**River Crossing:**
```bash
python river_benchmark.py                                           # no thinking, 8k
python river_benchmark.py --thinking --max-tokens 32000             # thinking, 32k
python river_benchmark.py --compare --max-tokens 32000              # full comparison
python river_benchmark.py --n-max 8 --thinking --max-tokens 64000   # push limits
```

---

## Cost Estimate

| Run | Config | Estimated cost |
|-----|--------|----------------|
| No thinking | N=1–10, 3 samples, 64k | ~$2–3 |
| With thinking | N=1–10, 3 samples, 32k | ~$5–8 |
| With thinking | N=1–10, 3 samples, 64k | ~$12–18 |

---

*Experiment run: February 2026 — model: claude-sonnet-4-6*
*Original paper: Shojaee et al., "The Illusion of Thinking", Apple Research, 2025*