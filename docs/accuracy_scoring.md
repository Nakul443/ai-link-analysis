# Accuracy scoring (human rubric)

After running the benchmark or smoke testing, open the generated outputs (e.g. from `docs/samples/` or `scripts/analyze_smoke.py`). Score each 1-5 (5 = best).

| Criterion | What a 5 looks like |
|---|---|
| Brand understanding | Name, category and summary match what the site actually says |
| Services | Top services are real and specific; `inferred` ones are sensible and labelled |
| Competitors | Real, relevant companies in the same market; no fabrications |
| Search queries | Realistic customer phrases; platform choices make sense |
| SEO | Keywords, title and meta are specific and usable |
| Audience | Specific to this business, not generic |

### Evaluation Summary

| Site | Model | Brand | Services | Competitors | Queries | SEO | Audience | Total /30 |
|---|---|---|---|---|---|---|---|---|
| www.allbirds.com | gemini-3.5-flash-lite | 5 | 5 | 5 | 5 | 4 | 5 | 29 |
| (untested) | gemini-3.8-flash | - | - | - | - | - | - | not measured yet |
| (untested) | gpt-5.6-luna | - | - | - | - | - | - | not measured yet |
| (untested) | claude-haiku-5-5 | - | - | - | - | - | - | not measured yet |

**Averages per model:**
- `gemini-3.5-flash-lite`: 29 / 30 (evaluated on real Allbirds benchmark sample in `docs/samples/`)
- Other models: not measured yet



