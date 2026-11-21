"""Phase 9 small LLM interface.

The agent only needs one thing from a model: "given these instructions, reply
with JSON that matches this Pydantic schema". Keeping that behind one class
means tests can use a fake model and Ollama can be swapped out later.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The model was unreachable or did not return valid JSON for the schema."""


class LLM(Protocol):
    name: str

    def structured(self, system: str, user: str, schema: type[T]) -> T: ...


class OllamaLLM:
    """Local model served by Ollama (http://localhost:11434)."""

    def __init__(self, model: str = "qwen3.5:4b", temperature: float = 0.0) -> None:
        self.name = f"ollama:{model}"
        self.model = model
        self.temperature = temperature

    def structured(self, system: str, user: str, schema: type[T]) -> T:
        import ollama  # imported here so the rest of the project works without it

        try:
            response = ollama.chat(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                format=schema.model_json_schema(),   # Ollama forces the reply into this JSON shape
                options={"temperature": self.temperature},
                think=False,                         # skip long "thinking" text; we only want the JSON
            )
        except Exception as exc:
            raise LLMError(f"Ollama call failed: {exc}") from exc
        try:
            return schema.model_validate_json(response.message.content)
        except ValidationError as exc:
            raise LLMError(f"Model reply did not match {schema.__name__}: {exc}") from exc
