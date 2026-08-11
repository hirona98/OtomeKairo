import unittest

from otomekairo.llm.contracts import (
    LLMError,
    validate_decision_contract,
    validate_operational_skill_selection_contract,
)


class DecisionContractTests(unittest.TestCase):
    def test_decision_contract_requires_foreground_selection(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "ユーザー発話へ返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
        }

        with self.assertRaises(LLMError):
            validate_decision_contract(payload)

    def test_decision_contract_accepts_foreground_selection(self) -> None:
        payload = {
            "kind": "speech",
            "reason_code": "reply",
            "reason_summary": "ユーザー発話へ返す。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": None,
            "foreground_selection": {
                "primary_factor_ref": "current_input:user_message",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "ユーザー発話を主因にした。",
            },
        }

        validate_decision_contract(payload)

    def test_operational_skill_selection_accepts_exact_selection(self) -> None:
        validate_operational_skill_selection_contract(
            {
                "selection": {
                    "mcp_server_id": "elyth",
                    "bundle_id": "elyth-remote-mcp-skills@0.1.0",
                    "skill_id": "elyth-post",
                    "reason_summary": "投稿依頼に対応する手順を使う。",
                }
            }
        )

    def test_operational_skill_selection_rejects_extra_fields(self) -> None:
        with self.assertRaises(LLMError):
            validate_operational_skill_selection_contract(
                {"selection": None, "fallback_skill": "elyth"}
            )


if __name__ == "__main__":
    unittest.main()
