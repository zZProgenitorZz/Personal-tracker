"""Sunny's system prompt: IDENTITY.md, SOUL.md and USER.md (in that order) as one fixed block,
and today's date as a separate block after it.

The fixed block carries cache_control: it doesn't change between messages (or between days),
so it is processed once and then read from the cache. The date comes after it on purpose,
otherwise every new day would break the cache.
"""
import datetime as dt
from functools import lru_cache
from pathlib import Path

IDENTITY_DIR = Path(__file__).parent / "identity"
PROMPT_FILES = ("IDENTITY.md", "SOUL.md", "USER.md")


@lru_cache(maxsize=1)
def fixed_prompt() -> str:
    return "\n\n".join((IDENTITY_DIR / name).read_text(encoding="utf-8").strip() for name in PROMPT_FILES)


def system_blocks(today: dt.date) -> list[dict]:
    return [
        {"type": "text", "text": fixed_prompt(), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Today is {today:%A} {today.day} {today:%B %Y} (Progen's local date). "
                                 "Dates in tools are YYYY-MM-DD."},
    ]
