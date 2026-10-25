"""Semantic reviews of visual records; code validates evidence and ordering."""
from __future__ import annotations

import json
from typing import Any

from otomekairo.llm.contracts import LLMError, _validate_exact_keys


SUPPORT_STATUS_VALUES = {"supported", "insufficient", "unsuitable"}
SUPPORT_RELATION_VALUES = {"same", "refine", "parallel", "conflict", "new", "uncertain"}


def support_reference_constraints(context: dict[str, Any]) -> dict[str, Any]:
    """Expose mechanically eligible references, leaving meaning to the LLM."""
    return {**context, "candidates": [{
        **candidate,
        "allowed_past_refs": [item["evidence_ref"] for item in context["evidence"]
                              if item["source_key"] == candidate["source_key"] and item["local_date"] < context["local_date"]],
        "allowed_memory_refs": [item["memory_ref"] for item in context["existing_memories"]
                                if item["source_key"] == candidate["source_key"]],
    } for candidate in context["candidates"]]}


def grouping_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": (
            "日次の視覚記録を意味で整理します。persona_contextを判断の基底とし、観測本文は資料として読みます。"
            "source_sequencesにはsource_keyごとの時系列が入ります。各系列内の観測順を保ち、連続した同じ場面をまとめてください。"
            "観測本文とobserved_atを読み、そのsourceで場面が継続しているかを判断します。別sourceの観測が間に入っても、その系列の連続性は保たれます。"
            "言い換えは同じ場面になりえます。物や人物の出現・消失、活動の変化、否定の違いは別の場面です。各groupは一つのsource_keyに対応します。"
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
    expected_by_source: dict[str, list[str]] = {}
    for record in records:
        expected_by_source.setdefault(record["source_key"], []).append(record["visual_observation_id"])
    used_by_source: dict[str, list[str]] = {}
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
        source_key = by_id[refs[0]]["source_key"]
        used_by_source.setdefault(source_key, []).extend(refs)
    if used_by_source != expected_by_source:
        raise LLMError("VisualDailyGrouping 各sourceの全観測を、そのsource内の順序通り一度ずつ参照してください。")


