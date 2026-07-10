const ROLE_LABELS = {
  input_interpretation: "入力内容の整理",
  decision_generation: "何をするかの判断",
  expression_generation: "会話の返答生成",
  memory_interpretation: "記憶更新の整理",
  memory_reflection_summary: "内省結果の要約",
  event_evidence_generation: "想起根拠の要約",
  recall_pack_selection: "想起候補の選別",
  pending_intent_selection: "保留候補の選別",
  autonomous_step_generation: "自律ステップ",
  memory_correction_reconciliation: "記憶補正",
};

const PRIMARY_MODEL_ROLE = "expression_generation";
const SHARED_MODEL_ROLES = [
  "input_interpretation",
  "decision_generation",
  "memory_interpretation",
  "memory_reflection_summary",
  "event_evidence_generation",
  "recall_pack_selection",
  "pending_intent_selection",
];

const state = {
  identity: null,
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

function addMessage(kind, text, images = []) {
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
  meta.textContent = nowLabel();

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
  addMessage("user", text, images);
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
        client_context: {
          source: "OtomeKairoWebUI",
          client_id: "web-ui",
          locale: navigator.language,
        },
      }),
    });
    const rendered = resultText(result);
    addMessage(rendered.kind, rendered.text);
    await loadStatus({ silent: true });
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
  element("persona-user-reference").value = persona.reference_style?.user_natural_reference || "";
  element("persona-prompt").value = persona.persona_prompt || "";
  element("persona-expression-addon").value = persona.expression_addon || "";
}

function syncPersona() {
  const persona = arrayById(state.editor.personas, "persona_id", state.selectedPersonaId);
  if (!persona) {
    return;
  }
  persona.display_name = textValue("persona-display-name");
  persona.reference_style = persona.reference_style || {};
  persona.reference_style.user_natural_reference = textValue("persona-user-reference");
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
  renderRoleList(preset);
}

function renderRoleList(preset) {
  const list = element("role-list");
  list.innerHTML = "";
  const roles = preset.roles || {};
  const expressionRole = roles[PRIMARY_MODEL_ROLE] || {};
  if (roles[PRIMARY_MODEL_ROLE]) {
    list.append(createModelRoleCard(PRIMARY_MODEL_ROLE, expressionRole, expressionRole, { primary: true }));
  }
  for (const roleName of SHARED_MODEL_ROLES) {
    const role = roles[roleName];
    if (role) {
      list.append(createModelRoleCard(roleName, role, expressionRole, { primary: false }));
    }
  }
}

function createModelRoleCard(roleName, role, expressionRole, { primary }) {
  const details = document.createElement("details");
  details.className = primary ? "role-card model-primary-role-card" : "role-card";
  details.open = primary;
  const summary = document.createElement("summary");
  summary.textContent = ROLE_LABELS[roleName] || roleName;

  const body = document.createElement("div");
  body.className = "role-card-body";
  if (!primary) {
    const sharedLabel = document.createElement("label");
    sharedLabel.className = "checkbox-field";
    sharedLabel.innerHTML = `表現生成と同じモデルを使用する<input data-role="${roleName}" data-share-expression="true" type="checkbox">`;
    body.append(sharedLabel);
  }

  const grid = document.createElement("div");
  grid.className = "model-role-grid";
  grid.innerHTML = `
    <label>モデル<input data-role="${roleName}" data-field="model"></label>
    <label>エンドポイントURL<input data-role="${roleName}" data-field="api_base"></label>
    <label>APIキー<input data-role="${roleName}" data-field="api_key" type="password" autocomplete="new-password"></label>
    <label>推論の深さ<input data-role="${roleName}" data-field="reasoning_effort"></label>
    <label>最大出力トークン<input data-role="${roleName}" data-field="max_output_tokens" type="number" min="1"></label>
    <label class="checkbox-field">Web検索を有効にする<input data-role="${roleName}" data-field="web_search_enabled" type="checkbox"></label>
  `;
  body.append(grid);
  details.append(summary, body);

  const shared = !primary && modelRoleUsesExpression(role, expressionRole);
  const shareInput = body.querySelector("[data-share-expression]");
  if (shareInput) {
    shareInput.checked = shared;
    shareInput.addEventListener("change", () => applySharedModelState(body, currentExpressionRoleValues()));
  }
  for (const input of grid.querySelectorAll("[data-field]")) {
    const field = input.dataset.field;
    const value = shared && isSharedModelField(field) ? expressionRole[field] : role[field];
    if (input.type === "checkbox") {
      input.checked = value === true;
    } else {
      input.value = value ?? "";
    }
  }
  if (primary) {
    for (const input of grid.querySelectorAll("[data-field]")) {
      if (isSharedModelField(input.dataset.field)) {
        input.addEventListener("input", refreshSharedRoleInputs);
      }
    }
  }
  applySharedModelState(body, expressionRole);
  return details;
}

