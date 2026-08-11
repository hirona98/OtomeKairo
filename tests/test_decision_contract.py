import unittest

from otomekairo.llm.contracts import (
    LLMError,
    validate_decision_contract,
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

    def test_decision_contract_accepts_finite_mcp_session(self) -> None:
        payload = {
            "kind": "autonomous_run",
            "reason_code": "mcp_session:start",
            "reason_summary": "対象 MCP を有限回操作する。",
            "requires_confirmation": False,
            "pending_intent": None,
            "capability_request": None,
            "autonomous_run": {
                "objective_summary": "対象 MCP 内の確認を完了する。",
                "initial_step_summary": "最初の tool を判断する。",
                "mcp_server_id": "elyth",
                "coordination": {
                    "mode": "create_new",
                    "target_run_ids": [],
                    "reason_summary": "新しい有限セッションを始める。",
                },
            },
            "foreground_selection": {
                "primary_factor_ref": "capability:mcp.call_tool",
                "supporting_factor_refs": [],
                "suppressed_factors": [],
                "summary_text": "MCP 操作を主因にした。",
            },
        }

        validate_decision_contract(payload)

        payload["autonomous_run"]["mcp_server_id"] = ""
        with self.assertRaises(LLMError):
            validate_decision_contract(payload)


if __name__ == "__main__":
    unittest.main()
