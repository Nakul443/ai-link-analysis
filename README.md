# ai-link-analysis

FastAPI service that analyzes any website URL with an LLM and returns competitor, search query, SEO, services and audience insights as structured JSON.

**Live demo:** https://ai-link-analysis.onrender.com  
**API endpoint:** `POST https://ai-link-analysis.onrender.com/link-analysis`  
**API docs:** https://ai-link-analysis.onrender.com/docs  

---

## How it works

```text
URL -> validate (format + SSRF) -> safe fetch (manual redirects, each hop re-checked)
    -> HTML cleanup -> single LLM call -> Pydantic validation (1 repair, fallback model)
    -> post-processing -> JSON response
```

1. **Validation & SSRF Protection:** Validates scheme (`http`/`https`) and ports (80/443). Blocks private, loopback, link-local, cloud metadata, NAT64, and 6to4 IP addresses.
2. **Safe Multi-Page Fetching:** Connects using `httpx`, manually follows redirects (max 3), re-verifying SSRF on every hop. Fetches homepage plus up to 2 high-value sub-pages (e.g., `/services`, `/about`).
3. **HTML Cleanup & Budgeting:** Strips noise (scripts, styles, tracking tags, cookie/GDPR containers) and deduplicates text within a strict character budget (`MAX_CONTENT_CHARS = 12000`).
4. **Single-Call Structured LLM Generation:** Sends prompt with a compact JSON template. Supports Gemini, OpenAI, and Anthropic. Validates with Pydantic v2 with automatic single-repair retry and model fallback.
5. **Post-Processing & Caching:** Sanitizes competitor sites, enforces SEO title/description constraints, attaches operational metadata, and caches in-memory with single-flight request deduplication.

---

## Setup & Running Locally

### Prerequisites
- Python 3.11+
- Git

### Installation
```bash
git clone https://github.com/Nakul443/ai-link-analysis.git && cd ai-link-analysis
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # Configure your LLM_API_KEY and model IDs
uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload
```

