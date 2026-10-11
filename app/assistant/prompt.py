"""Sunny's systeemprompt: IDENTITY.md, SOUL.md en USER.md (in die volgorde) als één vast blok,
en de datum van vandaag als apart blok erna.

Het vaste blok draagt cache_control: het verandert niet tussen berichten (en niet tussen dagen),
dus het wordt één keer verwerkt en daarna uit de cache gelezen. De datum staat bewust erna,
anders zou elke nieuwe dag de cache breken.
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
