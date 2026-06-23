import unittest

from otomekairo.service.input.mixin import ServiceInputMixin


class CognitiveContextInspectionTests(unittest.TestCase):
    def test_cycle_cognitive_context_uses_stable_empty_objects(self) -> None:
        service = ServiceInputMixin.__new__(ServiceInputMixin)
        service.store = _Store(
            {
                "cycle_id": "cycle:test",
                "cycle_summary": {"cycle_id": "cycle:test"},
                "decision_trace": {
                    "foreground_selection": {
                        "primary_factor_ref": "current_input:user_message",
                        "supporting_factor_refs": [],
                        "suppressed_factors": [],
                        "summary_text": "入力を主役にした。",
                    },
                    "workspace_context_summary": {"candidate_count": 1},
                },
            }
        )
        service._require_token = lambda token: None

        payload = service.get_cycle_cognitive_context("token", "cycle:test")

        self.assertEqual(payload["cycle_id"], "cycle:test")
        self.assertEqual(payload["workspace_context_summary"]["candidate_count"], 1)
        self.assertEqual(payload["self_state_context"], {})
        self.assertEqual(payload["relationship_context"], {})
        self.assertEqual(payload["prediction_error_context"], {})
        self.assertEqual(payload["default_mode_context"], {})

    def test_cycle_cognitive_context_reads_internal_context_summary(self) -> None:
        service = ServiceInputMixin.__new__(ServiceInputMixin)
        service.store = _Store(
            {
                "cycle_id": "cycle:test",
                "cycle_summary": {"cycle_id": "cycle:test"},
                "decision_trace": {
                    "internal_context_summary": {
                        "self_state_context": {
                            "focus_stability": {"summary_text": "継続行動は安定している。"},
                        }
                    }
                },
            }
        )
        service._require_token = lambda token: None

        payload = service.get_cycle_cognitive_context("token", "cycle:test")

        self.assertEqual(
            payload["self_state_context"]["focus_stability"]["summary_text"],
            "継続行動は安定している。",
        )


class _Store:
    def __init__(self, trace: dict) -> None:
        self._trace = trace

    def get_cycle_trace(self, cycle_id: str) -> dict | None:
        return self._trace if cycle_id == self._trace["cycle_id"] else None


if __name__ == "__main__":
    unittest.main()
