# Runs a manual end-to-end smoke test of analyze_url
# via `python -m scripts.analyze_smoke <url>`.

# Accepts a target URL from CLI arguments
# and defaults to "https://example.com" when none is provided.

# Executes the full analysis pipeline asynchronously and
# prints the response as formatted JSON.

"""Manual end-to-end run. Usage: python -m scripts.analyze_smoke https://some-business-site.com"""

import asyncio
import sys

from app.services.analysis_service import analyze_url


async def main(url: str) -> None:
    resp = await analyze_url(url)
    print(resp.model_dump_json(indent=2))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://example.com"))
