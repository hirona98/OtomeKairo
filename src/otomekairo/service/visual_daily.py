from __future__ import annotations

import hashlib
import threading
from typing import Any

from otomekairo.memory.utils import local_now
from otomekairo.llm.contexts import build_persona_context
from otomekairo.llm.visual_daily import validate_support
from otomekairo.service.common import debug_log, format_debug_log_text


VISUAL_DAILY_CHECK_INTERVAL_SECONDS = 3600.0
VISUAL_DAILY_RUN_DATE_LIMIT = 7
VISUAL_DAILY_PROMOTION_LOOKBACK_LIMIT = 14
VISUAL_DAILY_PROMOTION_LIMIT_PER_DIGEST = 8


class ServiceVisualDailyMixin:
    def start_background_visual_daily_worker(self) -> None:
        # 既存
        with self._runtime_state_lock:
            if (
                self._background_visual_daily_thread is not None
                and self._background_visual_daily_thread.is_alive()
            ):
                return

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._background_visual_daily_loop,
                args=(stop_event,),
                name="otomekairo-background-visual-daily",
                daemon=True,
            )
            self._background_visual_daily_stop_event = stop_event
            self._background_visual_daily_thread = thread
            self._visual_daily_runtime_state["current_digest_id"] = None

        # 開始
        thread.start()

    def stop_background_visual_daily_worker(self) -> None:
        # スナップショット
        with self._runtime_state_lock:
            stop_event = self._background_visual_daily_stop_event
            thread = self._background_visual_daily_thread
            self._background_visual_daily_stop_event = None
            self._background_visual_daily_thread = None
            self._visual_daily_runtime_state["current_digest_id"] = None

        # 停止
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        debug_log("VisualDaily", "stopped")

    def _background_visual_daily_loop(self, stop_event: threading.Event) -> None:
        # ループ
        while not stop_event.is_set():
            try:
                self._run_due_visual_daily_digests()
            except Exception as exc:  # noqa: BLE001
                debug_log("VisualDaily", f"loop error={type(exc).__name__}: {format_debug_log_text(str(exc), level='ERROR')}", level="ERROR")
            stop_event.wait(VISUAL_DAILY_CHECK_INTERVAL_SECONDS)

    def _run_due_visual_daily_digests(self) -> None:
        # 現在日はまだ途中なので、前日以前だけ整理する。
        state = self.store.read_state()
        today = local_now().date().isoformat()
        for memory_set_id in state["memory_sets"].keys():
            local_dates = self.store.list_visual_observation_local_dates(
                memory_set_id=memory_set_id,
                before_local_date=today,
                limit=VISUAL_DAILY_RUN_DATE_LIMIT,
            )
            for local_date in local_dates:
                if self.store.get_daily_visual_digest(memory_set_id=memory_set_id, local_date=local_date) is not None:
                    continue
                self._run_visual_daily_digest(memory_set_id=memory_set_id, local_date=local_date)
            self._run_due_visual_daily_promotions(memory_set_id=memory_set_id)

    def _run_visual_daily_digest(self, *, memory_set_id: str, local_date: str) -> dict[str, Any] | None:
        # 対象記録
        records = self.store.list_visual_observation_records_for_date(
            memory_set_id=memory_set_id,
            local_date=local_date,
        )
        if not records:
            return None

        started_at = self._now_iso()
        digest_id = self._visual_daily_digest_id(memory_set_id=memory_set_id, local_date=local_date)
        with self._runtime_state_lock:
            self._visual_daily_runtime_state["current_digest_id"] = digest_id

        digest = None
        try:
            groups = self._visual_daily_groups(
                memory_set_id=memory_set_id,
                local_date=local_date,
                records=records,
            )
            updated_records = self._visual_daily_updated_records(records=records, groups=groups)
            finished_at = self._now_iso()
            digest = self._build_visual_daily_digest(
                digest_id=digest_id,
                memory_set_id=memory_set_id,
                local_date=local_date,
                started_at=started_at,
                finished_at=finished_at,
                records=updated_records,
                groups=groups,
            )
            self.store.upsert_daily_visual_digest(digest=digest, updated_records=updated_records)
            self._promote_visual_daily_digest_memory_candidates(
                digest=digest,
                state=self._visual_daily_state_for_memory_set(
                    state=self.store.read_state(),
                    memory_set_id=memory_set_id,
                ),
            )
            debug_log(
                "VisualDaily",
                (
                    f"digest done memory_set={self._short_identifier(memory_set_id)} "
                    f"date={local_date} records={len(records)} groups={len(groups)}"
                ),
            )
            return digest
        except Exception:
            if digest is None:
                digest = self._build_visual_daily_digest(
                    digest_id=digest_id, memory_set_id=memory_set_id, local_date=local_date,
                    started_at=started_at, finished_at=self._now_iso(), records=records, groups=[])
                digest["result_status"] = "failed"
                digest["failure_reason"] = "visual_daily_grouping_failed"
                self.store.upsert_daily_visual_digest(digest=digest, updated_records=[])
            elif digest["memory_promotion"]["result_status"] == "not_started":
                self._store_visual_daily_promotion_result(
                    digest=digest, result_status="failed", promoted_memory_unit_ids=[],
                    skipped_candidate_count=0, failure_reason="visual_daily_promotion_failed", relation_index_sync=None)
            raise
        finally:
            with self._runtime_state_lock:
                self._visual_daily_runtime_state["current_digest_id"] = None

    def _visual_daily_groups(
        self,
        *,
        memory_set_id: str,
        local_date: str,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        state = self.store.read_state()
        persona = build_persona_context(state["personas"][state["selected_persona_id"]], role="visual_daily_grouping")
        inputs = [{"visual_observation_id": r["visual_observation_id"],
                   "source_key": self._visual_daily_source_key(r), "observed_at": r["observed_at"],
                   "detailed_summary_text": r["detailed_summary_text"]} for r in records]
        review = self.llm.generate_visual_daily_grouping(
            model_config=state["model_presets"][state["selected_model_preset_id"]],
            persona_context=persona, records=inputs)
        by_id = {r["visual_observation_id"]: r for r in records}
        record_order = {ref: index for index, ref in enumerate(by_id)}
        ordered_groups = sorted(
            review["groups"], key=lambda item: record_order[item["observation_ids"][0]])
        groups: list[dict[str, Any]] = []
        for item in ordered_groups:
            group_records = [by_id[ref] for ref in item["observation_ids"]]
            groups.append({"duplicate_group_id": self._visual_daily_group_id(
                memory_set_id=memory_set_id, local_date=local_date, index=len(groups) + 1),
                "source_key": self._visual_daily_source_key(group_records[0]),
                "records": group_records, "summary_text": item["summary_text"],
                "reason_summary": item["reason_summary"]})
        return groups

    def _visual_daily_updated_records(
        self,
        *,
        records: list[dict[str, Any]],
        groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        # 詳細説明は残し、低変化 group の中間記録だけ検索優先度を下げる。
        by_id: dict[str, dict[str, Any]] = {record["visual_observation_id"]: dict(record) for record in records}
        for group in groups:
            group_records = group["records"]
            compress_middle = len(group_records) >= 3
            for index, record in enumerate(group_records):
                visual_observation_id = record["visual_observation_id"]
                updated = by_id[visual_observation_id]
                status = str(updated.get("retention_status", "active"))
                if compress_middle and 0 < index < len(group_records) - 1 and self._visual_daily_compressible(updated):
                    status = "compressed"
                updated["retention_status"] = status
                updated["duplicate_group_id"] = group["duplicate_group_id"]
                updated["daily_digest_id"] = self._visual_daily_digest_id(
                    memory_set_id=updated["memory_set_id"],
                    local_date=updated["observed_at"][:10],
                )
        return list(by_id.values())

    def _build_visual_daily_digest(
        self,
        *,
        digest_id: str,
        memory_set_id: str,
        local_date: str,
        started_at: str,
        finished_at: str,
        records: list[dict[str, Any]],
        groups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # 集計
        retained_count = sum(1 for record in records if record.get("retention_status") == "active")
        compressed_count = sum(1 for record in records if record.get("retention_status") == "compressed")
        group_summaries = [self._visual_daily_group_summary(group) for group in groups]
        candidate_summaries = self._visual_daily_memory_candidate_summaries(group_summaries)

        # 結果
        return {
            "digest_id": digest_id,
            "memory_set_id": memory_set_id,
            "local_date": local_date,
            "started_at": started_at,
            "finished_at": finished_at,
            "result_status": "succeeded",
            "record_count": len(records),
            "group_count": len(groups),
            "retained_count": retained_count,
            "compressed_count": compressed_count,
            "group_summaries": group_summaries,
            "memory_candidate_summaries": candidate_summaries,
            "memory_promotion": {
                "result_status": "not_started",
                "promoted_memory_unit_ids": [],
                "skipped_candidate_count": 0,
                "failure_reason": None,
                "relation_index_sync": None,
            },
        }

    def _visual_daily_group_summary(self, group: dict[str, Any]) -> dict[str, Any]:
        # group要約
        group_records = group["records"]
        first_record = group_records[0]
        last_record = group_records[-1]
        return {
            "duplicate_group_id": group["duplicate_group_id"],
            "source_key": group["source_key"],
            "record_count": len(group_records),
            "first_observed_at": first_record["observed_at"],
            "last_observed_at": last_record["observed_at"],
            "representative_visual_observation_id": first_record["visual_observation_id"],
            "summary_text": group["summary_text"],
            "reason_summary": group["reason_summary"],
            "retention_status": "compressed" if len(group_records) >= 3 else "active",
        }

    def _visual_daily_memory_candidate_summaries(self, group_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 日次整理では候補化まで行い、memory_unit 化は通常の記憶整理に委ねる。
        candidates: list[dict[str, Any]] = []
        for group in group_summaries:
            if int(group["record_count"]) < 2 and group["retention_status"] != "active":
                continue
            candidates.append(
                {
                    "source": "daily_visual_digest",
                    "duplicate_group_id": group["duplicate_group_id"],
                    "representative_visual_observation_id": group.get("representative_visual_observation_id"),
                    "summary_text": group["summary_text"],
                    "source_key": group["source_key"],
                    "topic_ref": self._visual_daily_topic_ref(group["duplicate_group_id"]),
                    "reason_code": "repeated_or_retained_visual_context",
                }
            )
            if len(candidates) >= 6:
                break
        return candidates

    def _visual_daily_compressible(self, record: dict[str, Any]) -> bool:
        # ユーザー関心が強い入力は圧縮対象にしない。
        if record.get("image_input_kind") == "conversation_attachment":
            return False
        importance_score = record.get("importance_score")
        if isinstance(importance_score, (int, float)) and float(importance_score) >= 0.7:
            return False
        return True

    def _visual_daily_source_key(self, record: dict[str, Any]) -> str:
        # source単位
        vision_source_id = record.get("vision_source_id")
        if isinstance(vision_source_id, str) and vision_source_id.strip():
            return f"vision:{vision_source_id.strip()}"
        source_label = record.get("source_label")
        if isinstance(source_label, str) and source_label.strip():
            return f"label:{source_label.strip()}"
        return f"kind:{record.get('source_kind', 'unknown')}"

    def _visual_daily_digest_id(self, *, memory_set_id: str, local_date: str) -> str:
        # 安定ID
        digest_key = hashlib.sha256(f"{memory_set_id}:{local_date}".encode("utf-8")).hexdigest()[:24]
        return f"daily_visual_digest:{digest_key}"

    def _visual_daily_group_id(self, *, memory_set_id: str, local_date: str, index: int) -> str:
        # 安定ID
        group_key = hashlib.sha256(f"{memory_set_id}:{local_date}:{index}".encode("utf-8")).hexdigest()[:24]
        return f"visual_duplicate_group:{group_key}"

    def _run_due_visual_daily_promotions(self, *, memory_set_id: str) -> None:
        # 未昇格 digest を少数だけ処理する。
        digests = self.store.list_daily_visual_digests(
            memory_set_id=memory_set_id,
            query_text=None,
            limit=VISUAL_DAILY_RUN_DATE_LIMIT,
        )
        state = self.store.read_state()
        for digest in reversed(digests):
            promotion = digest.get("memory_promotion")
            if isinstance(promotion, dict) and promotion.get("result_status") in {"succeeded", "skipped", "failed"}:
                continue
            self._promote_visual_daily_digest_memory_candidates(
                digest=digest,
                state=self._visual_daily_state_for_memory_set(state=state, memory_set_id=memory_set_id),
            )

    def _promote_visual_daily_digest_memory_candidates(
        self,
        *,
        digest: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        # 候補なし
        candidates = [
            item
            for item in digest.get("memory_candidate_summaries", [])
            if isinstance(item, dict)
        ]
        if not candidates:
            self._store_visual_daily_promotion_result(
                digest=digest,
                result_status="skipped",
                promoted_memory_unit_ids=[],
                skipped_candidate_count=0,
                failure_reason=None,
                relation_index_sync=None,
            )
            return

        # 昇格
        memory_set_id = digest["memory_set_id"]
        finished_at = self._now_iso()
        reviewed_candidates: list[dict[str, Any]] = []
        skipped_count = 0
        candidates = candidates[:VISUAL_DAILY_PROMOTION_LIMIT_PER_DIGEST]
        failure_reason = "visual_daily_support_failed"
        try:
            support = self._visual_daily_repeated_support(digest=digest, candidates=candidates, state=state)
            failure_reason = "visual_daily_promotion_failed"
            for decision in support["review"]["decisions"]:
                if decision["support_status"] != "supported" or decision["relation"] == "uncertain":
                    skipped_count += len(decision["candidate_indexes"])
                    continue
                target = support["memories"].get(decision["target_memory_ref"])
                memory_candidate = self._visual_daily_memory_candidate(
                    digest=digest, candidates=candidates, decision=decision, support=support)
                reviewed_candidates.append({
                    "candidate": memory_candidate, "relation": decision["relation"],
                    "existing_memory": target["memory_unit"] if target else None,
                    "candidate_indexes": decision["candidate_indexes"],
                })
            actions = self.memory.action_resolver.resolve_visual_daily_memory_actions(
                memory_set_id=memory_set_id, finished_at=finished_at, reviewed_candidates=reviewed_candidates)
        except Exception:
            self._store_visual_daily_promotion_result(
                digest=digest, result_status="failed", promoted_memory_unit_ids=[],
                skipped_candidate_count=skipped_count, failure_reason=failure_reason, relation_index_sync=None)
            raise

        if not actions:
            self._store_visual_daily_promotion_result(
                digest=digest,
                result_status="skipped",
                promoted_memory_unit_ids=[],
                skipped_candidate_count=skipped_count,
                failure_reason=None,
                relation_index_sync=None,
            )
            return

        try:
            self.store.persist_memory_actions(
                memory_actions=actions,
                expected_memory_units=[item["memory_unit"] for item in support["memories"].values()])
        except Exception as exc:  # noqa: BLE001
            self._store_visual_daily_promotion_result(
                digest=digest,
                result_status="failed",
                promoted_memory_unit_ids=[],
                skipped_candidate_count=skipped_count,
                failure_reason=str(exc),
                relation_index_sync=None,
            )
            debug_log("VisualDaily", f"promotion persist failed digest={digest['digest_id']} error={type(exc).__name__}: {format_debug_log_text(str(exc), level='ERROR')}", level="ERROR")
            return

        auxiliary_failures: list[str] = []
        try:
            self.memory.vector_indexer.sync(
                state=state,
                finished_at=finished_at,
                episode=None,
                memory_actions=actions,
            )
        except Exception as exc:  # noqa: BLE001
            auxiliary_failures.append(f"vector_index_sync: {exc}")

        try:
            relation_index_sync = self.store.rebuild_relation_index(
                memory_set_id=memory_set_id,
                updated_at=self._now_iso(),
            )
        except Exception as exc:  # noqa: BLE001
            relation_index_sync = self._relation_index_sync_trace("failed", failure_reason=str(exc))
            auxiliary_failures.append(f"relation_index_sync: {exc}")
            self.store.append_events(
                events=[
                    self._build_memory_audit_event(
                        cycle_id=f"visual_daily:{digest['digest_id']}",
                        memory_set_id=memory_set_id,
                        kind="relation_index_sync_failure",
                        created_at=self._now_iso(),
                        payload={"failure_reason": str(exc)},
                    )
                ]
            )

        promoted_ids = [
            action["memory_unit_id"]
            for action in actions
            if isinstance(action.get("memory_unit_id"), str)
        ]
        self._store_visual_daily_promotion_result(
            digest=digest,
            result_status="failed" if auxiliary_failures else "succeeded",
            promoted_memory_unit_ids=promoted_ids,
            skipped_candidate_count=skipped_count,
            failure_reason="; ".join(auxiliary_failures) or None,
            relation_index_sync=relation_index_sync,
        )
        debug_log(
            "VisualDaily",
            f"promotion done digest={digest['digest_id']} promoted={len(promoted_ids)} skipped={skipped_count}",
        )

    def _visual_daily_repeated_support(self, *, digest: dict[str, Any], candidates: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
        previous_digests = self.store.list_daily_visual_digests(
            memory_set_id=digest["memory_set_id"],
            before_local_date=digest["local_date"],
            limit=VISUAL_DAILY_PROMOTION_LOOKBACK_LIMIT,
        )
        evidence: dict[str, dict[str, Any]] = {}
        topic_refs, promoted_ids = [], []
        for previous_digest in previous_digests:
            if previous_digest["result_status"] != "succeeded":
                continue
            for previous_candidate in previous_digest.get("memory_candidate_summaries", []):
                evidence[f"past:{len(evidence)}"] = {
                    **previous_candidate, "digest_id": previous_digest["digest_id"],
                    "local_date": previous_digest["local_date"],
                }
                topic_refs.append(previous_candidate["topic_ref"])
            promoted_ids.extend(previous_digest["memory_promotion"]["promoted_memory_unit_ids"])
        memories = {}
        if evidence:
            comparison = self.store.load_visual_daily_comparison_memories(
                memory_set_id=digest["memory_set_id"], topic_refs=topic_refs, memory_unit_ids=promoted_ids)
            sources = {candidate["source_key"] for candidate in candidates}
            memories = {f"memory:{index}": item for index, item in enumerate(
                item for item in comparison if item["source_key"] in sources)}
        context = {
            "local_date": digest["local_date"],
            "candidates": [{"candidate_index": index, "group_ref": f"current:{index}",
                            "source_key": item["source_key"], "summary_text": item["summary_text"]}
                           for index, item in enumerate(candidates)],
            "evidence": [{"evidence_ref": ref, "local_date": item["local_date"],
                          "source_key": item["source_key"], "summary_text": item["summary_text"]}
                         for ref, item in evidence.items()],
            "existing_memories": [{
                "memory_ref": ref, "summary_text": item["memory_unit"]["summary_text"],
                "status": item["memory_unit"]["status"], "scope_type": item["memory_unit"]["scope_type"],
                "formed_at": item["memory_unit"]["formed_at"], "source_key": item["source_key"],
                "observed_date": item["observed_date"],
                "observed_dates": sorted({item["observed_date"], *(
                    group["local_date"] for group in item["memory_unit"]["qualifiers"].get("visual_evidence", []))}),
                "valid_from": item["memory_unit"].get("valid_from"), "valid_to": item["memory_unit"].get("valid_to"),
                "evidence_refs": [evidence_ref for evidence_ref, group in evidence.items()
                                  if group["duplicate_group_id"] in {
                                      item["memory_unit"]["qualifiers"]["duplicate_group_id"],
                                      *item["memory_unit"]["qualifiers"].get("support_group_refs", []),
                                  }],
            } for ref, item in memories.items()],
        }
        if not evidence:
            review = {"decisions": [{
                "source_claims": [], "candidate_indexes": [index], "support_status": "insufficient", "shared_summary_text": None,
                "past_support_refs": [], "relation": "uncertain", "target_memory_ref": None,
                "memory_comparison": None, "reason_summary": "過去日の比較材料がありません。",
            } for index in range(len(candidates))]}
        else:
            review = self.llm.generate_visual_daily_support(
                model_config=state["model_presets"][state["selected_model_preset_id"]],
                persona_context=build_persona_context(state["personas"][state["selected_persona_id"]], role="visual_daily_support"),
                context=context)
        validate_support(review, context)
        digest["support_review"] = {**review, "references": {
            "candidate_groups": {str(index): item["duplicate_group_id"] for index, item in enumerate(candidates)},
            "evidence_groups": {ref: item["duplicate_group_id"] for ref, item in evidence.items()},
            "memory_unit_ids": {ref: item["memory_unit"]["memory_unit_id"] for ref, item in memories.items()},
        }}
        return {"review": review, "evidence": evidence, "memories": memories}

    def _visual_daily_memory_candidate(
        self, *, digest: dict[str, Any], candidates: list[dict[str, Any]],
        decision: dict[str, Any], support: dict[str, Any],
    ) -> dict[str, Any]:
        indexes = sorted(decision["candidate_indexes"])
        candidate = candidates[indexes[0]]
        relation = decision["relation"]
        if relation in {"same", "refine"}:
            topic_hint = support["memories"][decision["target_memory_ref"]]["memory_unit"]["scope_key"]
        else:
            topic_hint = self._visual_daily_topic_ref(f"{digest['digest_id']}:claim:{','.join(map(str, indexes))}")
        summary_text = decision["shared_summary_text"]
        groups = [{**candidates[index], "digest_id": digest["digest_id"], "local_date": digest["local_date"]}
                  for index in indexes]
        groups.extend(support["evidence"][ref] for ref in decision["past_support_refs"])
        if relation in {"same", "refine"}:
            groups.append(support["memories"][decision["target_memory_ref"]]["observation_group"])
        evidence = {}
        for group in groups:
            record = {key: group[key] for key in (
                "duplicate_group_id", "digest_id", "local_date", "source_key", "representative_visual_observation_id")}
            if not record["representative_visual_observation_id"]:
                raise ValueError("視覚記憶の根拠には代表観測が必要です。")
            evidence[record["duplicate_group_id"]] = record
        return {
            "memory_type": "interpretation",
            "scope": "topic",
            "subject_hint": topic_hint,
            "predicate_hint": "visual_daily_pattern",
            "object_hint": None,
            "summary_text": summary_text,
            "confidence_hint": "medium",
            "qualifiers_hint": {
                "source": "daily_visual_digest",
                "digest_id": digest["digest_id"],
                "local_date": digest["local_date"],
                "duplicate_group_id": candidate["duplicate_group_id"],
                "representative_visual_observation_id": candidate["representative_visual_observation_id"],
                "support_group_refs": [support["evidence"][ref]["duplicate_group_id"] for ref in decision["past_support_refs"]],
                "visual_evidence": list(evidence.values()),
            },
            "evidence_text": decision["reason_summary"],
        }

    def _visual_daily_topic_ref(self, duplicate_group_id: str) -> str:
        # 観測候補または新規主張の処理参照からIDだけを作る。
        key = hashlib.sha256(duplicate_group_id.encode("utf-8")).hexdigest()[:24]
        return f"topic:visual_daily_{key}"

    def _store_visual_daily_promotion_result(
        self,
        *,
        digest: dict[str, Any],
        result_status: str,
        promoted_memory_unit_ids: list[str],
        skipped_candidate_count: int,
        failure_reason: str | None,
        relation_index_sync: dict[str, Any] | None,
    ) -> None:
        # digest payload に昇格結果を残す。
        updated_digest = {
            **digest,
            "memory_promotion": {
                "result_status": result_status,
                "promoted_memory_unit_ids": promoted_memory_unit_ids,
                "skipped_candidate_count": skipped_candidate_count,
                "failure_reason": failure_reason,
                "relation_index_sync": relation_index_sync,
            },
        }
        self.store.upsert_daily_visual_digest(digest=updated_digest, updated_records=[])
        digest["memory_promotion"] = updated_digest["memory_promotion"]

    def _visual_daily_state_for_memory_set(self, *, state: dict[str, Any], memory_set_id: str) -> dict[str, Any]:
        # vector sync は selected_memory_set_id を見るため、対象 memory_set を選択状態にする。
        return {
            **state,
            "selected_memory_set_id": memory_set_id,
        }
