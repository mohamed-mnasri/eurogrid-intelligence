"""Phase 9 small LLM interface.

The agent only needs one thing from a model: "given these instructions, reply
with JSON that matches this Pydantic schema". Keeping that behind one class
means tests can use a fake model and Ollama can be swapped out later.
"""

from __future__ import annotations

import statistics
import time
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The model was unreachable or did not return valid JSON for the schema."""


class LLM(Protocol):
    name: str

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...


def ollama_status(model: str) -> tuple[bool, str]:
    """Check that the Ollama server is reachable and the model is downloaded (no generation)."""
    try:
        import ollama

        names = {item.model for item in ollama.list().models}
    except Exception as exc:  # server not running, library missing, connection refused
        return False, f"Ollama is not reachable ({exc}). Start Ollama and try again."
    if model not in names and f"{model}:latest" not in names:
        return False, f"Model {model} is not downloaded; run: ollama pull {model}"
    return True, "ok"


class OllamaLLM:
    """Local model served by Ollama (http://localhost:11434)."""

    def __init__(self, model: str = "qwen3.5:4b", temperature: float = 0.0) -> None:
        self.name = f"ollama:{model}"
        self.model = model
        self.temperature = temperature
        self.calls: list[dict[str, Any]] = []   # one entry per model call: timing and token counts

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        import ollama  # imported here so the rest of the project works without it

        started = time.perf_counter()
        call: dict[str, Any] = {"schema": schema.__name__, "ok": False}
        self.calls.append(call)
        try:
            response = ollama.chat(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                format=schema.model_json_schema(),   # Ollama forces the reply into this JSON shape
                options={"temperature": self.temperature},
                think=False,                         # skip long "thinking" text; we only want the JSON
            )
        except Exception as exc:
            call["wall_s"] = round(time.perf_counter() - started, 3)
            raise LLMError(f"Ollama call failed: {exc}") from exc
        call.update({
            "wall_s": round(time.perf_counter() - started, 3),
            "model_s": round((getattr(response, "total_duration", None) or 0) / 1e9, 3),
            "load_s": round((getattr(response, "load_duration", None) or 0) / 1e9, 3),
            "prompt_tokens": getattr(response, "prompt_eval_count", None),
            "completion_tokens": getattr(response, "eval_count", None),
        })
        try:
            result = schema.model_validate_json(response.message.content)
            call["ok"] = True
            return result
        except ValidationError as exc:
            raise LLMError(f"Model reply did not match {schema.__name__}: {exc}") from exc


def summarize_calls(calls: list[dict[str, Any]], wall_s: float | None = None) -> dict[str, Any]:
    """Totals for one investigation: number of model calls, time, tokens and failed replies."""
    if not calls:
        return {"measured": False, "wall_s": round(wall_s, 3) if wall_s is not None else None}
    def total(key: str) -> int | float:
        return sum(call.get(key) or 0 for call in calls)
    return {"measured": True, "calls": len(calls), "failed_calls": sum(1 for call in calls if not call.get("ok")),
            "wall_s": round(wall_s, 3) if wall_s is not None else round(total("wall_s"), 3),
            "model_s": round(total("model_s"), 3), "prompt_tokens": int(total("prompt_tokens")),
            "completion_tokens": int(total("completion_tokens")),
            "median_call_s": round(statistics.median(call.get("wall_s") or 0 for call in calls), 3),
            "per_call": calls}
