# Example Reports

These are representative outputs of `evaluator/report.py` rendered as markdown.
Images (gnuplot / graphviz) are described in ASCII where they would appear.
Two examples are shown: a seed generation (gen 1) and a follow-on generation (gen 2).

---

## Example 1 — Generation 1 (seed run, no parent)

> `gen_id: a3f9c012`
> 5 pods launched, all on resource class `2060-12gb`, no parent candidate.

---

# SoTA@Home Generation Report: a3f9c012

Generated: 2026-03-14T18:42:01Z

## Executive Summary
- Runs: 5 total, 5 valid, 0 improved
- Promotions: 0 bronze, 0 silver, 0 gold
- Best candidate: `c7e1a4b2` — val_bpb=2.8134

## Resource Class Summary
| resource_class | total | valid |
| --- | --- | --- |
| 2060-12gb | 5 | 5 |

## Best So Far
_Seed generation — no parent-relative delta to plot._

## Promotions
| candidate_id | resource_class | promotion_level |
| --- | --- | --- |
| c7e1a4b2 | 2060-12gb | none |
| d9b3f501 | 2060-12gb | none |
| e1c24a88 | 2060-12gb | none |
| f4d87e3c | 2060-12gb | none |
| 0a6f19d7 | 2060-12gb | none |

_Reason for all: "baseline seed candidate: no parent-relative delta to score"_

## Frontier
| candidate_id | resource_class | role | promotion_level | score_hint |
| --- | --- | --- | --- | --- |
| c7e1a4b2 | 2060-12gb | gold | none | 2.8134 |
| d9b3f501 | 2060-12gb | diversity | none | 2.8291 |

_Best seed candidate selected as gold-role for next generation. One diversity slot filled._

## Lineage
```
(seed generation — no parent edges)

  [c7e1a4b2]   val_bpb=2.8134
  [d9b3f501]   val_bpb=2.8291
  [e1c24a88]   val_bpb=2.8307
  [f4d87e3c]   val_bpb=2.8419
  [0a6f19d7]   val_bpb=2.8512
```

## Next Iteration Plan
| resource_class | parent_candidate_id | job_type | rationale |
| --- | --- | --- | --- |
| 2060-12gb | c7e1a4b2 | exploit | best seed: val_bpb=2.8134; exploit with tighter lr schedule |
| 2060-12gb | c7e1a4b2 | exploit | best seed: replicate to confirm |
| 2060-12gb | c7e1a4b2 | verify | verify improvement reproducibility |
| 2060-12gb | d9b3f501 | explore | diversity candidate: try wider hidden dim |
| 2060-12gb | d9b3f501 | explore | diversity candidate: try different activation |

## Notable Failures
_None._

## Artifacts
- S3 report zip: s3://runs/reports/a3f9c012/report.zip
- S3 eval data: s3://runs/evaluations/a3f9c012/

---
---

## Example 2 — Generation 2 (first improvement generation)

> `gen_id: b7d4e823`
> 5 pods launched. Parent candidates from gen 1: `c7e1a4b2` (exploit ×3, verify ×1) and `d9b3f501` (explore ×1).
> Two candidates show improvement; one reaches Silver via seed diversity.

---

# SoTA@Home Generation Report: b7d4e823

Generated: 2026-03-15T02:11:38Z

## Executive Summary
- Runs: 5 total, 5 valid, 3 improved
- Promotions: 2 bronze, 1 silver, 0 gold
- Best candidate: `11f83a90` — val_bpb=2.7801

## Resource Class Summary
| resource_class | total | valid |
| --- | --- | --- |
| 2060-12gb | 5 | 5 |

## Best So Far

```
Best delta per candidate (2060-12gb)
─────────────────────────────────────────────────────
  11f83a90 ███████████████████░░░  Δ=-0.0333  ✓ improved
  29c07b1e █████████████░░░░░░░░░  Δ=-0.0221  ✓ improved
  3ad15f62 ██████░░░░░░░░░░░░░░░░  Δ=-0.0098  ✓ improved
  4be92c4d ░░░░░░░░░░░░░░░░░░░░░░  Δ=+0.0012  ✗ no improvement
  5cf43e71 ░░░░░░░░░░░░░░░░░░░░░░  Δ=+0.0041  ✗ no improvement

  (lower val_bpb is better; bars show magnitude of improvement)
```

