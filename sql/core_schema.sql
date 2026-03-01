-- Block: SQLite runtime settings
PRAGMA foreign_keys = ON;

BEGIN IMMEDIATE;

-- Block: Runtime singleton state tables
CREATE TABLE self_state (
    row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
    personality_json TEXT NOT NULL,
    current_emotion_json TEXT NOT NULL,
    long_term_goals_json TEXT NOT NULL,
    relationship_overview_json TEXT NOT NULL,
    invariants_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE attention_state (
    row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
    primary_focus_json TEXT NOT NULL,
    secondary_focuses_json TEXT NOT NULL,
    suppressed_items_json TEXT NOT NULL,
    revisit_queue_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE body_state (
    row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
    posture_json TEXT NOT NULL,
    mobility_json TEXT NOT NULL,
    sensor_availability_json TEXT NOT NULL,
    output_locks_json TEXT NOT NULL,
    load_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE world_state (
    row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
    location_json TEXT NOT NULL,
    situation_summary TEXT NOT NULL,
    surroundings_json TEXT NOT NULL,
    affordances_json TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    attention_targets_json TEXT NOT NULL,
    external_waits_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE drive_state (
    row_id INTEGER PRIMARY KEY CHECK (row_id = 1),
    drive_levels_json TEXT NOT NULL,
    priority_effects_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Block: Runtime mutable state tables
CREATE TABLE task_state (
    task_id TEXT PRIMARY KEY,
    task_kind TEXT NOT NULL,
    task_status TEXT NOT NULL CHECK (
        task_status IN (
            'idle',
            'active',
            'waiting_external',
            'paused',
            'completed',
            'abandoned'
        )
    ),
    goal_hint TEXT NOT NULL,
    completion_hint_json TEXT NOT NULL,
    resume_condition_json TEXT NOT NULL,
    interruptible INTEGER NOT NULL CHECK (interruptible IN (0, 1)),
    priority INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    title TEXT,
    step_hints_json TEXT,
    deadline_at INTEGER,
    abandon_reason TEXT
);

CREATE INDEX idx_task_state_status_priority_updated
    ON task_state (task_status, priority DESC, updated_at DESC);

CREATE TABLE working_memory_items (
    slot_no INTEGER PRIMARY KEY CHECK (slot_no >= 0),
    item_kind TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    confidence REAL
);

CREATE TABLE recent_event_window_items (
    window_pos INTEGER PRIMARY KEY CHECK (window_pos >= 0),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('input_journal', 'event')),
    source_id TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    captured_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE skill_registry (
    skill_id TEXT PRIMARY KEY,
    trigger_pattern_json TEXT NOT NULL,
    preconditions_json TEXT NOT NULL,
    action_pattern_json TEXT NOT NULL,
    success_signature_json TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    summary_text TEXT,
    last_used_at INTEGER
);

CREATE INDEX idx_skill_registry_enabled_updated
    ON skill_registry (enabled, updated_at DESC);

-- Block: Control plane tables
CREATE TABLE pending_inputs (
    input_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel = 'browser_chat'),
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'claimed', 'consumed', 'discarded')
    ),
    claimed_at INTEGER,
    resolved_at INTEGER,
    discard_reason TEXT
);

CREATE INDEX idx_pending_inputs_status_priority_created
    ON pending_inputs (status, priority DESC, created_at ASC);

CREATE TABLE settings_overrides (
    override_id TEXT PRIMARY KEY,
    key TEXT NOT NULL,
    requested_value_json TEXT NOT NULL,
    apply_scope TEXT NOT NULL CHECK (apply_scope IN ('runtime', 'next_boot')),
    created_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'claimed', 'applied', 'rejected')
    ),
    claimed_at INTEGER,
    resolved_at INTEGER,
    reject_reason TEXT
);

CREATE INDEX idx_settings_overrides_status_created
    ON settings_overrides (status, created_at ASC);

CREATE TABLE ui_outbound_events (
    ui_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL CHECK (channel = 'browser_chat'),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('token', 'message', 'status', 'notice', 'error')
    ),
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    source_cycle_id TEXT
);

CREATE INDEX idx_ui_outbound_events_channel_event
    ON ui_outbound_events (channel, ui_event_id ASC);

-- Block: Observation and commit tables
CREATE TABLE input_journal (
    journal_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL UNIQUE,
    cycle_id TEXT NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    captured_at INTEGER NOT NULL,
    receipt_summary TEXT NOT NULL,
    payload_ref_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX idx_input_journal_cycle
    ON input_journal (cycle_id);

CREATE INDEX idx_input_journal_source_captured
    ON input_journal (source, captured_at DESC);

CREATE TABLE action_history (
    result_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    command_json TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER NOT NULL CHECK (finished_at >= started_at),
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'stopped')),
    failure_mode TEXT,
    observed_effects_json TEXT,
    raw_result_ref_json TEXT,
    adapter_trace_ref_json TEXT
);

CREATE INDEX idx_action_history_cycle
    ON action_history (cycle_id);