def support_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": (
            "視覚日次記録から、複数日の観測に支えられる理解を抽出し、既存記憶との関係を審査します。"
            "persona_contextを判断の基底とし、観測事実の根拠にはcandidatesとevidenceを使います。"
            "existing_memoriesは更新先を比較する正本であり、反復観測の代わりにはしません。\n"
            "各候補のallowed_past_refsとallowed_memory_refsは、この候補が選べる参照です。"
            "allowed_past_refsが空なら反復支持がなく、insufficientとします。\n"
            "まず各groupが単独で支持する内容をsource_claimsに示し、同じsource_keyの当日と過去日で意味が共通する範囲を抽出します。"
            "共通主張は、選んだ各日の観測だけを読んでも全体が成り立つものです。"
            "対象の有無、活動、状態、属性、否定、条件を揃え、全ての支持観測で確認できる具体性に合わせてください。"
            "同じ場所や画面という背景だけで異なる内容を同じ活動にはせず、別々の日の出来事の並記や組み合わせを反復支持と区別します。"
            "shared_summary_textは過去の観測の記述だと本文でも分かる形で、観測時点と条件の範囲に限定します。"
            "反復から推測する頻度や、未観測期間の継続、主体の同定、内面は事実に加えません。\n"
            "当日と少なくとも一つの過去日が主張全体を支持すればsupport_status=supportedです。"
            "既存正本がなくても反復支持があれば昇格できます。今日だけの内容や支持を確定できない候補はinsufficientとします。"
            "秘密情報、個人情報、一時的な画面文言など長期理解に適さない内容はunsuitableとします。\n"
            "支持を確定してから、共通主張と既存正本全文を次の意味関係で比較します。\n"
            "比較する正本がある場合は、関係名を選ぶ前にmemory_comparisonを作ります。"
            "existing_claimsには正本に含まれる主張を網羅し、共通主張だけが正しいと分かったとき、その旧主張の全内容も必ず正しいと言えるかをentailed_by_sharedで答えます。"
            "矛盾しないことと、正しいと確認できることは別です。共通主張からは正しさが分からない部分をunconfirmed_existing_partsに全て挙げます。"
            "entailed_by_shared=trueのときだけunconfirmed_existing_parts=[]です。"
            "抽象的な判断例として、既存が『PとQ』、共通が『P』なら、Qは未確認であり、entailed_by_shared=false、unconfirmed_existing_parts=['Q']、relation=parallelです。"
            "逆に既存が『P』、共通が『PとQ』なら、旧主張Pは全て確認できる一方、共通のQは旧主張から確認できないためrefineです。"
            "unconfirmed_existing_partsには旧主張の中にある未確認部分だけを挙げ、共通主張にだけある追加情報を入れません。"
            "次に逆方向をshared_claimsで確認します。共通主張を網羅し、旧記憶だけが正しいと分かったとき、その全内容も必ず正しいと言えるかをentailed_by_existingで答えます。"
            "旧記憶からは確認できない共通主張の部分だけをunconfirmed_shared_partsへ挙げ、trueの場合だけ空配列にします。"
            "existing_conditionsとshared_conditionsにはそれぞれの主張の適用時点・対象・条件を示します。"
            "same_conditions_confirmedは、新しい観測が旧主張の適用条件を実際に満たすことを根拠から確認できる場合だけtrueです。"
            "同じ場所や対象であること、条件が重なる可能性だけでは、その確認にはなりません。"
            "主張に書かれた適用時点を使い、記憶の形成日時や資料の時刻へ置き換えないでください。"
            "根拠となる観測日が増えること自体は、共通主張が加える新しい内容には数えません。"
            "incompatibleは同じ条件なら主張が両立しないかを示します。\n"
            "same: 対象・内容・条件を保つ同義の主張。既存全文が再確認できます。\n"
            "refine: 共通主張自体が既存全文の意味と条件を保ち、各日の根拠が支える詳細や精度を加えます。\n"
            "parallel: 独立した主張、異なる時点や条件の観測、または既存全文の一部だけに対応する主張。旧記憶をそのまま保持します。\n"
            "conflict: 同じ対象・時点・条件について両立しない主張。条件や時点が違うだけならparallelです。\n"
            "new: 対応する既存正本がない主張。uncertain: 既存正本との関係を確定できない主張。\n"
            "本文を更新する場合に保存されるのはshared_summary_textだけです。既存全文の意味がその本文に収まらなければparallelにします。\n"
            "関係は比較結果と揃えます。既存・共通の主張が互いに全て確認できればsame、既存全文は確認できるが逆方向に未確認部分があればrefineです。"
            "一つでも確認できない旧主張がある場合、same_conditions_confirmed=trueかつincompatible=trueのときだけconflict、それ以外はparallelです。\n"
            "全candidate_indexをcandidate_indexesで一度ずつ扱い、同義の当日候補と同じ正本への更新は一つのdecisionへ集約します。"
            "supportedではpast_support_refsに過去のevidence_refだけを選び、source_claimsにはその全参照と全当日group_refを一度ずつ含めます。"
            "same/refine/parallel/conflictではtarget_memory_refに比較したmemory_refを必ず指定し、new/uncertainではnullにします。"
            "new/uncertainではmemory_comparisonもnullです。"
            "insufficient/unsuitableではrelation=uncertain、shared_summary_text・target_memory_ref・memory_comparisonはnull、past_support_refs=[]です。"
            "reason_summaryは支持範囲と関係判定に整合する理由を述べ、decisionsだけのJSONを返してください。"
        )},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def validate_support(payload: dict[str, Any], context: dict[str, Any]) -> None:
    _validate_exact_keys(payload, {"decisions"}, "VisualDailySupport")
    decisions = payload["decisions"]
    if not isinstance(decisions, list):
        raise LLMError("VisualDailySupport decisionsは配列です。")
    evidence = {r["evidence_ref"]: r for r in context["evidence"]}
    memories = {r["memory_ref"]: r for r in context["existing_memories"]}
    if len(evidence) != len(context["evidence"]) or len(memories) != len(context["existing_memories"]):
        raise LLMError("VisualDailySupport 入力参照が重複しています。")
    seen: set[int] = set()
    updated_targets: set[str] = set()
    for item in decisions:
        _validate_exact_keys(item, {"source_claims", "candidate_indexes", "support_status", "shared_summary_text", "past_support_refs",
                                  "relation", "target_memory_ref", "memory_comparison", "reason_summary"}, "VisualDailySupportDecision")
        indexes = item["candidate_indexes"]
        if (not isinstance(indexes, list) or not indexes
                or any(type(index) is not int or index not in range(len(context["candidates"])) for index in indexes)
                or len(set(indexes)) != len(indexes) or seen.intersection(indexes)):
            raise LLMError("VisualDailySupport 候補位置が不正です。")
        seen.update(indexes)
        sources = {context["candidates"][index]["source_key"] for index in indexes}
        if len(sources) != 1:
            raise LLMError("VisualDailySupport 別sourceの候補をまとめられません。")
        refs = item["past_support_refs"]
        if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in evidence for ref in refs) or len(set(refs)) != len(refs):
            raise LLMError(f"VisualDailySupport past_support_refsには過去のevidence_refだけを重複なく指定してください。許可参照: {list(evidence)}。当日のcurrent参照はsource_claimsに記録します。")
        for ref in refs:
            if evidence[ref]["local_date"] >= context["local_date"] or evidence[ref]["source_key"] not in sources:
                allowed = [key for key, record in evidence.items() if record["source_key"] in sources and record["local_date"] < context["local_date"]]
                raise LLMError(f"VisualDailySupport 支持は同一sourceの過去日です。許可されたpast_support_refs: {allowed}。空ならinsufficientです。")
        status, relation = item["support_status"], item["relation"]
        if not isinstance(status, str) or status not in SUPPORT_STATUS_VALUES or not isinstance(relation, str) or relation not in SUPPORT_RELATION_VALUES:
            raise LLMError("VisualDailySupport 支持・関係の判定が不正です。")
        target = item["target_memory_ref"]
        if target is not None and (not isinstance(target, str) or target not in memories):
            raise LLMError("VisualDailySupport 対象記憶が入力にありません。")
        if status == "supported":
            if not refs or not isinstance(item["shared_summary_text"], str) or not item["shared_summary_text"].strip():
                raise LLMError("VisualDailySupport supportedには共通主張と過去根拠が必要です。過去の支持がなければinsufficient、本文=null、past_support_refs=[]、relation=uncertain、target_memory_ref=nullにしてください。")
            if relation in {"same", "refine", "parallel", "conflict"}:
                if target is None or memories[target]["source_key"] not in sources:
                    allowed = [ref for ref, memory in memories.items() if memory["source_key"] in sources]
                    raise LLMError(f"VisualDailySupport {relation}には同一sourceのtarget_memory_refが必要です。許可された記憶参照: {allowed}。")
            elif target is not None:
                raise LLMError("VisualDailySupport new/uncertainは対象正本を指定しません。")
        elif refs or item["shared_summary_text"] is not None or target is not None or relation != "uncertain":
            raise LLMError("VisualDailySupport 見送りには本文・支持・対象を指定できません。")
        _validate_memory_comparison(item)
        if relation in {"same", "refine"}:
            if target in updated_targets:
                raise LLMError("VisualDailySupport 同じ正本への更新は一つにまとめてください。")
            updated_targets.add(target)
        claims = item["source_claims"]
        if not isinstance(claims, list):
            raise LLMError("VisualDailySupport source_claimsは配列です。")
        claim_refs = []
        for claim in claims:
            _validate_exact_keys(claim, {"group_ref", "claim_text"}, "VisualDailySupportingClaim")
            if not isinstance(claim["group_ref"], str) or not isinstance(claim["claim_text"], str) or not claim["claim_text"].strip():
                raise LLMError("VisualDailySupport 根拠ごとの主張が不正です。")
            claim_refs.append(claim["group_ref"])
        current_refs = {f"current:{index}" for index in indexes}
        allowed_claim_refs = current_refs | {ref for ref, record in evidence.items()
                                            if record["source_key"] in sources and record["local_date"] < context["local_date"]}
        expected_refs = current_refs | set(refs)
        if (len(set(claim_refs)) != len(claim_refs) or not set(claim_refs).issubset(allowed_claim_refs)
                or (status == "supported" and set(claim_refs) != expected_refs)):
            required = sorted(expected_refs) if status == "supported" else sorted(allowed_claim_refs)
            raise LLMError(f"VisualDailySupport source_claims.group_refを支持参照に対応させてください。"
                           f"{'全件を一度ずつ指定' if status == 'supported' else 'この中から選択'}: {required}")
        if not isinstance(item["reason_summary"], str) or not item["reason_summary"].strip():
            raise LLMError("VisualDailySupport 理由がありません。")
    if seen != set(range(len(context["candidates"]))):
        raise LLMError("VisualDailySupport 全候補の判定が必要です。")


