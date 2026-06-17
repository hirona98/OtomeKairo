from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from otomekairo.llm.client import LLMContractError, LLMError
from otomekairo.service.common import (
    PENDING_INTENT_EXPIRES_HOURS,
    PENDING_INTENT_NOT_BEFORE_MINUTES,
    debug_log,
)


class PendingIntentSelectionError(LLMError):
    def __init__(
        self,
        message: str,
        *,
        pending_intent_selection: dict[str, Any],
        failure_stage: str,
    ) -> None:
        super().__init__(message)
        self.pending_intent_selection = pending_intent_selection
        self.failure_stage = failure_stage


class ServiceSpontaneousPendingIntentMixin:
    def _pending_intent_trace_summary(
        self,
        *,
        cycle_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        # 確認
        if decision.get("kind") != "pending_intent":
            return None
        pending_intent = decision.get("pending_intent")
        if not isinstance(pending_intent, dict):
            return None

        # 結果
        return {
            "source_cycle_id": cycle_id,
            "intent_kind": pending_intent.get("intent_kind"),
            "intent_summary": pending_intent.get("intent_summary"),
            "reason_summary": decision.get("reason_summary"),
            "dedupe_key": pending_intent.get("dedupe_key"),
        }

    def _select_due_pending_intent_candidate(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        current_time: str,
    ) -> dict[str, Any]:
        # 初期状態
        trace = self._empty_pending_intent_selection_trace()
        memory_set_id = state["selected_memory_set_id"]

        # 候補群
        candidate_pool = self._pending_intent_candidate_pool(
            memory_set_id=memory_set_id,
            current_time=current_time,
        )
        trace["candidate_pool_count"] = len(candidate_pool)
        current_dt = self._parse_iso(current_time)
        eligible_candidates = [
            candidate
            for candidate in candidate_pool
            if not isinstance(candidate.get("not_before"), str)
            or not candidate["not_before"]
            or self._parse_iso(candidate["not_before"]) <= current_dt
        ]
        trace["eligible_candidate_count"] = len(eligible_candidates)
        debug_log(
            "PendingIntent",
            (
                f"selection start trigger={trigger_kind} pool={len(candidate_pool)} "
                f"eligible={len(eligible_candidates)}"
            ),
            level="DEBUG",
        )
        if not eligible_candidates:
            debug_log("PendingIntent", f"selection skipped trigger={trigger_kind} reason=no_eligible_candidates")
            return {
                "selected_candidate": None,
                "pending_intent_selection": trace,
            }

        # source pack
        try:
            source_pack = self._build_pending_intent_selection_source_pack(
                state=state,
                trigger_kind=trigger_kind,
                client_context=client_context,
                recent_turns=recent_turns,
                candidates=eligible_candidates,
                current_time=current_time,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=build_source_pack error={self._clamp(str(exc))}",
                level="ERROR",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="build_source_pack",
            ) from exc

        # 選択
        role_definition = state["model_presets"][state["selected_model_preset_id"]]["roles"]["pending_intent_selection"]
        persona_context = self._build_selected_persona_context(state=state, role="pending_intent_selection")
        try:
            payload = self.llm.generate_pending_intent_selection(
                role_definition=role_definition,
                persona_context=persona_context,
                source_pack=source_pack,
            )
        except LLMContractError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=contract_validation error={self._clamp(str(exc))}",
                level="ERROR",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="contract_validation",
            ) from exc
        except LLMError as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=llm_generation error={self._clamp(str(exc))}",
                level="ERROR",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="llm_generation",
            ) from exc

        # 反映
        try:
            selection_result = self._apply_pending_intent_selection(
                payload=payload,
                source_pack=source_pack,
                candidates=eligible_candidates,
            )
        except (KeyError, TypeError, ValueError) as exc:
            trace["result_status"] = "failed"
            trace["failure_reason"] = str(exc)
            debug_log(
                "PendingIntent",
                f"selection failed trigger={trigger_kind} stage=apply_selection error={self._clamp(str(exc))}",
                level="ERROR",
            )
            raise PendingIntentSelectionError(
                str(exc),
                pending_intent_selection=trace,
                failure_stage="apply_selection",
            ) from exc

        # 結果
        trace["selected_candidate_ref"] = selection_result["selected_candidate_ref"]
        trace["selection_reason"] = selection_result["selection_reason"]
        trace["result_status"] = "succeeded"
        selected_candidate = selection_result["selected_candidate"]
        if selected_candidate is not None:
            trace["selected_candidate_id"] = selected_candidate.get("candidate_id")
        debug_log(
            "PendingIntent",
            (
                f"selection done trigger={trigger_kind} selected={trace.get('selected_candidate_ref') or '-'} "
                f"candidate_id={trace.get('selected_candidate_id') or '-'}"
            ),
        )
        return {
            "selected_candidate": selected_candidate,
            "pending_intent_selection": trace,
        }

    def _pending_intent_candidate_pool(
        self,
        *,
        memory_set_id: str,
        current_time: str,
    ) -> list[dict[str, Any]]:
        # ロック下読み取り
        with self._runtime_state_lock:
            self._prune_pending_intent_candidates(current_time=current_time)
            return [
                dict(candidate)
                for candidate in self._pending_intent_candidates
                if candidate.get("memory_set_id") == memory_set_id
            ]

    def _build_pending_intent_selection_source_pack(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        recent_turns: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        current_time: str,
    ) -> dict[str, Any]:
        return {
            "trigger_kind": trigger_kind,
            "persona_context": self._build_selected_persona_context(
                state=state,
                role="pending_intent_selection",
            ).to_prompt_payload(),
            "input_context": self._build_pending_intent_selection_input_context(
                state=state,
                trigger_kind=trigger_kind,
                client_context=client_context,
                current_time=current_time,
            ),
            "recent_turns": self._pending_intent_selection_recent_turns(recent_turns),
            "selection_policy": {
                "allow_none": True,
                "max_selected_candidates": 1,
            },
            "candidates": [
                self._pending_intent_selection_candidate_source_item(
                    candidate_ref=f"candidate:{index}",
                    candidate=candidate,
                    current_time=current_time,
                )
                for index, candidate in enumerate(candidates, start=1)
            ],
        }

    def _build_pending_intent_selection_input_context(
        self,
        *,
        state: dict[str, Any],
        trigger_kind: str,
        client_context: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self._client_context_text(client_context.get("source"), limit=48) or trigger_kind,
        }
        active_app = self._client_context_text(client_context.get("active_app"), limit=80)
        if active_app is not None:
            payload["active_app"] = active_app
        window_title = self._client_context_text(client_context.get("window_title"), limit=120)
        if window_title is not None:
            payload["window_title"] = window_title
        locale = self._client_context_text(client_context.get("locale"), limit=32)
        if locale is not None:
            payload["locale"] = locale
        drive_state_summary = self._summarize_drive_states(
            self._list_current_drive_states(
                state=state,
                current_time=current_time,
            )
        )
        if drive_state_summary:
            payload["drive_state_summary"] = drive_state_summary
        ongoing_action_summary = self._summarize_ongoing_action(
            self._current_ongoing_action(
                state=state,
                current_time=current_time,
            )
        )
        if isinstance(ongoing_action_summary, dict):
            payload["ongoing_action_summary"] = ongoing_action_summary
        return payload

    def _pending_intent_selection_recent_turns(self, recent_turns: list[dict[str, Any]]) -> list[dict[str, str]]:
        compact_turns: list[dict[str, str]] = []
        for turn in recent_turns[-4:]:
            if not isinstance(turn, dict):
                continue
            role = turn.get("role")
            text = turn.get("text")
            if not isinstance(role, str) or not role.strip():
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            compact_turns.append(
                {
                    "role": role.strip(),
                    "text": self._clamp(text.strip(), limit=120),
                }
            )
        return compact_turns

    def _pending_intent_selection_candidate_source_item(
        self,
        *,
        candidate_ref: str,
        candidate: dict[str, Any],
        current_time: str,
    ) -> dict[str, Any]:
        intent_kind = candidate.get("intent_kind")
        intent_summary = candidate.get("intent_summary")
        reason_summary = candidate.get("reason_summary")
        created_at = candidate.get("created_at")
        updated_at = candidate.get("updated_at") or created_at
        expires_at = candidate.get("expires_at")
        if not isinstance(intent_kind, str) or not intent_kind.strip():
            raise ValueError("pending_intent candidate.intent_kind is invalid.")
        if not isinstance(intent_summary, str) or not intent_summary.strip():
            raise ValueError("pending_intent candidate.intent_summary is invalid.")
        if not isinstance(reason_summary, str) or not reason_summary.strip():
            raise ValueError("pending_intent candidate.reason_summary is invalid.")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ValueError("pending_intent candidate.created_at is invalid.")
        if not isinstance(updated_at, str) or not updated_at.strip():
            raise ValueError("pending_intent candidate.updated_at is invalid.")
        if not isinstance(expires_at, str) or not expires_at.strip():
            raise ValueError("pending_intent candidate.expires_at is invalid.")
        return {
            "candidate_ref": candidate_ref,
            "intent_kind": intent_kind.strip(),
            "intent_summary": self._clamp(intent_summary.strip(), limit=120),
            "reason_summary": self._clamp(reason_summary.strip(), limit=160),
            "minutes_since_created": self._pending_intent_selection_minutes_since(
                current_time=current_time,
                timestamp=created_at,
            ),
            "minutes_since_updated": self._pending_intent_selection_minutes_since(
                current_time=current_time,
                timestamp=updated_at,
            ),
            "minutes_until_expiry": self._pending_intent_selection_minutes_until(
                current_time=current_time,
                timestamp=expires_at,
            ),
        }

    def _pending_intent_selection_minutes_since(
        self,
        *,
        current_time: str,
        timestamp: str,
    ) -> int:
        delta_seconds = (self._parse_iso(current_time) - self._parse_iso(timestamp)).total_seconds()
        return max(0, int(delta_seconds // 60))

    def _pending_intent_selection_minutes_until(
        self,
        *,
        current_time: str,
        timestamp: str,
    ) -> int:
        delta_seconds = (self._parse_iso(timestamp) - self._parse_iso(current_time)).total_seconds()
        return max(0, int(delta_seconds // 60))

    def _apply_pending_intent_selection(
        self,
        *,
        payload: dict[str, Any],
        source_pack: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # lookup
        candidate_lookup = {
            source_candidate["candidate_ref"]: dict(candidate)
            for source_candidate, candidate in zip(source_pack["candidates"], candidates, strict=True)
        }

        # 結果
        selected_candidate_ref = str(payload["selected_candidate_ref"]).strip()
        selection_reason = str(payload["selection_reason"]).strip()
        if selected_candidate_ref == "none":
            return {
                "selected_candidate_ref": "none",
                "selected_candidate": None,
                "selection_reason": selection_reason,
            }
        return {
            "selected_candidate_ref": selected_candidate_ref,
            "selected_candidate": candidate_lookup[selected_candidate_ref],
            "selection_reason": selection_reason,
        }

    def _apply_pending_intent_candidate(
        self,
        *,
        cycle_id: str,
        memory_set_id: str,
        decision: dict[str, Any],
        occurred_at: str,
    ) -> dict[str, Any] | None:
        # 確認
        base_summary = self._pending_intent_trace_summary(cycle_id=cycle_id, decision=decision)
        if base_summary is None:
            return None

        # ロック下upsert
        with self._runtime_state_lock:
            self._prune_pending_intent_candidates(current_time=occurred_at)
            existing = self._find_pending_intent_candidate(
                memory_set_id=memory_set_id,
                dedupe_key=base_summary["dedupe_key"],
                current_time=occurred_at,
            )
            not_before = self._pending_intent_not_before(occurred_at)
            expires_at = self._pending_intent_expires_at(occurred_at)
            if existing is None:
                candidate = {
                    "candidate_id": f"pending_intent_candidate:{uuid.uuid4().hex}",
                    "memory_set_id": memory_set_id,
                    "intent_kind": base_summary["intent_kind"],
                    "intent_summary": base_summary["intent_summary"],
                    "reason_summary": base_summary["reason_summary"],
                    "source_cycle_id": cycle_id,
                    "not_before": not_before,
                    "expires_at": expires_at,
                    "dedupe_key": base_summary["dedupe_key"],
                    "created_at": occurred_at,
                    "updated_at": occurred_at,
                }
                self._pending_intent_candidates.append(candidate)
                queue_action = "created"
            else:
                candidate = existing
                candidate.update(
                    {
                        "intent_kind": base_summary["intent_kind"],
                        "intent_summary": base_summary["intent_summary"],
                        "reason_summary": base_summary["reason_summary"],
                        "source_cycle_id": cycle_id,
                        "not_before": not_before,
                        "expires_at": expires_at,
                        "updated_at": occurred_at,
                    }
                )
                queue_action = "updated"

            # 結果
            return {
                **base_summary,
                "candidate_id": candidate["candidate_id"],
                "queue_action": queue_action,
                "not_before": candidate["not_before"],
                "expires_at": candidate["expires_at"],
            }

    def _remove_pending_intent_candidate(self, candidate_id: Any) -> None:
        # 確認
        if not isinstance(candidate_id, str) or not candidate_id:
            return
        with self._runtime_state_lock:
            self._pending_intent_candidates = [
                candidate
                for candidate in self._pending_intent_candidates
                if candidate.get("candidate_id") != candidate_id
            ]

    def _find_pending_intent_candidate(
        self,
        *,
        memory_set_id: str,
        dedupe_key: str,
        current_time: str,
    ) -> dict[str, Any] | None:
        # ロック下走査
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            for candidate in self._pending_intent_candidates:
                if candidate.get("memory_set_id") != memory_set_id:
                    continue
                if candidate.get("dedupe_key") != dedupe_key:
                    continue
                expires_at = candidate.get("expires_at")
                if isinstance(expires_at, str) and expires_at and self._parse_iso(expires_at) <= current_dt:
                    continue
                return candidate
            return None

    def _prune_pending_intent_candidates(self, *, current_time: str) -> None:
        # ロック下絞り込み
        with self._runtime_state_lock:
            current_dt = self._parse_iso(current_time)
            self._pending_intent_candidates = [
                candidate
                for candidate in self._pending_intent_candidates
                if not isinstance(candidate.get("expires_at"), str)
                or self._parse_iso(candidate["expires_at"]) > current_dt
            ]

    def _clear_pending_intent_candidates(self) -> None:
        # リセット
        with self._runtime_state_lock:
            self._pending_intent_candidates = []
            self._wake_runtime_state = {
                "last_wake_at": None,
                "last_spontaneous_at": None,
                "initial_delay_until": None,
                "retry_after": None,
                "speech_history_by_dedupe": {},
                "active_user_response_cycle_count": 0,
            }

    def _pending_intent_not_before(self, occurred_at: str) -> str:
        # オフセット
        return (self._parse_iso(occurred_at) + timedelta(minutes=PENDING_INTENT_NOT_BEFORE_MINUTES)).isoformat()

    def _pending_intent_expires_at(self, occurred_at: str) -> str:
        # オフセット
        return (self._parse_iso(occurred_at) + timedelta(hours=PENDING_INTENT_EXPIRES_HOURS)).isoformat()
