const endpoints = {
  editor: {
    elementId: "editor-state",
    path: "/ui/api/config/editor-state",
  },
  camera: {
    elementId: "camera-state",
    path: "/ui/api/config/camera-sources/editor-state",
  },
  mcp: {
    elementId: "mcp-state",
    path: "/ui/api/config/mcp-servers/editor-state",
  },
};

const state = {
  identity: null,
};

function element(id) {
  return document.getElementById(id);
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

function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}

function parseEditorJson(id) {
  try {
    return JSON.parse(element(id).value);
  } catch (error) {
    throw new Error(`JSONを解釈できません: ${error.message}`);
  }
}

function renderIdentity() {
  const summary = element("server-summary");
  const identity = state.identity;
  if (!identity) {
    summary.textContent = "接続確認中";
    return;
  }
  summary.textContent = `${identity.server_display_name} / ${identity.server_id} / API ${identity.api_version}`;

  const actions = element("bootstrap-actions");
  actions.innerHTML = "";
  const message = document.createElement("p");
  message.textContent = "同一サーバの設定APIへ接続済みです。";
  actions.append(message);
}

async function loadIdentity() {
  try {
    const data = await apiRequest("/ui/api/bootstrap/server-identity");
    state.identity = data;
    renderIdentity();
    setStatus("接続済み", "ready");
  } catch (error) {
    setStatus("接続失敗", "error");
    showNotice(error.message, true);
  }
}

async function loadStatus() {
  try {
    const data = await apiRequest("/ui/api/status");
    const snapshot = data.settings_snapshot || {};
    const runtime = data.runtime_summary || {};
    renderSummary({
      selected_persona_id: snapshot.selected_persona_id,
      selected_memory_set_id: snapshot.selected_memory_set_id,
      selected_model_preset_id: snapshot.selected_model_preset_id,
      wake_policy_mode: snapshot.wake_policy?.mode,
      thinking_speech_level: snapshot.thinking_speech_level,
      connection_state: runtime.connection_state,
      memory_job_worker_active: runtime.memory_job_worker_active,
      pending_memory_job_count: runtime.pending_memory_job_count,
    });
    setStatus("接続済み", "ready");
    showNotice("現在状態を読み込みました。");
  } catch (error) {
    setStatus("API失敗", "error");
    showNotice(error.message, true);
  }
}

function renderSummary(values) {
  const summary = element("status-summary");
  summary.innerHTML = "";
  for (const [key, value] of Object.entries(values)) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = key;
    dd.textContent = String(value ?? "");
    summary.append(dt, dd);
  }
}

async function loadEditor(kind) {
  const target = endpoints[kind];
  try {
    const data = await apiRequest(target.path);
    element(target.elementId).value = prettyJson(data);
    showNotice(`${sectionLabel(kind)}を読み込みました。`);
  } catch (error) {
    showNotice(error.message, true);
  }
}

async function saveEditor(kind) {
  const target = endpoints[kind];
  try {
    const payload = parseEditorJson(target.elementId);
    const data = await apiRequest(target.path, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    element(target.elementId).value = prettyJson(data);
    showNotice(`${sectionLabel(kind)}を保存しました。`);
  } catch (error) {
    showNotice(error.message, true);
  }
}

function sectionLabel(kind) {
  if (kind === "editor") {
    return "基本設定";
  }
  if (kind === "camera") {
    return "Camera Source";
  }
  return "MCP Server";
}

function bindEvents() {
  element("reload-identity").addEventListener("click", loadIdentity);
  element("load-status").addEventListener("click", loadStatus);

  document.querySelectorAll("[data-load]").forEach((button) => {
    button.addEventListener("click", () => loadEditor(button.dataset.load));
  });
  document.querySelectorAll("[data-save]").forEach((button) => {
    button.addEventListener("click", () => saveEditor(button.dataset.save));
  });
}

bindEvents();
loadIdentity();
