"""Phase 9 typed tools for the agent, with the guardrails.

The agent never calls a database or writes SQL. It may only ask for one of the
approved tools below, by name. Code (not the model) fills in the zone and the
time window from the anomaly, then checks the request against a policy:

* the tool must be on the allowlist,
* the zone must be an allowed geography,
* the window must stay inside the anomaly window,
* the total number of tool calls is capped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from eurogrid.investigation_tools import InvestigationTools, ToolResult

# The tools the model may choose from. Data-quality is run by code first, so it is not offered.
AgentToolName = Literal[
    "compare_price_baseline",
    "get_generation_mix",
    "compare_demand",
    "get_cross_border_flows",
    "compare_neighbour_prices",
    "get_weather_context",
    "get_reported_outages",
]

TOOL_DESCRIPTIONS: dict[str, str] = {
    "compare_price_baseline": "How far prices were from their usual level for the same hours.",
    "get_generation_mix": "Wind, solar and other generation compared with the previous 14 days.",
    "compare_demand": "Electricity demand (load) compared with the previous 14 days.",
    "get_cross_border_flows": "Net cross-border trading compared with the previous 14 days.",
    "compare_neighbour_prices": "Whether connected countries' prices moved the same way.",
    "get_weather_context": "Wind speed, sunlight and temperature at the country's reference city.",
    "get_reported_outages": "Power-plant outage records (currently not available).",
}


class ToolCallRequest(BaseModel):
    """One validated tool call. Built by code from the model's choice plus the anomaly."""

    tool_name: AgentToolName
    zone: str = Field(min_length=2, max_length=16)
    start_utc: datetime
    end_utc: datetime


class ToolPolicy(BaseModel):
    """Limits for one investigation."""

    allowed_tools: list[str] = list(TOOL_DESCRIPTIONS)
    allowed_zones: list[str]
    window_start: datetime
    window_end: datetime
    max_tool_calls: int = Field(default=6, ge=1, le=10)


class PolicyViolation(Exception):
    """Raised when a tool request breaks the policy; the call is never executed."""


def execute_tool(tools: InvestigationTools, request: ToolCallRequest, policy: ToolPolicy,
                 calls_so_far: int) -> ToolResult:
    """Check the request against the policy, then run the approved tool."""
    if calls_so_far >= policy.max_tool_calls:
        raise PolicyViolation(f"tool-call limit of {policy.max_tool_calls} reached")
    if request.tool_name not in policy.allowed_tools:
        raise PolicyViolation(f"tool {request.tool_name} is not allowed")
    if request.zone not in policy.allowed_zones:
        raise PolicyViolation(f"zone {request.zone} is not allowed")
    if request.start_utc < policy.window_start or request.end_utc > policy.window_end:
        raise PolicyViolation("time window is outside the anomaly window")
    tool = getattr(tools, request.tool_name)  # safe: tool_name is one of the Literal values above
    return tool(request.zone, request.start_utc, request.end_utc)
