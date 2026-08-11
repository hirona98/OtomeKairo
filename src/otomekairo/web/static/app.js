const state = {
  clientId: "",
  conversationPersonRef: "",
  conversationInteractionRef: "",
  conversationDisplayName: "",
  conversationDisplayNames: [],
  editor: null,
  avatarSpeech: null,
  consoleClient: null,
  // 一度も connect していない間の desktop_capture 編集用。
  desktopCaptureDefaults: null,
  camera: null,
  mcp: null,
  apiDocs: null,
  audioInputDevices: null,
  audioOutputDevices: null,
  speakers: [],
  audioRuntime: null,
  activeEnrollment: null,
  selectedAvatarId: "",
  selectedPersonaId: "",
  selectedModelPresetId: "",
  selectedMemorySetId: "",
  // memory_set_id ごとの下書きメタ（server 未保存 / 記憶複製元）
  memoryDraftMeta: {},
  selectedCameraId: "",
  selectedWatcherSourceId: "",
  selectedMcpId: "",
  attachment: null,
  settingsOpen: false,
  sending: false,
  pendingConversationInputs: new Map(),
  dashboard: {
    currentState: null,
    cycleSummaries: [],
    memorySnapshot: null,
  },
  dashboardRefreshing: false,
  dashboardTimer: null,
  audioMeters: {
    speakerResult: null,
    lastSpeakerUtteranceSeq: null,
    speakerResetTimer: null,
  },
  eventSocket: null,
  eventReconnectTimer: null,
  assistantAudio: {
    pendingMetadata: null,
    playbackTail: Promise.resolve(),
  },
  webAudio: {
    inputSessionId: null,
    inputSource: null,
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
    sttToggling: false,
    ttsToggling: false,
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
const MICROPHONE_SOURCE_LABELS = {
  local_microphone: "OtomeKairo",
  console_microphone: "CocoroConsole",
  web_microphone: "ブラウザ",
};
// 音声メータの表示スケール。直近ピークやしきい値では正規化しない。
const AUDIO_METER_LEVEL_MIN_DBFS = -60;
const AUDIO_METER_LEVEL_MAX_DBFS = -12; // バー満杯（上限固定）
const AUDIO_METER_UNIT_MIN = 0;
const AUDIO_METER_UNIT_MAX = 1; // VAD probability / speaker similarity
const SPEAKER_METER_HOLD_MS = 3000;

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

function uniqueDisplayName(existingNames, preferredName) {
  const names = new Set(existingNames.filter((name) => typeof name === "string"));
  if (!names.has(preferredName)) {
    return preferredName;
  }
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `${preferredName} ${index}`;
    if (!names.has(candidate)) {
      return candidate;
    }
  }
  return `${preferredName} ${idSuffix()}`;
}

function resetMemoryDraftMeta(memorySets) {
  const nextMeta = {};
  for (const memory of memorySets || []) {
    nextMeta[memory.memory_set_id] = {
      serverBacked: true,
      cloneSourceMemorySetId: null,
    };
  }
  state.memoryDraftMeta = nextMeta;
}

function memoryDraftMeta(memorySetId) {
  if (!state.memoryDraftMeta[memorySetId]) {
    state.memoryDraftMeta[memorySetId] = {
      serverBacked: false,
      cloneSourceMemorySetId: null,
    };
  }
  return state.memoryDraftMeta[memorySetId];
}

function resolveCloneSourceMemorySetId(memory) {
  if (!memory) {
    return null;
  }
  const meta = memoryDraftMeta(memory.memory_set_id);
  if (meta.serverBacked) {
    return memory.memory_set_id;
  }
  if (meta.cloneSourceMemorySetId) {
    return meta.cloneSourceMemorySetId;
  }
  return null;
}

function initializeClientId() {
  const storedClientId = sessionStorage.getItem("otomekairo.client_id");
  state.clientId = storedClientId || `web-ui:${idSuffix()}`;
  sessionStorage.setItem("otomekairo.client_id", state.clientId);
}

function loadConversationIdentity() {
  const personRef = localStorage.getItem("otomekairo.person_ref") || `person:web:${idSuffix()}`;
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
    state.conversationDisplayNames = clone(config.conversation_display_names || []);
    const selectedDisplayName = arrayById(
      state.conversationDisplayNames,
      "conversation_display_name_id",
      config.settings_snapshot.selected_conversation_display_name_id,
    );
    state.conversationDisplayName = selectedDisplayName?.display_name || "";
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

function parseKeyValueLines(value, label) {
  const result = {};
  for (const line of value.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    const separator = trimmed.indexOf("=");
    if (separator < 1) {
      throw new Error(`${label} は KEY=value 形式で入力してください。`);
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
    await apiRequest("/ui/api/bootstrap/server-identity");
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

// 表示しても意味が取りづらい機械参照を落とす（UUID・prefix:hex など閉じた ID パターン）。
function isOpaqueRef(value) {
  if (typeof value !== "string") {
    return false;
  }
  const text = value.trim();
  if (!text) {
    return false;
  }
  if (/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(text)) {
    return true;
  }
  if (/[0-9a-f]{16,}/i.test(text)) {
    return true;
  }
  // person:web:… / cycle:… / request:… など typed ref に長い hex 断片が付くもの
  if (
    /^(person|entity|cycle|run|request|relation_index|memory_set|event|digest|memory_unit|episode|client|observation)[:/]/i.test(text)
    && /[0-9a-f]{8,}/i.test(text)
  ) {
    return true;
  }
  return false;
}

// opaque な参照は空にし、人間可読な文字列だけ返す。
function humanDisplayValue(value, fallback = "") {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  if (typeof value !== "string" || !value.trim()) {
    return fallback;
  }
  const text = value.trim();
  if (isOpaqueRef(text)) {
    return fallback;
  }
  return text;
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

function formatScore(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
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
  href = "",
}) {
  // 判断一覧など、詳細画面へ辿れる項目はリンクにする。
  const item = document.createElement(href ? "a" : "article");
  item.className = `dashboard-item ${itemKind}`.trim();
  if (href) {
    item.href = href;
    item.target = "_blank";
    item.rel = "noopener noreferrer";
  }

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

function createDashboardChip(text, kind = "") {
  const chip = document.createElement("span");
  chip.className = `dashboard-chip ${kind}`.trim();
  chip.textContent = text;
  return chip;
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

function dashboardSnapshotParts() {
  const snapshot = state.dashboard.currentState || {};
  return {
    snapshot,
    runtime: snapshot.runtime_summary || {},
    detail: snapshot.runtime_detail || {},
    current: snapshot.current_state || {},
    capabilities: snapshot.capability_inspection?.capabilities || [],
  };
}

function formatVadLine(vad) {
  if (!vad || typeof vad !== "object") {
    return "";
  }
  return [
    `valence ${formatScore(vad.v)}`,
    `arousal ${formatScore(vad.a)}`,
    `dominance ${formatScore(vad.d)}`,
  ].join(" · ");
}

// 構造化済みの説明があれば使い、無ければ空。VAD からの感情語推測はしない。
function moodMeaningText(mood) {
  if (!mood || typeof mood !== "object") {
    return "";
  }
  return displayValue(
    mood.summary_text || mood.label || mood.mood_label || mood.description,
    "",
  );
}

function primaryActivityLabel(current) {
  for (const activityContext of current.activity_contexts || []) {
    const activity = activityContext.current_activity;
    if (!activity) {
      continue;
    }
    const label = displayValue(activity.label || activity.reason_summary, "");
    if (label) {
      return label;
    }
  }
  return "";
}

function primaryWorldStateLabel(current) {
  for (const worldState of current.foreground_world_states || []) {
    const summary = displayValue(worldState.summary_text, "");
    if (summary) {
      return summary;
    }
  }
  return "";
}

function nonTerminalRuns(current, runtime) {
  const runs = current.autonomous_runs || [];
  const live = runs.filter((run) => !["completed", "cancelled"].includes(run.status));
  if (live.length) {
    return live;
  }
  const count = (runtime.active_autonomous_run_count || 0) + (runtime.paused_autonomous_run_count || 0);
  return count > 0 ? runs.slice(0, count) : [];
}

function buildDashboardHeadline({ runtime, detail, current }) {
  const runtimeReady = runtime.connection_state === "ready";
  const parts = [];
  if (!runtimeReady) {
    parts.push(`注意: ${displayValue(runtime.connection_state, "未接続")}`);
  } else {
    parts.push("稼働中");
  }

  const pendingRequests = detail.pending_capability_requests || [];
  const ongoing = current.ongoing_action;
  if (ongoing) {
    const goal = displayValue(ongoing.goal_summary || ongoing.step_summary, "");
    if (ongoing.status === "failed") {
      parts.push(goal ? `継続行動失敗 · ${goal}` : "継続行動失敗");
    } else {
      parts.push(goal ? `継続中 · ${goal}` : "継続行動中");
    }
  } else if (pendingRequests.length) {
    const pending = pendingRequests[0];
    const summary = displayValue(
      pending.goal_summary || pending.capability_id || pending.request_id,
      "能力結果待ち",
    );
    parts.push(`結果待ち · ${summary}`);
  } else {
    const liveRuns = (current.autonomous_runs || []).filter(
      (run) => !["completed", "cancelled"].includes(run.status),
    );
    if (liveRuns.length) {
      const run = liveRuns[0];
      parts.push(displayValue(run.objective_summary || run.current_step_summary, "自律実行中"));
    } else {
      const activity = primaryActivityLabel(current);
      const world = primaryWorldStateLabel(current);
      if (activity) {
        parts.push(`活動: ${activity}`);
      } else if (world) {
        parts.push(`前景: ${world}`);
      } else {
        parts.push("静か");
      }
    }
  }

  const unavailable = (state.dashboard.currentState?.capability_inspection?.capabilities || [])
    .filter((capability) => capability.available !== true)
    .slice(0, 1);
  if (unavailable.length && runtimeReady) {
    const capability = unavailable[0];
    const reason = CAPABILITY_REASON_LABELS[capability.unavailable_reason]
      || displayValue(capability.unavailable_reason, "利用不可");
    if (["no_binding", "no_vision_source", "camera_source_disabled"].includes(capability.unavailable_reason)) {
      parts.push(`${capability.capability_id}: ${reason}`);
    }
  }

  return parts.filter(Boolean).join(" · ");
}

function renderDashboardHeader() {
  const { snapshot, runtime, detail, current, capabilities } = dashboardSnapshotParts();
  element("dashboard-headline").textContent = buildDashboardHeadline({ runtime, detail, current });

  const chips = element("dashboard-chips");
  const runtimeReady = runtime.connection_state === "ready";
  const liveRuns = nonTerminalRuns(current, runtime);
  const pendingCount = (detail.pending_capability_requests || []).length;
  const availableCount = capabilities.filter((capability) => capability.available === true).length;
  const failedRecent = (state.dashboard.cycleSummaries || []).some((cycle) => cycle.failed === true);

  const nodes = [
    createDashboardChip(runtimeReady ? "稼働" : displayValue(runtime.connection_state, "未接続"), runtimeReady ? "ok" : "error"),
  ];
  if (current.ongoing_action) {
    nodes.push(createDashboardChip("継続行動", current.ongoing_action.status === "failed" ? "error" : "waiting"));
  } else if (pendingCount) {
    nodes.push(createDashboardChip(`結果待ち ${pendingCount}`, "waiting"));
  } else {
    nodes.push(createDashboardChip("静か"));
  }
  nodes.push(createDashboardChip(`自律 ${liveRuns.length || runtime.active_autonomous_run_count || 0}`));
  if (capabilities.length) {
    nodes.push(createDashboardChip(`能力 ${availableCount}/${capabilities.length}`));
  }
  if (failedRecent) {
    nodes.push(createDashboardChip("直近に失敗あり", "error"));
  }
  chips.replaceChildren(...nodes);

  element("dashboard-generated-at").textContent = `更新 ${formatDateTime(snapshot.generated_at)}`;
}

function renderDashboardActive() {
  const container = element("dashboard-active");
  const { detail, current } = dashboardSnapshotParts();
  const items = [];

  const ongoing = current.ongoing_action;
  if (ongoing) {
    items.push(createDashboardItem({
      title: "継続行動",
      badge: displayValue(ongoing.status),
      badgeKind: ongoing.status === "failed" ? "error" : "waiting",
      body: displayValue(ongoing.goal_summary || ongoing.step_summary),
      meta: [
        ongoing.step_summary && ongoing.goal_summary ? ongoing.step_summary : "",
        humanDisplayValue(ongoing.last_capability_id),
        formatDateTime(ongoing.updated_at),
      ].filter(Boolean).join(" · "),
    }));
  }

  for (const request of (detail.pending_capability_requests || []).slice(0, 6)) {
    items.push(createDashboardItem({
      title: "能力結果待ち",
      badge: "待ち",
      badgeKind: "waiting",
      body: displayValue(
        request.goal_summary || request.summary_text || humanDisplayValue(request.capability_id),
        "能力結果待ち",
      ),
      meta: [
        humanDisplayValue(request.capability_id),
        humanDisplayValue(request.target_client_id),
        formatDateTime(request.created_at),
      ].filter(Boolean).join(" · "),
    }));
  }

  for (const intent of (current.pending_intent_candidates || []).slice(0, 6)) {
    items.push(createDashboardItem({
      title: `保留意図 · ${displayValue(intent.intent_kind, "未分類")}`,
      badge: "保留",
      badgeKind: "waiting",
      body: displayValue(intent.intent_summary || intent.reason_summary),
      meta: [
        intent.reason_summary && intent.intent_summary ? intent.reason_summary : "",
        intent.not_before ? `開始 ${formatDateTime(intent.not_before)}` : "",
        intent.expires_at ? `期限 ${formatDateTime(intent.expires_at)}` : "",
      ].filter(Boolean).join(" · "),
    }));
  }

  const runs = (current.autonomous_runs || []).slice(0, 12);
  for (const run of runs) {
    const status = displayValue(run.status);
    const terminal = ["completed", "cancelled"].includes(status);
    if (terminal) {
      continue;
    }
    const item = createDashboardItem({
      title: displayValue(run.objective_summary, "自律実行"),
      badge: RUN_STATUS_LABELS[status] || status,
      badgeKind: runBadgeKind(status),
      body: displayValue(run.current_step_summary || run.history_summary),
      meta: [
        run.next_run_at ? `次回 ${formatDateTime(run.next_run_at)}` : "",
        run.pause_reason ? `理由 ${run.pause_reason}` : "",
        `更新 ${formatDateTime(run.updated_at)}`,
      ].filter(Boolean).join(" · "),
    });
    const actions = document.createElement("div");
    actions.className = "dashboard-item-actions";
    if (status === "paused") {
      actions.append(createRunAction("再開", "resume", run.run_id));
    } else {
      actions.append(createRunAction("一時停止", "pause", run.run_id));
    }
    actions.append(createRunAction("取消", "cancel", run.run_id, { danger: true }));
    item.append(actions);
    items.push(item);
  }

  if (!items.length) {
    showDashboardEmpty(container, "いま進行中の行動はありません。");
    return;
  }
  container.replaceChildren(...items);
}

function renderDashboardInner() {
  const container = element("dashboard-inner");
  const { current } = dashboardSnapshotParts();
  const items = [];

  for (const drive of (current.drive_states || []).slice(0, 5)) {
    items.push(createDashboardItem({
      title: `動機 · ${displayValue(drive.drive_kind, "未分類")}`,
      body: displayValue(drive.summary_text),
      meta: `salience ${formatScore(drive.salience)} · 更新 ${formatDateTime(drive.updated_at)}`,
    }));
  }

  const mood = current.mood_state;
  const currentVad = mood?.current_vad;
  if (mood && currentVad) {
    const meaning = moodMeaningText(mood);
    const vadLine = formatVadLine(currentVad);
    // 意味テキストがあれば主表示し、VAD 生値は常に併記する。
    items.push(createDashboardItem({
      title: "気分",
      body: meaning || vadLine,
      meta: [
        meaning ? vadLine : "",
        `更新 ${formatDateTime(mood.updated_at)}`,
      ].filter(Boolean).join(" · "),
    }));
  }

  for (const affect of (current.affect_states || []).slice(0, 4)) {
    const scopeType = humanDisplayValue(affect.target_scope_type);
    const scopeKey = humanDisplayValue(affect.target_scope_key);
    const scope = [scopeType, scopeKey].filter(Boolean).join(":");
    items.push(createDashboardItem({
      title: `感情 · ${displayValue(affect.affect_label, "affect")}`,
      body: displayValue(affect.summary_text),
      meta: [
        scope,
        `更新 ${formatDateTime(affect.updated_at || affect.observed_at)}`,
      ].filter(Boolean).join(" · "),
    }));
  }

  if (!items.length) {
    showDashboardEmpty(container, "前景の動機・気分はまだありません。");
    return;
  }
  container.replaceChildren(...items);
}

function renderDashboardWorld() {
  const container = element("dashboard-world");
  const { detail, current } = dashboardSnapshotParts();
  const items = [];

  for (const worldState of (current.foreground_world_states || []).slice(0, 6)) {
    // scope_key が長い機械参照のときは type だけ残す。
    const scopeType = humanDisplayValue(worldState.scope_type);
    const scopeKey = humanDisplayValue(worldState.scope_key);
    const scope = [scopeType, scopeKey].filter(Boolean).join(":");
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

  for (const activityContext of (current.activity_contexts || []).slice(0, 4)) {
    const activity = activityContext.current_activity;
    if (!activity) {
      continue;
    }
    const targetLabel = humanDisplayValue(activity.target);
    items.push(createDashboardItem({
      title: `活動 · ${displayValue(humanDisplayValue(activity.actor), "主体")}`,
      body: displayValue(activity.label || activity.reason_summary),
      meta: [
        targetLabel ? `対象 ${targetLabel}` : "",
        activity.duration_label,
        activity.age_label,
      ].filter(Boolean).join(" · "),
    }));
  }

  for (const observation of (detail.wake_policy_observations || []).slice(0, 6)) {
    if (!observation.enabled && !observation.last_summary && !observation.last_status) {
      continue;
    }
    items.push(createDashboardItem({
      title: `観測 · ${displayValue(
        humanDisplayValue(observation.observation_id) || humanDisplayValue(observation.capability_id),
        "観測",
      )}`,
      badge: observation.enabled ? displayValue(observation.last_status, "有効") : "無効",
      badgeKind: observation.last_status === "failed" || observation.last_error ? "error" : "",
      body: displayValue(observation.last_summary || observation.last_error, "まだ観測結果がありません"),
      meta: [
        humanDisplayValue(observation.vision_source_id),
        observation.last_run_at ? `最終 ${formatDateTime(observation.last_run_at)}` : "",
      ].filter(Boolean).join(" · "),
    }));
  }

  for (const relation of (current.relation_index || []).slice(0, 6)) {
    // source_ref / target_ref は長い機械 ID になりやすく、UI では出さない。
    items.push(createDashboardItem({
      title: `関係 · ${displayValue(relation.relation_predicate, "relation")}`,
      body: displayValue(relation.representative_summary),
      meta: [
        relation.derived_status,
        `salience ${formatScore(relation.salience)}`,
      ].filter(Boolean).join(" · "),
    }));
  }

  const visualDaily = current.visual_daily_summary;
  if (visualDaily && typeof visualDaily === "object") {
    items.push(createDashboardItem({
      title: "視覚日次",
      body: [
        visualDaily.latest_local_date ? `日付 ${visualDaily.latest_local_date}` : "",
        `記録 ${visualDaily.record_count || 0}`,
        `群 ${visualDaily.group_count || 0}`,
        `記憶候補 ${visualDaily.memory_candidate_count || 0}`,
      ].filter(Boolean).join(" · "),
      meta: "",
    }));
  }

  if (!items.length) {
    showDashboardEmpty(container, "前景の外界状態はまだありません。");
    return;
  }
  container.replaceChildren(...items);
}

function renderDashboardMemory() {
  const container = element("dashboard-memory");
  const snapshot = state.dashboard.memorySnapshot;
  const { current } = dashboardSnapshotParts();
  const items = [];

  // 継続理解・経験を先に出し、関係索引と対象は補助情報として続ける。
  for (const unit of (snapshot?.memory_units || []).slice(0, 12)) {
    items.push(createDashboardItem({
      title: `理解 · ${displayValue(unit.memory_type, "memory_unit")}`,
      body: displayValue(unit.summary_text),
      meta: [
        unit.status,
        `salience ${formatScore(unit.salience)}`,
        formatDateTime(unit.updated_at || unit.last_confirmed_at || unit.formed_at),
      ].filter(Boolean).join(" · "),
    }));
  }
  for (const episode of (snapshot?.episodes || []).slice(0, 8)) {
    items.push(createDashboardItem({
      title: `経験 · ${displayValue(episode.episode_type, "episode")}`,
      body: displayValue(episode.summary_text || episode.outcome_text),
      meta: [
        `salience ${formatScore(episode.salience)}`,
        formatDateTime(episode.formed_at || episode.started_at),
      ].filter(Boolean).join(" · "),
    }));
  }
  for (const relation of (current.relation_index || []).slice(0, 4)) {
    // source_ref / target_ref は長い機械 ID になりやすく、UI では出さない。
    items.push(createDashboardItem({
      title: `関係索引 · ${displayValue(relation.relation_predicate)}`,
      body: displayValue(relation.representative_summary),
      meta: [
        relation.derived_status,
        `salience ${formatScore(relation.salience)}`,
      ].filter(Boolean).join(" · "),
    }));
  }
  for (const entity of (current.entity_registry || []).slice(0, 4)) {
    const name = humanDisplayValue(entity.display_name) || humanDisplayValue(entity.entity_type, "対象");
    items.push(createDashboardItem({
      title: `対象 · ${name}`,
      body: humanDisplayValue(entity.entity_type),
      meta: [
        `salience ${formatScore(entity.salience)}`,
        formatDateTime(entity.last_seen_at),
      ].filter(Boolean).join(" · "),
    }));
  }

  if (!items.length) {
    showDashboardEmpty(container, "表示できる記憶要約はまだありません。");
    return;
  }
  container.replaceChildren(...items);
}

function renderDashboardCycles() {
  const container = element("dashboard-cycles");
  const cycles = state.dashboard.cycleSummaries || [];
  if (!cycles.length) {
    showDashboardEmpty(container, "記録済みの判断はまだありません。");
    return;
  }

  // 俯瞰用に入力・結果・判断理由を出す。日時は一覧では出さない。
  const items = cycles.map((cycle) => {
    const trigger = TRIGGER_KIND_LABELS[cycle.trigger_kind] || displayValue(cycle.trigger_kind);
    const result = RESULT_KIND_LABELS[cycle.result_kind] || displayValue(cycle.result_kind);
    const failed = cycle.failed === true;
    const inputSummary = humanDisplayValue(cycle.input_summary);
    const outcomeSummary = humanDisplayValue(cycle.outcome_summary);
    const reasonSummary = humanDisplayValue(cycle.reason_summary);
    const lines = [];
    if (inputSummary && outcomeSummary) {
      lines.push(`${inputSummary} → ${outcomeSummary}`);
    } else if (inputSummary || outcomeSummary) {
      lines.push(inputSummary || outcomeSummary);
    }
    if (reasonSummary) {
      lines.push(`理由: ${reasonSummary}`);
    }
    const cycleId = typeof cycle.cycle_id === "string" ? cycle.cycle_id : "";
    return createDashboardItem({
      title: trigger,
      badge: failed ? "失敗" : result,
      badgeKind: failed ? "error" : "ok",
      body: lines.join("\n"),
      itemKind: failed ? "failed" : "",
      href: cycleId ? `/ui/cycles?cycle_id=${encodeURIComponent(cycleId)}` : "",
    });
  });
  container.replaceChildren(...items);
}

function renderDashboardHealth() {
  const container = element("dashboard-health");
  const { runtime, detail, capabilities } = dashboardSnapshotParts();
  const items = [];

  items.push(createDashboardItem({
    title: "Runtime",
    badge: runtime.connection_state === "ready" ? "稼働中" : displayValue(runtime.connection_state),
    badgeKind: runtime.connection_state === "ready" ? "ok" : "error",
    body: [
      `定期思考 ${runtime.background_thinking_scheduler_active ? "稼働" : "停止"}`,
      `自律実行scheduler ${runtime.autonomous_run_scheduler_active ? "稼働" : "停止"}`,
      `記憶worker ${runtime.memory_job_worker_active ? "稼働" : "停止"}`,
      `視覚日次 ${runtime.visual_daily_worker_active ? "稼働" : "停止"}`,
    ].join(" · "),
    meta: [
      `記憶job待ち ${runtime.pending_memory_job_count || 0}`,
      runtime.memory_job_in_progress ? "記憶処理中" : "",
      runtime.visual_daily_in_progress ? "視覚日次処理中" : "",
    ].filter(Boolean).join(" · "),
  }));

  const audio = detail.audio_runtime_state;
  if (audio && typeof audio === "object") {
    const last = audio.last_utterance_result;
    items.push(createDashboardItem({
      title: "音声入力",
      badge: audio.available === true ? "利用可能" : displayValue(audio.unavailable_reason, "利用不可"),
      badgeKind: audio.available === true ? "ok" : "error",
      body: [
        `設定 ${displayValue(audio.configured_source)}`,
        `実効 ${displayValue(audio.effective_source || audio.active_source)}`,
        audio.mode ? `mode ${audio.mode}` : "",
      ].filter(Boolean).join(" · "),
      meta: [
        audio.selected_device?.name,
        typeof audio.vad?.dbfs === "number" && Number.isFinite(audio.vad.dbfs)
          ? `${audio.vad.dbfs.toFixed(1)} dBFS`
          : "",
        audio.vad?.speaking ? "区間中" : "",
        typeof audio.vad?.probability === "number" ? `VAD ${formatScore(audio.vad.probability)}` : "",
        last && typeof last.top1_similarity === "number"
          ? `識別 ${formatScore(last.top1_similarity)}${last.threshold_met ? " 超過" : " 未満"}`
          : "",
        audio.paused_reason ? `pause ${audio.paused_reason}` : "",
      ].filter(Boolean).join(" · "),
    }));
  }

  for (const capability of capabilities) {
    const available = capability.available === true;
    const reason = capability.unavailable_reason;
    const bindingCount = capability.binding?.eligible_client_count || 0;
    const sourceCount = capability.vision_sources?.filter((source) => source.available === true).length || 0;
    const toolCount = capability.mcp_servers
      ?.reduce((count, server) => count + (server.tools?.length || 0), 0) || 0;
    items.push(createDashboardItem({
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
    }));
  }

  container.replaceChildren(...items);
}

function renderDashboard() {
  renderDashboardHeader();
  renderDashboardActive();
  renderDashboardInner();
  renderDashboardWorld();
  renderDashboardMemory();
  renderDashboardCycles();
  renderDashboardHealth();
}

async function refreshDashboard({ silent = false } = {}) {
  if (state.dashboardRefreshing) {
    return;
  }
  state.dashboardRefreshing = true;
  element("refresh-dashboard").disabled = true;
  try {
    const [currentState, cycles, memorySnapshot] = await Promise.all([
      apiRequest("/ui/api/inspection/current-state"),
      apiRequest("/ui/api/inspection/cycle-summaries"),
      apiRequest("/ui/api/inspection/memory-snapshot").catch(() => null),
    ]);
    state.dashboard.currentState = currentState;
    state.dashboard.cycleSummaries = cycles.cycle_summaries || [];
    // 記憶 snapshot は補助面なので失敗しても他の「いま」表示は続ける。
    if (memorySnapshot) {
      state.dashboard.memorySnapshot = memorySnapshot;
    }
    // イベント未受信時の初期表示用。連続更新は audio_runtime_state event が担う。
    const audioSnapshot = currentState?.runtime_detail?.audio_runtime_state;
    if (audioSnapshot && typeof audioSnapshot === "object") {
      syncSpeakerMeterResult(audioSnapshot);
      state.audioRuntime = clone(audioSnapshot);
      renderWebMicrophoneControls();
      renderAudioMeters();
    }
    renderDashboard();
    if (!silent) {
      showNotice("いまを更新しました。");
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

function isDashboardVisible() {
  return !element("workspace-layout").classList.contains("dashboard-hidden");
}

function setDashboardVisible(visible) {
  // いまパネルの表示を明示的に切り替える。
  element("workspace-layout").classList.toggle("dashboard-hidden", !visible);
}

function setConfirmMenuOpen(open) {
  const popup = element("confirm-menu-popup");
  const toggle = element("confirm-menu-toggle");
  popup.hidden = !open;
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

function setEventStreamStatus(text, kind = "") {
  const status = element("event-stream-status");
  status.textContent = text;
  status.className = `status ${kind}`.trim();
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

// チャットと音声 runtime を全 client 同期し、browser 選択時は合成音声も受け取る。
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
      client_kind: "browser",
      caps: [],
      event_subscriptions: [
        "conversation_input",
        "assistant_message",
        "assistant_audio",
        "audio_runtime_state",
        "system_notice",
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
    if (
      payload?.type === "assistant_message"
      && typeof payload.data?.message === "string"
      && typeof payload.data?.persona_display_name === "string"
    ) {
      addMessage(
        "assistant",
        payload.data.message,
        [],
        { displayName: payload.data.persona_display_name },
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
      const pending = state.pendingConversationInputs.get(payload.data.message_id);
      state.pendingConversationInputs.delete(payload.data.message_id);
      addMessage(
        "person",
        payload.data.message,
        pending?.images || [],
        { displayName: payload.data.display_name },
      );
      refreshDashboard({ silent: true });
    } else if (
      payload?.type === "system_notice"
      && payload.data?.conversation_visible === true
      && typeof payload.data?.message === "string"
    ) {
      addMessage("system", payload.data.message);
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
    if (state.webAudio.inputSessionId) {
      stopWebMicrophone({ sendStop: false });
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
  // statusbar 向けに接頭辞を揃え、呼び出し側は本文だけ渡す。
  const body = String(text || "").replace(/^音声:\s*/, "");
  status.textContent = body ? `音声: ${body}` : "音声: 停止中";
  status.className = `status ${kind}`.trim();
}

function webMicrophoneHasLease() {
  // 話者登録は active な音声リースがあれば開始できる（STT 無効でも可）。
  // Web マイクは当該タブの capture session、local/console は connector 側リースを見る。
  if (state.webAudio.inputSessionId && state.webAudio.inputSource === "web_microphone") {
    return (
      state.webAudio.socket?.readyState === WebSocket.OPEN
      && Number.isInteger(state.webAudio.leaseGeneration)
    );
  }
  const runtime = state.audioRuntime;
  if (!runtime?.active_source) {
    return false;
  }
  // stt_disabled 中でも登録開始で pause が解除される。
  return (
    runtime.paused_reason === null
    || runtime.paused_reason === "stt_disabled"
    || runtime.mode === "enrollment"
  );
}

function renderWebMicrophoneControls() {
  const sttEnabled = state.audioRuntime?.stt_enabled === true;
  const ttsEnabled = state.audioRuntime?.tts_enabled === true;
  const running = Boolean(state.webAudio.inputSessionId);
  const busy = (
    state.webAudio.starting
    || state.webAudio.stopping
    || state.webAudio.sttToggling
  );
  const source = state.audioRuntime?.configured_source || "";
  const usesWebDevice = source === "web_microphone";
  element("web-input-source-label").textContent = source
    ? `入力元: ${MICROPHONE_SOURCE_LABELS[source] || source}`
    : "入力元: 読み込み中";
  element("web-microphone-device").hidden = !usesWebDevice;
  element("web-microphone-device").disabled = busy || !usesWebDevice;
  // 共通運用トグル。aria-pressed は stt.enabled を示す。
  const button = element("toggle-web-microphone");
  button.disabled = busy || !source;
  button.setAttribute("aria-pressed", sttEnabled ? "true" : "false");
  button.title = sttEnabled
    ? "マイク（音声認識）をOFF"
    : "マイク（音声認識）をON";
  button.setAttribute(
    "aria-label",
    sttEnabled ? "マイク（音声認識）をOFF" : "マイク（音声認識）をON",
  );
  // TTS 運用トグル。入力元の有無に依存せず選択中アバターの tts.enabled を示す。
  const ttsButton = element("toggle-web-tts");
  ttsButton.disabled = state.webAudio.ttsToggling;
  ttsButton.setAttribute("aria-pressed", ttsEnabled ? "true" : "false");
  ttsButton.title = ttsEnabled ? "音声合成をOFF" : "音声合成をON";
  ttsButton.setAttribute(
    "aria-label",
    ttsEnabled ? "音声合成をOFF" : "音声合成をON",
  );
  const conversationInputBlockedReason = (
    state.audioRuntime?.conversation_input_blocked_reason || null
  );
  if (!busy && conversationInputBlockedReason) {
    setWebMicrophoneStatus(
      `入力確認中・${audioPauseLabel(conversationInputBlockedReason)}`,
      "processing",
    );
  } else if (!busy && !running) {
    if (!source) {
      setWebMicrophoneStatus("停止中");
    } else if (!sttEnabled) {
      setWebMicrophoneStatus("STT無効");
    } else if (usesWebDevice) {
      setWebMicrophoneStatus("ブラウザ入力待ち");
    } else if (state.audioRuntime?.paused_reason) {
      setWebMicrophoneStatus(
        audioPauseLabel(state.audioRuntime.paused_reason),
        "processing",
      );
    } else {
      setWebMicrophoneStatus(
        state.audioRuntime?.mode === "enrollment" ? "話者登録中" : "待機中",
      );
    }
  }
  if (state.settingsOpen) {
    renderSpeakerEnrollment();
  }
}

async function refreshWebMicrophoneDevices({ requestPermission = false } = {}) {
  if (!navigator.mediaDevices?.enumerateDevices) {
    showNotice("このブラウザではマイク入力を使用できません。", true);
    return false;
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
    return true;
  } catch (error) {
    showNotice(`マイク一覧を取得できません: ${error.message}`, true);
    return false;
  } finally {
    permissionStream?.getTracks().forEach((track) => track.stop());
  }
}

async function handleMicrophoneInputSourceChange() {
  // ブラウザ入力を選んだユーザー操作だけを、初回権限取得の入口にする。
  if (element("microphone-input-source").value !== "web_microphone") {
    return;
  }
  await refreshWebMicrophoneDevices({ requestPermission: true });
}

function audioPauseLabel(reason) {
  return {
    queue_full: "処理待ち",
    stt_disabled: "STT無効",
    stt_configuration_error: "STT設定エラー",
    speaker_enrollment_required: "話者登録待ち",
    microphone_device_unavailable: "マイク利用不可",
    audio_runtime_unavailable: "音声処理利用不可",
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
    if (
      payload.reason === "settings_reloaded"
      || payload.reason === "source_switched"
    ) {
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

async function setSttEnabled(enabled) {
  const result = await apiRequest("/ui/api/audio/stt-enabled", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
  if (state.audioRuntime) {
    state.audioRuntime.stt_enabled = result.enabled === true;
  }
  if (state.avatarSpeech?.selected_avatar) {
    state.avatarSpeech.selected_avatar.stt = {
      ...state.avatarSpeech.selected_avatar.stt,
      enabled: result.enabled === true,
    };
  }
  const sttCheckbox = document.getElementById("stt-enabled");
  if (sttCheckbox) {
    sttCheckbox.checked = result.enabled === true;
  }
  return result;
}

async function setTtsEnabled(enabled) {
  const result = await apiRequest("/ui/api/audio/tts-enabled", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
  if (state.audioRuntime) {
    state.audioRuntime.tts_enabled = result.enabled === true;
  }
  if (state.avatarSpeech?.selected_avatar) {
    state.avatarSpeech.selected_avatar.tts = {
      ...state.avatarSpeech.selected_avatar.tts,
      enabled: result.enabled === true,
    };
  }
  const ttsCheckbox = document.getElementById("tts-enabled");
  if (ttsCheckbox) {
    ttsCheckbox.checked = result.enabled === true;
  }
  return result;
}

async function toggleWebTts() {
  if (state.webAudio.ttsToggling) {
    return;
  }
  const current = state.audioRuntime?.tts_enabled === true;
  state.webAudio.ttsToggling = true;
  renderWebMicrophoneControls();
  try {
    await setTtsEnabled(!current);
  } catch (error) {
    showNotice(`音声合成の切替に失敗しました: ${error.message}`, true);
  } finally {
    state.webAudio.ttsToggling = false;
    renderWebMicrophoneControls();
  }
}

async function toggleWebMicrophone() {
  if (
    state.webAudio.starting
    || state.webAudio.stopping
    || state.webAudio.sttToggling
  ) {
    return;
  }
  const source = state.audioRuntime?.configured_source || "";
  if (!Object.hasOwn(MICROPHONE_SOURCE_LABELS, source)) {
    showNotice("保存済みのマイク入力元を取得できません。", true);
    return;
  }
  const sttEnabled = state.audioRuntime?.stt_enabled === true;
  const usesWebDevice = source === "web_microphone";
  const hasSession = Boolean(state.webAudio.inputSessionId);

  state.webAudio.sttToggling = true;
  renderWebMicrophoneControls();
  try {
    if (!sttEnabled) {
      // 共通運用: STT を ON にする。Web マイク時だけこのタブで capture を始める。
      await setSttEnabled(true);
      if (usesWebDevice) {
        await startWebMicrophone();
      } else {
        renderWebMicrophoneControls();
      }
      return;
    }
    if (usesWebDevice && !hasSession) {
      // 他接点で STT だけ ON の場合、このタブは capture を開始する。
      await startWebMicrophone();
      return;
    }
    // STT OFF。Web session は server 側でも終了するが、capture は先に解放する。
    await stopWebMicrophone();
    await setSttEnabled(false);
    renderWebMicrophoneControls();
  } catch (error) {
    showNotice(`マイク切替に失敗しました: ${error.message}`, true);
    renderWebMicrophoneControls();
  } finally {
    state.webAudio.sttToggling = false;
    renderWebMicrophoneControls();
  }
}

async function startWebMicrophone() {
  if (state.webAudio.inputSessionId || state.webAudio.starting) {
    return;
  }
  if (state.eventSocket?.readyState !== WebSocket.OPEN) {
    showNotice("イベント接続が完了してから音声入力を開始してください。", true);
    return;
  }
  const inputSource = state.audioRuntime?.configured_source || "";
  if (inputSource !== "web_microphone") {
    showNotice("ブラウザマイク入力元のときだけWeb取得を開始します。", true);
    return;
  }
  let deviceId = element("web-microphone-device").value;
  if (!deviceId) {
    // 未許可ではdevice IDを列挙できないbrowserがあるため、開始操作から許可を取得する。
    const refreshed = await refreshWebMicrophoneDevices({ requestPermission: true });
    if (!refreshed) {
      return;
    }
    deviceId = element("web-microphone-device").value;
    if (!deviceId) {
      showNotice("使用するWebマイクを選択してください。", true);
      return;
    }
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

    const inputSession = await apiRequest("/ui/api/audio/input-sessions", {
      method: "POST",
      body: JSON.stringify({
        owner_client_id: state.clientId,
      }),
    });
    state.webAudio.inputSessionId = inputSession.input_session_id;
    state.webAudio.inputSource = inputSession.input_source;

    const socket = new WebSocket(websocketUrl("/ui/api/audio/stream"));
    state.webAudio.socket = socket;
    state.webAudio.starting = false;
    renderWebMicrophoneControls();
    socket.addEventListener("open", () => {
      if (
        state.webAudio.socket !== socket
        || state.webAudio.inputSessionId !== inputSession.input_session_id
      ) {
        socket.close();
        return;
      }
      const settings = track.getSettings();
      const actualBoolean = (value) => (
        typeof value === "boolean" ? value : null
      );
      socket.send(JSON.stringify({
        type: "audio_start",
        protocol_version: "2",
        client_id: state.clientId,
        input_source: "web_microphone",
        input_session_id: state.webAudio.inputSessionId,
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
      setWebMicrophoneStatus("入力準備中", "processing");
    });
    socket.addEventListener("message", (event) => {
      if (typeof event.data !== "string") {
        return;
      }
      try {
        handleWebAudioControl(socket, JSON.parse(event.data));
      } catch {
        showNotice("音声入力から不正な制御情報を受信しました。", true);
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
    showNotice(`音声入力を開始できません: ${error.message}`, true);
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
  const inputSessionId = state.webAudio.inputSessionId;

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
  state.webAudio.inputSessionId = null;
  state.webAudio.inputSource = null;
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
  if (inputSessionId) {
    try {
      await apiRequest(
        `/ui/api/audio/input-sessions/${encodeURIComponent(inputSessionId)}`,
        { method: "DELETE" },
      );
    } catch {
      // WebSocket切断でserver側が先にsessionを終了した場合も停止完了とする。
    }
  }
  state.webAudio.stopping = false;
  renderWebMicrophoneControls();
}

function updateAudioRuntimeState(runtimeState) {
  const hadEnrollment = Boolean(state.audioRuntime?.enrollment || state.activeEnrollment);
  syncSpeakerMeterResult(runtimeState);
  state.audioRuntime = clone(runtimeState);
  if (!state.settingsOpen) {
    // 設定パネル編集中は下書きを壊さない。通常画面では runtime を正本にする。
    const sttCheckbox = document.getElementById("stt-enabled");
    if (sttCheckbox) {
      sttCheckbox.checked = runtimeState.stt_enabled === true;
    }
    const ttsCheckbox = document.getElementById("tts-enabled");
    if (ttsCheckbox) {
      ttsCheckbox.checked = runtimeState.tts_enabled === true;
    }
  }
  renderWebMicrophoneControls();
  renderAudioMeters();
  if (runtimeState.enrollment) {
    state.activeEnrollment = {
      ...state.activeEnrollment,
      ...runtimeState.enrollment,
    };
  } else if (hadEnrollment) {
    state.activeEnrollment = null;
    refreshSpeakers();
  }
  if (
    state.webAudio.inputSessionId
    && (
      runtimeState.stt_enabled !== true
      || runtimeState.configured_source !== "web_microphone"
    )
  ) {
    // STT OFF・session 失効・入力元変更ではブラウザ capture を解放する。
    stopWebMicrophone({ sendStop: false });
  }
  renderSpeakerEnrollment();
}

// 設定スライダは未保存値も thr マーカーに使う。それ以外は保存済み設定。
function currentVadThreshold() {
  const slider = element("vad-probability-threshold");
  if (slider && state.settingsOpen) {
    return numberValue("vad-probability-threshold", 0.5);
  }
  const saved = state.avatarSpeech?.microphone_settings?.vad_probability_threshold;
  return typeof saved === "number" ? saved : 0.5;
}

function currentSpeakerThreshold() {
  const slider = element("speaker-recognition-threshold");
  if (slider && state.settingsOpen) {
    return numberValue("speaker-recognition-threshold", 0.6);
  }
  const saved = state.avatarSpeech?.microphone_settings?.speaker_recognition_threshold;
  return typeof saved === "number" ? saved : 0.6;
}

function clampMeterRatio(ratio) {
  return Math.min(1, Math.max(0, ratio));
}

function dbfsToRatio(dbfs) {
  // 固定レンジ: MIN..MAX dBFS。無音 null は 0。MAX 超過は 100% clip。
  if (typeof dbfs !== "number" || !Number.isFinite(dbfs)) {
    return 0;
  }
  const span = AUDIO_METER_LEVEL_MAX_DBFS - AUDIO_METER_LEVEL_MIN_DBFS;
  if (span <= 0) {
    return 0;
  }
  return clampMeterRatio((dbfs - AUDIO_METER_LEVEL_MIN_DBFS) / span);
}

// VAD probability / speaker similarity を 0..1 固定スケールへ写像する。
function unitMeterRatio(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 0;
  }
  const span = AUDIO_METER_UNIT_MAX - AUDIO_METER_UNIT_MIN;
  if (span <= 0) {
    return 0;
  }
  return clampMeterRatio((value - AUDIO_METER_UNIT_MIN) / span);
}

function setMeterThreshold(markerId, ratio) {
  const marker = element(markerId);
  if (!marker) {
    return;
  }
  marker.style.left = `${(clampMeterRatio(ratio) * 100).toFixed(1)}%`;
}

function setMeterFill(fillId, ratio, row, { active = null, empty = false } = {}) {
  const fill = element(fillId);
  if (!fill || !row) {
    return;
  }
  const clamped = empty ? 0 : clampMeterRatio(ratio);
  fill.style.width = `${(clamped * 100).toFixed(1)}%`;
  row.dataset.empty = empty ? "true" : "false";
  if (active === null) {
    delete row.dataset.active;
  } else {
    row.dataset.active = active ? "true" : "false";
  }
}

// 同じ snapshot の再受信で保持時間を延ばさず、新しい発話結果だけ3秒表示する。
function syncSpeakerMeterResult(runtimeState) {
  const last = runtimeState?.last_utterance_result || null;
  const utteranceSeq = Number.isInteger(last?.utterance_seq)
    ? last.utterance_seq
    : null;
  if (utteranceSeq === null) {
    state.audioMeters.lastSpeakerUtteranceSeq = null;
    state.audioMeters.speakerResult = null;
    window.clearTimeout(state.audioMeters.speakerResetTimer);
    state.audioMeters.speakerResetTimer = null;
    return;
  }
  if (utteranceSeq === state.audioMeters.lastSpeakerUtteranceSeq) {
    return;
  }

  state.audioMeters.lastSpeakerUtteranceSeq = utteranceSeq;
  const top1 = last.top1_similarity;
  state.audioMeters.speakerResult = (
    typeof top1 === "number" && Number.isFinite(top1)
      ? clone(last)
      : null
  );
  window.clearTimeout(state.audioMeters.speakerResetTimer);
  state.audioMeters.speakerResetTimer = null;
  if (!state.audioMeters.speakerResult) {
    return;
  }
  state.audioMeters.speakerResetTimer = window.setTimeout(() => {
    state.audioMeters.speakerResult = null;
    state.audioMeters.speakerResetTimer = null;
    renderAudioMeters();
  }, SPEAKER_METER_HOLD_MS);
}

function renderAudioMeters() {
  const runtime = state.audioRuntime;
  const vad = runtime?.vad || {};
  const speakerResult = state.audioMeters.speakerResult;
  const available = runtime?.available === true;
  const hasActiveSource = Boolean(runtime?.active_source);
  const liveReady = available && hasActiveSource;
  const vadThreshold = currentVadThreshold();
  const speakerThreshold = currentSpeakerThreshold();

  // --- 入力レベル ---
  const levelRows = document.querySelectorAll('.audio-meter-row[data-meter="level"]');
  const dbfs = vad.dbfs;
  const hasLevel = liveReady && typeof dbfs === "number" && Number.isFinite(dbfs);
  const levelText = hasLevel ? `${dbfs.toFixed(1)} dBFS` : (liveReady ? "silence" : "—");
  for (const row of levelRows) {
    const isSettings = row.closest(".audio-meters-settings");
    const fillId = isSettings ? "settings-meter-level-fill" : "audio-meter-level-fill";
    const valueId = isSettings ? "settings-meter-level-value" : "audio-meter-level-value";
    setMeterFill(fillId, dbfsToRatio(dbfs), row, { empty: !hasLevel });
    element(valueId).textContent = levelText;
  }

  // --- VAD（値が未取得でも 0.0 としてしきい値と併記する） ---
  const vadRows = document.querySelectorAll('.audio-meter-row[data-meter="vad"]');
  const probability = (
    liveReady && typeof vad.probability === "number" && Number.isFinite(vad.probability)
      ? vad.probability
      : 0
  );
  const vadExceeded = probability >= vadThreshold;
  const vadValueText = `P ${probability.toFixed(2)} / ${vadThreshold.toFixed(2)}`;
  for (const row of vadRows) {
    const isSettings = row.closest(".audio-meters-settings");
    const fillId = isSettings ? "settings-meter-vad-fill" : "audio-meter-vad-fill";
    const thrId = isSettings ? "settings-meter-vad-threshold" : "audio-meter-vad-threshold";
    const valueId = isSettings ? "settings-meter-vad-value" : "audio-meter-vad-value";
    setMeterFill(fillId, unitMeterRatio(probability), row, {
      active: vadExceeded,
    });
    setMeterThreshold(thrId, unitMeterRatio(vadThreshold));
    element(valueId).textContent = vadValueText;
  }

  // --- 識別（新しい発話結果を3秒間だけ表示） ---
  const speakerRows = document.querySelectorAll('.audio-meter-row[data-meter="speaker"]');
  const top1 = speakerResult?.top1_similarity ?? 0;
  const hasSpeaker = speakerResult !== null;
  // threshold_met はサーバ処理時のしきい値。表示マーカーは現在スライダも示す。
  const thresholdMet = speakerResult?.threshold_met === true;
  const speakerValueText = `sim ${top1.toFixed(2)} / thr ${speakerThreshold.toFixed(2)}`;
  for (const row of speakerRows) {
    const isSettings = row.closest(".audio-meters-settings");
    const fillId = isSettings ? "settings-meter-speaker-fill" : "audio-meter-speaker-fill";
    const thrId = isSettings ? "settings-meter-speaker-threshold" : "audio-meter-speaker-threshold";
    const valueId = isSettings ? "settings-meter-speaker-value" : "audio-meter-speaker-value";
    setMeterFill(fillId, unitMeterRatio(top1), row, {
      active: hasSpeaker ? thresholdMet : false,
    });
    setMeterThreshold(thrId, unitMeterRatio(speakerThreshold));
    element(valueId).textContent = speakerValueText;
  }
}

function addMessage(kind, text, images = [], options = {}) {
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
  if (options.cycleId) {
    const link = document.createElement("a");
    link.className = "message-cycle-link";
    link.href = `/ui/cycles?cycle_id=${encodeURIComponent(options.cycleId)}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "判断で開く";
    bubble.append(document.createElement("br"), link);
  }

  if (kind === "system") {
    wrapper.append(bubble);
  } else {
    // CocoroConsole と同じく、表示名は person バルーン直上、時刻はバルーンの外側に置く。
    const content = document.createElement("div");
    content.className = "message-content";
    if (kind === "person" || kind === "assistant") {
      const displayName = document.createElement("div");
      displayName.className = "message-display-name";
      displayName.textContent = options.displayName;
      content.append(displayName);
    }
    content.append(bubble);

    const time = document.createElement("div");
    time.className = "message-time";
    time.textContent = nowLabel();
    if (kind === "person") {
      wrapper.append(time, content);
    } else {
      wrapper.append(content, time);
    }
  }
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
    const cycleId = typeof result.cycle_id === "string" ? result.cycle_id : "";
    return {
      kind: "system",
      text: cycleId
        ? `応答の生成に失敗しました。「判断」で cycle を確認するか、「ログ」を見てください。\ncycle: ${cycleId}`
        : "応答の生成に失敗しました。「判断」または「ログ」を確認してください。",
      cycleId,
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
  if (!state.eventSocket || state.eventSocket.readyState !== WebSocket.OPEN) {
    showNotice("イベント接続が完了してから送信してください。", true);
    return;
  }
  const messageId = `chat_message:${idSuffix()}`;
  state.pendingConversationInputs.set(messageId, { images });
  input.value = "";
  clearAttachment();
  state.sending = true;
  element("send-message").disabled = true;
  setStatus("状態: 会話入力処理中", "processing");
  try {
    const result = await apiRequest("/ui/api/conversation", {
      method: "POST",
      body: JSON.stringify({
        message_id: messageId,
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
          source_kind: "user_message",
          client_id: state.clientId,
          locale: navigator.language,
        },
      }),
    });
    if (result?.result_kind !== "speech") {
      const rendered = resultText(result);
      addMessage(rendered.kind, rendered.text, [], { cycleId: rendered.cycleId || "" });
    }
    await loadStatus({ silent: true });
    await refreshDashboard({ silent: true });
  } catch (error) {
    state.pendingConversationInputs.delete(messageId);
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
      audioOutputDevices,
      speakers,
      conversationDisplayNames,
    ] = await Promise.all([
      apiRequest("/ui/api/config/editor-state"),
      apiRequest("/ui/api/config/avatar-speech/editor-state"),
      apiRequest("/ui/api/config/camera-sources/editor-state"),
      apiRequest("/ui/api/config/mcp-servers/editor-state"),
      apiRequest("/ui/api/docs"),
      apiRequest("/ui/api/audio/input-devices"),
      apiRequest("/ui/api/audio/output-devices"),
      apiRequest("/ui/api/audio/speakers"),
      apiRequest("/ui/api/config/conversation-display-names"),
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
    // 端末が無くても取得方針は編集できるよう既定を常に読む。
    const desktopCaptureDefaults = await apiRequest(
      "/ui/api/config/desktop-capture-defaults",
    );
    state.editor = clone(editor);
    state.avatarSpeech = clone(avatarSpeech);
    state.consoleClient = consoleClient ? clone(consoleClient) : null;
    state.desktopCaptureDefaults = clone(
      desktopCaptureDefaults?.desktop_capture || desktopCaptureDefaults,
    );
    state.camera = clone(camera);
    state.mcp = clone(mcp);
    state.apiDocs = clone(apiDocs);
    state.audioInputDevices = clone(audioInputDevices);
    state.audioOutputDevices = clone(audioOutputDevices);
    state.speakers = clone(speakers.speakers || []);
    state.conversationDisplayNames = clone(
      conversationDisplayNames.conversation_display_names || [],
    );
    resetMemoryDraftMeta(state.editor.memory_sets);
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

// コレクション内の名前重複を検出し、最初の重複名を返す。空文字は対象外。
function findDuplicateName(names) {
  const seen = new Set();
  for (const raw of names) {
    const name = typeof raw === "string" ? raw.trim() : "";
    if (!name) {
      continue;
    }
    if (seen.has(name)) {
      return name;
    }
    seen.add(name);
  }
  return "";
}

async function saveSettings({ closeAfterSave = false } = {}) {
  try {
    syncAllForms();
    const duplicateCameraName = findDuplicateName(
      (state.camera?.camera_sources || []).map((camera) => camera.display_name),
    );
    if (duplicateCameraName) {
      showNotice(`カメラの表示名「${duplicateCameraName}」が重複しています。`, true);
      return;
    }
    const duplicateMcpName = findDuplicateName(
      (state.mcp?.mcp_servers || []).map((server) => server.mcp_server_id),
    );
    if (duplicateMcpName) {
      showNotice(`MCP の名前「${duplicateMcpName}」が重複しています。`, true);
      return;
    }
    // 記憶実体の clone は editor-state 置換の前に専用 endpoint で確定する。
    const pendingClones = (state.editor.memory_sets || []).filter((memory) => {
      const meta = memoryDraftMeta(memory.memory_set_id);
      return !meta.serverBacked && Boolean(meta.cloneSourceMemorySetId);
    });
    for (const memory of pendingClones) {
      const meta = memoryDraftMeta(memory.memory_set_id);
      await apiRequest("/ui/api/config/memory-sets/clone", {
        method: "POST",
        body: JSON.stringify({
          source_memory_set_id: meta.cloneSourceMemorySetId,
          memory_set_id: memory.memory_set_id,
          display_name: memory.display_name,
        }),
      });
    }
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
      // アバター複製で増やした VRM 表示設定を、音声設定保存後に端末設定へ反映する。
      const consoleSettingsPatch = {
        process: consoleClient.settings.process,
        desktop_capture: consoleClient.settings.desktop_capture,
        avatar_presentations: consoleClient.settings.avatar_presentations,
      };
      consoleClient = await apiRequest(
        `/ui/api/config/console-clients/${encodeURIComponent(consoleClient.client_id)}`,
        {
          method: "PATCH",
          body: JSON.stringify(consoleSettingsPatch),
        },
      );
    } else if (state.desktopCaptureDefaults) {
      // 未接続時は取得方針既定だけを永続化し、初回 connect で端末へ渡す。
      const defaultsResponse = await apiRequest(
        "/ui/api/config/desktop-capture-defaults",
        {
          method: "PUT",
          body: JSON.stringify({ desktop_capture: state.desktopCaptureDefaults }),
        },
      );
      state.desktopCaptureDefaults = clone(
        defaultsResponse?.desktop_capture || defaultsResponse,
      );
    }
    state.editor = clone(editor);
    state.avatarSpeech = clone(avatarSpeech);
    state.camera = clone(camera);
    state.mcp = clone(mcp);
    state.consoleClient = consoleClient ? clone(consoleClient) : null;
    if (consoleClient?.settings?.desktop_capture) {
      state.desktopCaptureDefaults = clone(consoleClient.settings.desktop_capture);
    }
    resetMemoryDraftMeta(state.editor.memory_sets);
    const selectedDisplayName = arrayById(
      state.conversationDisplayNames,
      "conversation_display_name_id",
      state.editor.current.selected_conversation_display_name_id,
    );
    state.conversationDisplayName = selectedDisplayName?.display_name || "";
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
  renderPreSendCheck();
  renderApiDocumentation();
}

function renderApiDocumentation() {
  const baseUrl = window.location.origin;
  element("api-doc-base-url").textContent = baseUrl;
  element("watcher-wake-api-url").textContent = `${baseUrl}/api/wake`;
  const sections = state.apiDocs?.sections || [];
  const documents = sections.map((section) => {
    const details = document.createElement("details");
    details.className = "advanced-settings";

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
  element("stt-profile-id").value = stt.profile_id;
  element("stt-api-key").value = stt.api_key;
  element("tts-enabled").checked = tts.enabled;
  element("tts-engine").value = tts.engine;
  element("voicevox-endpoint-url").value = voicevox.endpoint_url;
  element("voicevox-secondary-endpoint-url").value = voicevox.secondary_endpoint_url ?? "";
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
  avatar.stt.profile_id = textValue("stt-profile-id");
  avatar.stt.api_key = textValue("stt-api-key");
  avatar.tts.enabled = boolValue("tts-enabled");
  avatar.tts.engine = textValue("tts-engine");
  avatar.tts.voicevox_config = {
    endpoint_url: textValue("voicevox-endpoint-url"),
    secondary_endpoint_url: textValue("voicevox-secondary-endpoint-url"),
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
  const hasConsoleClient = Boolean(state.consoleClient);
  document.querySelectorAll("[data-console-setting]").forEach((fieldset) => {
    // VRM など常時無効の欄だけ止め、desktop 取得方針は未接続でも編集する。
    if (fieldset.hasAttribute("data-always-disabled")) {
      fieldset.disabled = true;
      return;
    }
    // デスクトップ取得方針は defaults でも編集できる。
    if (fieldset.querySelector("#desktop-capture-idle-timeout")) {
      fieldset.disabled = false;
      return;
    }
    fieldset.disabled = !hasConsoleClient;
  });
  // 思考前デスクトップ観測は server 側 wake_policy なので常に編集可。
  element("current-wake-desktop-observation").disabled = false;

  const desktop = hasConsoleClient
    ? state.consoleClient.settings.desktop_capture
    : state.desktopCaptureDefaults;
  if (desktop) {
    element("desktop-capture-idle-timeout").value = desktop.idle_timeout_minutes;
    element("desktop-capture-exclude-patterns").value = (desktop.exclude_patterns || []).join(
      "\n",
    );
  }
  renderAvatarPresentation();
}

function syncConsoleClientSettings() {
  const idleTimeout = intValue("desktop-capture-idle-timeout", 10);
  const excludePatterns = parseLines(textValue("desktop-capture-exclude-patterns"));
  if (state.consoleClient) {
    const settings = state.consoleClient.settings;
    // process は port 群だけを正本にする。旧 conversation_input_enabled を残さない。
    settings.process = {
      console_api_port: settings.process.console_api_port,
      cocoro_shell_port: settings.process.cocoro_shell_port,
    };
    // CocoroConsoleに表示しない取得方式の設定値は変更せず保持する。
    settings.desktop_capture = {
      ...settings.desktop_capture,
      idle_timeout_minutes: idleTimeout,
      exclude_patterns: excludePatterns,
    };
    state.desktopCaptureDefaults = clone(settings.desktop_capture);
    return;
  }
  if (!state.desktopCaptureDefaults) {
    state.desktopCaptureDefaults = {
      enabled: false,
      capture_active_window_only: true,
      idle_timeout_minutes: idleTimeout,
      exclude_patterns: excludePatterns,
    };
    return;
  }
  state.desktopCaptureDefaults = {
    ...state.desktopCaptureDefaults,
    idle_timeout_minutes: idleTimeout,
    exclude_patterns: excludePatterns,
  };
}

function renderTtsPanel(engine) {
  document.querySelectorAll("[data-tts-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.ttsPanel !== engine;
  });
}

function renderMicrophoneSettings() {
  const microphone = state.avatarSpeech.microphone_settings;
  element("microphone-input-source").value = microphone.input_source;
  element("microphone-console-client-id").value = microphone.console?.client_id || "未設定";
  element("microphone-console-device").value = microphone.console?.input_device?.name || "未設定";
  renderLocalInputDevices(microphone.local_input_device);
  const audioOutput = state.avatarSpeech.audio_output_settings;
  element("audio-output-destination").value = audioOutput.destination;
  renderLocalOutputDevices(audioOutput.local_output_device);
  element("vad-probability-threshold").value = microphone.vad_probability_threshold;
  element("speaker-recognition-threshold").value = microphone.speaker_recognition_threshold;
  updateMicrophoneSettingLabels();
  renderSpeakerList();
  renderSpeakerEnrollment();
}

function syncMicrophoneSettings() {
  const selectedDevice = textValue("local-input-device");
  state.avatarSpeech.microphone_settings = {
    input_source: textValue("microphone-input-source"),
    local_input_device: selectedDevice ? JSON.parse(selectedDevice) : null,
    console: state.avatarSpeech.microphone_settings.console,
    vad_probability_threshold: numberValue("vad-probability-threshold", 0.5),
    speaker_recognition_threshold: numberValue("speaker-recognition-threshold", 0.6),
  };
  const selectedOutputDevice = textValue("local-output-device");
  state.avatarSpeech.audio_output_settings = {
    destination: textValue("audio-output-destination"),
    local_output_device: selectedOutputDevice ? JSON.parse(selectedOutputDevice) : null,
  };
}

function updateMicrophoneSettingLabels() {
  element("vad-probability-threshold-value").textContent =
    numberValue("vad-probability-threshold", 0.5).toFixed(2);
  element("speaker-recognition-threshold-value").textContent =
    numberValue("speaker-recognition-threshold", 0.6).toFixed(2);
  // しきい値スライダ操作中は未保存値を thr マーカーへ即時反映する。
  renderAudioMeters();
}

function renderLocalInputDevices(selectedDevice) {
  const select = element("local-input-device");
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

function renderLocalOutputDevices(selectedDevice) {
  const select = element("local-output-device");
  const selectedValue = selectedDevice
    ? JSON.stringify({ host_api: selectedDevice.host_api, name: selectedDevice.name })
    : "";
  select.innerHTML = "";
  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = "未選択";
  select.append(emptyOption);
  let selectedFound = false;
  for (const device of state.audioOutputDevices?.devices || []) {
    const option = document.createElement("option");
    option.value = JSON.stringify({ host_api: device.host_api, name: device.name });
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
    unavailableOption.textContent = `${selectedDevice.host_api}: ${selectedDevice.name} / 現在利用不可`;
    select.append(unavailableOption);
  }
  select.value = selectedValue;
}

async function refreshSpeakers() {
  try {
    const response = await apiRequest("/ui/api/audio/speakers");
    state.speakers = clone(response.speakers || []);
    renderSpeakerEnrollment();
  } catch (error) {
    showNotice(error.message, true);
  }
}

function conversationDisplayNameErrorMessage(error) {
  if (error?.code === "duplicate_conversation_display_name") {
    return "同じ呼ばれ方が既に登録されています。";
  }
  if (error?.code === "conversation_display_name_in_use") {
    return "この呼ばれ方は最後の1件か、音声話者に割り当てられているため削除できません。";
  }
  if (error?.code === "conversation_display_name_not_found") {
    return "指定した呼ばれ方が見つかりません。";
  }
  return error?.message || "呼ばれ方の操作に失敗しました。";
}

async function refreshConversationDisplayNames(preferredSelectedId = undefined) {
  const response = await apiRequest("/ui/api/config/conversation-display-names");
  state.conversationDisplayNames = clone(response.conversation_display_names || []);
  if (preferredSelectedId !== undefined && state.editor) {
    state.editor.current.selected_conversation_display_name_id = preferredSelectedId;
  }
  // 呼ばれ方は常に1件選択する。選択中が消えた場合は残りの先頭へ寄せる。
  if (state.editor) {
    const selectedStillExists = arrayById(
      state.conversationDisplayNames,
      "conversation_display_name_id",
      state.editor.current.selected_conversation_display_name_id,
    );
    if (!selectedStillExists) {
      state.editor.current.selected_conversation_display_name_id =
        state.conversationDisplayNames[0]?.conversation_display_name_id || null;
    }
  }
  const selectedDisplayName = arrayById(
    state.conversationDisplayNames,
    "conversation_display_name_id",
    state.editor?.current?.selected_conversation_display_name_id,
  );
  state.conversationDisplayName = selectedDisplayName?.display_name || "";
  if (state.editor) {
    renderCurrent();
  }
  renderSpeakerEnrollment();
}

function selectedConversationDisplayNameId() {
  return element("settings-conversation-display-name-select").value || "";
}

function syncConversationDisplayNameEditFromSelection() {
  const conversationDisplayNameId = selectedConversationDisplayNameId();
  if (state.editor && conversationDisplayNameId) {
    state.editor.current.selected_conversation_display_name_id =
      conversationDisplayNameId;
  }
  const definition = arrayById(
    state.conversationDisplayNames,
    "conversation_display_name_id",
    conversationDisplayNameId,
  );
  element("conversation-display-name-edit").value = definition?.display_name || "";
  state.conversationDisplayName = definition?.display_name || "";
}

async function addConversationDisplayName() {
  const displayName = element("conversation-display-name-edit").value.trim();
  if (!displayName) {
    showNotice("追加する呼ばれ方を入力してください。", true);
    return;
  }
  try {
    const created = await apiRequest("/ui/api/config/conversation-display-names", {
      method: "POST",
      body: JSON.stringify({ display_name: displayName }),
    });
    const createdId = created.conversation_display_name_id || null;
    await refreshConversationDisplayNames(createdId);
    showNotice(`呼ばれ方「${displayName}」を追加しました。`);
  } catch (error) {
    showNotice(conversationDisplayNameErrorMessage(error), true);
  }
}

async function updateConversationDisplayName() {
  const conversationDisplayNameId = selectedConversationDisplayNameId();
  const definition = arrayById(
    state.conversationDisplayNames,
    "conversation_display_name_id",
    conversationDisplayNameId,
  );
  if (!definition) {
    showNotice("変更する呼ばれ方を、上の一覧から選択してください。", true);
    return;
  }
  const displayName = element("conversation-display-name-edit").value.trim();
  if (!displayName) {
    showNotice("呼ばれ方を入力してください。", true);
    return;
  }
  if (displayName === definition.display_name) {
    showNotice("呼ばれ方は変更されていません。");
    return;
  }
  try {
    await apiRequest(
      `/ui/api/config/conversation-display-names/${encodeURIComponent(conversationDisplayNameId)}`,
      {
        method: "PUT",
        body: JSON.stringify({ display_name: displayName }),
      },
    );
    await Promise.all([
      refreshConversationDisplayNames(conversationDisplayNameId),
      refreshSpeakers(),
    ]);
    showNotice(`呼ばれ方を「${displayName}」に変更しました。`);
  } catch (error) {
    showNotice(conversationDisplayNameErrorMessage(error), true);
  }
}

async function deleteConversationDisplayName() {
  const conversationDisplayNameId = selectedConversationDisplayNameId();
  const definition = arrayById(
    state.conversationDisplayNames,
    "conversation_display_name_id",
    conversationDisplayNameId,
  );
  if (!definition) {
    showNotice("削除する呼ばれ方を、上の一覧から選択してください。", true);
    return;
  }
  if (!window.confirm(`「${definition.display_name}」を削除しますか？`)) {
    return;
  }
  try {
    try {
      await apiRequest(
        `/ui/api/config/conversation-display-names/${encodeURIComponent(conversationDisplayNameId)}`,
        { method: "DELETE" },
      );
    } catch (error) {
      if (error.code === "conversation_display_name_in_use") {
        showNotice(
          "この呼ばれ方は最後の1件か、音声話者に割り当てられているため削除できません。",
          true,
        );
        return;
      }
      throw error;
    }
    await Promise.all([
      refreshConversationDisplayNames(
        state.editor?.current?.selected_conversation_display_name_id,
      ),
      refreshSpeakers(),
    ]);
    showNotice(`呼ばれ方「${definition.display_name}」を削除しました。`);
  } catch (error) {
    showNotice(conversationDisplayNameErrorMessage(error), true);
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
    const name = document.createElement("select");
    name.dataset.speakerDisplayNameSelect = speaker.person_ref;
    const availableDefinitions = state.conversationDisplayNames.filter((definition) => (
      definition.conversation_display_name_id === speaker.conversation_display_name_id
      || !state.speakers.some((candidate) => (
        candidate.person_ref !== speaker.person_ref
        && candidate.conversation_display_name_id === definition.conversation_display_name_id
      ))
    ));
    setSelectOptions(
      name,
      availableDefinitions,
      "conversation_display_name_id",
      speaker.conversation_display_name_id,
    );
    const registration = document.createElement("span");
    registration.className = "speaker-registration-status";
    registration.textContent = speaker.registration_status === "registered"
      ? "音声登録済み"
      : "音声未登録";

    const actions = document.createElement("div");
    actions.className = "speaker-actions";
    for (const [action, label, className] of [
      ["assign", "割当変更", "plain-button"],
      ["reenroll", "再登録", "plain-button"],
      ["unregister", "登録解除", "danger-button"],
    ]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
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
    row.append(name, registration, actions);
    return row;
  });
  container.replaceChildren(...rows);
}

function renderSpeakerEnrollment() {
  const enrollment = state.activeEnrollment || state.audioRuntime?.enrollment;
  const startButton = element("start-speaker-enrollment");
  const cancelButton = element("cancel-speaker-enrollment");
  const assignedIds = new Set(
    state.speakers.map((speaker) => speaker.conversation_display_name_id),
  );
  const availableDefinitions = state.conversationDisplayNames.filter(
    (definition) => !assignedIds.has(definition.conversation_display_name_id),
  );
  const hasInputLease = webMicrophoneHasLease();
  setSelectOptions(
    element("speaker-enrollment-display-name-id"),
    availableDefinitions,
    "conversation_display_name_id",
    element("speaker-enrollment-display-name-id").value,
  );
  startButton.disabled = Boolean(enrollment)
    || !hasInputLease
    || availableDefinitions.length === 0;
  cancelButton.disabled = !enrollment;
  if (!enrollment) {
    let status = "話者登録を開始できます";
    if (availableDefinitions.length === 0) {
      status = "新規登録できる未割当の呼ばれ方がありません";
    } else if (!hasInputLease) {
      status = "音声入力が有効になってから話者登録を開始してください";
    }
    element("speaker-enrollment-status").textContent = status;
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
    showNotice("音声入力が有効になってから話者登録を開始してください。", true);
    return;
  }
  const body = personRef
    ? { owner_client_id: state.clientId, person_ref: personRef }
    : {
      owner_client_id: state.clientId,
      conversation_display_name_id: element("speaker-enrollment-display-name-id").value,
    };
  if (!personRef && !body.conversation_display_name_id) {
    showNotice("未割当の呼ばれ方を登録してください。", true);
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

async function assignSpeakerDisplayName(personRef, conversationDisplayNameId) {
  try {
    await apiRequest(
      `/ui/api/audio/speakers/${encodeURIComponent(personRef)}/conversation-display-name`,
      {
        method: "PUT",
        body: JSON.stringify({
          conversation_display_name_id: conversationDisplayNameId,
        }),
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
  const wakePolicy = state.editor.current.wake_policy || {};
  const observations = Array.isArray(wakePolicy.observations) ? wakePolicy.observations : [];
  setSelectOptions(
    element("settings-conversation-display-name-select"),
    state.conversationDisplayNames,
    "conversation_display_name_id",
    state.editor.current.selected_conversation_display_name_id,
  );
  syncConversationDisplayNameEditFromSelection();
  element("current-thinking-level").value = state.editor.current.thinking_speech_level ?? 5;
  element("current-wake-enabled").checked = wakePolicy.mode === "interval";
  element("current-wake-interval").value = wakePolicy.interval_seconds;
  element("current-wake-desktop-observation").checked =
    observations.some((observation) => isDesktopWakeObservation(observation));
}

const PRE_SEND_CHECK_MODEL_PRESET_ID = "model_preset:pre_send_check";

function generationModelPresets() {
  return (state.editor?.model_presets || []).filter(
    (preset) => preset.model_preset_id !== PRE_SEND_CHECK_MODEL_PRESET_ID,
  );
}

function preSendCheckModelPreset() {
  let preset = arrayById(
    state.editor?.model_presets || [],
    "model_preset_id",
    PRE_SEND_CHECK_MODEL_PRESET_ID,
  );
  if (preset) {
    return preset;
  }
  // editor-state に専用定義が無い旧下書き向けに、その場で確保する。
  preset = {
    model_preset_id: PRE_SEND_CHECK_MODEL_PRESET_ID,
    display_name: "送信前チェック",
    prompt_window: {
      recent_turn_limit: 30,
      recent_turn_minutes: 30,
    },
    model: "",
    api_key: "",
    max_output_tokens: 4000,
    timeout_seconds: 90,
    web_search_enabled: false,
  };
  if (state.editor) {
    state.editor.model_presets = state.editor.model_presets || [];
    state.editor.model_presets.push(preset);
    if (state.editor.current) {
      state.editor.current.pre_send_check_model_preset_id =
        PRE_SEND_CHECK_MODEL_PRESET_ID;
    }
  }
  return preset;
}

function renderPreSendCheck() {
  if (!state.editor?.current || !state.mcp) {
    return;
  }
  const preset = preSendCheckModelPreset();
  element("pre-send-check-model").value = preset.model || "";
  element("pre-send-check-api-base").value = preset.api_base || "";
  element("pre-send-check-api-key").value = preset.api_key || "";
  element("pre-send-check-reasoning-effort").value = preset.reasoning_effort || "";
  element("pre-send-check-max-output-tokens").value = preset.max_output_tokens || 4000;
  element("pre-send-check-timeout-seconds").value = preset.timeout_seconds || 90;
  renderPreSendCheckMcpList();
}

function renderPreSendCheckMcpList() {
  const container = element("pre-send-check-mcp-list");
  if (!container) {
    return;
  }
  container.replaceChildren();
  const servers = state.mcp?.mcp_servers || [];
  if (!servers.length) {
    const empty = document.createElement("span");
    empty.className = "pre-send-check-mcp-empty";
    empty.textContent = "MCP server がありません。接続の MCP タブで追加してください。";
    container.appendChild(empty);
    return;
  }
  for (const server of servers) {
    const label = document.createElement("label");
    label.className = "checkbox-field";
    const name = server.mcp_server_id || "(unnamed)";
    label.appendChild(
      document.createTextNode(`${name}`),
    );
    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.mcpServerId = server.mcp_server_id || "";
    input.checked = server.pre_send_check_enabled === true;
    label.appendChild(input);
    container.appendChild(label);
  }
}

function syncPreSendCheck() {
  if (!state.editor?.current) {
    return;
  }
  const preset = preSendCheckModelPreset();
  preset.model = textValue("pre-send-check-model");
  preset.api_key = textValue("pre-send-check-api-key");
  preset.max_output_tokens = intValue("pre-send-check-max-output-tokens", 4000);
  preset.timeout_seconds = boundedIntValue(
    "pre-send-check-timeout-seconds",
    "タイムアウト（秒）",
    1,
  );
  preset.web_search_enabled = false;
  const apiBase = textValue("pre-send-check-api-base").trim();
  if (apiBase) {
    preset.api_base = apiBase;
  } else {
    delete preset.api_base;
  }
  const reasoningEffort = textValue("pre-send-check-reasoning-effort").trim();
  if (reasoningEffort) {
    preset.reasoning_effort = reasoningEffort;
  } else {
    delete preset.reasoning_effort;
  }
  state.editor.current.pre_send_check_model_preset_id =
    PRE_SEND_CHECK_MODEL_PRESET_ID;
  syncPreSendCheckMcpList();
}

function syncPreSendCheckMcpList() {
  const container = element("pre-send-check-mcp-list");
  if (!container || !state.mcp) {
    return;
  }
  const inputs = container.querySelectorAll("input[type='checkbox'][data-mcp-server-id]");
  for (const input of inputs) {
    const serverId = input.dataset.mcpServerId || "";
    if (!serverId) {
      continue;
    }
    const server = arrayById(state.mcp.mcp_servers || [], "mcp_server_id", serverId);
    if (server) {
      server.pre_send_check_enabled = input.checked;
    }
  }
}

function syncCurrent() {
  state.editor.current.selected_persona_id = state.selectedPersonaId;
  state.editor.current.selected_memory_set_id = state.selectedMemorySetId;
  state.editor.current.selected_model_preset_id = state.selectedModelPresetId;
  const selectedConversationDisplayNameIdValue =
    element("settings-conversation-display-name-select").value;
  if (selectedConversationDisplayNameIdValue) {
    state.editor.current.selected_conversation_display_name_id =
      selectedConversationDisplayNameIdValue;
  }
  state.editor.current.thinking_speech_level = intValue("current-thinking-level", 5);
  let observations = Array.isArray(state.editor.current.wake_policy?.observations)
    ? state.editor.current.wake_policy.observations
    : [];
  // デスクトップ観測方針は接続有無に依存せず wake_policy へ保存する。
  // 実要求（vision.capture_request）は接続中 source があるときだけ配送される。
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
  const clientId = state.consoleClient?.client_id?.trim();
  if (!clientId) {
    // 未接続時は kind 再解決用の安定 ID を使う。
    return "vision_source:desktop";
  }
  const sourceToken = Array.from(clientId)
    .filter((character) => /[\p{L}\p{N}_-]/u.test(character))
    .join("");
  return `vision_source:${sourceToken || "desktop"}:desktop`;
}

function isDesktopWakeObservation(observation) {
  if (observation?.observation_id === DESKTOP_WAKE_OBSERVATION_ID) {
    return true;
  }
  if (observation?.capability_id !== "vision.capture") {
    return false;
  }
  const sourceId = observation?.input?.vision_source_id;
  if (typeof sourceId !== "string" || !sourceId) {
    return false;
  }
  if (sourceId === desktopVisionSourceId()) {
    return true;
  }
  // 保存済み ID の末尾が desktop なら同一観測項目として扱う。
  return sourceId === "vision_source:desktop" || sourceId.endsWith(":desktop");
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
  element("persona-wake-words").value = (persona.wake_words || []).join("\n");
}

function syncPersona() {
  const persona = arrayById(state.editor.personas, "persona_id", state.selectedPersonaId);
  if (!persona) {
    return;
  }
  persona.display_name = textValue("persona-display-name");
  persona.persona_prompt = textValue("persona-prompt");
  persona.expression_addon = textValue("persona-expression-addon");
  persona.wake_words = parseLines(textValue("persona-wake-words"));
}

function renderModel() {
  const generationPresets = generationModelPresets();
  if (
    state.selectedModelPresetId === PRE_SEND_CHECK_MODEL_PRESET_ID
    || !arrayById(generationPresets, "model_preset_id", state.selectedModelPresetId)
  ) {
    state.selectedModelPresetId = generationPresets[0]?.model_preset_id || "";
    if (state.editor?.current && state.selectedModelPresetId) {
      state.editor.current.selected_model_preset_id = state.selectedModelPresetId;
    }
  }
  state.selectedModelPresetId = selectedOrFirst(
    generationPresets,
    "model_preset_id",
    state.selectedModelPresetId,
  );
  setSelectOptions(
    element("model-select"),
    generationPresets,
    "model_preset_id",
    state.selectedModelPresetId,
  );
  const preset = arrayById(generationPresets, "model_preset_id", state.selectedModelPresetId);
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

function pasteLlmApiKey(inputId) {
  const apiKey = preferredLlmApiKey();
  if (!apiKey) {
    showNotice("貼り付け元の LLM モデル API キーが空です。", true);
    return;
  }
  element(inputId).value = apiKey;
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

function setCollectionEditorEnabled(selectId, fieldsetSelector, deleteAction, hasItems) {
  // 0 件のときは選択・入力・削除を無効化し、追加だけ残す。
  const select = element(selectId);
  select.disabled = !hasItems;
  const page = select.closest(".tab-page");
  const fieldset = page?.querySelector(fieldsetSelector);
  if (fieldset) {
    fieldset.disabled = !hasItems;
  }
  const deleteButton = page?.querySelector(`[data-action="${deleteAction}"]`);
  if (deleteButton) {
    deleteButton.disabled = !hasItems;
  }
}

function renderCamera() {
  const cameras = state.camera.camera_sources || [];
  const hasCamera = cameras.length > 0;
  state.selectedCameraId = selectedOrFirst(cameras, "vision_source_id", state.selectedCameraId);
  setSelectOptions(element("camera-select"), cameras, "vision_source_id", state.selectedCameraId);
  setCollectionEditorEnabled("camera-select", "fieldset.settings-group", "delete-camera", hasCamera);
  const camera = arrayById(cameras, "vision_source_id", state.selectedCameraId);
  const connection = camera?.connection || {};
  element("camera-display-name").value = camera?.display_name || "";
  element("camera-host").value = connection.host || "";
  element("camera-username").value = connection.camera_username || "";
  element("camera-password").value = connection.camera_password || "";
  element("camera-connector-kind").value = camera?.connector_kind || "tapo_c220";
  element("camera-client-id").value = camera?.client_id || "tapo-c220-connector-main";
  element("camera-vision-source-id").value = camera?.vision_source_id || "";
  element("camera-watcher-id").value = cameraWatcher(camera).watcher_id;
  // 接続側の表示名変更を定期思考の観測一覧へ反映する。
  renderCameraObservations();
}

// 定期思考で観測するカメラの一覧を描画する。接続定義は接続タブ側で編集する。
function renderCameraObservations() {
  const container = element("wake-camera-observations");
  if (!container) {
    return;
  }
  const cameras = state.camera?.camera_sources || [];
  if (!cameras.length) {
    const empty = document.createElement("p");
    empty.className = "field-hint";
    empty.textContent = "登録済みカメラがありません。「接続 → カメラ」で登録してください。";
    container.replaceChildren(empty);
    return;
  }
  const labels = cameras.map((camera, index) => {
    const label = document.createElement("label");
    label.className = "checkbox-field";
    const displayName = camera.display_name || camera.vision_source_id || "カメラ";
    label.append(document.createTextNode(`${displayName} を観測する`));
    const input = document.createElement("input");
    input.type = "checkbox";
    // 表示名変更で vision_source_id が変わっても、描画順で同期できるよう index も持つ。
    input.dataset.cameraIndex = String(index);
    input.dataset.visionSourceId = camera.vision_source_id || "";
    input.checked = camera.enabled === true;
    label.append(input);
    return label;
  });
  container.replaceChildren(...labels);
}

// 定期思考タブのカメラ観測チェックを camera_source.enabled へ反映する。
function syncCameraObservations() {
  const container = element("wake-camera-observations");
  if (!container || !state.camera?.camera_sources) {
    return;
  }
  const checkboxes = [...container.querySelectorAll("input[type='checkbox'][data-camera-index]")];
  if (!checkboxes.length) {
    return;
  }
  const cameras = state.camera.camera_sources;
  // 一覧は render 時の camera_sources 順と対応させる。ID 再生成後も enabled を落とさない。
  if (checkboxes.length === cameras.length) {
    cameras.forEach((camera, index) => {
      camera.enabled = checkboxes[index].checked;
    });
    return;
  }
  const enabledById = new Map(
    checkboxes.map((input) => [input.dataset.visionSourceId, input.checked]),
  );
  for (const camera of cameras) {
    if (enabledById.has(camera.vision_source_id)) {
      camera.enabled = enabledById.get(camera.vision_source_id) === true;
    }
  }
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
  // 定期思考向けの観測有効フラグは接続フォームではなく観測一覧から同期する。
  syncCameraObservations();
  const camera = arrayById(state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  if (!camera) {
    return;
  }
  const previousVisionSourceId = camera.vision_source_id;
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
  const hasWatcher = items.length > 0;
  state.selectedWatcherSourceId = selectedOrFirst(items, "vision_source_id", state.selectedWatcherSourceId);
  setSelectOptions(element("watcher-select"), items, "vision_source_id", state.selectedWatcherSourceId);
  // Watcher はカメラ定義に従属するため、0 件時は入力を無効化する。
  const watcherSelect = element("watcher-select");
  watcherSelect.disabled = !hasWatcher;
  const watcherFieldset = watcherSelect.closest(".tab-page")?.querySelector("fieldset.settings-group");
  if (watcherFieldset) {
    watcherFieldset.disabled = !hasWatcher;
  }
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
  const displayName = camera?.display_name || "カメラ";
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
  const servers = state.mcp.mcp_servers || [];
  const hasMcp = servers.length > 0;
  state.selectedMcpId = selectedOrFirst(servers, "mcp_server_id", state.selectedMcpId);
  setSelectOptions(element("mcp-select"), servers, "mcp_server_id", state.selectedMcpId);
  setCollectionEditorEnabled("mcp-select", "fieldset.settings-group", "delete-mcp", hasMcp);
  const mcp = arrayById(servers, "mcp_server_id", state.selectedMcpId);
  element("mcp-enabled").checked = mcp?.enabled === true;
  element("mcp-server-id").value = mcp?.mcp_server_id || "";
  element("mcp-client-id").value = mcp?.client_id || "mcp-client-connector-main";
  element("mcp-transport").value = mcp?.transport || "stdio";
  element("mcp-command").value = mcp?.command || "";
  element("mcp-args").value = (mcp?.args || []).join("\n");
  element("mcp-cwd").value = mcp?.cwd || "";
  element("mcp-env").value = formatEnv(mcp?.env || {});
  element("mcp-url").value = mcp?.url || "";
  element("mcp-headers").value = formatEnv(mcp?.headers || {});
  element("mcp-autonomous-enabled").checked = mcp?.autonomous_session?.enabled === true;
  element("mcp-autonomous-background-enabled").checked = mcp?.autonomous_session?.background_enabled === true;
  element("mcp-session-interval").value = mcp?.autonomous_session?.min_interval_seconds || 3600;
  element("mcp-session-calls").value = mcp?.autonomous_session?.max_tool_calls || 10;
  const remote = mcp?.transport === "streamable_http";
  document.querySelectorAll("[data-mcp-stdio-field]").forEach((row) => { row.hidden = remote; });
  document.querySelectorAll("[data-mcp-http-field]").forEach((row) => { row.hidden = !remote; });
}

function syncMcp() {
  const mcp = arrayById(state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  if (!mcp) {
    return;
  }
  mcp.mcp_server_id = textValue("mcp-server-id");
  // connector_kind は UI に出さず既存値を保持する。
  mcp.client_id = textValue("mcp-client-id");
  mcp.enabled = boolValue("mcp-enabled");
  // pre_send_check_enabled は送信前チェック専用タブが正とする。
  if (typeof mcp.pre_send_check_enabled !== "boolean") {
    mcp.pre_send_check_enabled = true;
  }
  mcp.transport = textValue("mcp-transport");
  if (mcp.transport === "streamable_http") {
    mcp.url = textValue("mcp-url");
    mcp.headers = parseKeyValueLines(textValue("mcp-headers"), "headers");
    delete mcp.command;
    delete mcp.args;
    delete mcp.cwd;
    delete mcp.env;
  } else {
    mcp.command = textValue("mcp-command");
    mcp.args = parseLines(textValue("mcp-args"));
    const cwd = textValue("mcp-cwd").trim();
    mcp.cwd = cwd || null;
    mcp.env = parseKeyValueLines(textValue("mcp-env"), "env");
    delete mcp.url;
    delete mcp.headers;
  }
  mcp.autonomous_session = {
    enabled: boolValue("mcp-autonomous-enabled"),
    background_enabled: boolValue("mcp-autonomous-background-enabled"),
    min_interval_seconds: boundedIntValue("mcp-session-interval", "最短間隔", 1, 31536000),
    max_tool_calls: boundedIntValue("mcp-session-calls", "tool call上限", 1, 1000),
  };
  // 旧下書きに enabled_tools が残っていれば捨てる（設定正本から廃止済み）。
  delete mcp.enabled_tools;
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
  syncPreSendCheck();
}

// 選択切替や追加前に、現在フォームの値を下書きへ戻す。
function withSyncedCollection(run) {
  syncAllForms();
  return run();
}

// 追加用: 選択中を clone せず、空テンプレを push する。
function pushBlankCollectionItem({
  items,
  idKey,
  setSelectedId,
  buildItem,
  afterCreate,
  render,
}) {
  withSyncedCollection(() => {
    const item = buildItem(items);
    items.push(item);
    setSelectedId(item[idKey]);
    if (afterCreate) {
      afterCreate(item);
    }
    render();
  });
}

function duplicateClonedCollectionItem({
  items,
  idKey,
  selectedId,
  setSelectedId,
  idPrefix,
  nameBuilder,
  emptyMessage,
  afterClone,
  render,
}) {
  withSyncedCollection(() => {
    const source = arrayById(items, idKey, selectedId);
    if (!source) {
      if (emptyMessage) {
        showNotice(emptyMessage, true);
      }
      return;
    }
    const item = clone(source);
    item[idKey] = `${idPrefix}:${idSuffix()}`;
    item.display_name = nameBuilder(source, items);
    if (afterClone) {
      afterClone(item, source);
    }
    items.push(item);
    setSelectedId(item[idKey]);
    render();
  });
}

function deleteCollectionItem({
  items,
  idKey,
  selectedId,
  setSelectedId,
  minCount = 1,
  lastItemMessage,
  afterDelete,
  render,
}) {
  if (items.length <= minCount) {
    showNotice(lastItemMessage, true);
    return;
  }
  const removedId = selectedId;
  removeById(items, idKey, removedId);
  setSelectedId(items[0]?.[idKey] || "");
  if (afterDelete) {
    afterDelete(removedId);
  }
  render();
}

// select 切替: 現在値を保存してから選択を差し替え、再描画する。
function bindCollectionSelect(selectId, { sync, setSelected, render }) {
  element(selectId).addEventListener("change", () => {
    sync();
    setSelected(element(selectId).value);
    render();
  });
}

function setSelectedAvatarId(avatarId) {
  state.selectedAvatarId = avatarId;
  state.avatarSpeech.selected_avatar_id = avatarId;
}

// VRM 表示設定は端末設定側の avatar_presentations に avatar_id 参照で載る。
// アバター複製時は、編集対象（最終接続）端末の presentation も同じ内容で複製する。
function copyAvatarPresentation(sourceAvatarId, newAvatarId) {
  const presentations = state.consoleClient?.settings?.avatar_presentations;
  if (!Array.isArray(presentations) || !sourceAvatarId || !newAvatarId) {
    return;
  }
  if (arrayById(presentations, "avatar_id", newAvatarId)) {
    return;
  }
  const source = arrayById(presentations, "avatar_id", sourceAvatarId);
  if (!source) {
    return;
  }
  const copied = clone(source);
  copied.avatar_id = newAvatarId;
  presentations.push(copied);
}

// 削除したアバターの presentation を下書きから外し、保存時の参照検証エラーを防ぐ。
function removeAvatarPresentation(avatarId) {
  const presentations = state.consoleClient?.settings?.avatar_presentations;
  if (!Array.isArray(presentations) || !avatarId) {
    return;
  }
  const index = presentations.findIndex((entry) => entry.avatar_id === avatarId);
  if (index >= 0) {
    presentations.splice(index, 1);
  }
}

function duplicateAvatar() {
  duplicateClonedCollectionItem({
    items: state.avatarSpeech.avatars,
    idKey: "avatar_id",
    selectedId: state.selectedAvatarId,
    setSelectedId: setSelectedAvatarId,
    idPrefix: "avatar",
    nameBuilder: (source) => `${source.display_name || "アバター"}_copy`,
    afterClone: (item, source) => {
      copyAvatarPresentation(source.avatar_id, item.avatar_id);
    },
    render: renderAvatar,
  });
}

function deleteAvatar() {
  deleteCollectionItem({
    items: state.avatarSpeech.avatars,
    idKey: "avatar_id",
    selectedId: state.selectedAvatarId,
    setSelectedId: setSelectedAvatarId,
    lastItemMessage: "最後のアバターは削除できません。",
    afterDelete: (removedId) => {
      removeAvatarPresentation(removedId);
    },
    render: renderAvatar,
  });
}

function addPersona() {
  pushBlankCollectionItem({
    items: state.editor.personas,
    idKey: "persona_id",
    setSelectedId: (id) => {
      state.selectedPersonaId = id;
    },
    buildItem: (items) => ({
      persona_id: `persona:${idSuffix()}`,
      display_name: uniqueDisplayName(
        items.map((item) => item.display_name),
        "新規人格設定",
      ),
      // UI に無い必須構造。選択中人格の本文等はコピーしない。
      initiative_baseline: "medium",
      persona_prompt: "",
      expression_addon: "",
      wake_words: [],
    }),
    render: renderSettings,
  });
}

function duplicatePersona() {
  duplicateClonedCollectionItem({
    items: state.editor.personas,
    idKey: "persona_id",
    selectedId: state.selectedPersonaId,
    setSelectedId: (id) => {
      state.selectedPersonaId = id;
    },
    idPrefix: "persona",
    nameBuilder: (source) => `${source.display_name || "人格設定"} Copy`,
    render: renderSettings,
  });
}

function deletePersona() {
  deleteCollectionItem({
    items: state.editor.personas,
    idKey: "persona_id",
    selectedId: state.selectedPersonaId,
    setSelectedId: (id) => {
      state.selectedPersonaId = id;
      state.editor.current.selected_persona_id = id;
    },
    lastItemMessage: "最後の人格設定は削除できません。",
    render: renderSettings,
  });
}

function addModel() {
  pushBlankCollectionItem({
    items: state.editor.model_presets,
    idKey: "model_preset_id",
    setSelectedId: (id) => {
      state.selectedModelPresetId = id;
    },
    buildItem: () => ({
      model_preset_id: `model_preset:${idSuffix()}`,
      display_name: uniqueDisplayName(
        generationModelPresets().map((item) => item.display_name),
        "新規モデルプリセット",
      ),
      // モデル名・キー等は空。数値はシステム定数（選択中プリセットはコピーしない）。
      model: "",
      api_key: "",
      max_output_tokens: 4000,
      timeout_seconds: 90,
      web_search_enabled: false,
      prompt_window: {
        recent_turn_limit: 30,
        recent_turn_minutes: 30,
      },
    }),
    render: renderSettings,
  });
}

function duplicateModel() {
  if (state.selectedModelPresetId === PRE_SEND_CHECK_MODEL_PRESET_ID) {
    showNotice("送信前チェック用モデルはモデルタブから複製できません。", true);
    return;
  }
  duplicateClonedCollectionItem({
    items: state.editor.model_presets,
    idKey: "model_preset_id",
    selectedId: state.selectedModelPresetId,
    setSelectedId: (id) => {
      state.selectedModelPresetId = id;
    },
    idPrefix: "model_preset",
    nameBuilder: (source) => `${source.display_name || "モデルプリセット"} Copy`,
    render: renderSettings,
  });
}

function deleteModel() {
  if (state.selectedModelPresetId === PRE_SEND_CHECK_MODEL_PRESET_ID) {
    showNotice("送信前チェック用モデルは削除できません。", true);
    return;
  }
  if (generationModelPresets().length <= 1) {
    showNotice("最後のモデルプリセットは削除できません。", true);
    return;
  }
  deleteCollectionItem({
    items: state.editor.model_presets,
    idKey: "model_preset_id",
    selectedId: state.selectedModelPresetId,
    // 送信前チェック専用定義を含む配列なので、生成用が 1 件残るまで許す。
    minCount: 2,
    setSelectedId: (id) => {
      const nextId =
        id === PRE_SEND_CHECK_MODEL_PRESET_ID
          ? generationModelPresets()[0]?.model_preset_id || ""
          : id;
      state.selectedModelPresetId = nextId;
      state.editor.current.selected_model_preset_id = nextId;
      state.editor.current.pre_send_check_model_preset_id =
        PRE_SEND_CHECK_MODEL_PRESET_ID;
    },
    lastItemMessage: "最後のモデルプリセットは削除できません。",
    render: renderSettings,
  });
}

function markMemoryDraft(item, { cloneSourceMemorySetId = null } = {}) {
  state.memoryDraftMeta[item.memory_set_id] = {
    serverBacked: false,
    cloneSourceMemorySetId,
  };
}

function addMemory() {
  pushBlankCollectionItem({
    items: state.editor.memory_sets,
    idKey: "memory_set_id",
    setSelectedId: (id) => {
      state.selectedMemorySetId = id;
    },
    buildItem: (items) => ({
      memory_set_id: `memory_set:${idSuffix()}`,
      display_name: uniqueDisplayName(
        items.map((item) => item.display_name),
        "新規記憶集合",
      ),
      embedding: {
        model: "",
        api_key: "",
        embedding_dimension: 3072,
      },
    }),
    afterCreate: (item) => markMemoryDraft(item),
    render: renderSettings,
  });
}

function cloneMemoryData() {
  withSyncedCollection(() => {
    const source = arrayById(state.editor.memory_sets, "memory_set_id", state.selectedMemorySetId);
    if (!source) {
      showNotice("複製する記憶集合を選択してください。", true);
      return;
    }
    const cloneSourceMemorySetId = resolveCloneSourceMemorySetId(source);
    if (!cloneSourceMemorySetId) {
      showNotice("未保存の記憶集合は複製できません。先に適用するか、設定の複製を使ってください。", true);
      return;
    }
    const item = clone(source);
    item.memory_set_id = `memory_set:${idSuffix()}`;
    item.display_name = uniqueDisplayName(
      state.editor.memory_sets.map((entry) => entry.display_name),
      `${source.display_name || "記憶集合"} (記憶複製)`,
    );
    markMemoryDraft(item, { cloneSourceMemorySetId });
    state.editor.memory_sets.push(item);
    state.selectedMemorySetId = item.memory_set_id;
    renderSettings();
  });
}

function duplicateMemory() {
  duplicateClonedCollectionItem({
    items: state.editor.memory_sets,
    idKey: "memory_set_id",
    selectedId: state.selectedMemorySetId,
    setSelectedId: (id) => {
      state.selectedMemorySetId = id;
    },
    idPrefix: "memory_set",
    emptyMessage: "設定を複製する記憶集合を選択してください。",
    nameBuilder: (source, items) => uniqueDisplayName(
      items.map((item) => item.display_name),
      `${source.display_name || "記憶集合"} (設定コピー)`,
    ),
    afterClone: (item) => markMemoryDraft(item),
    render: renderSettings,
  });
}

function deleteMemory() {
  deleteCollectionItem({
    items: state.editor.memory_sets,
    idKey: "memory_set_id",
    selectedId: state.selectedMemorySetId,
    setSelectedId: (id) => {
      state.selectedMemorySetId = id;
      state.editor.current.selected_memory_set_id = id;
    },
    lastItemMessage: "最後の記憶セットは削除できません。",
    afterDelete: (removedId) => {
      delete state.memoryDraftMeta[removedId];
    },
    render: renderSettings,
  });
}

function addCamera() {
  syncAllForms();
  // 接続情報は空。disabled の接続種別・クライアントと生成 ID 用の構造だけ残す。
  const camera = {
    display_name: uniqueDisplayName(
      state.camera.camera_sources.map((item) => item.display_name),
      "カメラ",
    ),
    vision_source_id: "",
    connector_kind: "tapo_c220",
    client_id: "tapo-c220-connector-main",
    kind: "camera",
    source_owner: "self",
    enabled: false,
    connection: {
      host: "",
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
  // 名前以外の編集項目は空。接続クライアントと disabled の transport は残す。
  const id = uniqueDisplayName(
    state.mcp.mcp_servers.map((item) => item.mcp_server_id),
    "MCP",
  );
  state.mcp.mcp_servers.push({
    mcp_server_id: id,
    connector_kind: "mcp_client",
    client_id: "mcp-client-connector-main",
    enabled: false,
    pre_send_check_enabled: false,
    transport: "stdio",
    command: "",
    args: [],
    cwd: null,
    env: {},
    autonomous_session: {
      enabled: false,
      background_enabled: false,
      min_interval_seconds: 3600,
      max_tool_calls: 10,
    },
  });
  state.selectedMcpId = id;
  renderCapabilities();
  renderPreSendCheck();
}

function deleteMcp() {
  removeById(state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  state.selectedMcpId = state.mcp.mcp_servers[0]?.mcp_server_id || "";
  renderCapabilities();
  renderPreSendCheck();
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
  element("confirm-menu-toggle").addEventListener("click", (event) => {
    event.stopPropagation();
    setConfirmMenuOpen(element("confirm-menu-popup").hidden);
  });
  // いまはトグル。開いていれば閉じ、閉じていれば開く。
  element("confirm-now").addEventListener("click", () => {
    const nextVisible = !isDashboardVisible();
    setDashboardVisible(nextVisible);
    setConfirmMenuOpen(false);
    if (nextVisible) {
      refreshDashboard({ silent: true });
    }
  });
  element("confirm-cycles").addEventListener("click", () => setConfirmMenuOpen(false));
  element("confirm-logs").addEventListener("click", () => setConfirmMenuOpen(false));
  document.addEventListener("click", (event) => {
    const menu = element("confirm-menu");
    if (!(event.target instanceof Node) || !menu.contains(event.target)) {
      setConfirmMenuOpen(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setConfirmMenuOpen(false);
    }
  });
  element("refresh-dashboard").addEventListener("click", () => refreshDashboard({ silent: false }));
  element("close-dashboard").addEventListener("click", () => setDashboardVisible(false));
  element("dashboard-active").addEventListener("click", (event) => {
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
  element("toggle-web-microphone").addEventListener("click", () => {
    toggleWebMicrophone();
  });
  element("toggle-web-tts").addEventListener("click", () => {
    toggleWebTts();
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

  bindCollectionSelect("avatar-select", {
    sync: syncAvatar,
    setSelected: setSelectedAvatarId,
    render: renderAvatar,
  });
  element("tts-engine").addEventListener("change", () => {
    renderTtsPanel(element("tts-engine").value);
  });
  element("microphone-input-source").addEventListener("change", () => {
    handleMicrophoneInputSourceChange();
  });
  element("vad-probability-threshold").addEventListener("input", updateMicrophoneSettingLabels);
  element("speaker-recognition-threshold").addEventListener("input", updateMicrophoneSettingLabels);
  renderAudioMeters();
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
    if (button.dataset.speakerAction === "assign") {
      const select = element("speaker-list").querySelector(
        `select[data-speaker-display-name-select="${CSS.escape(button.dataset.personRef)}"]`,
      );
      if (select) {
        assignSpeakerDisplayName(button.dataset.personRef, select.value);
      }
    } else if (button.dataset.speakerAction === "reenroll") {
      startSpeakerEnrollment(button.dataset.personRef);
    } else if (button.dataset.speakerAction === "unregister") {
      unregisterSpeaker(button.dataset.personRef);
    }
  });
  element("settings-conversation-display-name-select").addEventListener("change", () => {
    syncConversationDisplayNameEditFromSelection();
  });
  element("add-conversation-display-name").addEventListener(
    "click",
    addConversationDisplayName,
  );
  element("update-conversation-display-name").addEventListener(
    "click",
    updateConversationDisplayName,
  );
  element("delete-conversation-display-name").addEventListener(
    "click",
    deleteConversationDisplayName,
  );
  bindCollectionSelect("persona-select", {
    sync: syncPersona,
    setSelected: (id) => {
      state.selectedPersonaId = id;
    },
    render: renderPersona,
  });
  bindCollectionSelect("model-select", {
    sync: syncModel,
    setSelected: (id) => {
      state.selectedModelPresetId = id;
    },
    render: renderModel,
  });
  bindCollectionSelect("memory-select", {
    sync: syncMemory,
    setSelected: (id) => {
      state.selectedMemorySetId = id;
    },
    render: renderMemory,
  });
  bindCollectionSelect("camera-select", {
    sync: syncCamera,
    setSelected: (id) => {
      state.selectedCameraId = id;
      state.selectedWatcherSourceId = id;
    },
    render: () => {
      renderCamera();
      renderWatcher();
    },
  });
  element("camera-display-name").addEventListener("input", updateCameraGeneratedIds);
  bindCollectionSelect("watcher-select", {
    sync: syncWatcher,
    setSelected: (id) => {
      state.selectedWatcherSourceId = id;
    },
    render: renderWatcher,
  });
  bindCollectionSelect("mcp-select", {
    sync: syncMcp,
    setSelected: (id) => {
      state.selectedMcpId = id;
    },
    render: renderMcp,
  });
  element("mcp-transport").addEventListener("change", () => {
    syncMcp();
    renderMcp();
  });
  // 設定パネル内のコレクション操作と秘密入力欄を委譲で共通処理する。
  const settingsActions = {
    "duplicate-avatar": duplicateAvatar,
    "delete-avatar": deleteAvatar,
    "add-persona": addPersona,
    "duplicate-persona": duplicatePersona,
    "delete-persona": deletePersona,
    "add-model": addModel,
    "duplicate-model": duplicateModel,
    "delete-model": deleteModel,
    "add-memory": addMemory,
    "clone-memory": cloneMemoryData,
    "duplicate-memory": duplicateMemory,
    "delete-memory": deleteMemory,
    "add-camera": addCamera,
    "delete-camera": deleteCamera,
    "add-mcp": addMcp,
    "delete-mcp": deleteMcp,
  };
  element("settings-panel").addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) {
      return;
    }

    const actionButton = target.closest("[data-action]");
    if (actionButton && !actionButton.disabled) {
      const handler = settingsActions[actionButton.dataset.action];
      if (handler) {
        handler();
        return;
      }
    }

    const secretButton = target.closest("[data-secret-action]");
    if (!secretButton || secretButton.disabled) {
      return;
    }
    const secretAction = secretButton.dataset.secretAction;
    const inputId = secretButton.dataset.secretInput;
    const label = secretButton.dataset.secretLabel || "APIキー";
    if (secretAction === "copy") {
      copyApiKey(inputId, label);
    } else if (secretAction === "paste") {
      pasteApiKey(inputId, label);
    } else if (secretAction === "paste-from-llm") {
      pasteLlmApiKey(inputId);
    }
  });

  window.addEventListener("beforeunload", () => {
    state.unloading = true;
    window.clearInterval(state.dashboardTimer);
    window.clearTimeout(state.eventReconnectTimer);
    window.clearTimeout(state.audioMeters.speakerResetTimer);
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
    if (!state.webAudio.inputSessionId) {
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
  renderWebMicrophoneControls();
  connectEventStream();
  state.dashboardTimer = window.setInterval(() => refreshDashboard({ silent: true }), 5000);
}

startApp();