CREATE INDEX idx_action_history_status_finished
    ON action_history (status, finished_at DESC);

CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'observation',
            'action',
            'action_result',
            'internal_decision',
            'external_response'
        )
    ),
    searchable INTEGER NOT NULL CHECK (searchable IN (0, 1)),
    updated_at INTEGER,
    observation_summary TEXT,
    action_summary TEXT,
    result_summary TEXT,
    payload_ref_json TEXT,
    input_journal_refs_json TEXT
);

CREATE INDEX idx_events_cycle
    ON events (cycle_id);

CREATE INDEX idx_events_created
    ON events (created_at DESC);

CREATE INDEX idx_events_searchable_created
    ON events (searchable, created_at DESC);

CREATE INDEX idx_events_source_created
    ON events (source, created_at DESC);

CREATE TABLE commit_records (
    commit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL UNIQUE,
    committed_at INTEGER NOT NULL,
    log_sync_status TEXT NOT NULL CHECK (
        log_sync_status IN ('pending', 'synced', 'needs_replay')
    ),
    commit_payload_json TEXT NOT NULL,
    last_log_sync_error TEXT
);

CREATE INDEX idx_commit_records_sync
    ON commit_records (log_sync_status, committed_at ASC);

-- Block: Memory core tables
CREATE TABLE memory_states (
    memory_state_id TEXT PRIMARY KEY,
    memory_kind TEXT NOT NULL CHECK (
        memory_kind IN (
            'fact',
            'relation',
            'task',
            'summary',
            'long_mood_state',
            'reflection_note'
        )
    ),
    body_text TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    importance REAL NOT NULL,
    memory_strength REAL NOT NULL,
    searchable INTEGER NOT NULL CHECK (searchable IN (0, 1)),
    last_confirmed_at INTEGER NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    valid_from_ts INTEGER,
    valid_to_ts INTEGER,
    last_accessed_at INTEGER
);

CREATE INDEX idx_memory_states_kind_searchable_updated
    ON memory_states (memory_kind, searchable, updated_at DESC);

CREATE INDEX idx_memory_states_searchable_confirmed
    ON memory_states (searchable, last_confirmed_at DESC);

CREATE INDEX idx_memory_states_accessed
    ON memory_states (last_accessed_at DESC);