function applySharedModelState(container, expressionRole) {
  const shareInput = container.querySelector("[data-share-expression]");
  const shared = shareInput?.checked === true;
  for (const input of container.querySelectorAll("[data-field]")) {
    if (!isSharedModelField(input.dataset.field)) {
      continue;
    }
    input.disabled = shared;
    if (shared) {
      input.value = expressionRole[input.dataset.field] ?? "";
    }
  }
}

function refreshSharedRoleInputs() {
  const expressionRole = currentExpressionRoleValues();
  for (const container of element("role-list").querySelectorAll(".role-card-body")) {
    if (container.querySelector("[data-share-expression]")?.checked === true) {
      applySharedModelState(container, expressionRole);
    }
  }
}

function currentExpressionRoleValues() {
  const result = {};
  for (const input of element("role-list").querySelectorAll(`[data-role="${PRIMARY_MODEL_ROLE}"][data-field]`)) {
    result[input.dataset.field] = input.value;
  }
  return result;
}

function isSharedModelField(field) {
  return field === "model" || field === "api_base" || field === "api_key";
}

function modelRoleUsesExpression(role, expressionRole) {
  return normalizeModelField(role.model) === normalizeModelField(expressionRole.model)
    && normalizeModelField(role.api_base) === normalizeModelField(expressionRole.api_base)
    && String(role.api_key ?? "") === String(expressionRole.api_key ?? "");
}

function normalizeModelField(value) {
  return String(value ?? "").trim();
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
  const expressionRole = preset.roles[PRIMARY_MODEL_ROLE] || {};
  const sharedRoleNames = new Set();
  for (const input of element("role-list").querySelectorAll("[data-share-expression]")) {
    if (input.checked) {
      sharedRoleNames.add(input.dataset.role);
    }
  }
  for (const input of element("role-list").querySelectorAll("[data-field]")) {
    const role = preset.roles[input.dataset.role];
    if (!role) {
      continue;
    }
    const field = input.dataset.field;
    if (sharedRoleNames.has(input.dataset.role) && isSharedModelField(field)) {
      role[field] = expressionRole[field] ?? "";
      continue;
    }
    if (input.type === "checkbox") {
      role[field] = input.checked;
    } else if (input.type === "number") {
      role[field] = intValueFromInput(input, role[field]);
    } else if (field === "api_base" || field === "reasoning_effort") {
      const value = input.value.trim();
      if (value) {
        role[field] = value;
      } else {
        delete role[field];
      }
    } else {
      role[field] = input.value;
    }
  }
}

function intValueFromInput(input, fallback = 1) {
  const value = Number.parseInt(input.value, 10);
  return Number.isFinite(value) ? value : fallback;
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
  const roles = preset?.roles || {};
  const roleNames = [
    PRIMARY_MODEL_ROLE,
    "decision_generation",
    "input_interpretation",
    "memory_interpretation",
    "memory_reflection_summary",
    "event_evidence_generation",
    "recall_pack_selection",
    "pending_intent_selection",
  ];
  for (const roleName of roleNames) {
    const apiKey = roles[roleName]?.api_key;
    if (typeof apiKey === "string" && apiKey.trim()) {
      return apiKey.trim();
    }
  }
  return "";
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
}

bindEvents();
loadIdentity();
loadStatus({ silent: true });
