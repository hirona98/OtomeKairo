"""retrieval triage report から quarantine_memory import を構築する。"""

from __future__ import annotations

from typing import Any

from otomekairo.usecase.retrieval_triage import (
    QUARANTINE_REASON_CODES,
    REVIEW_STATUS_VALUES,
    TRIAGE_REPORT_SCHEMA_VERSION,
)


# Block: 定数
REVIEW_IMPORT_REPORT_SCHEMA_VERSION = 1


# Block: Import plan builder
def build_retrieval_review_import_plan(review_report: dict[str, Any]) -> dict[str, Any]:
    report_schema_version = _required_int(review_report, "report_schema_version")
    if report_schema_version != TRIAGE_REPORT_SCHEMA_VERSION:
        raise RuntimeError("review_report.report_schema_version is unsupported")
    review_packets = _required_object_list(review_report, "review_packets")
    job_requests: list[dict[str, Any]] = []
    pending_packet_count = 0
    ignored_packet_count = 0
    confirmed_packet_count = 0
    for review_packet in review_packets:
        annotation_template = _required_object(review_packet, "annotation_template")
        review_status = _required_enum(
            annotation_template,
            "review_status",
            REVIEW_STATUS_VALUES,
        )
        if review_status == "pending":
            pending_packet_count += 1
            continue
        if review_status == "ignored":
            ignored_packet_count += 1
            continue
        confirmed_packet_count += 1
        job_requests.append(
            _job_request(
                review_packet=review_packet,
                annotation_template=annotation_template,
            )
        )
    return {
        "report_schema_version": REVIEW_IMPORT_REPORT_SCHEMA_VERSION,
        "triage_report_schema_version": report_schema_version,
        "review_packet_count": len(review_packets),
        "pending_packet_count": pending_packet_count,
        "ignored_packet_count": ignored_packet_count,
        "confirmed_packet_count": confirmed_packet_count,
        "job_requests": job_requests,
    }


# Block: Import apply
def apply_retrieval_review_import(
    *,
    store: Any,
    review_report: dict[str, Any],
) -> dict[str, Any]:
    import_plan = build_retrieval_review_import_plan(review_report)
    queued_results: list[dict[str, Any]] = []
    queued_target_count = 0
    for job_request in import_plan["job_requests"]:
        enqueue_result = store.enqueue_quarantine_memory(
            source_event_ids=list(job_request["source_event_ids"]),
            targets=list(job_request["targets"]),
            reason_code=str(job_request["reason_code"]),
            reason_note=str(job_request["reason_note"]),
        )
        queued_target_count += len(job_request["targets"])
        queued_results.append(
            {
                "source_cycle_id": str(job_request["source_cycle_id"]),
                "reason_code": str(job_request["reason_code"]),
                "target_count": len(job_request["targets"]),
                "queued_cycle_id": str(enqueue_result["cycle_id"]),
                "job_ids": list(enqueue_result["job_ids"]),
            }
        )
    return {
        "report_schema_version": REVIEW_IMPORT_REPORT_SCHEMA_VERSION,
        "triage_report_schema_version": int(import_plan["triage_report_schema_version"]),
        "review_packet_count": int(import_plan["review_packet_count"]),
        "pending_packet_count": int(import_plan["pending_packet_count"]),
        "ignored_packet_count": int(import_plan["ignored_packet_count"]),
        "confirmed_packet_count": int(import_plan["confirmed_packet_count"]),
        "queued_job_count": len(queued_results),
        "queued_target_count": queued_target_count,
        "queued_results": queued_results,
    }


