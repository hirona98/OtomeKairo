"""Deterministic end-to-end verification for the memory write job chain."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from otomekairo import __version__
from otomekairo.infra.sqlite_state_store import SqliteStateStore
from otomekairo.schema.runtime_types import ActionHistoryRecord
from otomekairo.schema.settings import build_default_settings


# Block: Report constants
REPORT_SCHEMA_VERSION = 1


# Block: Scripted cycle types
@dataclass(frozen=True, slots=True)
class ScriptedActionSpec:
    action_type: str
    status: str
    command: dict[str, Any]
    observed_effects: dict[str, Any] | None
    failure_mode: str | None = None


@dataclass(frozen=True, slots=True)
class ScriptedCycleSpec:
    cycle_id: str
    user_text: str
    assistant_text: str
    actions: tuple[ScriptedActionSpec, ...]


# Block: E2E entrypoint
def run_memory_write_e2e(*, db_path: Path) -> dict[str, Any]:
    if db_path.exists():
        raise RuntimeError("memory_write_e2e db_path must point to a new database")
    if db_path.parent.exists() is False:
        raise RuntimeError("memory_write_e2e db_path parent must exist")
    default_settings = build_default_settings()
    store = SqliteStateStore(
        db_path=db_path,
        initializer_version=__version__,
    )
    store.initialize()
    effective_settings = store.read_effective_settings(default_settings)
    embedding_model = _required_non_empty_string(
        effective_settings.get("llm.embedding_model"),
        "llm.embedding_model must be non-empty string",
    )
    cycle_reports: list[dict[str, Any]] = []
    for cycle_spec in _scripted_cycles():
        cycle_reports.append(
            _run_scripted_cycle(
                store=store,
                db_path=db_path,
                cycle_spec=cycle_spec,
                embedding_model=embedding_model,
            )
        )
    cognition_state = store.read_cognition_state(
        default_settings,
        observation_hint_text="2024-05-03 下北沢 展示",
    )
    report = _build_memory_write_e2e_report(
        db_path=db_path,
        cycle_reports=cycle_reports,
        cognition_state=cognition_state,
    )
    _validate_memory_write_e2e_report(report)
    return report


# Block: Scripted cycle execution
def _run_scripted_cycle(
    *,
    store: SqliteStateStore,
    db_path: Path,
    cycle_spec: ScriptedCycleSpec,
    embedding_model: str,
) -> dict[str, Any]:
    enqueue_result = store.enqueue_chat_message(
        text=cycle_spec.user_text,
        client_message_id=f"{cycle_spec.cycle_id}_client",
        attachments=[],
    )
    pending_input = store.claim_next_pending_input()
    if pending_input is None:
        raise RuntimeError("memory_write_e2e pending input was not queued")
    if str(enqueue_result["input_id"]) != pending_input.input_id:
        raise RuntimeError("memory_write_e2e claimed unexpected pending input")
    store.append_input_journal_for_pending_input(
        pending_input=pending_input,
        cycle_id=cycle_spec.cycle_id,
    )
    action_results = _action_results_for_cycle(
        cycle_spec=cycle_spec,
        created_at=pending_input.created_at,
    )
    ui_events = _assistant_ui_events_for_cycle(
        cycle_spec=cycle_spec,
        created_at=_response_created_at(action_results=action_results),
    )
    commit_id = store.finalize_pending_input_cycle(
        pending_input=pending_input,
        cycle_id=cycle_spec.cycle_id,
        resolution_status="consumed",
        action_results=action_results,
        task_mutations=[],
        pending_input_mutations=[],
        ui_events=ui_events,
        commit_payload={
            "cycle_kind": "short",
            "trigger_reason": "external_input",
            "processed_input_id": pending_input.input_id,
            "processed_input_kind": pending_input.payload["input_kind"],
            "emitted_event_types": [ui_event["event_type"] for ui_event in ui_events],
            "executed_action_types": [
                action_result.action_type
                for action_result in action_results
            ],
            "resolution_status": "consumed",
        },
        retrieval_run=None,
        attention_snapshot=None,
        camera_available=False,
    )
    drained_jobs = _drain_memory_jobs(
        store=store,
        embedding_model=embedding_model,
    )
    cycle_snapshot = _read_cycle_snapshot(
        db_path=db_path,
        cycle_id=cycle_spec.cycle_id,
    )
    return {
        "cycle_id": cycle_spec.cycle_id,
        "commit_id": commit_id,
        "user_text": cycle_spec.user_text,
        "assistant_text": cycle_spec.assistant_text,
        "event_count": cycle_snapshot["event_count"],
        "event_ids": cycle_snapshot["event_ids"],
        "drained_job_counts": drained_jobs["job_counts"],
        "drained_jobs": drained_jobs["jobs"],
        "preference_status_counts": cycle_snapshot["preference_status_counts"],
        "dialogue_thread_keys": cycle_snapshot["dialogue_thread_keys"],
        "active_preferences": cycle_snapshot["active_preferences"],
        "long_mood_primary_label": cycle_snapshot["long_mood_primary_label"],
    }


# Block: Action result build
def _action_results_for_cycle(
    *,
    cycle_spec: ScriptedCycleSpec,
    created_at: int,
) -> list[ActionHistoryRecord]:
    action_results: list[ActionHistoryRecord] = []
    for index, action_spec in enumerate(cycle_spec.actions, start=1):
        started_at = created_at + index * 10
        finished_at = started_at + 5
        action_results.append(
            ActionHistoryRecord(
                result_id=f"{cycle_spec.cycle_id}_result_{index}",
                command_id=f"{cycle_spec.cycle_id}_command_{index}",
                action_type=action_spec.action_type,
                command=dict(action_spec.command),
                started_at=started_at,
                finished_at=finished_at,
                status=action_spec.status,
                failure_mode=action_spec.failure_mode,
                observed_effects=(
                    dict(action_spec.observed_effects)
                    if action_spec.observed_effects is not None
                    else None
                ),
                raw_result_ref=None,
                adapter_trace_ref=None,
            )
        )
    return action_results


# Block: Assistant UI event build
def _assistant_ui_events_for_cycle(
    *,
    cycle_spec: ScriptedCycleSpec,
    created_at: int,
) -> list[dict[str, Any]]:
    return [
        {
            "event_type": "message",
            "payload": {
                "message_id": f"{cycle_spec.cycle_id}_assistant_message",
                "role": "assistant",
                "text": cycle_spec.assistant_text,
                "created_at": created_at,
            },
        }
    ]


# Block: Response timing
def _response_created_at(*, action_results: list[ActionHistoryRecord]) -> int:
    if not action_results:
        raise RuntimeError("memory_write_e2e requires at least one action result")
    return max(action_result.finished_at for action_result in action_results) + 1


# Block: Memory job drain
def _drain_memory_jobs(
    *,
    store: SqliteStateStore,
    embedding_model: str,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    job_counts: dict[str, int] = {}
    while True:
        memory_job = store.claim_next_memory_job()
        if memory_job is None:
            break
        if memory_job.job_kind == "write_memory":
            completed_ref = store.complete_write_memory_job(memory_job=memory_job)
        elif memory_job.job_kind == "refresh_preview":
            completed_ref = store.complete_refresh_preview_job(
                memory_job=memory_job,
                embedding_model=embedding_model,
            )
        elif memory_job.job_kind == "embedding_sync":
            completed_ref = store.complete_embedding_sync_job(memory_job=memory_job)
        else:
            raise RuntimeError(f"memory_write_e2e encountered unsupported job kind: {memory_job.job_kind}")
        job_counts[memory_job.job_kind] = job_counts.get(memory_job.job_kind, 0) + 1
        jobs.append(
            {
                "job_id": memory_job.job_id,
                "job_kind": memory_job.job_kind,
                "completed_ref": completed_ref,
            }
        )
    return {
        "job_counts": job_counts,
        "jobs": jobs,
    }


# Block: Cycle snapshot read
def _read_cycle_snapshot(
    *,
    db_path: Path,
    cycle_id: str,
) -> dict[str, Any]:
    with _connect_row_db(db_path) as connection:
        commit_row = connection.execute(
            """
            SELECT commit_id, commit_payload_json
            FROM commit_records
            WHERE cycle_id = ?
            ORDER BY commit_id DESC
            LIMIT 1
            """,
            (cycle_id,),
        ).fetchone()
        if commit_row is None:
            raise RuntimeError("memory_write_e2e commit record is missing")
        commit_payload = json.loads(commit_row["commit_payload_json"])
        if not isinstance(commit_payload, dict):
            raise RuntimeError("memory_write_e2e commit_payload_json must decode to object")
        event_ids = _required_string_list(
            commit_payload.get("event_ids"),
            "memory_write_e2e commit_payload.event_ids must be string array",
        )
        preference_rows = connection.execute(
            """
            SELECT
                domain,
                target_entity_ref_json,
                polarity,
                status,
                confidence
            FROM preference_memory
            ORDER BY domain ASC, polarity ASC, updated_at ASC
            """
        ).fetchall()
        long_mood_row = connection.execute(
            """
            SELECT payload_json
            FROM memory_states
            WHERE memory_kind = 'long_mood_state'
            ORDER BY updated_at DESC, created_at DESC, memory_state_id DESC
            LIMIT 1
            """
        ).fetchone()
    return {
        "event_count": len(event_ids),
        "event_ids": event_ids,
        "preference_status_counts": _count_rows_by_key(
            rows=preference_rows,
            key_name="status",
        ),
        "dialogue_thread_keys": _read_cycle_dialogue_thread_keys(
            connection=connection,
            event_ids=event_ids,
        ),
        "active_preferences": [
            _preference_row_summary(row)
            for row in preference_rows
            if str(row["status"]) in {"candidate", "confirmed"}
        ],
        "long_mood_primary_label": (
            _read_long_mood_primary_label(long_mood_row["payload_json"])
            if long_mood_row is not None
            else None
        ),
    }


# Block: Report build
def _build_memory_write_e2e_report(
    *,
    db_path: Path,
    cycle_reports: list[dict[str, Any]],
    cognition_state: Any,
) -> dict[str, Any]:
    database_counts = _read_database_counts(db_path=db_path)
    relationship_items = cognition_state.memory_snapshot["relationship_items"]
    affective_items = cognition_state.memory_snapshot["affective_items"]
    active_preferences = [
        _active_preference_snapshot_entry(relationship_item)
        for relationship_item in relationship_items
        if relationship_item["memory_kind"] == "preference"
    ]
    long_mood_state = _latest_long_mood_snapshot(affective_items)
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "db_path": str(db_path),
        "cycle_count": len(cycle_reports),
        "cycle_reports": cycle_reports,
        "job_totals": _sum_cycle_job_counts(cycle_reports),
        "database_counts": database_counts,
        "snapshot_summary": {
            "working_memory_count": len(cognition_state.memory_snapshot["working_memory_items"]),
            "semantic_memory_count": len(cognition_state.memory_snapshot["semantic_items"]),
            "affective_memory_count": len(cognition_state.memory_snapshot["affective_items"]),
            "relationship_memory_count": len(relationship_items),
            "reflection_memory_count": len(cognition_state.memory_snapshot["reflection_items"]),
            "confirmed_preferences": [
                active_preference
                for active_preference in active_preferences
                if active_preference["status"] == "confirmed"
            ],
            "long_mood_state": long_mood_state,
            "current_emotion": cognition_state.self_state["current_emotion"],
        },
    }
    report["checks"] = _build_report_checks(report)
    return report


# Block: Report checks
def _build_report_checks(report: dict[str, Any]) -> dict[str, bool]:
    database_counts = report["database_counts"]
    job_totals = report["job_totals"]
    snapshot_summary = report["snapshot_summary"]
    checks = {
        "write_memory_jobs_completed": int(job_totals.get("write_memory", 0)) == 4,
        "refresh_preview_jobs_completed": (
            int(job_totals.get("refresh_preview", 0))
            == int(database_counts["event_count"])
        ),
        "embedding_sync_jobs_completed": (
            int(job_totals.get("embedding_sync", 0))
            == int(job_totals.get("write_memory", 0)) + int(job_totals.get("refresh_preview", 0))
        ),
        "summary_memory_created": int(database_counts["memory_state_kind_counts"].get("summary", 0)) >= 4,
        "fact_memory_created": int(database_counts["memory_state_kind_counts"].get("fact", 0)) >= 4,
        "reflection_memory_created": (
            int(database_counts["memory_state_kind_counts"].get("reflection_note", 0)) >= 4
        ),
        "long_mood_state_upserted": (
            int(database_counts["memory_state_kind_counts"].get("long_mood_state", 0)) == 1
        ),
        "preview_cache_filled": (
            int(database_counts["event_preview_count"])
            == int(database_counts["event_count"])
        ),
        "embedding_targets_materialized": (
            int(database_counts["vec_item_counts"].get("event", 0)) > 0
            and int(database_counts["vec_item_counts"].get("memory_state", 0)) > 0
            and int(database_counts["vec_item_counts"].get("event_affect", 0)) > 0
        ),
        "graph_context_materialized": (
            int(database_counts["event_link_count"]) > 0
            and int(database_counts["event_thread_count"]) >= int(database_counts["event_count"])
            and int(database_counts["state_link_count"]) > 0
        ),
        "dialogue_thread_continuity_materialized": (
            bool(database_counts["dialogue_threads"])
            and int(database_counts["dialogue_threads"][0]["cycle_count"]) >= 4
        ),
        "about_time_materialized": (
            int(database_counts["event_about_time_count"]) > 0
            and int(database_counts["state_about_time_count"]) > 0
        ),
        "preference_lifecycle_materialized": (
            int(database_counts["preference_status_counts"].get("confirmed", 0)) == 2
            and int(database_counts["preference_status_counts"].get("revoked", 0)) == 2
        ),
        "confirmed_preferences_visible": len(snapshot_summary["confirmed_preferences"]) == 2,
    }
    return checks


# Block: Report validation
def _validate_memory_write_e2e_report(report: dict[str, Any]) -> None:
    failed_checks = [
        check_name
        for check_name, check_value in report["checks"].items()
        if check_value is not True
    ]
    if failed_checks:
        raise RuntimeError(
            "memory_write_e2e checks failed: " + ", ".join(sorted(failed_checks))
        )


# Block: Report formatting
def format_memory_write_e2e_report(report: dict[str, Any]) -> str:
    database_counts = report["database_counts"]
    snapshot_summary = report["snapshot_summary"]
    lines = [
        "memory write e2e",
        f"db: {report['db_path']}",
        f"cycles: {report['cycle_count']}",
        (
            "jobs: "
            f"write_memory {report['job_totals'].get('write_memory', 0)}, "
            f"refresh_preview {report['job_totals'].get('refresh_preview', 0)}, "
            f"embedding_sync {report['job_totals'].get('embedding_sync', 0)}"
        ),
        (
            "database: "
            f"events {database_counts['event_count']}, "
            f"previews {database_counts['event_preview_count']}, "
            f"vec_items {database_counts['vec_item_count']}, "
            f"event_links {database_counts['event_link_count']}, "
            f"state_links {database_counts['state_link_count']}, "
            f"dialogue_threads {len(database_counts['dialogue_threads'])}"
        ),
        (
            "memory_states: "
            f"summary {database_counts['memory_state_kind_counts'].get('summary', 0)}, "
            f"fact {database_counts['memory_state_kind_counts'].get('fact', 0)}, "
            f"reflection {database_counts['memory_state_kind_counts'].get('reflection_note', 0)}, "
            f"long_mood {database_counts['memory_state_kind_counts'].get('long_mood_state', 0)}"
        ),
        (
            "preferences: "
            f"confirmed {database_counts['preference_status_counts'].get('confirmed', 0)}, "
            f"candidate {database_counts['preference_status_counts'].get('candidate', 0)}, "
            f"revoked {database_counts['preference_status_counts'].get('revoked', 0)}"
        ),
        (
            "snapshot: "
            f"confirmed_preferences {len(snapshot_summary['confirmed_preferences'])}, "
            f"long_mood {snapshot_summary['long_mood_state']}"
        ),
        "checks: " + ", ".join(
            sorted(
                check_name
                for check_name, check_value in report["checks"].items()
                if check_value
            )
        ),
    ]
    return "\n".join(lines)


# Block: Database counts read
def _read_database_counts(*, db_path: Path) -> dict[str, Any]:
    with _connect_row_db(db_path) as connection:
        event_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM events",
        )
        event_preview_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM event_preview_cache",
        )
        vec_item_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM vec_items",
        )
        event_link_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM event_links",
        )
        event_thread_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM event_threads",
        )
        state_link_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM state_links",
        )
        event_about_time_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM event_about_time",
        )
        state_about_time_count = _read_single_count(
            connection=connection,
            sql="SELECT COUNT(*) FROM state_about_time",
        )
        memory_state_kind_counts = _read_group_counts(
            connection=connection,
            sql="""
            SELECT memory_kind, COUNT(*) AS item_count
            FROM memory_states
            GROUP BY memory_kind
            """,
            key_name="memory_kind",
        )
        preference_status_counts = _read_group_counts(
            connection=connection,
            sql="""
            SELECT status, COUNT(*) AS item_count
            FROM preference_memory
            GROUP BY status
            """,
            key_name="status",
        )
        vec_item_counts = _read_group_counts(
            connection=connection,
            sql="""
            SELECT entity_type, COUNT(*) AS item_count
            FROM vec_items
            GROUP BY entity_type
            """,
            key_name="entity_type",
        )
        dialogue_threads = [
            {
                "thread_key": str(row["thread_key"]),
                "cycle_count": int(row["cycle_count"]),
                "event_count": int(row["event_count"]),
            }
            for row in connection.execute(
                """
                SELECT
                    event_threads.thread_key,
                    COUNT(DISTINCT events.cycle_id) AS cycle_count,
                    COUNT(DISTINCT event_threads.event_id) AS event_count
                FROM event_threads
                INNER JOIN events
                        ON events.event_id = event_threads.event_id
                WHERE event_threads.thread_key LIKE 'dialogue:%'
                GROUP BY event_threads.thread_key
                ORDER BY cycle_count DESC, event_count DESC, event_threads.thread_key ASC
                """
            ).fetchall()
        ]
    return {
        "event_count": event_count,
        "event_preview_count": event_preview_count,
        "vec_item_count": vec_item_count,
        "event_link_count": event_link_count,
        "event_thread_count": event_thread_count,
        "state_link_count": state_link_count,
        "event_about_time_count": event_about_time_count,
        "state_about_time_count": state_about_time_count,
        "memory_state_kind_counts": memory_state_kind_counts,
        "preference_status_counts": preference_status_counts,
        "vec_item_counts": vec_item_counts,
        "dialogue_threads": dialogue_threads,
    }


# Block: Single count read
def _read_single_count(
    *,
    connection: sqlite3.Connection,
    sql: str,
) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise RuntimeError("memory_write_e2e count query returned no rows")
    return int(row[0])


# Block: Group counts read
def _read_group_counts(
    *,
    connection: sqlite3.Connection,
    sql: str,
    key_name: str,
) -> dict[str, int]:
    rows = connection.execute(sql).fetchall()
    return {
        str(row[key_name]): int(row["item_count"])
        for row in rows
    }


# Block: Row count helper
def _count_rows_by_key(
    *,
    rows: list[sqlite3.Row],
    key_name: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row[key_name])
        counts[key] = counts.get(key, 0) + 1
    return counts


# Block: Preference row summary
def _preference_row_summary(row: sqlite3.Row) -> dict[str, Any]:
    target_entity_ref = json.loads(row["target_entity_ref_json"])
    if not isinstance(target_entity_ref, dict):
        raise RuntimeError("preference target_entity_ref_json must decode to object")
    return {
        "domain": str(row["domain"]),
        "target_key": _required_non_empty_string(
            target_entity_ref.get("target_key"),
            "preference target_key must be non-empty string",
        ),
        "polarity": str(row["polarity"]),
        "status": str(row["status"]),
        "confidence": float(row["confidence"]),
    }


# Block: Active preference snapshot entry
def _active_preference_snapshot_entry(relationship_item: dict[str, Any]) -> dict[str, Any]:
    payload = relationship_item["payload"]
    target_entity_ref = payload["target_entity_ref"]
    if not isinstance(target_entity_ref, dict):
        raise RuntimeError("relationship_item.payload.target_entity_ref must be object")
    return {
        "domain": str(payload["domain"]),
        "target_key": _required_non_empty_string(
            target_entity_ref.get("target_key"),
            "relationship_item target_key must be non-empty string",
        ),
        "polarity": str(payload["polarity"]),
        "status": str(payload["status"]),
        "confidence": float(relationship_item["confidence"]),
    }


# Block: Long mood snapshot pick
def _latest_long_mood_snapshot(affective_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    long_mood_items = [
        affective_item
        for affective_item in affective_items
        if affective_item["memory_kind"] == "long_mood_state"
    ]
    if not long_mood_items:
        return None
    latest_item = sorted(
        long_mood_items,
        key=lambda affective_item: int(affective_item["updated_at"]),
        reverse=True,
    )[0]
    payload = latest_item["payload"]
    if not isinstance(payload, dict):
        raise RuntimeError("long_mood_state payload must be object")
    return {
        "primary_label": str(payload["primary_label"]),
        "labels": list(payload["labels"]),
        "stability": float(payload["stability"]),
    }


# Block: Job count sum
def _sum_cycle_job_counts(cycle_reports: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for cycle_report in cycle_reports:
        drained_job_counts = cycle_report["drained_job_counts"]
        for job_kind, count in drained_job_counts.items():
            totals[str(job_kind)] = totals.get(str(job_kind), 0) + int(count)
    return totals


# Block: Long mood label read
def _read_long_mood_primary_label(payload_json: str) -> str:
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        raise RuntimeError("long_mood payload_json must decode to object")
    return _required_non_empty_string(
        payload.get("primary_label"),
        "long_mood payload.primary_label must be non-empty string",
    )


# Block: Cycle dialogue thread keys
def _read_cycle_dialogue_thread_keys(
    *,
    connection: sqlite3.Connection,
    event_ids: list[str],
) -> list[str]:
    if not event_ids:
        return []
    placeholders = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        f"""
        SELECT thread_key
        FROM event_threads
        WHERE event_id IN ({placeholders})
          AND thread_key LIKE 'dialogue:%'
        ORDER BY created_at DESC, thread_key ASC
        """,
        tuple(event_ids),
    ).fetchall()
    dialogue_thread_keys: list[str] = []
    for row in rows:
        thread_key = str(row["thread_key"])
        if thread_key not in dialogue_thread_keys:
            dialogue_thread_keys.append(thread_key)
    return dialogue_thread_keys


# Block: SQLite row connection
def _connect_row_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


# Block: String helpers
def _required_non_empty_string(value: Any, error_text: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(error_text)
    return value


def _required_string_list(value: Any, error_text: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(error_text)
    normalized: list[str] = []
    for item in value:
        normalized.append(_required_non_empty_string(item, error_text))
    return normalized


# Block: Scripted scenario
def _scripted_cycles() -> tuple[ScriptedCycleSpec, ...]:
    return (
        ScriptedCycleSpec(
            cycle_id="cycle_memory_e2e_01",
            user_text="2024-05-03 の下北沢の展示を調べて教えて。",
            assistant_text="2024-05-03 の下北沢の展示は駅近の会場で 11 時開始だったよ。",
            actions=(
                ScriptedActionSpec(
                    action_type="complete_browse_task",
                    status="succeeded",
                    command={
                        "parameters": {"query": "下北沢 展示 2024-05-03"},
                        "related_task_id": "task_browse_e2e_01",
                    },
                    observed_effects={
                        "summary_text": "2024-05-03 の下北沢展示は駅近の会場で 11 時開始",
                    },
                ),
            ),
        ),
        ScriptedCycleSpec(
            cycle_id="cycle_memory_e2e_02",
            user_text="その調べ方、また同じ感じでお願い。",
            assistant_text="同じやり方で、2024-05-03 の展示情報をもう一度整理したよ。",
            actions=(
                ScriptedActionSpec(
                    action_type="complete_browse_task",
                    status="succeeded",
                    command={
                        "parameters": {"query": "下北沢 展示 2024-05-03 アクセス"},
                        "related_task_id": "task_browse_e2e_02",
                    },
                    observed_effects={
                        "summary_text": "2024-05-03 の展示会場は駅から近く、入場案内も確認できた",
                    },
                ),
            ),
        ),
        ScriptedCycleSpec(
            cycle_id="cycle_memory_e2e_03",
            user_text="もう一度検索して。タイムアウトしたらそのまま知らせて。",
            assistant_text="検索を試したけど timeout で止まった。少し待ってやり直すね。",
            actions=(
                ScriptedActionSpec(
                    action_type="enqueue_browse_task",
                    status="failed",
                    command={
                        "parameters": {"query": "下北沢 展示 2024-05-03 再検索"},
                        "related_task_id": "task_browse_e2e_03",
                    },
                    observed_effects={"error": "timeout"},
                    failure_mode="timeout",
                ),
            ),
        ),
        ScriptedCycleSpec(
            cycle_id="cycle_memory_e2e_04",
            user_text="再検索を二段で進めて、確実な方を採用して。",
            assistant_text="再検索を二段で進めて、2024-05-03 の会場と開始時刻を確認し直したよ。",
            actions=(
                ScriptedActionSpec(
                    action_type="complete_browse_task",
                    status="succeeded",
                    command={
                        "parameters": {"query": "下北沢 展示 2024-05-03 会場"},
                        "related_task_id": "task_browse_e2e_04a",
                    },
                    observed_effects={
                        "summary_text": "2024-05-03 の会場候補を再確認し、駅近の会場が有力だった",
                    },
                ),
                ScriptedActionSpec(
                    action_type="complete_browse_task",
                    status="succeeded",
                    command={
                        "parameters": {"query": "下北沢 展示 2024-05-03 開始時刻"},
                        "related_task_id": "task_browse_e2e_04b",
                    },
                    observed_effects={
                        "summary_text": "2024-05-03 の開始時刻は 11 時で案内が一致した",
                    },
                ),
            ),
        ),
    )
