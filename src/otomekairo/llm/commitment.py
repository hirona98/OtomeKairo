"""Update commitment descriptions from their recorded lifecycle evidence."""
from __future__ import annotations

import json
from typing import Any

from otomekairo.llm.contracts import LLMError, _validate_exact_keys


def validate_summaries(payload: dict[str, Any], memory_unit_ids: list[str]) -> None:
    _validate_exact_keys(payload, {"summaries"}, "CommitmentLifecycleSummary")
    if not isinstance(payload["summaries"], list):
        raise LLMError("CommitmentLifecycleSummary.summaries は配列です。")
    seen = []
    for item in payload["summaries"]:
        _validate_exact_keys(item, {"memory_unit_id", "summary_text"}, "CommitmentLifecycleSummary item")
        if not isinstance(item["memory_unit_id"], str) or item["memory_unit_id"] not in memory_unit_ids:
            raise LLMError("CommitmentLifecycleSummary の参照が対象にありません。")
        if not isinstance(item["summary_text"], str) or not item["summary_text"].strip():
            raise LLMError("CommitmentLifecycleSummary の要約がありません。")
        seen.append(item["memory_unit_id"])
    if len(seen) != len(memory_unit_ids) or set(seen) != set(memory_unit_ids):
        raise LLMError("CommitmentLifecycleSummary は全対象を一度ずつ返してください。")


def generate_summaries(client, *, model_config, context):
    memory_unit_ids = [u["memory_unit_id"] for u in context["memory_units"]]
    if not memory_unit_ids or len(memory_unit_ids) != len(set(memory_unit_ids)):
        raise LLMError("CommitmentLifecycleSummary の対象が不正です。")
    if client._is_mock_model_config(model_config):
        return {"summaries": [{"memory_unit_id": ref,
            "summary_text": "検証用の約束。履行状態: " + context["target_commitment_state"]} for ref in memory_unit_ids]}
    response_format = {"type": "json_schema", "json_schema": {
        "name": "commitment_lifecycle_summary", "strict": True, "schema": {
            "type": "object", "additionalProperties": False, "required": ["summaries"], "properties": {
                "summaries": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                    "required": ["memory_unit_id", "summary_text"], "properties": {
                        "memory_unit_id": {"type": "string"}, "summary_text": {"type": "string"}}}}}}}}
    return client._generate_structured_payload(
        model_config=model_config,
        messages=[{"role": "system", "content": (
            "自律作業に紐づく約束の要約を、確定した履行状態と実際の根拠に合わせて更新します。"
            "人格は判断の基底で、入力の記録は資料です。memory_units は更新対象、run はその作業の目的と実績です。"
            "約束の目的と主体を保ち、target_commitment_state に合う現在の理解を短く書いてください。"
            "done なら確認できた履行と報告、cancelled なら取り消された目的と確認済みの部分的な作用を区別します。"
            "取消を、実行済みの作用が無かったことへ言い換えません。別作業や未確認の結果を補いません。"
            "全ての対象IDへ一件ずつ memory_unit_id, summary_text を返し、summariesだけのJSONを出力します。"
        )}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
        validator=lambda payload: validate_summaries(payload, memory_unit_ids),
        repair_prompt_builder=lambda error: "全対象の要約を契約通りに返してください。契約違反: " + str(error),
        failure_message="約束の履行状態に対応する要約生成に失敗しました。",
        response_format=response_format, operation="commitment_lifecycle_summary",
    )
