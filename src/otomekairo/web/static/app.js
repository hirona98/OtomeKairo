const state = {
  identity: null,
  clientId: "",
  editor: null,
  camera: null,
  mcp: null,
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
    throw new Error(`${code}: ${message}`);
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
  element("conversation-person-ref").value = personRef;
  element("conversation-display-name").value = localStorage.getItem("otomekairo.display_name") || "";
  element("conversation-interaction-ref").value = interactionRef;
  saveConversationIdentity();
}

function saveConversationIdentity() {
  localStorage.setItem("otomekairo.person_ref", element("conversation-person-ref").value.trim());
  localStorage.setItem("otomekairo.display_name", element("conversation-display-name").value.trim());
  localStorage.setItem("otomekairo.interaction_ref", element("conversation-interaction-ref").value.trim());
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
      snapshot.settings_snapshot?.selected_persona_id,
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

// 対話入力と同じ client_id で購読し、非同期発話の物理配送先を一致させる。
function connectAssistantEventStream() {
  if (state.unloading || state.eventSocket) {
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/ui/api/events/stream`);
  state.eventSocket = socket;
  setEventStreamStatus("非同期発話: 接続中", "processing");

  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({
      type: "hello",
      client_id: state.clientId,
      caps: [],
      event_subscriptions: ["assistant_message"],
    }));
    setEventStreamStatus("非同期発話: 接続済み");
  });
  socket.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload?.type !== "assistant_message" || typeof payload.data?.message !== "string") {
      return;
    }
    addMessage("assistant", payload.data.message, [], assistantSourceLabel(payload.data.source_kind));
    refreshDashboard({ silent: true });
  });
  socket.addEventListener("error", () => socket.close());
  socket.addEventListener("close", () => {
    if (state.eventSocket === socket) {
      state.eventSocket = null;
    }
    if (state.unloading) {
      return;
    }
    setEventStreamStatus("非同期発話: 再接続中", "processing");
    window.clearTimeout(state.eventReconnectTimer);
    state.eventReconnectTimer = window.setTimeout(connectAssistantEventStream, 3000);
  });
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
  return { kind: "system", text: "処理が完了しました。" };
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
  const personRef = element("conversation-person-ref").value.trim();
  const displayName = element("conversation-display-name").value.trim();
  const interactionRef = element("conversation-interaction-ref").value.trim();
  if (
    !personRef.startsWith("person:")
    || personRef.length <= "person:".length
    || !displayName
    || !interactionRef
  ) {
    showNotice("人物参照は person:<key>、呼び名と会話参照は空でない値を指定してください。", true);
    return;
  }
  saveConversationIdentity();
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
    const [editor, camera, mcp] = await Promise.all([
      apiRequest("/ui/api/config/editor-state"),
      apiRequest("/ui/api/config/camera-sources/editor-state"),
      apiRequest("/ui/api/config/mcp-servers/editor-state"),
    ]);
    state.editor = clone(editor);
    state.camera = clone(camera);
    state.mcp = clone(mcp);
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
    const camera = await apiRequest("/ui/api/config/camera-sources/editor-state", {
      method: "PUT",
      body: JSON.stringify(state.camera),
    });
    const mcp = await apiRequest("/ui/api/config/mcp-servers/editor-state", {
      method: "PUT",
      body: JSON.stringify(state.mcp),
    });
    state.editor = clone(editor);
    state.camera = clone(camera);
    state.mcp = clone(mcp);
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
  if (!state.editor || !state.camera || !state.mcp) {
    return;
  }
  renderCurrent();
  renderPersona();
  renderModel();
  renderMemory();
  renderCapabilities();
}

function renderCurrent() {
  element("current-thinking-level").value = state.editor.current.thinking_speech_level ?? 5;
  element("current-wake-enabled").checked = state.editor.current.wake_policy?.mode === "interval";
  element("current-wake-interval").value = state.editor.current.wake_policy?.interval_seconds || "";
}

function syncCurrent() {
  state.editor.current.selected_persona_id = state.selectedPersonaId;
  state.editor.current.selected_memory_set_id = state.selectedMemorySetId;
  state.editor.current.selected_model_preset_id = state.selectedModelPresetId;
  state.editor.current.thinking_speech_level = intValue("current-thinking-level", 5);
  const observations = state.editor.current.wake_policy?.observations;
  state.editor.current.wake_policy = element("current-wake-enabled").checked
    ? { mode: "interval", interval_seconds: intValue("current-wake-interval", 60) }
    : { mode: "disabled" };
  if (Array.isArray(observations)) {
    state.editor.current.wake_policy.observations = observations;
  }
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
  preset.timeout_seconds = intValue("model-timeout-seconds", 90);
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

async function copyMemoryApiKey() {
  try {
    await navigator.clipboard.writeText(textValue("memory-api-key"));
    showNotice("記憶セットのAPIキーをコピーしました。");
  } catch (error) {
    showNotice(`クリップボードへコピーできません: ${error.message}`, true);
  }
}

async function pasteMemoryApiKey() {
  try {
    element("memory-api-key").value = await navigator.clipboard.readText();
    showNotice("記憶セットのAPIキーを貼り付けました。");
  } catch (error) {
    showNotice(`クリップボードから読み込めません: ${error.message}`, true);
  }
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
}

function syncCamera() {
  const camera = arrayById(state.camera.camera_sources, "vision_source_id", state.selectedCameraId);
  if (!camera) {
    return;
  }
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
}

function watcherItems() {
  return (state.camera?.camera_sources || []).map((camera) => {
    const watcher = cameraWatcher(camera);
    const displayName = camera.display_name || camera.vision_source_id;
    return {
      vision_source_id: camera.vision_source_id,
      display_name: `${displayName} (${camera.vision_source_id} / ${watcher.watcher_id})`,
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
  element("watcher-kind").value = watcher.kind;
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
  element("mcp-connector-kind").value = mcp?.connector_kind || "mcp_client";
  element("mcp-client-id").value = mcp?.client_id || "mcp-client-connector-main";
  element("mcp-transport").value = mcp?.transport || "stdio";
  element("mcp-command").value = mcp?.command || "";
  element("mcp-args").value = (mcp?.args || []).join("\n");
  element("mcp-enabled-tools").value = (mcp?.enabled_tools || []).join("\n");
  element("mcp-cwd").value = mcp?.cwd || "";
  element("mcp-env").value = formatEnv(mcp?.env || {});
}

function syncMcp() {
  const mcp = arrayById(state.mcp.mcp_servers, "mcp_server_id", state.selectedMcpId);
  if (!mcp) {
    return;
  }
  mcp.mcp_server_id = textValue("mcp-server-id");
  mcp.connector_kind = textValue("mcp-connector-kind");
  mcp.client_id = textValue("mcp-client-id");
  mcp.enabled = boolValue("mcp-enabled");
  mcp.transport = textValue("mcp-transport");
  mcp.command = textValue("mcp-command");
  mcp.args = parseLines(textValue("mcp-args"));
  mcp.enabled_tools = parseLines(textValue("mcp-enabled-tools"));
  const cwd = textValue("mcp-cwd").trim();
  mcp.cwd = cwd || null;
  mcp.env = parseEnv(textValue("mcp-env"));
  state.selectedMcpId = mcp.mcp_server_id;
}

function syncAllForms() {
  syncCurrent();
  syncPersona();
  syncModel();
  syncMemory();
  syncCamera();
  syncWatcher();
  syncMcp();
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
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".tab-page").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === tab);
  });
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
  for (const id of [
    "conversation-person-ref",
    "conversation-display-name",
    "conversation-interaction-ref",
  ]) {
    element(id).addEventListener("change", saveConversationIdentity);
  }

  element("open-settings").addEventListener("click", openSettings);
  element("close-settings").addEventListener("click", closeSettings);
  element("settings-backdrop").addEventListener("click", closeSettings);
  element("cancel-settings").addEventListener("click", closeSettings);
  element("apply-settings").addEventListener("click", () => saveSettings({ closeAfterSave: false }));
  element("ok-settings").addEventListener("click", () => saveSettings({ closeAfterSave: true }));
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
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

  document.querySelector("[data-action='add-persona']").addEventListener("click", addPersona);
  document.querySelector("[data-action='duplicate-persona']").addEventListener("click", duplicatePersona);
  document.querySelector("[data-action='delete-persona']").addEventListener("click", deletePersona);
  document.querySelector("[data-action='add-model']").addEventListener("click", addModel);
  document.querySelector("[data-action='duplicate-model']").addEventListener("click", duplicateModel);
  document.querySelector("[data-action='delete-model']").addEventListener("click", deleteModel);
  document.querySelector("[data-action='add-memory']").addEventListener("click", addMemory);
  document.querySelector("[data-action='duplicate-memory']").addEventListener("click", duplicateMemory);
  document.querySelector("[data-action='delete-memory']").addEventListener("click", deleteMemory);
  element("copy-memory-api-key").addEventListener("click", copyMemoryApiKey);
  element("paste-memory-api-key").addEventListener("click", pasteMemoryApiKey);
  element("paste-llm-api-key-to-memory").addEventListener("click", pasteLlmApiKeyToMemory);
  document.querySelector("[data-action='add-camera']").addEventListener("click", addCamera);
  document.querySelector("[data-action='delete-camera']").addEventListener("click", deleteCamera);
  document.querySelector("[data-action='add-mcp']").addEventListener("click", addMcp);
  document.querySelector("[data-action='delete-mcp']").addEventListener("click", deleteMcp);

  window.addEventListener("beforeunload", () => {
    state.unloading = true;
    window.clearInterval(state.dashboardTimer);
    window.clearTimeout(state.eventReconnectTimer);
    state.eventSocket?.close();
  });
}

async function startApp() {
  // 初回 token 発行後に inspection と event stream を順に開始する。
  initializeClientId();
  bindEvents();
  loadConversationIdentity();
  await loadIdentity();
  await loadStatus({ silent: true });
  await refreshDashboard({ silent: true });
  connectAssistantEventStream();
  state.dashboardTimer = window.setInterval(() => refreshDashboard({ silent: true }), 5000);
}

startApp();
