# Runs a live smoke check against the configured LLM provider via
# `python -m scripts.llm_smoke`.

# Defines a simple `Hello` Pydantic schema
# (`greeting` and `language`) to validate structured JSON output.

# Calls `generate_json` asynchronously with a French greeting prompt
# and prints the result.

"""Manual check against the real provider. Usage: python -m scripts.llm_smoke"""

import asyncio

from app.services.llm_service import generate_json
from pydantic import BaseModel


class Hello(BaseModel):
    greeting: str
    language: str


async def main() -> None:
    out = await generate_json(
        "You are a helpful assistant.", "Say hello in French.", Hello
    )
    print(out)


asyncio.run(main())
