"""Semantic reviews of visual records; code validates evidence and ordering."""
from __future__ import annotations

import json
from typing import Any

from otomekairo.llm.contracts import LLMError, _validate_exact_keys


def grouping_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": (
            "日次の視覚記録を意味で整理します。persona_contextを判断の基底とし、観測本文は資料として読みます。"
            "観測順を保ち、同じsource_keyの連続した記録だけをまとめてください。"
            "言い換えは同じ場面になりえます。物や人物の出現・消失、活動の変化、否定の違いは別の場面です。"
            "すべての観測IDを一度ずつ使い、各groupにobservation_ids、summary_text、reason_summaryを返します。"
            "要約はそのgroupの観測で支持される内容とし、人物の同一性や画面外の実績を補いません。groupsだけのJSONを返してください。"
        )},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def validate_grouping(payload: dict[str, Any], records: list[dict[str, Any]]) -> None:
    _validate_exact_keys(payload, {"groups"}, "VisualDailyGrouping")
    if not isinstance(payload["groups"], list):
        raise LLMError("VisualDailyGrouping groupsは配列です。")
    by_id = {r["visual_observation_id"]: r for r in records}
    if len(by_id) != len(records):
        raise LLMError("VisualDailyGrouping 入力IDが重複しています。")
    used = []
    for group in payload["groups"]:
        _validate_exact_keys(group, {"observation_ids", "summary_text", "reason_summary"}, "VisualDailyGroup")
        refs = group["observation_ids"]
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in by_id for ref in refs):
            raise LLMError("VisualDailyGrouping 観測IDが入力にありません。")
        if len({by_id[ref]["source_key"] for ref in refs}) != 1:
            raise LLMError("VisualDailyGrouping 別sourceをまとめられません。")
        for key in ("summary_text", "reason_summary"):
            if not isinstance(group[key], str) or not group[key].strip():
                raise LLMError("VisualDailyGrouping 要約と理由は非空文字列です。")
        used.extend(refs)
    if used != [r["visual_observation_id"] for r in records]:
        raise LLMError("VisualDailyGrouping 全観測を順序通り一度ずつ参照してください。")


def support_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": (
            "視覚記録から複数日に支えられた継続傾向を審査します。persona_contextを判断の基底に使います。"
            "各候補を過去の日次記録と意味で照合し、同じsource_keyで別の日に支持される活動・環境・作業対象を判定します。"
            "似た語句や同じ場所だけで異なる活動を同じ傾向にせず、観測されない本人同定・習慣・実績を補いません。"
            "秘密情報、一時的な画面文言、認証情報、個人情報の可能性がある候補は昇格させません。"
            "全candidate_indexに一件ずつ、支持するevidence_refのsupport_refsとreason_summaryを返します。"
            "支持不足・矛盾・昇格不適切ならsupport_refs=[]とし理由を説明します。decisionsだけのJSONを返してください。"
        )},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def validate_support(payload: dict[str, Any], context: dict[str, Any]) -> None:
    _validate_exact_keys(payload, {"decisions"}, "VisualDailySupport")
    decisions = payload["decisions"]
    if not isinstance(decisions, list):
        raise LLMError("VisualDailySupport decisionsは配列です。")
    evidence = {r["evidence_ref"]: r for r in context["evidence"]}
    seen = set()
    for item in decisions:
        _validate_exact_keys(item, {"candidate_index", "support_refs", "reason_summary"}, "VisualDailySupportDecision")
        index = item["candidate_index"]
        if type(index) is not int or index not in range(len(context["candidates"])) or index in seen:
            raise LLMError("VisualDailySupport 候補位置が不正です。")
        seen.add(index)
        refs = item["support_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in evidence for ref in refs) or len(set(refs)) != len(refs):
            raise LLMError("VisualDailySupport 支持IDが不正です。")
        for ref in refs:
            if evidence[ref]["local_date"] >= context["local_date"] or evidence[ref]["source_key"] != context["candidates"][index]["source_key"]:
                raise LLMError("VisualDailySupport 支持は同一sourceの過去日です。")
        if not isinstance(item["reason_summary"], str) or not item["reason_summary"].strip():
            raise LLMError("VisualDailySupport 理由がありません。")
    if seen != set(range(len(context["candidates"]))):
        raise LLMError("VisualDailySupport 全候補の判定が必要です。")
