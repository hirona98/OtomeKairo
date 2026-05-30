from __future__ import annotations

from typing import Any

from otomekairo.memory.utils import display_local_iso
from otomekairo.service.common import debug_log


class ServiceInputLoggingMixin:
    def _summarize_recall_pack(self, recall_pack: dict[str, Any]) -> dict[str, int]:
        evidence_pack = recall_pack.get("evidence_pack")
        # 要約
        summary = {
            "self_model": len(recall_pack["self_model"]),
            "user_model": len(recall_pack["user_model"]),
            "relationship_model": len(recall_pack["relationship_model"]),
            "active_topics": len(recall_pack["active_topics"]),
            "active_commitments": len(recall_pack["active_commitments"]),
            "episodic_evidence": len(recall_pack["episodic_evidence"]),
            "event_evidence": len(recall_pack["event_evidence"]),
            "visual_observations": len(recall_pack.get("visual_observations", [])),
            "conflicts": len(recall_pack["conflicts"]),
            "memory_links": int(
                (recall_pack.get("memory_link_context") or {}).get("link_count", 0)
                if isinstance(recall_pack.get("memory_link_context"), dict)
                else 0
            ),
        }
        if isinstance(evidence_pack, dict):
            summary["answer_evidence_items"] = len(evidence_pack.get("evidence_items", []))
        return summary

    def _empty_memory_link_context_trace(self) -> dict[str, Any]:
        # 結果
        return {
            "selected_memory_unit_count": 0,
            "link_count": 0,
            "label_counts": {},
            "representative_links": [],
            "result_status": "empty",
        }

    def _summarize_memory_link_context(self, value: Any) -> dict[str, Any]:
        # 形状
        if not isinstance(value, dict):
            return self._empty_memory_link_context_trace()

        # 代表 link
        representative_links: list[dict[str, Any]] = []
        for item in value.get("representative_links", []):
            if not isinstance(item, dict):
                continue
            representative_links.append(
                {
                    "memory_link_id": item.get("memory_link_id"),
                    "label": item.get("label"),
                    "selected_endpoint": item.get("selected_endpoint"),
                    "source_memory_unit_id": item.get("source_memory_unit_id"),
                    "target_memory_unit_id": item.get("target_memory_unit_id"),
                    "summary_text": item.get("summary_text"),
                }
            )
            if len(representative_links) >= 5:
                break

        # 結果
        return {
            "selected_memory_unit_count": int(value.get("selected_memory_unit_count", 0) or 0),
            "link_count": int(value.get("link_count", 0) or 0),
            "label_counts": value.get("label_counts", {}),
            "representative_links": representative_links,
            "result_status": value.get("result_status", "empty"),
        }

    def _emit_input_success_logs(
        self,
        *,
        cycle_id: str,
        trigger_kind: str,
        input_text: str,
        pipeline: dict[str, Any],
        result_kind: str,
        reply_payload: dict[str, Any] | None,
        pending_intent_selection: dict[str, Any] | None = None,
    ) -> None:
        # ログ群
        logs: list[dict[str, Any]] = []
        if reply_payload is None:
            logs.append(
                self._build_live_log_record(
                    level="INFO",
                    component="Result",
                    message=f"{self._short_cycle_id(cycle_id)} result={result_kind} reply=-",
                )
            )
        self._emit_live_logs(logs)

    def _emit_input_failure_logs(
        self,
        *,
        cycle_id: str,
        trigger_kind: str,
        input_text: str,
        failure_reason: str,
        pending_intent_selection: dict[str, Any] | None = None,
    ) -> None:
        # ログ群
        logs = [
            self._build_live_log_record(
                level="INFO",
                component="Input",
                message=(
                    f"{self._short_cycle_id(cycle_id)} trigger={trigger_kind} "
                    f"input={self._conversation_log_excerpt(input_text)}"
                ),
            ),
            self._build_live_log_record(
                level="ERROR",
                component="Failure",
                message=(
                    f"{self._short_cycle_id(cycle_id)} internal_failure "
                    f"reason={self._clamp(failure_reason)}"
                ),
            ),
        ]
        self._emit_live_logs(logs)

    def _emit_memory_trace_logs(self, *, cycle_id: str, memory_trace: dict[str, Any]) -> None:
        # status判定
        status = str(memory_trace.get("turn_consolidation_status", "unknown"))
        if status == "failed":
            level = "WARNING"
            message = (
                f"{self._short_cycle_id(cycle_id)} status=failed "
                f"reason={self._clamp(str(memory_trace.get('failure_reason') or '-'))}"
            )
        elif status == "skipped":
            level = "INFO"
            message = (
                f"{self._short_cycle_id(cycle_id)} status=skipped "
                f"reason={self._clamp(str(memory_trace.get('skip_reason') or '-'))}"
            )
        else:
            vector_sync = memory_trace.get("vector_index_sync") or {}
            correction = memory_trace.get("correction_reconciliation") or {}
            reflective = memory_trace.get("reflective_consolidation") or {}
            drive_update = memory_trace.get("drive_state_update") or {}
            message = (
                f"{self._short_cycle_id(cycle_id)} status={status} "
                f"episode={memory_trace.get('episode_id') or '-'} "
                f"memory_actions={memory_trace.get('memory_action_count', 0)} "
                f"episode_affects={memory_trace.get('episode_affect_count', 0)} "
                f"vector={vector_sync.get('result_status', 'unknown')}"
            )
            message += f" correction={correction.get('result_status', 'unknown')}"
            message += f" reflection={reflective.get('result_status', 'unknown')}"
            message += f" drive={drive_update.get('result_status', 'unknown')}"
            level = "INFO"

        # 送出
        self._emit_live_log(
            level=level,
            component="Memory",
            message=message,
        )

    def _emit_live_log(self, *, level: str, component: str, message: str) -> None:
        # 会話ウインドウ側で処理位置を追うための短い段階ログ。
        self._emit_live_logs(
            [
                self._build_live_log_record(
                    level=level,
                    component=component,
                    message=message,
                )
            ]
        )

    def _emit_live_logs(self, logs: list[dict[str, Any]]) -> None:
        # live log は server.log と logs/stream で同じ内容を扱う。
        if not logs:
            return
        for log in logs:
            component = str(log.get("logger") or "LiveLog")
            message = str(log.get("msg") or "")
            level = str(log.get("level") or "INFO")
            debug_log(component, message, level=level)

    def _build_live_log_record(self, *, level: str, component: str, message: str) -> dict[str, Any]:
        # 結果
        return {
            "ts": display_local_iso(self._now_iso()),
            "level": level,
            "logger": component,
            "msg": message,
        }

    def _conversation_log_excerpt(self, value: str | None, limit: int = 160) -> str | None:
        # 会話本文のログ表示は最初の行だけにする。
        if value is None:
            return None
        stripped = value.strip()
        first_line = stripped.splitlines()[0] if stripped else ""
        return self._clamp(first_line, limit=limit)

    def _short_cycle_id(self, cycle_id: str) -> str:
        # 空
        if ":" not in cycle_id:
            return cycle_id[:12]

        # 結果
        return cycle_id.split(":", 1)[1][:12]

    def _debug_cycle_label(self, cycle_id: str | None) -> str:
        # 未採番経路
        if not isinstance(cycle_id, str) or not cycle_id:
            return "-"
        return self._short_cycle_id(cycle_id)

    def _debug_context_keys(self, context: dict[str, Any]) -> str:
        # 値は出さずキーだけに留める。
        keys = sorted(str(key) for key in context.keys())[:8]
        return ",".join(keys) if keys else "-"

    def _format_list_for_log(self, values: list[Any]) -> str:
        # 空
        if not values:
            return "-"

        # 結果
        return ",".join(str(value) for value in values[:3])

    def _format_id_list_for_log(self, values: list[str]) -> str:
        # 空
        if not values:
            return "-"

        # 結果
        return ",".join(self._short_identifier(value) for value in values[:3])

    def _short_identifier(self, value: str) -> str:
        # 空
        if ":" not in value:
            return value[:18]

        # 結果
        prefix, suffix = value.split(":", 1)
        return f"{prefix}:{suffix[:8]}"