CREATE TABLE preference_memory (
    preference_id TEXT PRIMARY KEY,
    owner_scope TEXT NOT NULL CHECK (owner_scope IN ('self', 'other_entity')),
    target_entity_ref_json TEXT NOT NULL,
    domain TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK (polarity IN ('like', 'dislike')),
    status TEXT NOT NULL CHECK (status IN ('candidate', 'confirmed', 'revoked')),
    confidence REAL NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX idx_preference_memory_scope_status_updated
    ON preference_memory (owner_scope, status, updated_at DESC);

CREATE INDEX idx_preference_memory_domain_polarity_status
    ON preference_memory (domain, polarity, status);

CREATE TABLE event_affects (
    event_affect_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    moment_affect_text TEXT NOT NULL,
    moment_affect_labels_json TEXT NOT NULL,
    vad_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events (event_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX idx_event_affects_created
    ON event_affects (created_at DESC);

CREATE TABLE event_links (
    event_link_id TEXT PRIMARY KEY,
    from_event_id TEXT NOT NULL,
    to_event_id TEXT NOT NULL,
    label TEXT NOT NULL CHECK (
        label IN ('reply_to', 'same_topic', 'caused_by', 'continuation')
    ),
    confidence REAL NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (from_event_id, to_event_id, label),
    FOREIGN KEY (from_event_id) REFERENCES events (event_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    FOREIGN KEY (to_event_id) REFERENCES events (event_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX idx_event_links_from
    ON event_links (from_event_id);

CREATE INDEX idx_event_links_to
    ON event_links (to_event_id);

CREATE INDEX idx_event_links_label
    ON event_links (label);

CREATE TABLE event_threads (
    event_thread_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    thread_key TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    thread_role TEXT,
    UNIQUE (event_id, thread_key),
    FOREIGN KEY (event_id) REFERENCES events (event_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX idx_event_threads_event
    ON event_threads (event_id);

CREATE INDEX idx_event_threads_thread_key
    ON event_threads (thread_key);

CREATE TABLE state_links (
    state_link_id TEXT PRIMARY KEY,
    from_state_id TEXT NOT NULL,
    to_state_id TEXT NOT NULL,
    label TEXT NOT NULL CHECK (
        label IN ('relates_to', 'derived_from', 'supports', 'contradicts')
    ),
    confidence REAL NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (from_state_id, to_state_id, label),
    FOREIGN KEY (from_state_id) REFERENCES memory_states (memory_state_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,
    FOREIGN KEY (to_state_id) REFERENCES memory_states (memory_state_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX idx_state_links_from
    ON state_links (from_state_id);

CREATE INDEX idx_state_links_to
    ON state_links (to_state_id);

CREATE INDEX idx_state_links_label
    ON state_links (label);

CREATE TABLE event_entities (
    event_entity_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    entity_type_norm TEXT NOT NULL,
    entity_name_raw TEXT NOT NULL,
    entity_name_norm TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events (event_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX idx_event_entities_event
    ON event_entities (event_id);

CREATE INDEX idx_event_entities_norm
    ON event_entities (entity_type_norm, entity_name_norm);

CREATE TABLE state_entities (
    state_entity_id TEXT PRIMARY KEY,
    memory_state_id TEXT NOT NULL,
    entity_type_norm TEXT NOT NULL,
    entity_name_raw TEXT NOT NULL,
    entity_name_norm TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (memory_state_id) REFERENCES memory_states (memory_state_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX idx_state_entities_state
    ON state_entities (memory_state_id);

CREATE INDEX idx_state_entities_norm
    ON state_entities (entity_type_norm, entity_name_norm);

CREATE TABLE event_preview_cache (
    preview_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    preview_text TEXT NOT NULL,
    source_event_updated_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events (event_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);

CREATE INDEX idx_event_preview_cache_source_updated
    ON event_preview_cache (source_event_updated_at DESC);

CREATE TABLE revisions (
    revision_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_event_ids_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE INDEX idx_revisions_entity
    ON revisions (entity_type, entity_id, created_at DESC);

CREATE INDEX idx_revisions_created
    ON revisions (created_at DESC);

CREATE TABLE retrieval_runs (
    run_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    plan_json TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    selected_json TEXT NOT NULL,
    resolved_event_ids_json TEXT
);

CREATE INDEX idx_retrieval_runs_cycle
    ON retrieval_runs (cycle_id);

CREATE INDEX idx_retrieval_runs_created
    ON retrieval_runs (created_at DESC);

-- Block: Memory job tables
CREATE TABLE memory_jobs (
    job_id TEXT PRIMARY KEY,
    job_kind TEXT NOT NULL CHECK (
        job_kind IN (
            'write_memory',
            'refresh_preview',
            'embedding_sync',
            'tidy_memory',
            'quarantine_memory'
        )
    ),
    payload_ref_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'claimed', 'completed', 'dead_letter')
    ),
    tries INTEGER NOT NULL CHECK (tries >= 0),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    claimed_at INTEGER,
    completed_at INTEGER,
    last_error TEXT
);

CREATE INDEX idx_memory_jobs_status_created
    ON memory_jobs (status, created_at ASC);

CREATE INDEX idx_memory_jobs_kind_status_created
    ON memory_jobs (job_kind, status, created_at ASC);

CREATE TABLE memory_job_payloads (
    payload_id TEXT PRIMARY KEY,
    payload_kind TEXT NOT NULL CHECK (payload_kind = 'memory_job_payload'),
    payload_version INTEGER NOT NULL CHECK (payload_version >= 1),
    job_kind TEXT NOT NULL CHECK (
        job_kind IN (
            'write_memory',
            'refresh_preview',
            'embedding_sync',
            'tidy_memory',
            'quarantine_memory'
        )
    ),
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE INDEX idx_memory_job_payloads_kind_created
    ON memory_job_payloads (job_kind, created_at DESC);

-- Block: Search and derived index tables
CREATE TABLE vec_items (
    vec_item_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (
        entity_type IN ('event', 'memory_state', 'event_affect')
    ),
    entity_id TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_scope TEXT NOT NULL CHECK (embedding_scope IN ('recent', 'global')),
    searchable INTEGER NOT NULL CHECK (searchable IN (0, 1)),
    source_updated_at INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    UNIQUE (entity_type, entity_id, embedding_model, embedding_scope)
);

CREATE INDEX idx_vec_items_entity_searchable_updated
    ON vec_items (entity_type, searchable, source_updated_at DESC);

CREATE VIRTUAL TABLE events_fts USING fts5(
    event_id UNINDEXED,
    search_text
);

-- Block: FTS synchronization triggers
CREATE TRIGGER events_fts_after_insert
AFTER INSERT ON events
BEGIN
    INSERT INTO events_fts (rowid, event_id, search_text)
    VALUES (
        new.rowid,
        new.event_id,
        trim(
            coalesce(new.observation_summary, '') || ' ' ||
            coalesce(new.action_summary, '') || ' ' ||
            coalesce(new.result_summary, '')
        )
    );
END;

CREATE TRIGGER events_fts_after_update
AFTER UPDATE OF event_id, observation_summary, action_summary, result_summary ON events
BEGIN
    DELETE FROM events_fts
    WHERE rowid = old.rowid;

    INSERT INTO events_fts (rowid, event_id, search_text)
    VALUES (
        new.rowid,
        new.event_id,
        trim(
            coalesce(new.observation_summary, '') || ' ' ||
            coalesce(new.action_summary, '') || ' ' ||
            coalesce(new.result_summary, '')
        )
    );
END;

CREATE TRIGGER events_fts_after_delete
AFTER DELETE ON events
BEGIN
    DELETE FROM events_fts
    WHERE rowid = old.rowid;
END;

COMMIT;
