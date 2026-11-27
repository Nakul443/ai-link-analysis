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
| plumbing-austin.com | gemini-3.8-flash | 5 | 5 | 4 | 5 | 5 | 4 | 28 |
| saas-analytics.io | gemini-3.8-flash | 5 | 4 | 4 | 5 | 5 | 5 | 28 |
| local-bakery.com | gemini-3.8-flash | 5 | 5 | 4 | 4 | 5 | 4 | 27 |
| (untested) | gemini-3.7-flash | - | - | - | - | - | - | - |
| (untested) | gpt-5.6-luna | - | - | - | - | - | - | - |
| (untested) | claude-haiku-5-5 | - | - | - | - | - | - | - |

**Averages per model:**
- `gemini-3.8-flash`: 27.7 / 30 (`MEASURED` via smoke and structured validation test cases)
- `gemini-3.7-flash`: Not measured directly due to provider endpoint availability; expected parity with 3.8 Flash (`ESTIMATE`).
- `gpt-5.6-luna` / `claude-haiku-5-5`: Not measured live (no active keys configured); estimates derived from provider specifications (`SOURCE`).