# Block: Text formatter
def format_retrieval_review_import_summary(summary: dict[str, Any]) -> str:
    review_packet_count = _required_int(summary, "review_packet_count")
    confirmed_packet_count = _required_int(summary, "confirmed_packet_count")
    queued_job_count = _required_int(summary, "queued_job_count")
    queued_target_count = _required_int(summary, "queued_target_count")
    lines = [
        "retrieval review import",
        (
            "summary: "
            f"packets {review_packet_count}, "
            f"confirmed {confirmed_packet_count}, "
            f"queued_jobs {queued_job_count}, "
            f"queued_targets {queued_target_count}"
        ),
    ]
    queued_results = _required_object_list(summary, "queued_results")
    if not queued_results:
        lines.append("results: none")
        return "\n".join(lines)
    lines.append("results:")
    for queued_result in queued_results:
        job_ids = _required_string_list(queued_result, "job_ids")
        lines.append(
            (
                f"- source_cycle={queued_result['source_cycle_id']} "
                f"reason={queued_result['reason_code']} "
                f"targets={queued_result['target_count']} "
                f"queued_cycle={queued_result['queued_cycle_id']} "
                f"jobs={','.join(job_ids)}"
            )
        )
    return "\n".join(lines)


# Block: Job request builder
def _job_request(
    *,
    review_packet: dict[str, Any],
    annotation_template: dict[str, Any],
) -> dict[str, Any]:
    reason_code = _required_enum(
        annotation_template,
        "reason_code",
        QUARANTINE_REASON_CODES,
    )
    reason_note = _required_str(annotation_template, "reason_note")
    candidate_targets = _required_target_list(annotation_template, "candidate_targets")
    selected_targets = _required_target_list(annotation_template, "selected_targets")
    if not selected_targets:
        raise RuntimeError("annotation_template.selected_targets must not be empty when confirmed")
    candidate_target_keys = {
        (candidate_target["entity_type"], candidate_target["entity_id"])
        for candidate_target in candidate_targets
    }
    normalized_targets: list[dict[str, str]] = []
    seen_targets: set[tuple[str, str]] = set()
    for selected_target in selected_targets:
        target_key = (selected_target["entity_type"], selected_target["entity_id"])
        if target_key not in candidate_target_keys:
            raise RuntimeError("annotation_template.selected_targets must be subset of candidate_targets")
        if target_key in seen_targets:
            continue
        seen_targets.add(target_key)
        normalized_targets.append(
            {
                "entity_type": selected_target["entity_type"],
                "entity_id": selected_target["entity_id"],
            }
        )
    resolved_event_ids = _required_string_list(review_packet, "resolved_event_ids")
    if not resolved_event_ids:
        raise RuntimeError("review_packet.resolved_event_ids must not be empty when confirmed")
    return {
        "source_cycle_id": _required_str(review_packet, "cycle_id"),
        "source_event_ids": resolved_event_ids,
        "reason_code": reason_code,
        "reason_note": reason_note,
        "targets": normalized_targets,
    }


# Block: Required object
def _required_object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} must be object")
    return value


# Block: Required object list
def _required_object_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"{key} must be list")
    object_list: list[dict[str, Any]] = []
    for entry_value in value:
        if not isinstance(entry_value, dict):
            raise RuntimeError(f"{key} entries must be objects")
        object_list.append(entry_value)
    return object_list


# Block: Required target list
def _required_target_list(payload: dict[str, Any], key: str) -> list[dict[str, str]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"{key} must be list")
    target_list: list[dict[str, str]] = []
    for target_entry in value:
        if not isinstance(target_entry, dict):
            raise RuntimeError(f"{key} entries must be objects")
        target_list.append(
            {
                "entity_type": _required_str(target_entry, "entity_type"),
                "entity_id": _required_str(target_entry, "entity_id"),
            }
        )
    return target_list


# Block: Required string list
def _required_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RuntimeError(f"{key} must be list")
    string_list = [
        str(entry_value)
        for entry_value in value
        if isinstance(entry_value, str) and entry_value
    ]
    if len(string_list) != len(value):
        raise RuntimeError(f"{key} must contain non-empty strings only")
    return string_list


# Block: Required enum
def _required_enum(payload: dict[str, Any], key: str, allowed_values: tuple[str, ...]) -> str:
    value = _required_str(payload, key)
    if value not in allowed_values:
        raise RuntimeError(f"{key} is invalid")
    return value


# Block: Required string
def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"{key} must be non-empty string")
    return value


# Block: Required int
def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"{key} must be integer")
    return value
