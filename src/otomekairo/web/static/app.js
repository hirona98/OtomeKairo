const state = {
  identity: null,
  clientId: "",
  conversationPersonRef: "",
  conversationInteractionRef: "",
  conversationDisplayName: "",
  selectedPersonaDisplayName: "",
  editor: null,
  avatarSpeech: null,
  consoleClient: null,
  camera: null,
  mcp: null,
  apiDocs: null,
  audioInputDevices: null,
  speakers: [],
  audioRuntime: null,
  activeEnrollment: null,
  selectedAvatarId: "",
  selectedPersonaId: "",
  selectedModelPresetId: "",
  selectedMemorySetId: "",
  selectedCameraId: "",
  selectedWatcherSourceId: "",
  selectedMcpId: "",
  attachment: null,
  settingsOpen: false,
  sending: false,
  dashboard: {
    currentState: null,
    cycleSummaries: [],
  },
  dashboardRefreshing: false,
  dashboardTimer: null,
  eventSocket: null,
  eventReconnectTimer: null,
  assistantAudio: {
    pendingMetadata: null,
    playbackTail: Promise.resolve(),
  },
  webAudio: {
    socket: null,
    stream: null,
    context: null,
    sourceNode: null,
    workletNode: null,
    heartbeatTimer: null,
    leaseGeneration: null,
    paused: true,
    starting: false,
    stopping: false,
    startSequence: 0,
  },
  unloading: false,
};

const RUN_STATUS_LABELS = {
  active: "実行中",
  waiting_timer: "時刻待ち",
  waiting_result: "結果待ち",
  paused: "一時停止",
  completed: "完了",
  cancelled: "取消済み",
};

const CAPABILITY_REASON_LABELS = {
  no_binding: "未接続",
  permission_denied: "権限不足",
  paused: "一時停止",
  busy: "実行中",
  unavailable: "利用不可",
  dispatch_failed: "配送失敗",
  request_timeout: "応答待ち超過",
  parallel_blocked: "別の実行を待機",
  camera_source_disabled: "カメラ無効",
  no_vision_source: "視覚ソースなし",
  no_supported_control: "操作対象なし",
  no_mcp_tool: "許可済みtoolなし",
};

const TRIGGER_KIND_LABELS = {
  user_message: "対話入力",
  wake: "起床",
  background_thinking: "定期思考",
  capability_result: "能力結果",
  autonomous_run: "自律実行",
};

const RESULT_KIND_LABELS = {
  speech: "発話",
  capability_request: "能力要求",
  autonomous_run: "自律実行",
  noop: "変化なし",
  skipped: "見送り",
  failed: "失敗",
};

const DESKTOP_WAKE_OBSERVATION_ID = "observation:main_desktop";
const DEFAULT_WAKE_INTERVAL_SECONDS = 300;
const WEB_MICROPHONE_DEVICE_KEY = "otomekairo.web_microphone_device_id";

function element(id) {
  return document.getElementById(id);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function nowLabel() {
  return new Intl.DateTimeFormat("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
}

function setStatus(text, kind) {
  const status = element("connection-status");
  status.textContent = text;
  status.className = `status ${kind || ""}`.trim();
}

function showNotice(message, isError = false) {
  const notice = element("notice");
  notice.textContent = message;
  notice.className = isError ? "notice error" : "notice";
  notice.hidden = false;
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => {
    notice.hidden = true;
  }, 5000);
}

async function apiRequest(path, options = {}) {
  const headers = {
    ...(options.headers || {}),
  };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const payload = await response.json();
  if (!payload.ok) {
    const code = payload.error?.code || `http_${response.status}`;
    const message = payload.error?.message || "API request failed.";
    const error = new Error(`${code}: ${message}`);
    error.code = code;
    error.status = response.status;
    throw error;
  }
  return payload.data;
}

function setSelectOptions(select, items, idKey, selectedId) {
  select.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item[idKey];
    option.textContent = item.display_name || item.label || item[idKey];
    select.append(option);
  }
  select.value = selectedId || items[0]?.[idKey] || "";
}

function idSuffix() {
  return crypto.randomUUID ? crypto.randomUUID().replaceAll("-", "") : String(Date.now());
}

function initializeClientId() {
  const storedClientId = sessionStorage.getItem("otomekairo.client_id");
  state.clientId = storedClientId || `web-ui:${idSuffix()}`;
  sessionStorage.setItem("otomekairo.client_id", state.clientId);
}

function loadConversationIdentity() {
  const generatedId = idSuffix();
  const personRef = localStorage.getItem("otomekairo.person_ref") || `person:web:${generatedId}`;
  const interactionRef = localStorage.getItem("otomekairo.interaction_ref")
    || `interaction:web:direct:${personRef.slice("person:".length)}`;
  state.conversationPersonRef = personRef;
  state.conversationInteractionRef = interactionRef;
  localStorage.setItem("otomekairo.person_ref", personRef);
  localStorage.setItem("otomekairo.interaction_ref", interactionRef);
}

async function loadConversationConfig() {
  try {
    const config = await apiRequest("/ui/api/config");
    state.conversationDisplayName =
      config.settings_snapshot.conversation_display_name || "";
    state.selectedPersonaDisplayName =
      config.selected_persona?.display_name || "";
  } catch (error) {
    showNotice(error.message, true);
  }
}

function arrayById(items, idKey, id) {
  return (items || []).find((item) => item[idKey] === id) || null;
}

function removeById(items, idKey, id) {
  const index = items.findIndex((item) => item[idKey] === id);
  if (index >= 0) {
    items.splice(index, 1);
  }
}

function selectedOrFirst(items, idKey, selectedId) {
  if (items.some((item) => item[idKey] === selectedId)) {
    return selectedId;
  }
  return items[0]?.[idKey] || "";
}

function textValue(id) {
  return element(id).value;
}

function intValue(id, fallback = 1) {
  const value = Number.parseInt(element(id).value, 10);
  return Number.isFinite(value) ? value : fallback;
}

// APIへ送る前に整数契約を検証し、小数の切り捨てを防ぐ。
function boundedIntValue(id, label, min, max = null) {
  const value = Number(textValue(id));
  const isInRange = value >= min && (max === null || value <= max);
  if (!Number.isInteger(value) || !isInRange) {
    const range = max === null ? `${min}以上` : `${min}以上${max}以下`;
    throw new Error(`${label}には${range}の整数を入力してください。`);
  }
  return value;
}

function numberValue(id, fallback = 1) {
  const value = Number.parseFloat(element(id).value);
  return Number.isFinite(value) ? value : fallback;
}

function boolValue(id) {
  return element(id).checked;
}

function parseLines(value) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function parseEnv(value) {
  const result = {};
  for (const line of value.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    const separator = trimmed.indexOf("=");
    if (separator < 1) {
      throw new Error("Env は KEY=value 形式で入力してください。");
    }
    result[trimmed.slice(0, separator).trim()] = trimmed.slice(separator + 1);
  }
  return result;
}

function formatEnv(value) {
  return Object.entries(value || {})
    .map(([key, envValue]) => `${key}=${envValue}`)
    .join("\n");
}

async function loadIdentity() {
  try {
    state.identity = await apiRequest("/ui/api/bootstrap/server-identity");
    element("server-summary").textContent = state.identity.server_display_name || state.identity.server_id || "OtomeKairo";
    setStatus("接続済み");
  } catch (error) {
    setStatus("接続失敗", "error");
    showNotice(error.message, true);
  }
}

async function loadStatus({ silent = false } = {}) {
  try {
    const data = await apiRequest("/ui/api/status");
    const runtime = data.runtime_summary || {};
    setStatus(runtime.connection_state === "ready" ? "接続済み" : String(runtime.connection_state || "接続中"));
    if (!silent) {
      showNotice("現在状態を読み込みました。");
    }
  } catch (error) {
    setStatus("API失敗", "error");
    showNotice(error.message, true);
  }
}

function displayValue(value, fallback = "—") {
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return fallback;
}

