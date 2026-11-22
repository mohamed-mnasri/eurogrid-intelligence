"""Phase 9 step 1: check that the local model can call a tool and return structured JSON."""

import json

import ollama
from pydantic import BaseModel

MODEL = "qwen3.5:4b"


# --- Test A: native tool calling -------------------------------------------
def get_price(zone: str) -> str:
    """Return the latest day-ahead price for a bidding zone.

    Args:
        zone: Bidding zone code, for example DE-LU
    """
    return json.dumps({"zone": zone, "price_eur_per_mwh": 87.5})   # fake value for the test


messages = [{"role": "user", "content": "What is the electricity price in DE-LU? Use the tool."}]
response = ollama.chat(model=MODEL, messages=messages, tools=[get_price], think=False,
                       options={"temperature": 0})
calls = response.message.tool_calls or []
print("A) Model asked for:", [(c.function.name, c.function.arguments) for c in calls])

if calls:
    messages.append(response.message)
    for call in calls:
        messages.append({"role": "tool", "tool_name": call.function.name,
                         "content": get_price(**call.function.arguments)})
    final = ollama.chat(model=MODEL, messages=messages, think=False, options={"temperature": 0})
    print("   Final answer:", final.message.content)


# --- Test B: structured output (the method the agent uses) -------------------
class ToolChoice(BaseModel):
    next_tool: str
    reason: str


reply = ollama.chat(
    model=MODEL,
    messages=[{"role": "user", "content":
               "A German electricity price spiked. Pick ONE tool from: get_generation_mix, compare_demand. "
               "Reply as JSON."}],
    format=ToolChoice.model_json_schema(),   # forces the reply into this shape
    think=False,
    options={"temperature": 0},
)
choice = ToolChoice.model_validate_json(reply.message.content)
print("B) Structured choice:", choice)
