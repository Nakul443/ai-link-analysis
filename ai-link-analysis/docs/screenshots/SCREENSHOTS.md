# API Verification & Terminal Outputs

This directory stores verification records and screenshots of the deployed and local API.

### 1. Swagger UI `/docs`
Endpoint: `GET /docs`
Status: `200 OK`
Provides interactive OpenAPI schema documentation for `POST /link-analysis` and `GET /health`.

---

### 2. Successful Analysis Response (`POST /link-analysis`)
```bash
curl -X POST http://localhost:4000/link-analysis \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}'
```
Response:
```json
{
  "success": true,
  "website": "https://example.com/",
  "brand": {
    "name": "Acme Plumbing",
    "category": "Home services",
    "summary": "Plumbers in Austin offering emergency pipe, drain, and heater repairs."
  },
  "analysis": {
    "competitor_analysis": { ... },
    "target_search_queries": [ ... ],
    "seo_improvements": { ... },
    "top_services": [ ... ],
    "target_audience": { ... }
  },
  "meta": {
    "provider": "gemini",
    "model_used": "gemini-3.8-flash",
    "input_tokens": 4210,
    "output_tokens": 1845,
    "pages_analyzed": 2,
    "cached": false,
    "processing_ms": 1850,
    "warnings": [
      "Competitors are AI-generated suggestions from general knowledge and have not been verified against live sources."
    ]
  }
}
```

---

### 3. SSRF Guard Blocking Internal/Private Host (`http://localhost`)
```bash
curl -i -X POST http://localhost:4000/link-analysis \
  -H "Content-Type: application/json" \
  -d '{"url":"http://localhost"}'
```
Output:
```http
HTTP/1.1 400 Bad Request
content-type: application/json
x-request-id: 97d57a74

{
  "success": false,
  "error": {
    "code": "BLOCKED_URL",
    "message": "This URL points to a private or internal address and cannot be analyzed."
  }
}
```