def _validate_memory_comparison(decision: dict[str, Any]) -> None:
    comparison = decision["memory_comparison"]
    if decision["relation"] in {"new", "uncertain"}:
        if comparison is not None:
            raise LLMError("VisualDailySupport new/uncertainのmemory_comparisonはnullです。")
        return
    _validate_exact_keys(comparison, {"existing_claims", "shared_claims", "existing_conditions",
                                     "shared_conditions", "same_conditions_confirmed", "incompatible"}, "VisualDailyMemoryComparison")
    preserved = _validate_entailment_claims(comparison["existing_claims"], "entailed_by_shared", "unconfirmed_existing_parts")
    reverse_preserved = _validate_entailment_claims(comparison["shared_claims"], "entailed_by_existing", "unconfirmed_shared_parts")
    for key in ("existing_conditions", "shared_conditions"):
        if not isinstance(comparison[key], str) or not comparison[key].strip():
            raise LLMError("VisualDailySupport 比較する対象・時点・条件が必要です。")
    if any(type(comparison[key]) is not bool for key in ("same_conditions_confirmed", "incompatible")):
        raise LLMError("VisualDailySupport 条件の確認と両立不能はbooleanです。")
    if comparison["incompatible"] and (preserved or reverse_preserved):
        raise LLMError("VisualDailySupport 全文の確認と両立不能を同時に指定できません。")
    if preserved:
        expected = "same" if reverse_preserved else "refine"
    else:
        expected = "conflict" if comparison["same_conditions_confirmed"] and comparison["incompatible"] else "parallel"
    if decision["relation"] != expected:
        raise LLMError(f"VisualDailySupport 両方向の確認可否と条件判断に対応するrelationは{expected}です。比較判断とrelationを整合させてください。")


def _validate_entailment_claims(claims: Any, entailed_key: str, unconfirmed_key: str) -> bool:
    if not isinstance(claims, list) or not claims:
        raise LLMError("VisualDailySupport 比較する本文の主張を網羅して列挙してください。")
    for claim in claims:
        _validate_exact_keys(claim, {"claim_text", entailed_key, unconfirmed_key}, "VisualDailyComparedClaim")
        if not isinstance(claim["claim_text"], str) or not claim["claim_text"].strip() or type(claim[entailed_key]) is not bool:
            raise LLMError("VisualDailySupport 主張と確認可否が不正です。")
        unconfirmed = claim[unconfirmed_key]
        if (not isinstance(unconfirmed, list) or any(not isinstance(part, str) or not part.strip() for part in unconfirmed)
                or claim[entailed_key] != (not unconfirmed)):
            raise LLMError(f"VisualDailySupport 確認できない部分を{unconfirmed_key}へ列挙し、{entailed_key}と整合させてください。")
    return all(claim[entailed_key] for claim in claims)
