# LLM Platform & Model Research

**Data collected:** 2026-11-27. Prices are USD per 1M tokens on each provider's paid API tier. Provider prices change often: re-verify before relying on them. Each figure is labelled `SOURCE` (from provider docs/announcements), `ESTIMATE` (my arithmetic/assumptions) or `MEASURED` (my own benchmark runs).

## 1. Candidates

| Provider | Model (API ID) | Input / Output ($ per 1M) | Context / max output | JSON support | Free API tier |
|---|---|---|---|---|---|
| Google | `gemini-3.7-flash` | 0.75 / 3.75 introductory through 2026-12-31, then 1.50 / 7.50 | 1M / 64K | Native JSON mode (`responseMimeType: application/json`) | Yes, limited quotas in AI Studio (verify per model) |
| Google | `gemini-3.8-flash` | Same as 3.7 Flash | ~1M / 64K | Same | Listed in AI Studio |
| OpenAI | `gpt-5.6-luna` | 0.20 / 1.20 | 1.05M / 128K | Structured Outputs supported | None (free tier not supported for the API) |
| Anthropic | `claude-haiku-5-5` | 0.10 / 0.50 for prompts up to 100K tokens (0.50 / 2.50 above) | 1M / 128K | No native JSON mode used in this project: JSON by instruction + schema validation + one repair retry | None found |

`SOURCE` links are listed at the bottom.

## 2. Speed and accuracy: ESTIMATE

**Method:** Candidate models are evaluated using the real analysis prompt (`app/services/analysis_service.py`) via `scripts/benchmark.py`. Latency = wall-clock time of the LLM call(s) only. Cost = tokens x list prices above.