function formatDateTime(value) {
  if (typeof value !== "string" || !value.trim()) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("ja-JP", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(parsed);
}

function formatDuration(startedAt, finishedAt) {
  const started = new Date(startedAt);
  const finished = new Date(finishedAt);
  if (Number.isNaN(started.getTime()) || Number.isNaN(finished.getTime())) {
    return "";
  }
  const milliseconds = Math.max(0, finished.getTime() - started.getTime());
  return milliseconds < 1000 ? `${milliseconds}ms` : `${(milliseconds / 1000).toFixed(1)}秒`;
}

function formatScore(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
}

function setDashboardMetric(id, value, kind = "") {
  const metric = element(id);
  metric.textContent = value;
  metric.className = `dashboard-metric-value ${kind}`.trim();
}

// inspection の正本 shape を表示専用の小さなカードへ投影する。
function showDashboardEmpty(container, text) {
  const empty = document.createElement("div");
  empty.className = "dashboard-empty";
  empty.textContent = text;
  container.replaceChildren(empty);
}

function createDashboardItem({
  title,
  badge = "",
  badgeKind = "",
  body = "",
  meta = "",
  itemKind = "",
}) {
  const item = document.createElement("article");
  item.className = `dashboard-item ${itemKind}`.trim();

  const heading = document.createElement("div");
  heading.className = "dashboard-item-heading";
  const titleElement = document.createElement("div");
  titleElement.className = "dashboard-item-title";
  titleElement.textContent = title;
  heading.append(titleElement);
  if (badge) {
    const badgeElement = document.createElement("span");
    badgeElement.className = `dashboard-badge ${badgeKind}`.trim();
    badgeElement.textContent = badge;
    heading.append(badgeElement);
  }
  item.append(heading);

  if (body) {
    const bodyElement = document.createElement("div");
    bodyElement.className = "dashboard-item-body";
    bodyElement.textContent = body;
    item.append(bodyElement);
  }
  if (meta) {
    const metaElement = document.createElement("div");
    metaElement.className = "dashboard-item-meta";
    metaElement.textContent = meta;
    item.append(metaElement);
  }
  return item;
}

function runBadgeKind(status) {
  if (status === "completed") {
    return "ok";
  }
  if (status === "cancelled") {
    return "error";
  }
  if (status === "waiting_timer" || status === "waiting_result" || status === "paused") {
    return "waiting";
  }
  return "";
}

function renderDashboardOverview() {
  const snapshot = state.dashboard.currentState || {};
  const runtime = snapshot.runtime_summary || {};
  const current = snapshot.current_state || {};
  const capabilities = snapshot.capability_inspection?.capabilities || [];
  const foregroundWorldStates = current.foreground_world_states || [];
  const runtimeReady = runtime.connection_state === "ready";
  const nonTerminalRunCount = (
    (runtime.active_autonomous_run_count || 0)
    + (runtime.paused_autonomous_run_count || 0)
  );
  const availableCapabilities = capabilities.filter((capability) => capability.available === true);

  setDashboardMetric(
    "dashboard-runtime-state",
    runtimeReady ? "稼働中" : displayValue(runtime.connection_state),
    runtimeReady ? "ok" : "error",
  );
  setDashboardMetric("dashboard-run-count", String(nonTerminalRunCount));
  setDashboardMetric("dashboard-capability-count", `${availableCapabilities.length}/${capabilities.length}`);
  setDashboardMetric("dashboard-foreground-count", String(foregroundWorldStates.length));
  element("dashboard-generated-at").textContent = `更新 ${formatDateTime(snapshot.generated_at)}`;
  if (state.identity) {
    element("server-summary").textContent = [
      state.identity.server_display_name || state.identity.server_id,
      state.selectedPersonaDisplayName,
    ].filter(Boolean).join(" · ");
  }
}

function renderDashboardCurrentState() {
  const container = element("dashboard-current-state");
  const runtime = state.dashboard.currentState?.runtime_summary || {};
  const current = state.dashboard.currentState?.current_state || {};
  const items = [
    createDashboardItem({
      title: "Runtime",
      badge: runtime.connection_state === "ready" ? "稼働中" : displayValue(runtime.connection_state),
      badgeKind: runtime.connection_state === "ready" ? "ok" : "error",
      body: [
        `定期思考 ${runtime.background_thinking_scheduler_active ? "稼働" : "停止"}`,
        `自律実行 ${runtime.autonomous_run_scheduler_active ? "稼働" : "停止"}`,
      ].join(" · "),
      meta: `記憶job待ち ${runtime.pending_memory_job_count || 0}`,
    }),
  ];
  const ongoingAction = current.ongoing_action;
  if (ongoingAction) {
    items.push(createDashboardItem({
      title: "継続行動",
      badge: displayValue(ongoingAction.status),
      badgeKind: ongoingAction.status === "failed" ? "error" : "waiting",
      body: displayValue(ongoingAction.goal_summary || ongoingAction.step_summary),
      meta: [
        ongoingAction.step_summary,
        ongoingAction.last_capability_id,
        formatDateTime(ongoingAction.updated_at),
      ].filter(Boolean).join(" · "),
    }));
  }

  const mood = current.mood_state;
  const currentVad = mood?.current_vad;
  if (currentVad) {
    items.push(createDashboardItem({
      title: "気分状態",
      body: `valence ${formatScore(currentVad.v)} · arousal ${formatScore(currentVad.a)} · dominance ${formatScore(currentVad.d)}`,
      meta: `更新 ${formatDateTime(mood.updated_at)}`,
    }));
  }

  for (const activityContext of (current.activity_contexts || []).slice(0, 3)) {
    const activity = activityContext.current_activity;
    if (!activity) {
      continue;
    }
    items.push(createDashboardItem({
      title: `活動 · ${displayValue(activity.actor, "主体")}`,
      body: displayValue(activity.label || activity.reason_summary),
      meta: [
        activity.target ? `対象 ${activity.target}` : "",
        activity.duration_label,
        activity.age_label,
      ].filter(Boolean).join(" · "),
    }));
  }

  for (const drive of (current.drive_states || []).slice(0, 3)) {
    items.push(createDashboardItem({
      title: `動機 · ${displayValue(drive.drive_kind, "未分類")}`,
      body: displayValue(drive.summary_text),
      meta: `salience ${formatScore(drive.salience)} · 更新 ${formatDateTime(drive.updated_at)}`,
    }));
  }

  for (const worldState of (current.foreground_world_states || []).slice(0, 5)) {
    const scope = [worldState.scope_type, worldState.scope_key].filter(Boolean).join(":");
    items.push(createDashboardItem({
      title: `前景 · ${displayValue(worldState.state_type, "world_state")}`,
      body: displayValue(worldState.summary_text),
      meta: [
        scope,
        `salience ${formatScore(worldState.salience)}`,
        formatDateTime(worldState.updated_at || worldState.observed_at),
      ].filter(Boolean).join(" · "),
    }));
  }

  container.replaceChildren(...items);
}

function createRunAction(label, action, runId, { danger = false } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.dataset.runAction = action;
  button.dataset.runId = runId;
  if (danger) {
    button.className = "danger-button";
  }
  return button;
}

function renderDashboardRuns() {
  const container = element("dashboard-runs");
  const runs = state.dashboard.currentState?.current_state?.autonomous_runs || [];
  if (!runs.length) {
    showDashboardEmpty(container, "自律実行はありません。");
    return;
  }

  const items = runs.slice(0, 12).map((run) => {
    const status = displayValue(run.status);
    const item = createDashboardItem({
      title: displayValue(run.objective_summary, run.run_id),
      badge: RUN_STATUS_LABELS[status] || status,
      badgeKind: runBadgeKind(status),
      body: displayValue(run.current_step_summary || run.history_summary),
      meta: [
        run.next_run_at ? `次回 ${formatDateTime(run.next_run_at)}` : "",
        `更新 ${formatDateTime(run.updated_at)}`,
      ].filter(Boolean).join(" · "),
    });
    const terminal = ["completed", "cancelled"].includes(status);
    if (!terminal) {
      const actions = document.createElement("div");
      actions.className = "dashboard-item-actions";
      if (status === "paused") {
        actions.append(createRunAction("再開", "resume", run.run_id));
      } else {
        actions.append(createRunAction("一時停止", "pause", run.run_id));
      }
      actions.append(createRunAction("取消", "cancel", run.run_id, { danger: true }));
      item.append(actions);
    }
    return item;
  });
  container.replaceChildren(...items);
}

function renderDashboardCapabilities() {
  const container = element("dashboard-capabilities");
  const capabilities = state.dashboard.currentState?.capability_inspection?.capabilities || [];
  if (!capabilities.length) {
    showDashboardEmpty(container, "能力情報はありません。");
    return;
  }

  const items = capabilities.map((capability) => {
    const available = capability.available === true;
    const reason = capability.unavailable_reason;
    const bindingCount = capability.binding?.eligible_client_count || 0;
    const sourceCount = capability.vision_sources?.filter((source) => source.available === true).length || 0;
    const toolCount = capability.mcp_servers
      ?.reduce((count, server) => count + (server.tools?.length || 0), 0) || 0;
    return createDashboardItem({
      title: capability.capability_id,
      badge: available ? "利用可能" : (CAPABILITY_REASON_LABELS[reason] || displayValue(reason, "利用不可")),
      badgeKind: available ? "ok" : "error",
      body: displayValue(capability.kind),
      meta: [
        `接続 ${bindingCount}`,
        sourceCount ? `source ${sourceCount}` : "",
        toolCount ? `tool ${toolCount}` : "",
        capability.state?.busy ? "実行中" : "",
      ].filter(Boolean).join(" · "),
    });
  });
  container.replaceChildren(...items);
}

function renderDashboardCycles() {
  const container = element("dashboard-cycles");
  const cycles = state.dashboard.cycleSummaries || [];
  if (!cycles.length) {
    showDashboardEmpty(container, "記録済みサイクルはありません。");
    return;
  }

  const items = cycles.map((cycle) => {
    const trigger = TRIGGER_KIND_LABELS[cycle.trigger_kind] || displayValue(cycle.trigger_kind);
    const result = RESULT_KIND_LABELS[cycle.result_kind] || displayValue(cycle.result_kind);
    const failed = cycle.failed === true;
    const duration = formatDuration(cycle.started_at, cycle.finished_at);
    return createDashboardItem({
      title: trigger,
      badge: failed ? "失敗" : result,
      badgeKind: failed ? "error" : "ok",
      body: cycle.cycle_id,
      meta: [
        formatDateTime(cycle.started_at),
        duration,
      ].filter(Boolean).join(" · "),
      itemKind: failed ? "failed" : "",
    });
  });
  container.replaceChildren(...items);
}

function renderDashboard() {
  renderDashboardOverview();
  renderDashboardCurrentState();
  renderDashboardRuns();
  renderDashboardCapabilities();
  renderDashboardCycles();
}

async function refreshDashboard({ silent = false } = {}) {
  if (state.dashboardRefreshing) {
    return;
  }
  state.dashboardRefreshing = true;
  element("refresh-dashboard").disabled = true;
  try {
    const [currentState, cycles] = await Promise.all([
      apiRequest("/ui/api/inspection/current-state"),
      apiRequest("/ui/api/inspection/cycle-summaries"),
    ]);
    state.dashboard.currentState = currentState;
    state.dashboard.cycleSummaries = cycles.cycle_summaries || [];
    renderDashboard();
    if (!silent) {
      showNotice("運用ダッシュボードを更新しました。");
    }
  } catch (error) {
    element("dashboard-generated-at").textContent = "更新失敗";
    if (!silent) {
      showNotice(error.message, true);
    }
  } finally {
    state.dashboardRefreshing = false;
    element("refresh-dashboard").disabled = false;
  }
}

async function controlAutonomousRun(action, runId, button) {
  if (action === "cancel" && !window.confirm("この自律実行を取り消しますか？")) {
    return;
  }
  button.disabled = true;
  try {
    await apiRequest(`/ui/api/autonomous-runs/${encodeURIComponent(runId)}/${action}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshDashboard({ silent: true });
    const message = {
      pause: "自律実行を一時停止しました。",
      resume: "自律実行を再開しました。",
      cancel: "自律実行を取り消しました。",
    }[action];
    showNotice(message);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function toggleDashboard() {
  const workspace = element("workspace-layout");
  const hidden = workspace.classList.toggle("dashboard-hidden");
  element("toggle-dashboard").setAttribute("aria-expanded", String(!hidden));
}

function setEventStreamStatus(text, kind = "") {
  const status = element("event-stream-status");
  status.textContent = text;
  status.className = `status ${kind}`.trim();
}

function assistantSourceLabel(sourceKind) {
  return {
    capability_result: "能力結果",
    wake: "起床",
    background_thinking: "定期思考",
    autonomous_run: "自律実行",
  }[sourceKind] || "非同期";
}

function playAssistantAudio(arrayBuffer, metadata) {
  const playback = async () => {
    const blob = new Blob([arrayBuffer], { type: metadata.media_type });
    const objectUrl = URL.createObjectURL(blob);
    const audio = new Audio(objectUrl);
    try {
      await new Promise((resolve, reject) => {
        audio.addEventListener("ended", resolve, { once: true });
        audio.addEventListener("error", () => reject(new Error("音声を再生できません。")), { once: true });
        audio.play().catch(reject);
      });
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  };
  state.assistantAudio.playbackTail = state.assistantAudio.playbackTail
    .catch(() => undefined)
    .then(playback)
    .catch((error) => showNotice(error.message, true));
}

// 対話入力と同じ client_id で購読し、音声入力と発話の配送先を一致させる。
function connectEventStream() {
  if (state.unloading || state.eventSocket) {
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/ui/api/events/stream`);
  socket.binaryType = "arraybuffer";
  state.eventSocket = socket;
  setEventStreamStatus("イベント: 接続中", "processing");

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({
      type: "hello",
      client_id: state.clientId,
      caps: [],
      event_subscriptions: [
        "conversation_input",
        "assistant_message",
        "assistant_audio",
        "audio_runtime_state",
      ],
    }));
    setEventStreamStatus("イベント: 接続済み");
  });
  socket.addEventListener("message", (event) => {
    if (event.data instanceof ArrayBuffer) {
      const metadata = state.assistantAudio.pendingMetadata;
      state.assistantAudio.pendingMetadata = null;
      if (
        metadata
        && metadata.status === "succeeded"
        && metadata.media_type === "audio/wav"
        && event.data.byteLength === metadata.byte_count
      ) {
        playAssistantAudio(event.data, metadata);
      }
      return;
    }
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload?.type === "assistant_message" && typeof payload.data?.message === "string") {
      addMessage(
        "assistant",
        payload.data.message,
        [],
        assistantSourceLabel(payload.data.source_kind),
      );
      refreshDashboard({ silent: true });
    } else if (payload?.type === "assistant_audio" && payload.data) {
      state.assistantAudio.pendingMetadata = (
        payload.data.status === "succeeded"
          ? payload.data
          : null
      );
    } else if (
      payload?.type === "conversation_input"
      && typeof payload.data?.message === "string"
    ) {
      const sourceLabel = payload.data.source_kind === "web_microphone"
        ? "Webマイク"
        : "物理マイク";
      addMessage(
        "person",
        payload.data.message,
        [],
        [payload.data.display_name, sourceLabel].filter(Boolean).join(" · "),
      );
      refreshDashboard({ silent: true });
    } else if (payload?.type === "audio_runtime_state" && payload.data) {
      updateAudioRuntimeState(payload.data);
    }
  });
  socket.addEventListener("error", () => socket.close());
  socket.addEventListener("close", () => {
    state.assistantAudio.pendingMetadata = null;
    if (state.eventSocket === socket) {
      state.eventSocket = null;
    }
    if (state.unloading) {
      return;
    }
    setEventStreamStatus("イベント: 再接続中", "processing");
    window.clearTimeout(state.eventReconnectTimer);
    state.eventReconnectTimer = window.setTimeout(connectEventStream, 3000);
  });
}

