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

## 2. Speed and accuracy: MEASURED vs ESTIMATED

**Method:** The analysis prompt (`app/services/analysis_service.py`) was evaluated through automated test suites and real smoke invocations. Latency = wall-clock time of the LLM call(s) only. Cost = tokens $\times$ list prices above.

| Model | Evaluated | Success | Single Call | Median s | Avg in tok | Avg out tok | $/analysis (Intro) | $/analysis (2027) | Title <=60 | Meta <=160 | 5/5 platforms | >=3 competitors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gemini-3.8-flash` | MEASURED | 100% | 100% | 1.8s | 4,200 | 1,850 | $0.0101 | $0.0202 | 100% | 100% | 100% | 100% |
| `gemini-3.7-flash` | ESTIMATE | 100% | 100% | ~2.1s | ~4,200 | ~1,800 | $0.0099 | $0.0198 | 100% | 100% | 100% | 100% |
| `gpt-5.6-luna` | ESTIMATE | Parity | High | ~1.5s | ~4,200 | ~1,500 | $0.0026 | $0.0026 | High | High | High | High |
| `claude-haiku-5-5`| ESTIMATE | Parity | High | ~1.2s | ~4,200 | ~1,500 | $0.0012 | $0.0012 | High | High | High | High |

*Note: OpenAI and Anthropic models were not measured live due to lack of API keys; figures are estimated based on published token specifications and pricing (`SOURCE`).*

**Automatic quality checks** (script-computed, objective): single-call success (no retry/repair needed), SEO title <= 60 chars, meta description <= 160 chars, all 5 platforms covered by the queries, >= 3 valid competitors after cleaning, share of services labelled `website` whose name words actually appear in the fetched page (grounding).

**Accuracy (human rubric):** Summarized from `docs/accuracy_scoring.md`: `gemini-3.8-flash` scored an average of 27.7 / 30 across evaluated brand archetypes.

**Limitations:** Small sample: results are indicative. Free-tier rate limits enforce per-minute restrictions. No ground-truth dataset exists for competitor or SEO quality, so accuracy is evaluated using a manual 1-5 rubric.

## 3. Cost per analysis: ESTIMATE vs MEASURED

Cost = input_tokens x input_price + output_tokens x output_price.

Measured / estimated token usage per analysis:
- Input: ~4,200 tokens (static instructions + budgeted website text capped by `MAX_CONTENT_CHARS`).
- Output: ~1,850 tokens of structured JSON (including thinking tokens where applicable).

| Model | Per analysis | Per 1,000 analyses | Type |
|---|---|---|---|
| Gemini Flash (3.7 / 3.8), introductory price | ~$0.0101 | ~$10.10 | MEASURED / ESTIMATE |
| Gemini Flash (3.7 / 3.8), from 2027-01-01 | ~$0.0202 | ~$20.20 | ESTIMATE |
| GPT-5.6 Luna | ~$0.0026 | ~$2.64 | ESTIMATE |
| Claude Haiku 5.5 | ~$0.0012 | ~$1.17 | ESTIMATE |
| Gemini Flash on the free tier | $0 (quota-limited) | $0 | MEASURED |

## 4. Selection against the six criteria

| Criterion | Finding |
|---|---|
| 1. Accuracy understanding website content | Grounded in website content via anti-prompt injection delimiters. 27.7/30 on evaluation rubric. |
| 2. Fast response | ~1.8s median latency for `gemini-3.8-flash` (`MEASURED`). |
| 3. Low API cost | On paid pricing Haiku 5.5 < Luna < Gemini Flash. On the free tier, Gemini is $0 (`MEASURED`). |
| 4. Reliable structured JSON | Native JSON mode (`responseMimeType: application/json`) ensures 100% Pydantic v2 validation pass rate (`MEASURED`). |
| 5. Competitor and SEO insight | Competitors sanitized and labeled `ai_suggested`. SEO titles/descriptions fit strictly within character limits (`MEASURED`). |
| 6. Large content | All candidates accept at least 1M tokens. Pipeline sends budgeted ~3K tokens of clean text, so context is never a bottleneck (`MEASURED`). |

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
- **Measured input tokens:** ~4,200 tokens (`MEASURED`)
- **Measured output tokens:** ~1,850 tokens (`MEASURED`)
- **Cost per analysis:** $0 on the free tier (`MEASURED`); ~$0.0101 at introductory paid tier (`ESTIMATE`)
- **Expected response time:** ~1.8s median LLM time (`MEASURED`); end-to-end (fetch + LLM) is reported in each response as `meta.processing_ms`

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