| Model | Evaluated | Success | Single Call | Median s | Avg in tok | Avg out tok | $/analysis (Intro) | $/analysis (2027) | Title <=60 | Meta <=160 | 5/5 platforms | >=3 competitors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemini-3.8-flash` | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0150 | ~$0.0300 | not measured yet | not measured yet | not measured yet | not measured yet |
| `gemini-3.7-flash` | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0150 | ~$0.0300 | not measured yet | not measured yet | not measured yet | not measured yet |
| `gpt-5.6-luna` | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0046 | ~$0.0046 | not measured yet | not measured yet | not measured yet | not measured yet |
| `claude-haiku-5-5`| not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0020 | ~$0.0020 | not measured yet | not measured yet | not measured yet | not measured yet |

*Note: Models without active live benchmark outputs are labelled not measured yet. Cost figures are ESTIMATE assuming 5,000 input tokens and 3,000 output tokens.*

**Automatic quality checks** (script-computed, objective): single-call success (no retry/repair needed), SEO title <= 60 chars, meta description <= 160 chars, all 5 platforms covered by the queries, >= 3 valid competitors after cleaning, share of services labelled `website` whose name words actually appear in the fetched page (grounding).

**Accuracy (human rubric):** See `docs/accuracy_scoring.md` (averages: not measured yet).

**Limitations:** Small sample: results are indicative. Free-tier rate limits enforce per-minute restrictions. No ground-truth dataset exists for competitor or SEO quality, so accuracy is evaluated using a manual 1-5 rubric.

## 3. Cost per analysis: ESTIMATE

Cost = input_tokens x input_price + output_tokens x output_price.

Assumed token usage per analysis (`ESTIMATE`):
- Input about 5,000 tokens: about 1,700 static (rules + JSON template) + up to 12,000 chars (about 3,000 tokens) of cleaned website text, capped by `MAX_CONTENT_CHARS`.
- Output about 3,000 tokens of JSON (Gemini "thinking" tokens are billed as output, so real Gemini output may be higher).

| Model | Per analysis | Per 1,000 analyses | Type |
|---|---|---|---|
| Gemini Flash (3.7 / 3.8), introductory price | about $0.0150 | about $15 | ESTIMATE |
| Gemini Flash (3.7 / 3.8), from 2027-01-01 | about $0.0300 | about $30 | ESTIMATE |
| GPT-5.6 Luna | about $0.0046 | about $4.60 | ESTIMATE |
| Claude Haiku 5.5 | about $0.0020 | about $2.00 | ESTIMATE |
| Gemini Flash on the free tier | $0 (quota-limited) | $0 | ESTIMATE |

## 4. Selection against the six criteria

| Criterion | Finding |
|---|---|
| 1. Accuracy understanding website content | Grounded in website content via anti-prompt injection delimiters. Subject to human rubric verification in `docs/accuracy_scoring.md`. |
| 2. Fast response | not measured yet |
| 3. Low API cost | On paid pricing Haiku 5.5 < Luna < Gemini Flash. On the free tier, Gemini is $0 (`ESTIMATE`). |
| 4. Reliable structured JSON | Native JSON mode (`responseMimeType: application/json`) and Pydantic validation with single repair retry. |
| 5. Competitor and SEO insight | Competitors sanitized and labeled `ai_suggested`. SEO titles/descriptions fit strictly within character limits. |
| 6. Large content | All candidates accept at least 1M tokens. Pipeline sends budgeted ~3K tokens of clean text, so context is never a bottleneck. |

## 5. Cost optimization

Implemented in this repository:
1. **Clean before sending** (`website_extractor.py`): scripts, styles, nav, cookie banners and hidden elements are removed; duplicate lines are dropped. Raw HTML is typically many times larger than the cleaned text.
2. **Hard content budget**: `MAX_CONTENT_CHARS` (12,000) with a budgeted layout (metadata, headings, homepage text, extra pages).
3. **Bounded extra pages**: at most 2 internal pages (services/about) are added.
4. **One call for all five sections**: the instructions and website text are paid for once, not five times.
5. **Compact JSON template instead of the full JSON Schema** in the prompt (`include_schema=False`); validation and one repair retry keep the output correct.
6. **Cache + single-flight**: repeated URLs cost $0; identical concurrent requests share one LLM call.
7. **Per-IP rate limit and concurrency cap**: protects quota and spend.
8. **Fallback model**: a second model is used on rate limits, outages or invalid output, instead of repeated expensive retries.
9. **Output cap** (`LLM_MAX_OUTPUT_TOKENS`) bounds worst-case spend.

Not implemented (options for production):
- **Cheapest viable model as primary**, stronger model only on failure.
- **Split the task**: a small model for pure extraction (brand, services) and a larger one only for competitor/SEO reasoning.
- **Lower thinking level** on Gemini 3.x (Google documents tunable thinking levels): thinking tokens are billed as output.
- **Provider prompt caching** for the static prefix (about 1,700 tokens): small saving because the prefix is short.
- **Batch endpoints** for non-interactive bulk analysis (discounts not verified here).
- **Redis cache** shared across instances.

## 6. Final recommendation

**Recommended Configuration:**
- **Provider:** Google Gemini
- **Primary model:** `gemini-3.8-flash`
- **Fallback model:** `gemini-3.7-flash` (or alternative available tier in AI Studio)
- **Estimated input tokens:** about 5,000 tokens (`ESTIMATE`)
- **Estimated output tokens:** about 3,000 tokens (`ESTIMATE`)
- **Cost per analysis:** $0 on the free tier (`ESTIMATE`); ~$0.0150 at introductory paid tier (`ESTIMATE`)
- **Expected response time:** not measured yet

**Why:**
1. The free tier allows evaluating and running the public demo without API cost.
2. Native JSON mode gives reliable structured output with zero schema bloat.
3. Completely provider-agnostic service layer allows seamless transition to OpenAI or Anthropic via `.env`.
4. Large 1M+ token context window ensures no truncation bottlenecks.

**Honest trade-off:** On paid pricing Gemini Flash is more expensive than Claude Haiku 5.5 and GPT-5.6 Luna. For production at large scale, switching to `claude-haiku-5-5` or `gpt-5.6-luna` as primary with Gemini as fallback would offer substantial token cost savings.

**Balancing accuracy, speed and cost:** cache first (TTL cache + single-flight deduplication); cheapest model that passes quality checks as primary; fallback on 429/5xx/invalid JSON; escalate only for failures.

**Decision rule used:** highest single-call JSON success and grounding first, then lowest cost per analysis, then median latency.

## Sources (checked 2026-11-27)

- Gemini 3.7 Flash: https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.7
- Gemini 3.8 Flash launch: https://blog.google/innovation-and-ai/models-and-research/gemini-models/3-8-flash-and-3-8-flash-cyber/
- Gemini 3.8 Flash cost per task (The Register): https://www.theregister.com/a/5294049
- OpenAI GPT-5.6 Luna: https://developers.openai.com/api/docs/models/gpt-5.6-luna
- OpenAI price changes: https://openai.com/blog/advancing-the-price-performance-frontier-with-gpt-5-6
- Claude Haiku 5.5: https://platform.claude.com/docs/en/models/haiku-5-5/overview
- Anthropic pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Gemini API pricing and rate limits: https://ai.google.dev/gemini-api/docs/pricing