function websocketUrl(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

function setWebMicrophoneStatus(text, kind = "") {
  const status = element("web-microphone-status");
  status.textContent = text;
  status.className = `status ${kind}`.trim();
}

function webMicrophoneHasLease() {
  return (
    state.webAudio.socket?.readyState === WebSocket.OPEN
    && Number.isInteger(state.webAudio.leaseGeneration)
  );
}

function renderWebMicrophoneControls() {
  const running = Boolean(state.webAudio.socket);
  const busy = running || state.webAudio.starting || state.webAudio.stopping;
  element("web-microphone-device").disabled = busy;
  element("refresh-web-microphones").disabled = busy;
  const button = element("toggle-web-microphone");
  button.textContent = running ? "音声入力を停止" : "音声入力を開始";
  button.disabled = state.webAudio.starting || state.webAudio.stopping;
  if (!running && !state.webAudio.starting && !state.webAudio.stopping) {
    setWebMicrophoneStatus("停止中");
  }
  if (state.settingsOpen) {
    renderSpeakerEnrollment();
  }
}

async function refreshWebMicrophoneDevices({ requestPermission = false } = {}) {
  if (!navigator.mediaDevices?.enumerateDevices) {
    showNotice("このブラウザではマイク入力を使用できません。", true);
    return;
  }
  let permissionStream = null;
  try {
    if (requestPermission) {
      permissionStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: false,
      });
    }
    const devices = (await navigator.mediaDevices.enumerateDevices())
      .filter((device) => device.kind === "audioinput" && device.deviceId);
    const select = element("web-microphone-device");
    const storedDeviceId = localStorage.getItem(WEB_MICROPHONE_DEVICE_KEY) || "";
    select.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = devices.length > 0
      ? "デバイスを選択"
      : "利用可能なマイクなし";
    select.append(placeholder);
    devices.forEach((device, index) => {
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || `マイク ${index + 1}`;
      select.append(option);
    });
    // 保存済みdeviceが消えた場合は別deviceへ自動切替しない。
    select.value = devices.some((device) => device.deviceId === storedDeviceId)
      ? storedDeviceId
      : "";
  } catch (error) {
    showNotice(`マイク一覧を取得できません: ${error.message}`, true);
  } finally {
    permissionStream?.getTracks().forEach((track) => track.stop());
  }
}

function audioPauseLabel(reason) {
  return {
    queue_full: "処理待ち",
    stt_disabled: "STT無効",
    stt_configuration_error: "STT設定エラー",
    speaker_enrollment_required: "話者登録待ち",
    microphone_device_unavailable: "マイク利用不可",
    response_client_unavailable: "イベント接続待ち",
    audio_runtime_unavailable: "音声runtime利用不可",
    settings_reloaded: "設定更新",
  }[reason] || "一時停止";
}

function startWebAudioHeartbeat(intervalSeconds) {
  window.clearInterval(state.webAudio.heartbeatTimer);
  state.webAudio.heartbeatTimer = window.setInterval(() => {
    const socket = state.webAudio.socket;
    const leaseGeneration = state.webAudio.leaseGeneration;
    if (socket?.readyState !== WebSocket.OPEN || !Number.isInteger(leaseGeneration)) {
      return;
    }
    socket.send(JSON.stringify({
      type: "audio_heartbeat",
      lease_generation: leaseGeneration,
    }));
  }, intervalSeconds * 1000);
}

function handleWebAudioControl(socket, payload) {
  if (state.webAudio.socket !== socket || typeof payload?.type !== "string") {
    return;
  }
  if (payload.type === "audio_started") {
    if (
      !Object.hasOwn(payload, "paused_reason")
      || (payload.paused_reason !== null && typeof payload.paused_reason !== "string")
    ) {
      throw new Error("audio_started.paused_reason is invalid.");
    }
    state.webAudio.leaseGeneration = payload.lease_generation;
    state.webAudio.paused = payload.paused_reason !== null;
    startWebAudioHeartbeat(payload.heartbeat_interval_seconds || 5);
    setWebMicrophoneStatus(
      state.webAudio.paused
        ? audioPauseLabel(payload.paused_reason)
        : "入力中",
      state.webAudio.paused ? "processing" : "",
    );
    renderSpeakerEnrollment();
  } else if (payload.type === "audio_paused") {
    state.webAudio.paused = true;
    setWebMicrophoneStatus(audioPauseLabel(payload.reason), "processing");
    if (payload.reason === "settings_reloaded") {
      stopWebMicrophone({ sendStop: false });
    }
  } else if (payload.type === "audio_resumed") {
    if (payload.lease_generation === state.webAudio.leaseGeneration) {
      state.webAudio.paused = false;
      setWebMicrophoneStatus(
        state.audioRuntime?.mode === "enrollment" ? "話者登録中" : "入力中",
      );
    }
  } else if (payload.type === "audio_stopped") {
    stopWebMicrophone({ sendStop: false });
  } else if (payload.type === "audio_error") {
    showNotice(`${payload.code || "audio_error"}: ${payload.message || "音声入力に失敗しました。"}`, true);
    stopWebMicrophone({ sendStop: false });
  }
}

async function startWebMicrophone() {
  if (state.webAudio.socket || state.webAudio.starting) {
    return;
  }
  if (state.eventSocket?.readyState !== WebSocket.OPEN) {
    showNotice("イベント接続が完了してから音声入力を開始してください。", true);
    return;
  }
  const deviceId = element("web-microphone-device").value;
  if (!deviceId) {
    showNotice("使用するWebマイクを選択してください。", true);
    return;
  }

  const startSequence = state.webAudio.startSequence + 1;
  state.webAudio.startSequence = startSequence;
  state.webAudio.starting = true;
  renderWebMicrophoneControls();
  setWebMicrophoneStatus("開始中", "processing");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: { exact: deviceId },
        echoCancellation: { ideal: true },
        noiseSuppression: { ideal: true },
        autoGainControl: { ideal: false },
      },
      video: false,
    });
    if (startSequence !== state.webAudio.startSequence) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    state.webAudio.stream = stream;
    const track = stream.getAudioTracks()[0];
    if (!track) {
      throw new Error("音声trackを取得できません。");
    }

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass || !window.AudioWorkletNode) {
      throw new Error("AudioWorkletに対応していないブラウザです。");
    }
    const context = new AudioContextClass();
    state.webAudio.context = context;
    await context.audioWorklet.addModule("/ui/audio-worklet.js");
    if (startSequence !== state.webAudio.startSequence) {
      return;
    }
    const sourceNode = context.createMediaStreamSource(stream);
    const workletNode = new AudioWorkletNode(
      context,
      "otomekairo-pcm-processor",
      { numberOfInputs: 1, numberOfOutputs: 0 },
    );
    state.webAudio.sourceNode = sourceNode;
    state.webAudio.workletNode = workletNode;
    workletNode.port.onmessage = (event) => {
      const socket = state.webAudio.socket;
      if (
        socket?.readyState === WebSocket.OPEN
        && Number.isInteger(state.webAudio.leaseGeneration)
        && !state.webAudio.paused
        && event.data instanceof ArrayBuffer
        && event.data.byteLength === 640
      ) {
        socket.send(event.data);
      }
    };
    sourceNode.connect(workletNode);
    await context.resume();
    if (startSequence !== state.webAudio.startSequence) {
      return;
    }

    const socket = new WebSocket(websocketUrl("/ui/api/audio/stream"));
    state.webAudio.socket = socket;
    state.webAudio.starting = false;
    renderWebMicrophoneControls();
    socket.addEventListener("open", () => {
      const settings = track.getSettings();
      const actualBoolean = (value) => (
        typeof value === "boolean" ? value : null
      );
      socket.send(JSON.stringify({
        type: "audio_start",
        protocol_version: "1",
        client_id: state.clientId,
        input_source: "web_microphone",
        format: {
          sample_rate: 16000,
          channels: 1,
          sample_format: "pcm_s16le",
          frame_duration_ms: 20,
          bytes_per_frame: 640,
        },
        device: null,
        capture_settings: {
          source_sample_rate: context.sampleRate,
          echo_cancellation: actualBoolean(settings.echoCancellation),
          noise_suppression: actualBoolean(settings.noiseSuppression),
          auto_gain_control: actualBoolean(settings.autoGainControl),
          device_id_present: typeof settings.deviceId === "string" && settings.deviceId.length > 0,
        },
      }));
      setWebMicrophoneStatus("リース待ち", "processing");
    });
    socket.addEventListener("message", (event) => {
      if (typeof event.data !== "string") {
        return;
      }
      try {
        handleWebAudioControl(socket, JSON.parse(event.data));
      } catch {
        showNotice("音声streamから不正な制御messageを受信しました。", true);
        stopWebMicrophone({ sendStop: false });
      }
    });
    socket.addEventListener("error", () => socket.close());
    socket.addEventListener("close", () => {
      if (state.webAudio.socket === socket && !state.webAudio.stopping) {
        showNotice("Webマイク接続が終了しました。", true);
        stopWebMicrophone({ sendStop: false });
      }
    });
  } catch (error) {
    if (startSequence !== state.webAudio.startSequence) {
      return;
    }
    state.webAudio.starting = false;
    await stopWebMicrophone({ sendStop: false });
    showNotice(`Webマイクを開始できません: ${error.message}`, true);
  }
}

async function stopWebMicrophone({ sendStop = true } = {}) {
  if (state.webAudio.stopping) {
    return;
  }
  state.webAudio.stopping = true;
  state.webAudio.starting = false;
  state.webAudio.startSequence += 1;
  const socket = state.webAudio.socket;
  const stream = state.webAudio.stream;
  const context = state.webAudio.context;
  const sourceNode = state.webAudio.sourceNode;
  const workletNode = state.webAudio.workletNode;
  const leaseGeneration = state.webAudio.leaseGeneration;

  window.clearInterval(state.webAudio.heartbeatTimer);
  if (
    sendStop
    && socket?.readyState === WebSocket.OPEN
    && Number.isInteger(leaseGeneration)
  ) {
    socket.send(JSON.stringify({
      type: "audio_stop",
      lease_generation: leaseGeneration,
    }));
  }
  state.webAudio.socket = null;
  state.webAudio.stream = null;
  state.webAudio.context = null;
  state.webAudio.sourceNode = null;
  state.webAudio.workletNode = null;
  state.webAudio.heartbeatTimer = null;
  state.webAudio.leaseGeneration = null;
  state.webAudio.paused = true;
  if (workletNode?.port) {
    workletNode.port.onmessage = null;
  }
  try {
    sourceNode?.disconnect();
    workletNode?.disconnect();
  } catch {
    // 既にbrowser側で切断済みの場合も後始末を継続する。
  }
  stream?.getTracks().forEach((track) => track.stop());
  socket?.close();
  if (context && context.state !== "closed") {
    await context.close();
  }
  state.webAudio.stopping = false;
  renderWebMicrophoneControls();
}

