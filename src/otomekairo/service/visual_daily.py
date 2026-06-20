from __future__ import annotations

import hashlib
import threading
from difflib import SequenceMatcher
from typing import Any

from otomekairo.memory.utils import local_now
from otomekairo.service.common import debug_log


VISUAL_DAILY_CHECK_INTERVAL_SECONDS = 3600.0
VISUAL_DAILY_RUN_DATE_LIMIT = 7
VISUAL_DAILY_DUPLICATE_SIMILARITY = 0.86
VISUAL_DAILY_PROMOTION_LOOKBACK_LIMIT = 14
VISUAL_DAILY_PROMOTION_LIMIT_PER_DIGEST = 3
VISUAL_DAILY_PROMOTION_SIMILARITY = 0.74


class ServiceVisualDailyMixin:
    def start_background_visual_daily_worker(self) -> None:
        # 既存
        with self._runtime_state_lock:
            if (
                self._background_visual_daily_thread is not None
                and self._background_visual_daily_thread.is_alive()
            ):
                debug_log("VisualDaily", "already running", level="DEBUG")
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
        debug_log("VisualDaily", f"started thread={thread.name}", level="DEBUG")

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
        debug_log("VisualDaily", "loop started", level="DEBUG")
        while not stop_event.is_set():
            try:
                self._run_due_visual_daily_digests()
            except Exception as exc:  # noqa: BLE001
                debug_log("VisualDaily", f"loop error={type(exc).__name__}: {exc}", level="ERROR")
            stop_event.wait(VISUAL_DAILY_CHECK_INTERVAL_SECONDS)
        debug_log("VisualDaily", "loop stopped", level="DEBUG")

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
        # 連続する近似観測だけを同じ group にする。
        groups: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for record in records:
            if current is None or not self._visual_daily_record_matches_group(record, current):
                current = {
                    "duplicate_group_id": self._visual_daily_group_id(
                        memory_set_id=memory_set_id,
                        local_date=local_date,
                        index=len(groups) + 1,
                    ),
                    "source_key": self._visual_daily_source_key(record),
                    "records": [],
                    "representative_text": record["detailed_summary_text"],
                }
                groups.append(current)
            current["records"].append(record)
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
            "summary_text": first_record["detailed_summary_text"].strip(),
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
                    "reason_code": "repeated_or_retained_visual_context",
                }
            )
            if len(candidates) >= 6:
                break
        return candidates

    def _visual_daily_record_matches_group(self, record: dict[str, Any], group: dict[str, Any]) -> bool:
        # source が違う場合は別 group にする。
        if self._visual_daily_source_key(record) != group["source_key"]:
            return False
        similarity = SequenceMatcher(
            None,
            self._visual_daily_similarity_text(record["detailed_summary_text"]),
            self._visual_daily_similarity_text(group["representative_text"]),
        ).ratio()
        return similarity >= VISUAL_DAILY_DUPLICATE_SIMILARITY

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

    def _visual_daily_similarity_text(self, value: str) -> str:
        # 比較用に空白だけ潰す。
        return " ".join(value.strip().split())

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
            if isinstance(promotion, dict) and promotion.get("result_status") in {"succeeded", "skipped"}:
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
            )
            return

        # 昇格
        memory_set_id = digest["memory_set_id"]
        finished_at = self._now_iso()
        actions: list[dict[str, Any]] = []
        skipped_count = 0
        for candidate in candidates[:VISUAL_DAILY_PROMOTION_LIMIT_PER_DIGEST]:
            if not self._visual_daily_candidate_has_repeated_support(digest=digest, candidate=candidate):
                skipped_count += 1
                continue
            memory_candidate = self._visual_daily_memory_candidate(digest=digest, candidate=candidate)
            candidate_actions = self.memory.action_resolver.resolve_memory_actions(
                memory_set_id=memory_set_id,
                finished_at=finished_at,
                event_ids=[],
                cycle_ids=[],
                candidate=memory_candidate,
                embedding_definition=state["memory_sets"][memory_set_id]["embedding"],
                allow_summary=False,
            )
            actions.extend(self._cap_visual_daily_memory_action_scores(candidate_actions))

        if not actions:
            self._store_visual_daily_promotion_result(
                digest=digest,
                result_status="skipped",
                promoted_memory_unit_ids=[],
                skipped_candidate_count=skipped_count,
                failure_reason=None,
            )
            return

        try:
            self.store.persist_memory_actions(memory_actions=actions)
            self.memory.vector_indexer.sync(
                state=state,
                finished_at=finished_at,
                episode=None,
                memory_actions=actions,
            )
        except Exception as exc:  # noqa: BLE001
            self._store_visual_daily_promotion_result(
                digest=digest,
                result_status="failed",
                promoted_memory_unit_ids=[],
                skipped_candidate_count=skipped_count,
                failure_reason=str(exc),
            )
            debug_log("VisualDaily", f"promotion failed digest={digest['digest_id']} error={type(exc).__name__}: {exc}", level="ERROR")
            return

        promoted_ids = [
            action["memory_unit_id"]
            for action in actions
            if isinstance(action.get("memory_unit_id"), str)
        ]
        self._store_visual_daily_promotion_result(
            digest=digest,
            result_status="succeeded",
            promoted_memory_unit_ids=promoted_ids,
            skipped_candidate_count=skipped_count,
            failure_reason=None,
        )
        debug_log(
            "VisualDaily",
            f"promotion done digest={digest['digest_id']} promoted={len(promoted_ids)} skipped={skipped_count}",
        )

    def _visual_daily_candidate_has_repeated_support(self, *, digest: dict[str, Any], candidate: dict[str, Any]) -> bool:
        # 2 日以上にまたがる類似 digest だけ昇格する。
        previous_digests = self.store.list_daily_visual_digests(
            memory_set_id=digest["memory_set_id"],
            before_local_date=digest["local_date"],
            limit=VISUAL_DAILY_PROMOTION_LOOKBACK_LIMIT,
        )
        candidate_text = self._visual_daily_similarity_text(str(candidate.get("summary_text", "")))
        for previous_digest in previous_digests:
            for previous_candidate in previous_digest.get("memory_candidate_summaries", []):
                if not isinstance(previous_candidate, dict):
                    continue
                similarity = SequenceMatcher(
                    None,
                    candidate_text,
                    self._visual_daily_similarity_text(str(previous_candidate.get("summary_text", ""))),
                ).ratio()
                if similarity >= VISUAL_DAILY_PROMOTION_SIMILARITY:
                    return True
        return False

    def _visual_daily_memory_candidate(self, *, digest: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        # 日次視覚整理由来の候補は弱い inferred 記憶に固定する。
        summary_text = str(candidate.get("summary_text", "")).strip()
        topic_hint = self._visual_daily_topic_hint(summary_text)
        return {
            "memory_type": "interpretation",
            "scope": "topic",
            "subject_hint": topic_hint,
            "predicate_hint": "visual_daily_pattern",
            "object_hint": topic_hint,
            "summary_text": summary_text,
            "confidence_hint": "medium",
            "qualifiers_hint": {
                "source": "daily_visual_digest",
                "digest_id": digest["digest_id"],
                "local_date": digest["local_date"],
                "duplicate_group_id": candidate.get("duplicate_group_id"),
                "representative_visual_observation_id": candidate.get("representative_visual_observation_id"),
            },
            "evidence_text": f"{digest['local_date']} の視覚日次整理で反復または保持対象として整理された。",
        }

    def _visual_daily_topic_hint(self, summary_text: str) -> str:
        # 安定した topic hint
        key = hashlib.sha256(summary_text.encode("utf-8")).hexdigest()[:12]
        return f"topic:visual_daily_{key}"

    def _cap_visual_daily_memory_action_scores(self, actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 日次視覚由来の記憶は控えめな重みに固定する。
        capped: list[dict[str, Any]] = []
        for action in actions:
            memory_unit = action.get("memory_unit")
            if isinstance(memory_unit, dict) and action.get("operation") in {"create", "reinforce", "refine"}:
                memory_unit = {
                    **memory_unit,
                    "confidence": min(float(memory_unit.get("confidence", 0.0) or 0.0), 0.55),
                    "salience": min(float(memory_unit.get("salience", 0.0) or 0.0), 0.45),
                }
                action = {
                    **action,
                    "memory_unit": memory_unit,
                    "after_snapshot": memory_unit,
                }
            capped.append(action)
        return capped

    def _store_visual_daily_promotion_result(
        self,
        *,
        digest: dict[str, Any],
        result_status: str,
        promoted_memory_unit_ids: list[str],
        skipped_candidate_count: int,
        failure_reason: str | None,
    ) -> None:
        # digest payload に昇格結果を残す。
        updated_digest = {
            **digest,
            "memory_promotion": {
                "result_status": result_status,
                "promoted_memory_unit_ids": promoted_memory_unit_ids,
                "skipped_candidate_count": skipped_candidate_count,
                "failure_reason": failure_reason,
            },
        }
        self.store.upsert_daily_visual_digest(digest=updated_digest, updated_records=[])

    def _visual_daily_state_for_memory_set(self, *, state: dict[str, Any], memory_set_id: str) -> dict[str, Any]:
        # vector sync は selected_memory_set_id を見るため、対象 memory_set を選択状態にする。
        return {
            **state,
            "selected_memory_set_id": memory_set_id,
        }