## Promotions
| candidate_id | resource_class | promotion_level |
| --- | --- | --- |
| 11f83a90 | 2060-12gb | silver |
| 29c07b1e | 2060-12gb | bronze |
| 3ad15f62 | 2060-12gb | bronze |
| 4be92c4d | 2060-12gb | none |
| 5cf43e71 | 2060-12gb | none |

**Promotion details:**

`11f83a90` → **silver**
- 3 runs met bronze threshold (normalized_delta ≥ 0.001)
- improvement reproduced across multiple seeds (seeds: 42, 1337, 7) — single worker node, seed diversity path

`29c07b1e` → **bronze**
- 2 runs met bronze threshold
- only 1 distinct seed — silver requires ≥ 2 distinct seeds or ≥ 2 distinct workers

`3ad15f62` → **bronze**
- 1 run met bronze threshold (normalized_delta = 0.0021)

`4be92c4d` → **none**
- near miss: best normalized_delta 0.0004 ≥ near_miss_delta 0.0005? No — did not meet bronze threshold

`5cf43e71` → **none**
- did not meet bronze threshold (normalized_delta = -0.0018)

## Frontier
| candidate_id | resource_class | role | promotion_level | score_hint |
| --- | --- | --- | --- | --- |
| 11f83a90 | 2060-12gb | gold | silver | 2.7801 |
| 29c07b1e | 2060-12gb | silver | bronze | 2.8070 |
| 3ad15f62 | 2060-12gb | near_miss | bronze | 2.8036 |
| 4be92c4d | 2060-12gb | diversity | none | 2.8146 |

## Lineage

```
Generation 1 (seeds)          Generation 2 (current)

  [c7e1a4b2]  ────────────►  [11f83a90]  ★ SILVER  val_bpb=2.7801
  (val=2.8134)  ────────────►  [29c07b1e]  ◆ bronze  val_bpb=2.8070
                ────────────►  [3ad15f62]  ◆ bronze  val_bpb=2.8036
                ────────────►  [4be92c4d]  · none    val_bpb=2.8146

  [d9b3f501]  ────────────►  [5cf43e71]  · none    val_bpb=2.8175
  (val=2.8291)

  Node colors: yellow=gold, lightgray=silver, sandybrown=bronze, white=none
```

## Promotion Funnel

```
Total      ████████████████████  5
Valid      ████████████████████  5
Improved   ████████████          3
Bronze     ████████              2
Silver     ████                  1
Gold       ░                     0
```

## Next Iteration Plan
| resource_class | parent_candidate_id | job_type | rationale |
| --- | --- | --- | --- |
| 2060-12gb | 11f83a90 | exploit | silver candidate: best val_bpb=2.7801; push lr schedule further |
| 2060-12gb | 11f83a90 | exploit | silver candidate: replicate exploit with different batch size |
| 2060-12gb | 11f83a90 | verify | verify silver: need ≥3 improved runs for gold |
| 2060-12gb | 29c07b1e | explore | bronze: explore wider architecture variant |
| 2060-12gb | 4be92c4d | explore | diversity: explore near-miss candidate with different optimizer |

## Notable Failures
_None._

## Artifacts
- S3 report zip: s3://runs/reports/b7d4e823/report.zip
- S3 eval data: s3://runs/evaluations/b7d4e823/

---

## Notes on report generation

- Charts (gnuplot / graphviz) are embedded as PNG in the real zip; ASCII art above approximates their content.
- Silver via seed diversity is new as of the reproducible-promotion fix: a candidate with 2+ distinct seeds on a single worker node can now reach Silver.
- `train_s3_key` in next-job assignments now points to `generations/{parent_gen_id}/{run_id}/train.py` (using the best run's `run_id`, not `candidate_id`), which is what the pod downloads as its starting point.
- `expected_pods` in Redis now equals `len(job_assignments)` — each pod gets its own parent assignment rather than all pods sharing `parent_candidate_ids[0]`.
