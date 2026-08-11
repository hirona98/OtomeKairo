import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.llm.contracts import LLMError, validate_outbound_content_review_contract
from otomekairo.service.capability import (
    OutboundContentReviewFailureError,
    OutboundContentReviewWithheldError,
    ServiceCapabilityMixin,
)


class _Store:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["mcp_servers"]["e-stat"]["enabled"] = True

    def read_state(self) -> dict:
        return deepcopy(self.state)


class _Reviewer:
    def __init__(self, outcome: str = "allow") -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    def generate_outbound_content_review(self, *, model_config: dict, review_context: dict) -> dict:
        self.calls.append(deepcopy(review_context))
        return {
            "outcome": self.outcome,
            "reason_summary": "候補本文を含まない判定理由。",
        }


class _Service(ServiceCapabilityMixin):
    def __init__(self, reviewer: _Reviewer) -> None:
        self.store = _Store()
        self.llm = reviewer


def _tool() -> dict:
    return {
        "name": "create_post",
        "description": "投稿する",
        "inputSchema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
        },
    }


def _input(content: str) -> dict:
    return {
        "mcp_server_id": "e-stat",
        "tool_name": "create_post",
        "arguments": {"content": content},
    }


class OutboundContentReviewTests(unittest.TestCase):
    def test_contract_accepts_allow_and_withhold(self) -> None:
        for outcome in ("allow", "withhold"):
            validate_outbound_content_review_contract(
                {"outcome": outcome, "reason_summary": "判定理由"}
            )

    def test_contract_rejects_rewrite_and_extra_fields(self) -> None:
        with self.assertRaises(LLMError):
            validate_outbound_content_review_contract(
                {"outcome": "rewrite", "reason_summary": "判定理由"}
            )
        with self.assertRaises(LLMError):
            validate_outbound_content_review_contract(
                {"outcome": "allow", "reason_summary": "判定理由", "arguments": {}}
            )

    def test_allow_reviews_only_tool_metadata_and_final_arguments(self) -> None:
        reviewer = _Reviewer("allow")
        service = _Service(reviewer)

        audit = service._review_mcp_outbound_content(
            input_payload=_input("公開情報だけの投稿"),
            mcp_tool=_tool(),
            review_attempt=1,
        )

        self.assertEqual(audit["outcome"], "allow")
        self.assertEqual(audit["review_attempt"], 1)
        self.assertNotIn("arguments", audit)
        self.assertNotIn("reason_summary", audit)
        self.assertEqual(
            set(reviewer.calls[0]),
            {"channel", "tool", "arguments"},
        )
        self.assertEqual(reviewer.calls[0]["arguments"], {"content": "公開情報だけの投稿"})

    def test_llm_withhold_raises_without_persisting_reason_or_arguments(self) -> None:
        reviewer = _Reviewer("withhold")
        service = _Service(reviewer)

        with self.assertRaises(OutboundContentReviewWithheldError) as raised:
            service._review_mcp_outbound_content(
                input_payload=_input("審査対象の本文"),
                mcp_tool=_tool(),
                review_attempt=2,
            )

        audit = raised.exception.audit_summary
        self.assertEqual(audit["reason_code"], "reviewer_withheld")
        self.assertEqual(audit["review_attempt"], 2)
        self.assertNotIn("arguments", audit)
        self.assertNotIn("reason_summary", audit)

    def test_known_configured_secret_is_withheld_before_llm(self) -> None:
        reviewer = _Reviewer("allow")
        service = _Service(reviewer)
        service.store.state["mcp_servers"]["e-stat"]["env"]["E_STAT_APP_ID"] = "secret-value"

        with self.assertRaises(OutboundContentReviewWithheldError) as raised:
            service._review_mcp_outbound_content(
                input_payload=_input("prefix secret-value suffix"),
                mcp_tool=_tool(),
                review_attempt=1,
            )

        self.assertEqual(raised.exception.audit_summary["reason_code"], "known_secret_detected")
        self.assertEqual(reviewer.calls, [])

    def test_disabled_policy_bypasses_review(self) -> None:
        reviewer = _Reviewer("withhold")
        service = _Service(reviewer)
        service.store.state["mcp_servers"]["e-stat"]["outbound_content_review_required"] = False

        audit = service._review_mcp_outbound_content(
            input_payload=_input("任意の本文"),
            mcp_tool=_tool(),
            review_attempt=1,
        )

        self.assertIsNone(audit)
        self.assertEqual(reviewer.calls, [])

    def test_invalid_test_double_outcome_fails_closed(self) -> None:
        reviewer = _Reviewer("rewrite")
        service = _Service(reviewer)

        with self.assertRaises(OutboundContentReviewFailureError) as raised:
            service._review_mcp_outbound_content(
                input_payload=_input("本文"),
                mcp_tool=_tool(),
                review_attempt=1,
            )

        self.assertEqual(raised.exception.audit_summary["result_status"], "internal_failure")


if __name__ == "__main__":
    unittest.main()
