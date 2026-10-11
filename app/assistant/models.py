"""The models you choose from in Settings › Sunny. This is the only place in the app with model IDs."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    id: str
    name: str          # "Claude Haiku 5.5"
    short: str         # "Haiku 5.5", small below the chat panel's heading
    description: str
    price: str         # per 1M tokens, input / output


MODELS = [
    Model("claude-haiku-5-5", "Claude Haiku 5.5", "Haiku 5.5",
          "Fastest and cheapest. Fine for logging and simple questions. (default)", "$0.10 / $0.50"),
    Model("claude-sonnet-5-5", "Claude Sonnet 5.5", "Sonnet 5.5",
          "Smarter, for trickier questions. About 20× Haiku's price.", "$2 / $10"),
    Model("claude-opus-5-5", "Claude Opus 5.5", "Opus 5.5",
          "Smartest and most expensive. Usually overkill for Sunny.", "$4 / $20"),
]
DEFAULT_MODEL = MODELS[0].id


def model_by_id(model_id: str) -> Model | None:
    return next((m for m in MODELS if m.id == model_id), None)