- **Interactive API Docs:** [http://localhost:4000/docs](http://localhost:4000/docs)
- **Health Check:** `curl http://localhost:4000/health`
- **Run Tests:** `pytest -q` (92 unit & integration tests)

---

## Configuration

All configuration is managed via environment variables (see `.env.example`). Changing providers requires **zero code changes**:

```env
# App
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=4000
LOG_LEVEL=INFO

# LLM Configuration (supports: gemini | openai | anthropic)
LLM_PROVIDER=gemini
LLM_API_KEY=your_api_key_here
LLM_MODEL=gemini-3.8-flash
LLM_FALLBACK_MODEL=gemini-3.7-flash
LLM_MAX_OUTPUT_TOKENS=8192
LLM_TEMPERATURE=0.2
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2

# Fetching & Content Budget
FETCH_TIMEOUT_SECONDS=15
FETCH_MAX_BYTES=2000000
FETCH_MAX_REDIRECTS=3
MAX_CONTENT_CHARS=12000
EXTRA_PAGES_MAX=2

# Cache & Rate Limiting
CACHE_TTL_SECONDS=3600
CACHE_MAX_ENTRIES=200
RATE_LIMIT_PER_MINUTE=6
MAX_CONCURRENT_ANALYSES=2
ANALYSIS_TIMEOUT_SECONDS=120
TRUST_PROXY=false
CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

---

## Sample Request & Response

### Request
```bash
curl -X POST http://localhost:4000/link-analysis \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```

### Response (Trimmed)
```json
{
  "success": true,
  "website": "https://example.com/",
  "brand": {
    "name": "Example Services",
    "category": "Home services",
    "summary": "Example services provider in Austin offering emergency pipe, drain, and heater repairs."
  },
  "analysis": {
    "competitor_analysis": {
      "brand_overview": "Local company serving residential and small business clients.",
      "business_category": "Home & Drain Services",
      "main_products_services": ["Drain cleaning", "Leak detection", "Water heater repair"],
      "your_brand": {
        "brand_name": "Example Services",
        "main_service": "Emergency repairs",
        "target_market": "Austin metropolitan homes",
        "key_strength": "24/7 rapid response",
        "opportunity": "Online review collection and local SEO"
      },
      "competitors": [
        {
          "brand_name": "National Competitor",
          "website": "https://competitor.example",
          "main_service": "Repair and drain cleaning",
          "target_market": "Commercial and residential",
          "key_strength": "National brand recognition",
          "opportunity": "Transparent pricing",
          "strengths": ["24/7 availability", "Large fleet"],
          "weaknesses": ["Higher franchised costs"],
          "source": "ai_suggested"
        }
      ],
      "competitive_advantages": ["Fast emergency response", "Transparent local pricing"],
      "positioning_opportunities": ["Become the go-to emergency choice in North Austin"]
    },
    "target_search_queries": [
      {
        "query": "24/7 emergency service austin",
        "platforms": ["Google"],
        "intent": "Emergency Commercial Intent",
        "business_relevance": "Primary high-converting service inquiry",
        "target_audience": "Homeowners experiencing sudden leaks or clogs"
      },
      {
        "query": "best contractor recommendations austin tx",
        "platforms": ["Facebook", "X"],
        "intent": "Community Discovery & Peer Validation",
        "business_relevance": "Word-of-mouth referral capture",
        "target_audience": "Local neighborhood homeowners"
      }
    ],
    "seo_improvements": {
      "primary_keyword": "emergency service austin",
      "secondary_keywords": ["emergency repairs austin", "drain cleaning austin tx", "water heater repair"],
      "long_tail_keywords": ["24/7 emergency water heater repair austin", "clogged sewer line clearing austin"],
      "business_keywords": ["residential repair contractor", "licensed contractor austin"],
      "recommended_title": "Emergency Service Austin TX | Fast Drain & Pipe Repair",
      "title_structure": "Primary Keyword | Key Benefit | Brand Name",
      "meta_description": "Example Services provides 24/7 emergency service, drain cleaning, and water heater repair in Austin, TX. Fast, licensed local technicians. Call now!",
      "supporting_content": [
        {
          "type": "faq",
          "title": "Emergency FAQs: Costs, Arrival Times & What to Do",
          "rationale": "Answers common urgent homeowner questions to improve search click-through."
        }
      ]
    },
    "top_services": [
      {
        "name": "Drain Cleaning",
        "description": "Clearing main line and household clogs with motorized augers.",
        "target_industry": "Residential",
        "target_customer": "Homeowners",
        "main_benefit": "Immediate relief from water backup",
        "source": "website"
      },
      {
        "name": "Preventative Inspections",
        "description": "Annual video inspection of pipes to detect hairline fractures.",
        "target_industry": "Residential",
        "target_customer": "Home buyers and property managers",
        "main_benefit": "Prevents catastrophic flood damage",
        "source": "inferred"
      }
    ],
    "target_audience": {
      "primary_audience": ["Austin homeowners"],
      "secondary_audience": ["Residential property managers", "Landlords"],
      "industries": ["Residential Real Estate", "Property Management"],
      "business_types": ["Single-family homes", "Multi-family duplexes"],
      "customer_profiles": ["Busy working homeowners needing quick, reliable repairs"],
      "geographic_focus": "Austin, TX",
      "customer_needs": ["Rapid emergency dispatch", "Upfront honest quotes", "Clean work"],
      "customer_interests": ["Home improvement", "Property maintenance"],
      "recommended_segments": [
        {
          "audience": "Urgent Emergency Homeowners",
          "business_type": "B2C",
          "requirement": "Immediate same-day dispatch for repairs"
        }
      ]
    }
  },
  "meta": {
    "provider": "gemini",
    "model_used": "gemini-3.8-flash",
    "input_tokens": null,
    "output_tokens": null,
    "pages_analyzed": 2,
    "cached": false,
    "processing_ms": 1850,
    "warnings": [
      "Competitors are AI-generated suggestions from general knowledge and have not been verified against live sources."
    ]
  }
}
```

Full raw JSON samples are available in [`docs/samples/`](docs/samples/).

---

## Error Handling

All API errors return a standard `ErrorResponse` structure:
```json
{
  "success": false,
  "error": {
    "code": "BLOCKED_URL",
    "message": "This URL points to a private or internal address and cannot be analyzed."
  }
}
```

| HTTP Status | Error Code | Description |
|---|---|---|
| `400` | `INVALID_URL` | Malformed URL, unsupported scheme (must be http/https), or bad port |
| `400` | `BLOCKED_URL` | SSRF attempt (private IP, loopback, cloud metadata, link-local) |
| `400` | `HOST_NOT_RESOLVED` | DNS lookup failed |
| `400` | `INVALID_REQUEST` | Missing `url` in JSON body or unparseable JSON |
| `404` | `NOT_FOUND` | Route does not exist |
| `405` | `METHOD_NOT_ALLOWED` | Incorrect HTTP method |
| `422` | `SITE_ACCESS_RESTRICTED` | Target website returned 401, 403, or 429 |
| `422` | `UNSUPPORTED_CONTENT` | Non-HTML content (PDF, image, audio, etc.) |
| `422` | `INSUFFICIENT_CONTENT` | Page has insufficient text (empty or client-rendered SPA) |
| `429` | `RATE_LIMITED` | Too many requests per IP (returns `Retry-After` header) |
| `502` | `SITE_UNREACHABLE` | Could not connect to site or encountered redirect loop |
| `502` | `LLM_INVALID_RESPONSE` | Model output was not valid JSON or failed schema validation |
| `503` | `LLM_UNAVAILABLE` | AI service is temporarily down or rate-limited |
| `504` | `SITE_TIMEOUT` / `LLM_TIMEOUT` | Website or LLM request exceeded timeout limit |
| `500` | `INTERNAL_ERROR` | Unexpected server failure |

---

## Security

- **Strict SSRF Guard:** Enforces `http` or `https` on ports 80 and 443 only. Resolves hostnames and validates that *every* returned IP address is a publicly routable global IP. Blocks `localhost`, `metadata.google.internal`, `.local`, `.internal`, IPv4-mapped IPv6, NAT64 (`64:ff9b::/96`), and 6to4 (`2002::/16`).
- **Manual Redirect Auditing:** Redirects are disabled in HTTP client and handled explicitly hop-by-hop. Every intermediate redirect target is re-validated through the SSRF check.
- **Prompt Injection Defense:** Web content is wrapped in `<website_content>` tags, closing tags inside extracted content are stripped, and the system prompt explicitly commands the model to treat content as untrusted data and ignore embedded instructions.
- **Secret Safety:** API keys are injected exclusively through environment variables. Keys are never printed in logs, error payloads, or responses.

---

## Honesty Notes & Grounding Constraints

- **Competitors are AI Suggestions:** Competitors are labeled with `source="ai_suggested"`. Because the model does not browse the live web, competitor entries represent AI-generated market suggestions based on general knowledge and are never presented as verified live data.
- **Service Grounding:** Services are explicitly marked as `source="website"` only when confirmed by the page content, or `source="inferred"` when recommended by the LLM.
- **Zero Hallucinated Metrics:** No search volumes, traffic figures, CPCs, or ranking scores are included because no live SEO index is integrated.

---

## LLM Research & Model Comparison (Summary)

Candidate models evaluated for cost, latency, token consumption, and structured output reliability:

| Provider | Model (API ID) | Input / Output ($ per 1M) | Context / Max Output | JSON Mode | Free API Tier |
|---|---|---|---|---|---|
| Google | `gemini-3.8-flash` | $0.75 / $3.75 | 1M / 64K | Native JSON Mode | Yes (quota limited) |
| Google | `gemini-3.7-flash` | $0.75 / $3.75 | 1M / 64K | Native JSON Mode | Yes (quota limited) |
| OpenAI | `gpt-5.6-luna` | $0.20 / $1.20 | 1.05M / 128K | Structured Outputs | None |
| Anthropic | `claude-haiku-5-5` | $0.10 / $0.50 | 1M / 128K | Prompt + Validation | None |

### Model Benchmarks
Cost estimates assume 5,000 input tokens and 3,000 output tokens (`ESTIMATE`):

| Model | Evaluated | Success | Single Call | Median s | Avg in tok | Avg out tok | $/analysis (Intro) | $/analysis (2027) | Title <=60 | Meta <=160 |
|---|---|---|---|---|---|---|---|---|---|---|
| `gemini-3.8-flash` | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0150 | ~$0.0300 | not measured yet | not measured yet |
| `gemini-3.7-flash` | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0150 | ~$0.0300 | not measured yet | not measured yet |
| `gpt-5.6-luna` | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0046 | ~$0.0046 | not measured yet | not measured yet |
| `claude-haiku-5-5`| not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | not measured yet | ~$0.0020 | ~$0.0020 | not measured yet | not measured yet |

**Final Recommendation:**
- **Primary:** `gemini-3.8-flash` (provides $0 free-tier operations for evaluation and robust native JSON generation).
- **Fallback:** `gemini-3.7-flash` (separate quota bucket).
- **Scale Pick:** For commercial high-volume deployment, `claude-haiku-5-5` or `gpt-5.6-luna` reduces token costs by ~7.5x and ~2.3x respectively.

Full research, cost breakdown, and human scoring rubric: [docs/LLM_RESEARCH.md](docs/LLM_RESEARCH.md) and [docs/accuracy_scoring.md](docs/accuracy_scoring.md).

---

## Known Limitations

1. **No JavaScript Rendering:** Sites requiring heavy client-side JavaScript execution (SPAs) will return `INSUFFICIENT_CONTENT` errors. This is documented and by design to prevent headless browser resource exhaustion.
2. **In-Memory Cache & Limiter:** The default TTL cache and sliding-window rate limiter are stored in-process. Suitable for single-instance deployments; a Redis backend is recommended for distributed horizontal scaling.
3. **DNS Rebinding Window:** There is an inherent minor time-of-check to time-of-use (TOCTOU) gap between DNS validation and HTTP fetching.

