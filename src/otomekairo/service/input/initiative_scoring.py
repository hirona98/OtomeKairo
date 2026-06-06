from __future__ import annotations

from typing import Any


class ServiceInputInitiativeScoringMixin:
    def _initiative_drive_summaries(
        self,
        drive_state_summary: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for drive_state in drive_state_summary or []:
            if not isinstance(drive_state, dict):
                continue
            item: dict[str, Any] = {
                "drive_id": drive_state.get("drive_id"),
                "summary_text": drive_state.get("summary_text"),
                "salience": drive_state.get("salience"),
            }
            for key in (
                "drive_kind",
                "focus_scope_type",
                "focus_scope_key",
                "freshness_hint",
                "source_updated_at",
                "stability_hint",
            ):
                value = drive_state.get(key)
                if isinstance(value, str) and value.strip():
                    item[key] = value.strip()
            support_count = drive_state.get("support_count")
            if isinstance(support_count, int) and support_count > 0:
                item["support_count"] = support_count
            for key in ("support_strength", "scope_alignment", "signal_strength", "persona_alignment"):
                value = drive_state.get(key)
                if isinstance(value, (int, float)):
                    item[key] = round(max(0.0, min(float(value), 1.0)), 2)
            supporting_memory_types = drive_state.get("supporting_memory_types")
            if isinstance(supporting_memory_types, list):
                item["supporting_memory_types"] = [
                    value.strip()
                    for value in supporting_memory_types
                    if isinstance(value, str) and value.strip()
                ][:4]
            scope_support_kinds = drive_state.get("scope_support_kinds")
            if isinstance(scope_support_kinds, list):
                item["scope_support_kinds"] = [
                    value.strip()
                    for value in scope_support_kinds
                    if isinstance(value, str) and value.strip()
                ][:5]
            summaries.append(item)
        return summaries