function updateAudioRuntimeState(runtimeState) {
  const hadEnrollment = Boolean(state.audioRuntime?.enrollment || state.activeEnrollment);
  state.audioRuntime = clone(runtimeState);
  if (runtimeState.enrollment) {
    state.activeEnrollment = {
      ...state.activeEnrollment,
      ...runtimeState.enrollment,
    };
  } else if (hadEnrollment) {
    state.activeEnrollment = null;
    refreshSpeakers();
  }
  renderSpeakerEnrollment();
}

function addMessage(kind, text, images = [], sourceLabel = "") {
  const wrapper = document.createElement("article");
  wrapper.className = `message ${kind}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text || "";
  for (const image of images) {
    const img = document.createElement("img");
    img.className = "message-image";
    img.src = image.data || image;
    img.alt = "送信画像";
    bubble.append(img);
  }

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = [nowLabel(), sourceLabel].filter(Boolean).join(" · ");

  wrapper.append(bubble, meta);
  element("messages").append(wrapper);
  element("messages").scrollTop = element("messages").scrollHeight;
}

function resultText(result) {
  if (result?.speech?.text) {
    return { kind: "assistant", text: result.speech.text };
  }
  if (result?.capability_request) {
    const request = result.capability_request;
    return {
      kind: "system",
      text: `能力実行を要求しました。\n${request.capability_id || ""} ${request.request_id || ""}`.trim(),
    };
  }
  if (result?.result_kind === "noop") {
    return { kind: "system", text: "応答はありません。" };
  }
  if (result?.result_kind === "internal_failure") {
    return {
      kind: "system",
      text: `応答の生成に失敗しました。サーバーログを確認してください。cycle: ${result.cycle_id || "不明"}`,
    };
  }
  return { kind: "system", text: "予期しない処理結果を受信しました。" };
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.sending) {
    return;
  }
  const input = element("message-input");
  const text = input.value.trim();
  if (!text && !state.attachment) {
    return;
  }
  const images = state.attachment ? [state.attachment.data] : [];
  const personRef = state.conversationPersonRef;
  const displayName = state.conversationDisplayName;
  const interactionRef = state.conversationInteractionRef;
  if (!displayName) {
    showNotice("設定画面の「会話入力」で呼ばれ方を設定してください。", true);
    return;
  }
  addMessage("person", text, images);
  input.value = "";
  clearAttachment();
  state.sending = true;
  element("send-message").disabled = true;
  setStatus("状態: 対話入力処理中", "processing");
  try {
    const result = await apiRequest("/ui/api/conversation", {
      method: "POST",
      body: JSON.stringify({
        text,
        images,
        interaction_context: {
          interaction_ref: interactionRef,
          speaker_ref: personRef,
          participants: [{
            person_ref: personRef,
            display_name: displayName,
          }],
        },
        client_context: {
          source: "OtomeKairoWebUI",
          client_id: state.clientId,
          locale: navigator.language,
        },
      }),
    });
    const rendered = resultText(result);
    addMessage(rendered.kind, rendered.text);
    await loadStatus({ silent: true });
    await refreshDashboard({ silent: true });
  } catch (error) {
    setStatus("送信失敗", "error");
    addMessage("system", error.message);
    showNotice(error.message, true);
  } finally {
    state.sending = false;
    element("send-message").disabled = false;
    input.focus();
  }
}

function clearAttachment() {
  state.attachment = null;
  element("attachment-preview").hidden = true;
  element("attachment-image").removeAttribute("src");
  element("image-input").value = "";
}

function attachFile(file) {
  if (!file || !file.type.startsWith("image/")) {
    showNotice("画像ファイルを指定してください。", true);
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    state.attachment = {
      name: file.name,
      data: String(reader.result),
    };
    element("attachment-image").src = state.attachment.data;
    element("attachment-preview").hidden = false;
  };
  reader.readAsDataURL(file);
}

async function openSettings() {
  state.settingsOpen = true;
  element("settings-backdrop").hidden = false;
  element("settings-panel").classList.add("open");
  element("settings-panel").setAttribute("aria-hidden", "false");
  await loadSettingsDrafts();
}

function closeSettings() {
  state.settingsOpen = false;
  element("settings-backdrop").hidden = true;
  element("settings-panel").classList.remove("open");
  element("settings-panel").setAttribute("aria-hidden", "true");
}

async function loadSettingsDrafts() {
  try {
    const [
      editor,
      avatarSpeech,
      camera,
      mcp,
      apiDocs,
      audioInputDevices,
      speakers,
    ] = await Promise.all([
      apiRequest("/ui/api/config/editor-state"),
      apiRequest("/ui/api/config/avatar-speech/editor-state"),
      apiRequest("/ui/api/config/camera-sources/editor-state"),
      apiRequest("/ui/api/config/mcp-servers/editor-state"),
      apiRequest("/ui/api/docs"),
      apiRequest("/ui/api/audio/input-devices"),
      apiRequest("/ui/api/audio/speakers"),
    ]);
    let consoleClient = null;
    try {
      consoleClient = await apiRequest(
        "/ui/api/config/console-clients/last-connected/editor-state",
      );
    } catch (error) {
      if (error.code !== "console_client_settings_not_found") {
        throw error;
      }
    }
    state.editor = clone(editor);
    state.avatarSpeech = clone(avatarSpeech);
    state.consoleClient = consoleClient ? clone(consoleClient) : null;
    state.camera = clone(camera);
    state.mcp = clone(mcp);
    state.apiDocs = clone(apiDocs);
    state.audioInputDevices = clone(audioInputDevices);
    state.speakers = clone(speakers.speakers || []);
    state.selectedAvatarId = state.avatarSpeech.selected_avatar_id;
    state.selectedPersonaId = state.editor.current.selected_persona_id;
    state.selectedModelPresetId = state.editor.current.selected_model_preset_id;
    state.selectedMemorySetId = state.editor.current.selected_memory_set_id;
    state.selectedCameraId = state.camera.camera_sources[0]?.vision_source_id || "";
    state.selectedMcpId = state.mcp.mcp_servers[0]?.mcp_server_id || "";
    renderSettings();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function saveSettings({ closeAfterSave = false } = {}) {
  try {
    syncAllForms();
    const editor = await apiRequest("/ui/api/config/editor-state", {
      method: "PUT",
      body: JSON.stringify(state.editor),
    });
    const avatarSpeech = await apiRequest("/ui/api/config/avatar-speech/editor-state", {
      method: "PUT",
      body: JSON.stringify(state.avatarSpeech),
    });
    const camera = await apiRequest("/ui/api/config/camera-sources/editor-state", {
      method: "PUT",
      body: JSON.stringify(state.camera),
    });
    const mcp = await apiRequest("/ui/api/config/mcp-servers/editor-state", {
      method: "PUT",
      body: JSON.stringify(state.mcp),
    });
    let consoleClient = state.consoleClient;
    if (consoleClient) {
      const consoleSettingsPatch = {
        process: consoleClient.settings.process,
        desktop_capture: consoleClient.settings.desktop_capture,
      };
      consoleClient = await apiRequest(
        `/ui/api/config/console-clients/${encodeURIComponent(consoleClient.client_id)}`,
        {
          method: "PATCH",
          body: JSON.stringify(consoleSettingsPatch),
        },
      );
    }
    state.editor = clone(editor);
    state.avatarSpeech = clone(avatarSpeech);
    state.camera = clone(camera);
    state.mcp = clone(mcp);
    state.consoleClient = consoleClient ? clone(consoleClient) : null;
    state.conversationDisplayName =
      state.editor.current.conversation_display_name || "";
    const selectedPersona = arrayById(
      state.editor.personas,
      "persona_id",
      state.editor.current.selected_persona_id,
    );
    state.selectedPersonaDisplayName = selectedPersona?.display_name || "";
    renderSettings();
    await loadStatus({ silent: true });
    await refreshDashboard({ silent: true });
    showNotice("設定を保存しました。");
    if (closeAfterSave) {
      closeSettings();
    }
  } catch (error) {
    showNotice(error.message, true);
  }
}

function renderSettings() {
  if (!state.editor || !state.avatarSpeech || !state.camera || !state.mcp) {
    return;
  }
  renderAvatar();
  renderMicrophoneSettings();
  renderConsoleClientSettings();
  renderCurrent();
  renderPersona();
  renderModel();
  renderMemory();
  renderCapabilities();
  renderApiDocumentation();
}

function renderApiDocumentation() {
  const baseUrl = window.location.origin;
  element("api-doc-base-url").textContent = baseUrl;
  element("watcher-wake-api-url").textContent = `${baseUrl}/api/wake`;
  const sections = state.apiDocs?.sections || [];
  const documents = sections.map((section) => {
    const details = document.createElement("details");
    details.className = "api-doc-section";

    const summary = document.createElement("summary");
    summary.textContent = section.title || section.section_id;

    const body = document.createElement("pre");
    body.className = "api-doc-body";
    // 文書の共通プレースホルダーをブラウザから到達できるoriginへ置換する。
    body.textContent = (section.body_text || "").replaceAll("{BASE_URL}", baseUrl);

    details.append(summary, body);
    return details;
  });
  element("api-doc-sections").replaceChildren(...documents);
}

function renderAvatar() {
  state.selectedAvatarId = selectedOrFirst(
    state.avatarSpeech.avatars,
    "avatar_id",
    state.selectedAvatarId,
  );
  state.avatarSpeech.selected_avatar_id = state.selectedAvatarId;
  setSelectOptions(
    element("avatar-select"),
    state.avatarSpeech.avatars,
    "avatar_id",
    state.selectedAvatarId,
  );
  const avatar = arrayById(state.avatarSpeech.avatars, "avatar_id", state.selectedAvatarId);
  if (!avatar) {
    return;
  }
  const stt = avatar.stt;
  const tts = avatar.tts;
  const voicevox = tts.voicevox_config;
  const sbv2 = tts.style_bert_vits2_config;
  const aivis = tts.aivis_cloud_config;
  element("avatar-display-name").value = avatar.display_name;
  renderAvatarPresentation();
  element("stt-enabled").checked = stt.enabled;
  element("stt-engine").value = stt.engine;
  element("stt-wake-words").value = stt.wake_words.join("\n");
  element("stt-profile-id").value = stt.profile_id;
  element("stt-api-key").value = stt.api_key;
  element("tts-enabled").checked = tts.enabled;
  element("tts-engine").value = tts.engine;
  element("voicevox-endpoint-url").value = voicevox.endpoint_url;
  element("voicevox-speaker-id").value = voicevox.speaker_id;
  element("voicevox-speed-scale").value = voicevox.speed_scale;
  element("voicevox-pitch-scale").value = voicevox.pitch_scale;
  element("voicevox-intonation-scale").value = voicevox.intonation_scale;
  element("voicevox-volume-scale").value = voicevox.volume_scale;
  element("voicevox-pre-phoneme-length").value = voicevox.pre_phoneme_length;
  element("voicevox-post-phoneme-length").value = voicevox.post_phoneme_length;
  element("voicevox-output-sampling-rate").value = String(voicevox.output_sampling_rate);
  element("voicevox-output-stereo").checked = voicevox.output_stereo;
  element("sbv2-endpoint-url").value = sbv2.endpoint_url;
  element("sbv2-model-name").value = sbv2.model_name;
  element("sbv2-model-id").value = sbv2.model_id;
  element("sbv2-speaker-name").value = sbv2.speaker_name;
  element("sbv2-speaker-id").value = sbv2.speaker_id;
  element("sbv2-style").value = sbv2.style;
  element("sbv2-style-weight").value = sbv2.style_weight;
  element("sbv2-language").value = sbv2.language;
  element("sbv2-sdp-ratio").value = sbv2.sdp_ratio;
  element("sbv2-noise").value = sbv2.noise;
  element("sbv2-noise-w").value = sbv2.noise_w;
  element("sbv2-length").value = sbv2.length;
  element("sbv2-auto-split").checked = sbv2.auto_split;
  element("sbv2-split-interval").value = sbv2.split_interval;
  element("sbv2-assist-text").value = sbv2.assist_text;
  element("sbv2-assist-text-weight").value = sbv2.assist_text_weight;
  element("sbv2-reference-audio-path").value = sbv2.reference_audio_path;
  element("aivis-api-key").value = aivis.api_key;
  element("aivis-endpoint-url").value = aivis.endpoint_url;
  element("aivis-model-uuid").value = aivis.model_uuid;
  element("aivis-speaker-uuid").value = aivis.speaker_uuid;
  element("aivis-style-id").value = aivis.style_id;
  element("aivis-style-name").value = aivis.style_name;
  element("aivis-use-ssml").checked = aivis.use_ssml;
  element("aivis-language").value = aivis.language;
  element("aivis-speaking-rate").value = aivis.speaking_rate;
  element("aivis-emotional-intensity").value = aivis.emotional_intensity;
  element("aivis-tempo-dynamics").value = aivis.tempo_dynamics;
  element("aivis-pitch").value = aivis.pitch;
  element("aivis-volume").value = aivis.volume;
  element("aivis-output-format").value = aivis.output_format;
  element("aivis-output-bitrate").value = aivis.output_bitrate;
  element("aivis-output-sampling-rate").value = aivis.output_sampling_rate;
  element("aivis-output-audio-channels").value = aivis.output_audio_channels;
  renderTtsPanel(tts.engine);
}

function syncAvatar() {
  const avatar = arrayById(state.avatarSpeech.avatars, "avatar_id", state.selectedAvatarId);
  if (!avatar) {
    return;
  }
  avatar.display_name = textValue("avatar-display-name");
  avatar.stt.enabled = boolValue("stt-enabled");
  avatar.stt.engine = textValue("stt-engine");
  avatar.stt.wake_words = parseLines(textValue("stt-wake-words"));
  avatar.stt.profile_id = textValue("stt-profile-id");
  avatar.stt.api_key = textValue("stt-api-key");
  avatar.tts.enabled = boolValue("tts-enabled");
  avatar.tts.engine = textValue("tts-engine");
  avatar.tts.voicevox_config = {
    endpoint_url: textValue("voicevox-endpoint-url"),
    speaker_id: intValue("voicevox-speaker-id", 0),
    speed_scale: numberValue("voicevox-speed-scale", 1),
    pitch_scale: numberValue("voicevox-pitch-scale", 0),
    intonation_scale: numberValue("voicevox-intonation-scale", 1),
    volume_scale: numberValue("voicevox-volume-scale", 1),
    pre_phoneme_length: numberValue("voicevox-pre-phoneme-length", 0.1),
    post_phoneme_length: numberValue("voicevox-post-phoneme-length", 0.1),
    output_sampling_rate: intValue("voicevox-output-sampling-rate", 24000),
    output_stereo: boolValue("voicevox-output-stereo"),
  };
  avatar.tts.style_bert_vits2_config = {
    endpoint_url: textValue("sbv2-endpoint-url"),
    model_name: textValue("sbv2-model-name"),
    model_id: intValue("sbv2-model-id", 0),
    speaker_name: textValue("sbv2-speaker-name"),
    speaker_id: intValue("sbv2-speaker-id", 0),
    style: textValue("sbv2-style"),
    style_weight: numberValue("sbv2-style-weight", 1),
    sdp_ratio: numberValue("sbv2-sdp-ratio", 0.2),
    noise: numberValue("sbv2-noise", 0.6),
    noise_w: numberValue("sbv2-noise-w", 0.8),
    length: numberValue("sbv2-length", 1),
    language: textValue("sbv2-language"),
    auto_split: boolValue("sbv2-auto-split"),
    split_interval: numberValue("sbv2-split-interval", 0.5),
    assist_text: textValue("sbv2-assist-text"),
    assist_text_weight: numberValue("sbv2-assist-text-weight", 0),
    reference_audio_path: textValue("sbv2-reference-audio-path"),
  };
  avatar.tts.aivis_cloud_config = {
    api_key: textValue("aivis-api-key"),
    endpoint_url: textValue("aivis-endpoint-url"),
    model_uuid: textValue("aivis-model-uuid"),
    speaker_uuid: textValue("aivis-speaker-uuid"),
    style_id: intValue("aivis-style-id", 0),
    style_name: textValue("aivis-style-name"),
    use_ssml: boolValue("aivis-use-ssml"),
    language: textValue("aivis-language"),
    speaking_rate: numberValue("aivis-speaking-rate", 1),
    emotional_intensity: numberValue("aivis-emotional-intensity", 1),
    tempo_dynamics: numberValue("aivis-tempo-dynamics", 1),
    pitch: numberValue("aivis-pitch", 0),
    volume: numberValue("aivis-volume", 1),
    output_format: textValue("aivis-output-format"),
    output_bitrate: intValue("aivis-output-bitrate", 0),
    output_sampling_rate: intValue("aivis-output-sampling-rate", 16000),
    output_audio_channels: textValue("aivis-output-audio-channels"),
  };
  state.avatarSpeech.selected_avatar_id = state.selectedAvatarId;
}

function renderAvatarPresentation() {
  const presentations = state.consoleClient?.settings?.avatar_presentations || [];
  const presentation = arrayById(
    presentations,
    "avatar_id",
    state.selectedAvatarId,
  );
  element("avatar-model").value = presentation?.model || "";
  element("avatar-convert-mtoon").checked =
    presentation?.convert_unlit_to_mtoon || false;
  element("avatar-shadow-exclusion-enabled").checked =
    presentation?.shadow_exclusion_enabled || false;
  element("avatar-shadow-excluded-meshes").value =
    (presentation?.shadow_excluded_mesh_names || []).join(", ");
}

function renderConsoleClientSettings() {
  const available = Boolean(state.consoleClient);
  document.querySelectorAll("[data-console-setting]").forEach((fieldset) => {
    fieldset.disabled = fieldset.hasAttribute("data-always-disabled") || !available;
  });
  element("model-conversation-input-enabled").disabled = !available;
  element("model-conversation-input-enabled").checked =
    available && state.consoleClient.settings.process.conversation_input_enabled === true;
  element("current-wake-desktop-observation").disabled = !available;
  if (!available) {
    renderAvatarPresentation();
    return;
  }

  const settings = state.consoleClient.settings;
  const desktop = settings.desktop_capture;
  element("desktop-capture-idle-timeout").value = desktop.idle_timeout_minutes;
  element("desktop-capture-exclude-patterns").value = desktop.exclude_patterns.join("\n");
  renderAvatarPresentation();
}

function syncConsoleClientSettings() {
  if (!state.consoleClient) {
    return;
  }
  const settings = state.consoleClient.settings;
  settings.process = {
    ...settings.process,
    conversation_input_enabled: boolValue("model-conversation-input-enabled"),
  };
  // CocoroConsoleに表示しない取得方式の設定値は変更せず保持する。
  settings.desktop_capture = {
    ...settings.desktop_capture,
    idle_timeout_minutes: intValue("desktop-capture-idle-timeout", 10),
    exclude_patterns: parseLines(textValue("desktop-capture-exclude-patterns")),
  };
}

function renderTtsPanel(engine) {
  document.querySelectorAll("[data-tts-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.ttsPanel !== engine;
  });
}

function renderMicrophoneSettings() {
  const microphone = state.avatarSpeech.microphone_settings;
  element("physical-input-enabled").checked = microphone.physical_input_enabled;
  element("microphone-response-client-id").value = state.clientId;
  renderPhysicalInputDevices(microphone.input_device);
  element("vad-probability-threshold").value = microphone.vad_probability_threshold;
  element("speaker-recognition-threshold").value = microphone.speaker_recognition_threshold;
  updateMicrophoneSettingLabels();
  renderSpeakerList();
  renderSpeakerEnrollment();
}

function syncMicrophoneSettings() {
  const selectedDevice = textValue("physical-input-device");
  state.avatarSpeech.microphone_settings = {
    physical_input_enabled: boolValue("physical-input-enabled"),
    input_device: selectedDevice ? JSON.parse(selectedDevice) : null,
    response_client_id: state.clientId,
    vad_probability_threshold: numberValue("vad-probability-threshold", 0.5),
    speaker_recognition_threshold: numberValue("speaker-recognition-threshold", 0.6),
  };
}

function updateMicrophoneSettingLabels() {
  element("vad-probability-threshold-value").textContent =
    numberValue("vad-probability-threshold", 0.5).toFixed(2);
  element("speaker-recognition-threshold-value").textContent =
    numberValue("speaker-recognition-threshold", 0.6).toFixed(2);
}

function renderPhysicalInputDevices(selectedDevice) {
  const select = element("physical-input-device");
  const selectedValue = selectedDevice
    ? JSON.stringify({
      host_api: selectedDevice.host_api,
      name: selectedDevice.name,
    })
    : "";
  select.innerHTML = "";
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = "未選択";
  select.append(emptyOption);
  let selectedFound = false;
  for (const device of state.audioInputDevices?.devices || []) {
    const option = document.createElement("option");
    option.value = JSON.stringify({
      host_api: device.host_api,
      name: device.name,
    });
    option.textContent = [
      `${device.host_api}: ${device.name}`,
      `${device.default_sample_rate} Hz`,
      device.ambiguous ? "同名重複" : "",
    ].filter(Boolean).join(" / ");
    option.disabled = device.ambiguous === true;
    select.append(option);
    if (option.value === selectedValue && !option.disabled) {
      selectedFound = true;
    }
  }
  if (selectedValue && !selectedFound) {
    const unavailableOption = document.createElement("option");
    unavailableOption.value = selectedValue;
    unavailableOption.textContent =
      `${selectedDevice.host_api}: ${selectedDevice.name} / 現在利用不可`;
    select.append(unavailableOption);
  }
  select.value = selectedValue;
}

async function refreshSpeakers() {
  try {
    const response = await apiRequest("/ui/api/audio/speakers");
    state.speakers = clone(response.speakers || []);
    renderSpeakerList();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function renderSpeakerList() {
  const container = element("speaker-list");
  if (state.speakers.length === 0) {
    container.textContent = "登録済み話者はいません";
    return;
  }
  const rows = state.speakers.map((speaker) => {
    const row = document.createElement("div");
    row.className = "speaker-row";
    const summary = document.createElement("div");
    summary.className = "speaker-summary";
    const name = document.createElement("strong");
    name.textContent = speaker.display_name;
    const registration = document.createElement("span");
    registration.textContent = speaker.registration_status === "registered"
      ? "音声登録済み"
      : "音声未登録";
    summary.append(name, registration);

    const actions = document.createElement("div");
    actions.className = "speaker-actions";
    for (const [action, label] of [
      ["rename", "名前変更"],
      ["reenroll", "再登録"],
      ["unregister", "登録解除"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "plain-button";
      button.dataset.speakerAction = action;
      button.dataset.personRef = speaker.person_ref;
      button.textContent = label;
      if (
        action === "unregister"
        && speaker.registration_status !== "registered"
      ) {
        button.disabled = true;
      }
      if (action === "reenroll" && !webMicrophoneHasLease()) {
        button.disabled = true;
      }
      actions.append(button);
    }
    row.append(summary, actions);
    return row;
  });
  container.replaceChildren(...rows);
}

function renderSpeakerEnrollment() {
  const enrollment = state.activeEnrollment || state.audioRuntime?.enrollment;
  const startButton = element("start-speaker-enrollment");
  const cancelButton = element("cancel-speaker-enrollment");
  startButton.disabled = Boolean(enrollment) || !webMicrophoneHasLease();
  cancelButton.disabled = !enrollment;
  if (!enrollment) {
    element("speaker-enrollment-status").textContent = "登録待機中";
    renderSpeakerList();
    return;
  }
  const completed = enrollment.completed_samples || 0;
  const required = enrollment.required_samples || 3;
  element("speaker-enrollment-status").textContent =
    `話者登録中: ${completed} / ${required} 発話`;
  renderSpeakerList();
}

async function startSpeakerEnrollment(personRef = null) {
  if (!webMicrophoneHasLease()) {
    showNotice("Webマイクを開始してから話者登録を開始してください。", true);
    return;
  }
  const body = personRef
    ? { owner_client_id: state.clientId, person_ref: personRef }
    : {
      owner_client_id: state.clientId,
      display_name: textValue("speaker-enrollment-display-name").trim(),
    };
  if (!personRef && !body.display_name) {
    showNotice("新しい話者の呼ばれ方を入力してください。", true);
    return;
  }
  try {
    state.activeEnrollment = await apiRequest(
      "/ui/api/audio/speaker-enrollments",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    element("speaker-enrollment-display-name").value = "";
    renderSpeakerEnrollment();
    setWebMicrophoneStatus("話者登録中");
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function cancelSpeakerEnrollment() {
  const enrollmentId = state.activeEnrollment?.enrollment_id
    || state.audioRuntime?.enrollment?.enrollment_id;
  if (!enrollmentId) {
    return;
  }
  try {
    await apiRequest(
      `/ui/api/audio/speaker-enrollments/${encodeURIComponent(enrollmentId)}`,
      { method: "DELETE" },
    );
    state.activeEnrollment = null;
    renderSpeakerEnrollment();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function renameSpeaker(personRef) {
  const speaker = state.speakers.find((item) => item.person_ref === personRef);
  if (!speaker) {
    return;
  }
  const displayName = window.prompt("新しい呼ばれ方を入力してください。", speaker.display_name);
  if (displayName === null || !displayName.trim()) {
    return;
  }
  try {
    await apiRequest(
      `/ui/api/audio/speakers/${encodeURIComponent(personRef)}/display-name`,
      {
        method: "PUT",
        body: JSON.stringify({ display_name: displayName.trim() }),
      },
    );
    await refreshSpeakers();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function unregisterSpeaker(personRef) {
  const speaker = state.speakers.find((item) => item.person_ref === personRef);
  if (
    !speaker
    || !window.confirm(`${speaker.display_name}の音声登録を解除しますか？`)
  ) {
    return;
  }
  try {
    await apiRequest(
      `/ui/api/audio/speakers/${encodeURIComponent(personRef)}/registration`,
      { method: "DELETE" },
    );
    await refreshSpeakers();
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function copyApiKey(inputId, label) {
  try {
    await navigator.clipboard.writeText(textValue(inputId));
    showNotice(`${label}をコピーしました。`);
  } catch (error) {
    showNotice(`クリップボードへコピーできません: ${error.message}`, true);
  }
}

async function pasteApiKey(inputId, label) {
  try {
    element(inputId).value = await navigator.clipboard.readText();
    showNotice(`${label}を貼り付けました。`);
  } catch (error) {
    showNotice(`クリップボードから読み込めません: ${error.message}`, true);
  }
}

function renderCurrent() {
  const displayName = state.editor.current.conversation_display_name || "";
  const wakePolicy = state.editor.current.wake_policy || {};
  const observations = Array.isArray(wakePolicy.observations) ? wakePolicy.observations : [];
  element("settings-conversation-display-name").value = displayName;
  element("current-thinking-level").value = state.editor.current.thinking_speech_level ?? 5;
  element("current-wake-enabled").checked = wakePolicy.mode === "interval";
  element("current-wake-interval").value = wakePolicy.interval_seconds;
  element("current-wake-desktop-observation").checked =
    observations.some((observation) => isDesktopWakeObservation(observation));
}

function syncCurrent() {
  state.editor.current.selected_persona_id = state.selectedPersonaId;
  state.editor.current.selected_memory_set_id = state.selectedMemorySetId;
  state.editor.current.selected_model_preset_id = state.selectedModelPresetId;
  state.editor.current.conversation_display_name =
    textValue("settings-conversation-display-name").trim();
  state.editor.current.thinking_speech_level = intValue("current-thinking-level", 5);
  let observations = Array.isArray(state.editor.current.wake_policy?.observations)
    ? state.editor.current.wake_policy.observations
    : [];
  if (state.consoleClient) {
    // 対象端末のデスクトップ観測だけをCocoroConsoleと同じ定義で置き換える。
    observations = observations.filter((observation) => !isDesktopWakeObservation(observation));
    if (boolValue("current-wake-desktop-observation")) {
      observations.push({
        observation_id: DESKTOP_WAKE_OBSERVATION_ID,
        enabled: true,
        capability_id: "vision.capture",
        input: {
          vision_source_id: desktopVisionSourceId(),
          mode: "still",
        },
      });
    }
  }
  state.editor.current.wake_policy = {
    mode: element("current-wake-enabled").checked ? "interval" : "disabled",
    interval_seconds: intValue(
      "current-wake-interval",
      DEFAULT_WAKE_INTERVAL_SECONDS,
    ),
  };
  if (observations.length) {
    state.editor.current.wake_policy.observations = observations;
  }
}

function desktopVisionSourceId() {
  const clientId = state.consoleClient.client_id.trim();
  const sourceToken = Array.from(clientId)
    .filter((character) => /[\p{L}\p{N}_-]/u.test(character))
    .join("");
  return `vision_source:${sourceToken}:desktop`;
}

function isDesktopWakeObservation(observation) {
  if (observation?.observation_id === DESKTOP_WAKE_OBSERVATION_ID) {
    return true;
  }
  return Boolean(
    state.consoleClient
    && observation?.capability_id === "vision.capture"
    && observation?.input?.vision_source_id === desktopVisionSourceId(),
  );
}

function renderPersona() {
  state.selectedPersonaId = selectedOrFirst(state.editor.personas, "persona_id", state.selectedPersonaId);
  setSelectOptions(element("persona-select"), state.editor.personas, "persona_id", state.selectedPersonaId);
  const persona = arrayById(state.editor.personas, "persona_id", state.selectedPersonaId);
  if (!persona) {
    return;
  }
  element("persona-display-name").value = persona.display_name || "";
  element("persona-prompt").value = persona.persona_prompt || "";
  element("persona-expression-addon").value = persona.expression_addon || "";
}

function syncPersona() {
  const persona = arrayById(state.editor.personas, "persona_id", state.selectedPersonaId);
  if (!persona) {
    return;
  }
  persona.display_name = textValue("persona-display-name");
  persona.persona_prompt = textValue("persona-prompt");
  persona.expression_addon = textValue("persona-expression-addon");
}

function renderModel() {
  state.selectedModelPresetId = selectedOrFirst(state.editor.model_presets, "model_preset_id", state.selectedModelPresetId);
  setSelectOptions(element("model-select"), state.editor.model_presets, "model_preset_id", state.selectedModelPresetId);
  const preset = arrayById(state.editor.model_presets, "model_preset_id", state.selectedModelPresetId);
  if (!preset) {
    return;
  }
  element("model-display-name").value = preset.display_name || "";
  element("model-recent-turn-limit").value = preset.prompt_window?.recent_turn_limit || 30;
  element("model-recent-turn-minutes").value = preset.prompt_window?.recent_turn_minutes || 30;
  element("model-model").value = preset.model || "";
  element("model-api-base").value = preset.api_base || "";
  element("model-api-key").value = preset.api_key || "";
  element("model-reasoning-effort").value = preset.reasoning_effort || "";
  element("model-max-output-tokens").value = preset.max_output_tokens || 4000;
  element("model-timeout-seconds").value = preset.timeout_seconds || 90;
  element("model-web-search-enabled").checked = preset.web_search_enabled === true;
}

function syncModel() {
  const preset = arrayById(state.editor.model_presets, "model_preset_id", state.selectedModelPresetId);
  if (!preset) {
    return;
  }
  preset.display_name = textValue("model-display-name");
  preset.prompt_window = preset.prompt_window || {};
  preset.prompt_window.recent_turn_limit = intValue("model-recent-turn-limit", 30);
  preset.prompt_window.recent_turn_minutes = intValue("model-recent-turn-minutes", 30);
  preset.model = textValue("model-model");
  preset.api_key = textValue("model-api-key");
  preset.max_output_tokens = intValue("model-max-output-tokens", 4000);
  preset.timeout_seconds = boundedIntValue("model-timeout-seconds", "タイムアウト（秒）", 1);
  preset.web_search_enabled = boolValue("model-web-search-enabled");
  const apiBase = textValue("model-api-base").trim();
  if (apiBase) {
    preset.api_base = apiBase;
  } else {
    delete preset.api_base;
  }
  const reasoningEffort = textValue("model-reasoning-effort").trim();
  if (reasoningEffort) {
    preset.reasoning_effort = reasoningEffort;
  } else {
    delete preset.reasoning_effort;
  }
}

function renderMemory() {
  state.selectedMemorySetId = selectedOrFirst(state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId);
  setSelectOptions(element("memory-select"), state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId);
  const memory = arrayById(state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId);
  if (!memory) {
    return;
  }
  const embedding = memory.embedding || {};
  element("memory-display-name").value = memory.display_name || "";
  element("memory-embedding-model").value = embedding.model || "";
  element("memory-api-base").value = embedding.api_base || "";
  element("memory-dimension").value = embedding.embedding_dimension || 3072;
  element("memory-api-key").value = embedding.api_key || "";
}

function syncMemory() {
  const memory = arrayById(state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId);
  if (!memory) {
    return;
  }
  memory.display_name = textValue("memory-display-name");
  memory.embedding = memory.embedding || {};
  memory.embedding.model = textValue("memory-embedding-model");
  const apiBase = textValue("memory-api-base").trim();
  if (apiBase) {
    memory.embedding.api_base = apiBase;
  } else {
    delete memory.embedding.api_base;
  }
  memory.embedding.embedding_dimension = intValue("memory-dimension", 3072);
  memory.embedding.api_key = textValue("memory-api-key");
}

function pasteLlmApiKeyToMemory() {
  const apiKey = preferredLlmApiKey();
  if (!apiKey) {
    showNotice("貼り付け元の LLM モデル API キーが空です。", true);
    return;
  }
  element("memory-api-key").value = apiKey;
  showNotice("LLMモデルのAPIキーを貼り付けました。");
}

function preferredLlmApiKey() {
  syncModel();
  const preset = arrayById(state.editor.model_presets, "model_preset_id", state.selectedModelPresetId);
  return typeof preset?.api_key === "string" ? preset.api_key.trim() : "";
}

function renderCapabilities() {
  renderCamera();
  renderMcp();
  renderWatcher();
}

function renderCamera() {
  state.selectedCameraId = selectedOrFirst(state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  setSelectOptions(element("camera-select"), state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  const camera = arrayById(state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  const connection = camera?.connection || {};
  element("camera-enabled").checked = camera?.enabled === true;
  element("camera-display-name").value = camera?.display_name || "";
  element("camera-host").value = connection.host || "";
  element("camera-username").value = connection.camera_username || "";
  element("camera-password").value = connection.camera_password || "";
  element("camera-connector-kind").value = camera?.connector_kind || "tapo_c220";
  element("camera-client-id").value = camera?.client_id || "tapo-c220-connector-main";
  element("camera-vision-source-id").value = camera?.vision_source_id || "";
  element("camera-watcher-id").value = cameraWatcher(camera).watcher_id;
}

function updateCameraGeneratedIds() {
  const camera = arrayById(state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  if (!camera) {
    return;
  }
  const draft = {
    ...camera,
    display_name: textValue("camera-display-name"),
  };
  // 保存時にserverが生成するIDを入力中の表示名から先に表示する。
  draft.vision_source_id = defaultVisionSourceId(draft);
  element("camera-vision-source-id").value = draft.vision_source_id;
  element("camera-watcher-id").value = defaultWatcherId(draft);
}

function syncCamera() {
  const camera = arrayById(state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  if (!camera) {
    return;
  }
  const previousVisionSourceId = camera.vision_source_id;
  camera.enabled = boolValue("camera-enabled");
  camera.display_name = textValue("camera-display-name");
  camera.vision_source_id = defaultVisionSourceId(camera);
  camera.connector_kind = textValue("camera-connector-kind");
  camera.client_id = textValue("camera-client-id");
  camera.kind = "camera";
  camera.source_owner = "self";
  camera.connection = {
    host: textValue("camera-host"),
    camera_username: textValue("camera-username"),
    camera_password: textValue("camera-password"),
  };
  camera.watcher = cameraWatcher(camera);
  state.selectedCameraId = camera.vision_source_id;
  if (state.selectedWatcherSourceId === previousVisionSourceId) {
    state.selectedWatcherSourceId = camera.vision_source_id;
  }
}

function watcherItems() {
  return (state.camera?.camera_sources || []).map((camera) => {
    const watcher = cameraWatcher(camera);
    const displayName = camera.display_name || camera.vision_source_id;
    return {
      vision_source_id: camera.vision_source_id,
      display_name: `${displayName} / カメラモーション / ${camera.vision_source_id} / 即時wake`,
    };
  });
}

function renderWatcher() {
  const items = watcherItems();
  state.selectedWatcherSourceId = selectedOrFirst(items, "vision_source_id", state.selectedWatcherSourceId);
  setSelectOptions(element("watcher-select"), items, "vision_source_id", state.selectedWatcherSourceId);
  const camera = arrayById(state.camera.camera_sources, "vision_source_id", state.selectedWatcherSourceId);
  const watcher = cameraWatcher(camera);
  element("watcher-display-name").value = camera?.display_name || "";
  element("watcher-vision-source-id").value = camera?.vision_source_id || "";
  element("watcher-enabled").checked = watcher.enabled === true;
  element("watcher-id").value = watcher.watcher_id;
  element("watcher-kind").value = `カメラモーション (${watcher.kind})`;
  element("watcher-poll-interval").value = watcher.poll_interval_seconds;
  element("watcher-min-wake-interval").value = watcher.min_wake_interval_seconds;
  element("watcher-motion-threshold").value = watcher.motion_ratio_threshold;
  element("watcher-pixel-threshold").value = watcher.pixel_diff_threshold;
  element("watcher-resize-width").value = watcher.resize_width;
}

function syncWatcher() {
  const camera = arrayById(state.camera.camera_sources, "vision_source_id", state.selectedWatcherSourceId);
  if (!camera) {
    return;
  }
  camera.watcher = {
    enabled: boolValue("watcher-enabled"),
    watcher_id: defaultWatcherId(camera),
    kind: "tapo_c220_motion",
    poll_interval_seconds: numberValue("watcher-poll-interval", 10),
    min_wake_interval_seconds: numberValue("watcher-min-wake-interval", 30),
    motion_ratio_threshold: numberValue("watcher-motion-threshold", 0.2),
    pixel_diff_threshold: intValue("watcher-pixel-threshold", 10),
    resize_width: intValue("watcher-resize-width", 320),
  };
}

function cameraWatcher(camera) {
  const watcher = camera?.watcher || {};
  return {
    enabled: watcher.enabled === true,
    watcher_id: defaultWatcherId(camera),
    kind: "tapo_c220_motion",
    poll_interval_seconds: watcher.poll_interval_seconds ?? 10,
    min_wake_interval_seconds: watcher.min_wake_interval_seconds ?? 30,
    motion_ratio_threshold: watcher.motion_ratio_threshold ?? 0.2,
    pixel_diff_threshold: watcher.pixel_diff_threshold ?? 10,
    resize_width: watcher.resize_width ?? 320,
  };
}

function defaultVisionSourceId(camera) {
  const displayName = camera?.display_name || "新規カメラ";
  const suffix = identifierSuffix(displayName);
  return `vision_source:${suffix || "camera"}`;
}

function defaultWatcherId(camera) {
  const sourceId = camera?.vision_source_id || defaultVisionSourceId(camera);
  const suffix = identifierSuffix(sourceId.replace(/^vision_source:/, ""));
  return `watcher:${suffix || "camera"}`;
}

function identifierSuffix(value) {
  return Array.from(String(value || "").trim())
    .map((character) => (/[\p{L}\p{N}._-]/u.test(character) ? character : "_"))
    .join("")
    .replace(/^_+|_+$/g, "");
}

function renderMcp() {
  state.selectedMcpId = selectedOrFirst(state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  setSelectOptions(element("mcp-select"), state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  const mcp = arrayById(state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  element("mcp-enabled").checked = mcp?.enabled === true;
  element("mcp-server-id").value = mcp?.mcp_server_id || "";
  element("mcp-client-id").value = mcp?.client_id || "mcp-client-connector-main";
  element("mcp-transport").value = mcp?.transport || "stdio";
  element("mcp-command").value = mcp?.command || "";
  element("mcp-args").value = (mcp?.args || []).join("\n");
  element("mcp-cwd").value = mcp?.cwd || "";
  element("mcp-env").value = formatEnv(mcp?.env || {});
}

function syncMcp() {
  const mcp = arrayById(state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  if (!mcp) {
    return;
  }
  mcp.mcp_server_id = textValue("mcp-server-id");
  // CocoroConsoleに表示しないconnector_kindとenabled_toolsは既存値を保持する。
  mcp.client_id = textValue("mcp-client-id");
  mcp.enabled = boolValue("mcp-enabled");
  mcp.transport = textValue("mcp-transport");
  mcp.command = textValue("mcp-command");
  mcp.args = parseLines(textValue("mcp-args"));
  const cwd = textValue("mcp-cwd").trim();
  mcp.cwd = cwd || null;
  mcp.env = parseEnv(textValue("mcp-env"));
  state.selectedMcpId = mcp.mcp_server_id;
}

function syncAllForms() {
  syncAvatar();
  syncMicrophoneSettings();
  syncConsoleClientSettings();
  syncCurrent();
  syncPersona();
  syncModel();
  syncMemory();
  syncCamera();
  syncWatcher();
  syncMcp();
}

function addAvatar() {
  syncAllForms();
  const source = arrayById(state.avatarSpeech.avatars, "avatar_id", state.selectedAvatarId)
    || state.avatarSpeech.avatars[0];
  const avatar = clone(source);
  avatar.avatar_id = `avatar:${idSuffix()}`;
  avatar.display_name = "新規アバター";
  state.avatarSpeech.avatars.push(avatar);
  state.selectedAvatarId = avatar.avatar_id;
  state.avatarSpeech.selected_avatar_id = avatar.avatar_id;
  renderAvatar();
}

function duplicateAvatar() {
  syncAllForms();
  const avatar = clone(arrayById(state.avatarSpeech.avatars, "avatar_id", state.selectedAvatarId));
  avatar.avatar_id = `avatar:${idSuffix()}`;
  avatar.display_name = `${avatar.display_name || "アバター"}_copy`;
  state.avatarSpeech.avatars.push(avatar);
  state.selectedAvatarId = avatar.avatar_id;
  state.avatarSpeech.selected_avatar_id = avatar.avatar_id;
  renderAvatar();
}

function deleteAvatar() {
  if (state.avatarSpeech.avatars.length <= 1) {
    showNotice("最後のアバターは削除できません。", true);
    return;
  }
  removeById(state.avatarSpeech.avatars, "avatar_id", state.selectedAvatarId);
  state.selectedAvatarId = state.avatarSpeech.avatars[0].avatar_id;
  state.avatarSpeech.selected_avatar_id = state.selectedAvatarId;
  renderAvatar();
}

function addPersona() {
  syncAllForms();
  const base = clone(arrayById(state.editor.personas, "persona_id", state.selectedPersonaId) || state.editor.personas[0]);
  base.persona_id = `persona:${idSuffix()}`;
  base.display_name = "新規人格設定";
  state.editor.personas.push(base);
  state.selectedPersonaId = base.persona_id;
  renderSettings();
}

function duplicatePersona() {
  syncAllForms();
  const base = clone(arrayById(state.editor.personas, "persona_id", state.selectedPersonaId));
  base.persona_id = `persona:${idSuffix()}`;
  base.display_name = `${base.display_name || "人格設定"} Copy`;
  state.editor.personas.push(base);
  state.selectedPersonaId = base.persona_id;
  renderSettings();
}

function deletePersona() {
  if (state.editor.personas.length <= 1) {
    showNotice("最後の人格設定は削除できません。", true);
    return;
  }
  removeById(state.editor.personas, "persona_id", state.selectedPersonaId);
  state.selectedPersonaId = state.editor.personas[0].persona_id;
  state.editor.current.selected_persona_id = state.selectedPersonaId;
  renderSettings();
}

function addModel() {
  syncAllForms();
  const base = clone(arrayById(state.editor.model_presets, "model_preset_id", state.selectedModelPresetId) || state.editor.model_presets[0]);
  base.model_preset_id = `model_preset:${idSuffix()}`;
  base.display_name = "新規モデルプリセット";
  state.editor.model_presets.push(base);
  state.selectedModelPresetId = base.model_preset_id;
  renderSettings();
}

function duplicateModel() {
  syncAllForms();
  const base = clone(arrayById(state.editor.model_presets, "model_preset_id", state.selectedModelPresetId));
  base.model_preset_id = `model_preset:${idSuffix()}`;
  base.display_name = `${base.display_name || "モデルプリセット"} Copy`;
  state.editor.model_presets.push(base);
  state.selectedModelPresetId = base.model_preset_id;
  renderSettings();
}

function deleteModel() {
  if (state.editor.model_presets.length <= 1) {
    showNotice("最後のモデルプリセットは削除できません。", true);
    return;
  }
  removeById(state.editor.model_presets, "model_preset_id", state.selectedModelPresetId);
  state.selectedModelPresetId = state.editor.model_presets[0].model_preset_id;
  state.editor.current.selected_model_preset_id = state.selectedModelPresetId;
  renderSettings();
}

function addMemory() {
  syncAllForms();
  const base = clone(arrayById(state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId) || state.editor.memory_sets[0]);
  base.memory_set_id = `memory_set:${idSuffix()}`;
  base.display_name = "新規記憶セット";
  state.editor.memory_sets.push(base);
  state.selectedMemorySetId = base.memory_set_id;
  renderSettings();
}

function duplicateMemory() {
  syncAllForms();
  const base = clone(arrayById(state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId));
  base.memory_set_id = `memory_set:${idSuffix()}`;
  base.display_name = `${base.display_name || "記憶セット"} Copy`;
  state.editor.memory_sets.push(base);
  state.selectedMemorySetId = base.memory_set_id;
  renderSettings();
}

function deleteMemory() {
  if (state.editor.memory_sets.length <= 1) {
    showNotice("最後の記憶セットは削除できません。", true);
    return;
  }
  removeById(state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId);
  state.selectedMemorySetId = state.editor.memory_sets[0].memory_set_id;
  state.editor.current.selected_memory_set_id = state.selectedMemorySetId;
  renderSettings();
}

function addCamera() {
  syncAllForms();
  const camera = {
    display_name: `新規カメラ${idSuffix()}`,
    vision_source_id: "",
    connector_kind: "tapo_c220",
    client_id: "tapo-c220-connector-main",
    kind: "camera",
    source_owner: "self",
    enabled: false,
    connection: {
      host: "127.0.0.1",
      camera_username: "",
      camera_password: "",
    },
    watcher: {
      enabled: false,
      watcher_id: "",
      kind: "tapo_c220_motion",
      poll_interval_seconds: 10,
      min_wake_interval_seconds: 30,
      motion_ratio_threshold: 0.2,
      pixel_diff_threshold: 10,
      resize_width: 320,
    },
  };
  camera.vision_source_id = defaultVisionSourceId(camera);
  camera.watcher.watcher_id = defaultWatcherId(camera);
  state.camera.camera_sources.push(camera);
  state.selectedCameraId = camera.vision_source_id;
  state.selectedWatcherSourceId = camera.vision_source_id;
  renderCapabilities();
}

function deleteCamera() {
  removeById(state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  state.selectedCameraId = state.camera.camera_sources[0]?.vision_source_id || "";
  state.selectedWatcherSourceId = selectedOrFirst(state.camera.camera_sources, "vision_source_id", state.selectedWatcherSourceId);
  renderCapabilities();
}

function addMcp() {
  syncAllForms();
  const id = `mcp:${idSuffix()}`;
  state.mcp.mcp_servers.push({
    mcp_server_id: id,
    connector_kind: "mcp_client",
    client_id: "mcp-client-connector-main",
    enabled: false,
    transport: "stdio",
    command: "npx",
    args: ["-y", "elyth-mcp-server@latest"],
    cwd: null,
    enabled_tools: [],
    env: {},
  });
  state.selectedMcpId = id;
  renderCapabilities();
}

function deleteMcp() {
  removeById(state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  state.selectedMcpId = state.mcp.mcp_servers[0]?.mcp_server_id || "";
  renderCapabilities();
}

function switchTab(tab) {
  document.querySelectorAll(".settings-nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === tab);
  });
  element("settings-page-select").value = tab;
}

function bindEvents() {
  element("toggle-dashboard").addEventListener("click", toggleDashboard);
  element("refresh-dashboard").addEventListener("click", () => refreshDashboard({ silent: false }));
  element("dashboard-runs").addEventListener("click", (event) => {
    const button = event.target instanceof Element
      ? event.target.closest("[data-run-action]")
      : null;
    if (!button) {
      return;
    }
    controlAutonomousRun(button.dataset.runAction, button.dataset.runId, button);
  });
  element("composer").addEventListener("submit", sendMessage);
  element("message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      element("composer").requestSubmit();
    }
  });
  element("message-input").addEventListener("paste", (event) => {
    const file = [...event.clipboardData.files].find((item) => item.type.startsWith("image/"));
    if (file) {
      attachFile(file);
    }
  });
  element("composer").addEventListener("dragover", (event) => event.preventDefault());
  element("composer").addEventListener("drop", (event) => {
    event.preventDefault();
    attachFile([...event.dataTransfer.files][0]);
  });
  element("image-input").addEventListener("change", (event) => attachFile(event.target.files[0]));
  element("remove-attachment").addEventListener("click", clearAttachment);
  element("web-microphone-device").addEventListener("change", () => {
    const deviceId = element("web-microphone-device").value;
    if (deviceId) {
      localStorage.setItem(WEB_MICROPHONE_DEVICE_KEY, deviceId);
    } else {
      localStorage.removeItem(WEB_MICROPHONE_DEVICE_KEY);
    }
  });
  element("refresh-web-microphones").addEventListener("click", () => {
    refreshWebMicrophoneDevices({ requestPermission: true });
  });
  element("toggle-web-microphone").addEventListener("click", () => {
    if (state.webAudio.socket) {
      stopWebMicrophone();
    } else {
      startWebMicrophone();
    }
  });

  element("open-settings").addEventListener("click", openSettings);
  element("close-settings").addEventListener("click", closeSettings);
  element("settings-backdrop").addEventListener("click", closeSettings);
  element("cancel-settings").addEventListener("click", closeSettings);
  element("apply-settings").addEventListener("click", () => saveSettings({ closeAfterSave: false }));
  element("ok-settings").addEventListener("click", () => saveSettings({ closeAfterSave: true }));
  document.querySelectorAll(".settings-nav-button").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  element("settings-page-select").addEventListener("change", () => {
    switchTab(element("settings-page-select").value);
  });

  element("avatar-select").addEventListener("change", () => {
    syncAvatar();
    state.selectedAvatarId = element("avatar-select").value;
    state.avatarSpeech.selected_avatar_id = state.selectedAvatarId;
    renderAvatar();
  });
  element("tts-engine").addEventListener("change", () => {
    renderTtsPanel(element("tts-engine").value);
  });
  element("vad-probability-threshold").addEventListener("input", updateMicrophoneSettingLabels);
  element("speaker-recognition-threshold").addEventListener("input", updateMicrophoneSettingLabels);
  element("start-speaker-enrollment").addEventListener("click", () => {
    startSpeakerEnrollment();
  });
  element("cancel-speaker-enrollment").addEventListener("click", cancelSpeakerEnrollment);
  element("speaker-list").addEventListener("click", (event) => {
    const button = event.target instanceof Element
      ? event.target.closest("[data-speaker-action]")
      : null;
    if (!button || button.disabled) {
      return;
    }
    if (button.dataset.speakerAction === "rename") {
      renameSpeaker(button.dataset.personRef);
    } else if (button.dataset.speakerAction === "reenroll") {
      startSpeakerEnrollment(button.dataset.personRef);
    } else if (button.dataset.speakerAction === "unregister") {
      unregisterSpeaker(button.dataset.personRef);
    }
  });
  element("persona-select").addEventListener("change", () => {
    syncPersona();
    state.selectedPersonaId = element("persona-select").value;
    renderPersona();
  });
  element("model-select").addEventListener("change", () => {
    syncModel();
    state.selectedModelPresetId = element("model-select").value;
    renderModel();
  });
  element("memory-select").addEventListener("change", () => {
    syncMemory();
    state.selectedMemorySetId = element("memory-select").value;
    renderMemory();
  });
  element("camera-select").addEventListener("change", () => {
    syncCamera();
    state.selectedCameraId = element("camera-select").value;
    state.selectedWatcherSourceId = state.selectedCameraId;
    renderCamera();
    renderWatcher();
  });
  element("camera-display-name").addEventListener("input", updateCameraGeneratedIds);
  element("watcher-select").addEventListener("change", () => {
    syncWatcher();
    state.selectedWatcherSourceId = element("watcher-select").value;
    renderWatcher();
  });
  element("mcp-select").addEventListener("change", () => {
    syncMcp();
    state.selectedMcpId = element("mcp-select").value;
    renderMcp();
  });

  document.querySelector("[data-action='add-avatar']").addEventListener("click", addAvatar);
  document.querySelector("[data-action='duplicate-avatar']").addEventListener("click", duplicateAvatar);
  document.querySelector("[data-action='delete-avatar']").addEventListener("click", deleteAvatar);
  element("copy-stt-api-key").addEventListener("click", () => copyApiKey("stt-api-key", "STT APIキー"));
  element("paste-stt-api-key").addEventListener("click", () => pasteApiKey("stt-api-key", "STT APIキー"));
  element("copy-aivis-api-key").addEventListener("click", () => copyApiKey("aivis-api-key", "Aivis Cloud APIキー"));
  element("paste-aivis-api-key").addEventListener("click", () => pasteApiKey("aivis-api-key", "Aivis Cloud APIキー"));
  document.querySelector("[data-action='add-persona']").addEventListener("click", addPersona);
  document.querySelector("[data-action='duplicate-persona']").addEventListener("click", duplicatePersona);
  document.querySelector("[data-action='delete-persona']").addEventListener("click", deletePersona);
  document.querySelector("[data-action='add-model']").addEventListener("click", addModel);
  document.querySelector("[data-action='duplicate-model']").addEventListener("click", duplicateModel);
  document.querySelector("[data-action='delete-model']").addEventListener("click", deleteModel);
  element("copy-model-api-key").addEventListener("click", () => copyApiKey("model-api-key", "モデルのAPIキー"));
  element("paste-model-api-key").addEventListener("click", () => pasteApiKey("model-api-key", "モデルのAPIキー"));
  document.querySelector("[data-action='add-memory']").addEventListener("click", addMemory);
  document.querySelector("[data-action='duplicate-memory']").addEventListener("click", duplicateMemory);
  document.querySelector("[data-action='delete-memory']").addEventListener("click", deleteMemory);
  element("copy-memory-api-key").addEventListener("click", () => copyApiKey("memory-api-key", "記憶セットのAPIキー"));
  element("paste-memory-api-key").addEventListener("click", () => pasteApiKey("memory-api-key", "記憶セットのAPIキー"));
  element("paste-llm-api-key-to-memory").addEventListener("click", pasteLlmApiKeyToMemory);
  document.querySelector("[data-action='add-camera']").addEventListener("click", addCamera);
  document.querySelector("[data-action='delete-camera']").addEventListener("click", deleteCamera);
  document.querySelector("[data-action='add-mcp']").addEventListener("click", addMcp);
  document.querySelector("[data-action='delete-mcp']").addEventListener("click", deleteMcp);

  window.addEventListener("beforeunload", () => {
    state.unloading = true;
    window.clearInterval(state.dashboardTimer);
    window.clearTimeout(state.eventReconnectTimer);
    stopWebMicrophone();
    state.eventSocket?.close();
  });
  window.addEventListener("pagehide", () => stopWebMicrophone());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      stopWebMicrophone();
    }
  });
  navigator.mediaDevices?.addEventListener("devicechange", () => {
    if (!state.webAudio.socket) {
      refreshWebMicrophoneDevices({ requestPermission: false });
    }
  });
}

async function startApp() {
  // 初回 token 発行後に inspection と event stream を順に開始する。
  initializeClientId();
  bindEvents();
  loadConversationIdentity();
  await loadIdentity();
  await loadConversationConfig();
  await loadStatus({ silent: true });
  await refreshDashboard({ silent: true });
  await refreshWebMicrophoneDevices({ requestPermission: false });
  connectEventStream();
  state.dashboardTimer = window.setInterval(() => refreshDashboard({ silent: true }), 5000);
}

startApp();
