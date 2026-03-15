# Report Format

Each generation report is a zip file at `s3://runs/reports/{gen_id}/report.zip`.

## Zip contents

```
report.zip
├── single.md
└── images/
    ├── best_so_far_{resource_class}.png   (one per resource class)
    ├── promotion_funnel.png
    └── lineage.png
```

## single.md structure

| Section | Content |
|---|---|
| Executive Summary | total/valid/improved run counts; bronze/silver/gold counts; best candidate + val_bpb |
| Resource Class Summary | table of runs per resource class |
| Best So Far | embedded `best_so_far_*.png` images |
| Promotions | table of all promotion decisions |
| Frontier | frontier candidates with role and score |
| Lineage | embedded `lineage.png` digraph |
| Next Iteration Plan | table of next job recommendations |
| Notable Failures | list of failed/invalid runs (up to 20) |
| Artifacts | S3 paths for report zip and eval data |

## Images

**best_so_far_{resource_class}.png**
- x-axis: candidate short ID (8 chars)
- y-axis: best delta value for that candidate
- bars colored by whether the candidate had improved runs
- generated via gnuplot histogram

**promotion_funnel.png**
- horizontal bar showing: total → valid → improved → bronze → silver → gold
- generated via gnuplot

**lineage.png**
- directed graph: parent_candidate → child_candidate
- node fill color: gold=yellow, silver=lightgray, bronze=sandybrown, none=white
- generated via graphviz `dot -Tpng`
