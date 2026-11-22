"""LLM telemetry (timing, tokens, failed replies) and repeated evaluation runs, without a real model."""

import sys
import types
import unittest

from eurogrid.agent import ToolChoice
from eurogrid.evaluation import CASES, run_suite
from eurogrid.llm import LLMError, OllamaLLM, summarize_calls


def fake_ollama(content):
    """A stand-in `ollama` module whose chat() returns Ollama's response fields."""
    message = types.SimpleNamespace(content=content)
    response = types.SimpleNamespace(message=message, total_duration=2_500_000_000, load_duration=100_000_000,
                                     prompt_eval_count=812, eval_count=37)
    return types.SimpleNamespace(chat=lambda **kwargs: response)


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.saved = sys.modules.get("ollama")

    def tearDown(self):
        if self.saved is not None:
            sys.modules["ollama"] = self.saved
        else:
            sys.modules.pop("ollama", None)

    def test_each_call_records_time_and_tokens_and_invalid_replies_count_as_failed(self):
        llm = OllamaLLM("test-model")
        sys.modules["ollama"] = fake_ollama('{"next_tool": "compare_demand", "reason": "load"}')
        self.assertEqual(llm.structured("s", "u", ToolChoice).next_tool, "compare_demand")
        sys.modules["ollama"] = fake_ollama('{"not": "the schema"}')
        with self.assertRaises(LLMError):
            llm.structured("s", "u", ToolChoice)
        first, second = llm.calls
        self.assertEqual((first["ok"], first["model_s"], first["prompt_tokens"], first["completion_tokens"]),
                         (True, 2.5, 812, 37))
        self.assertFalse(second["ok"])
        summary = summarize_calls(llm.calls, wall_s=6.0)
        self.assertEqual((summary["calls"], summary["failed_calls"], summary["prompt_tokens"],
                          summary["completion_tokens"], summary["wall_s"]), (2, 1, 1624, 74, 6.0))
        self.assertEqual(summarize_calls([], 1.0), {"measured": False, "wall_s": 1.0})

    def test_repeated_suite_reports_stability_and_latency(self):
        payload = run_suite("rule-based", cases=CASES[:2], repeats=2)
        self.assertEqual((payload["repeats"], payload["cases_passed_per_run"]), (2, [2, 2]))
        self.assertEqual([case["details"]["passed_runs"] for case in payload["cases"]], [2, 2])
        self.assertEqual(payload["performance"]["investigations"], 4)
        self.assertGreater(payload["performance"]["median_s"], 0)


if __name__ == "__main__":
    unittest.main()
