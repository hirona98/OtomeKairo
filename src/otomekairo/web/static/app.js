(() => {
  "use strict";

  // Block: DOM references
  const chatScroll = document.getElementById("chat-scroll");
  const chatPanel = document.getElementById("chat-panel");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendButton = document.getElementById("btn-send");
  const cancelButton = document.getElementById("btn-cancel");
  const micButton = document.getElementById("btn-mic");
  const cameraButton = document.getElementById("btn-camera");
  const settingsButton = document.getElementById("btn-settings");
  const settingsPanel = document.getElementById("settings-panel");
  const settingsDummyButton = document.getElementById("btn-settings-dummy");
  const settingsReloadButton = document.getElementById("btn-settings-reload");
  const settingsSaveButton = document.getElementById("btn-settings-save");
  const settingsCloseButton = document.getElementById("btn-settings-close");
  const settingsBehaviorCard = document.getElementById("settings-behavior-card");
  const settingsLlmCard = document.getElementById("settings-llm-card");
  const settingsMemoryCard = document.getElementById("settings-memory-card");
  const settingsOutputCard = document.getElementById("settings-output-card");
  const settingsDirectCard = document.getElementById("settings-direct-card");
  const settingsStatus = document.getElementById("settings-status");
  const settingsTabButtons = Array.from(document.querySelectorAll("[data-settings-tab]"));
  const settingsPages = Array.from(document.querySelectorAll("[data-settings-page]"));
  const statusJson = document.getElementById("status-json");
  const settingsJson = document.getElementById("settings-json");
  const connectionText = document.getElementById("connection-text");
  const runtimeText = document.getElementById("runtime-text");

  // Block: Settings schema
  const SETTINGS_TAB_KEYS = ["behavior", "llm", "memory", "system"];
  const SETTINGS_PRESET_KINDS = ["behavior", "llm", "memory", "output"];
  const PRESET_DESCRIPTORS = {
    behavior: [
      { path: "response_pace", label: "応答ペース", kind: "select", options: ["calm", "normal", "quick"] },
      { path: "proactivity_level", label: "自発性", kind: "select", options: ["low", "medium", "high"] },
      { path: "browse_preference", label: "検索傾向", kind: "select", options: ["avoid", "balanced", "prefer"] },
      { path: "notify_preference", label: "通知傾向", kind: "select", options: ["quiet", "balanced", "proactive"] },
      { path: "speech_style", label: "話し方", kind: "select", options: ["soft", "neutral", "formal"] },
      { path: "verbosity_bias", label: "詳細さ", kind: "select", options: ["short", "balanced", "detailed"] },
    ],
    llm: [
      { path: "llm.provider", label: "LLM プロバイダ", kind: "text" },
      { path: "llm.default_model", label: "LLM モデル", kind: "text" },
      { path: "llm.temperature", label: "Temperature", kind: "number", min: 0, max: 2, step: 0.1 },
      { path: "llm.max_output_tokens", label: "最大出力トークン", kind: "integer", min: 256, max: 8192, step: 1 },
      { path: "llm.api_key", label: "LLM API キー", kind: "password" },
      { path: "llm.base_url", label: "LLM Base URL", kind: "text" },
    ],
    memory: [
      { path: "llm.embedding_provider", label: "埋め込みプロバイダ", kind: "text" },
      { path: "llm.embedding_model", label: "埋め込みモデル", kind: "text" },
      { path: "llm.embedding_api_key", label: "埋め込み API キー", kind: "password" },
      { path: "llm.embedding_base_url", label: "埋め込み Base URL", kind: "text" },
      { path: "runtime.context_budget_tokens", label: "文脈上限", kind: "integer", min: 1024, max: 32768, step: 1 },
      { path: "retrieval_profile.semantic_top_k", label: "Semantic Top K", kind: "integer", min: 1, max: 32, step: 1 },
      { path: "retrieval_profile.recent_window_limit", label: "Recent Window", kind: "integer", min: 1, max: 16, step: 1 },
      { path: "retrieval_profile.fact_bias", label: "Fact Bias", kind: "number", min: 0, max: 1, step: 0.05 },
      { path: "retrieval_profile.summary_bias", label: "Summary Bias", kind: "number", min: 0, max: 1, step: 0.05 },
      { path: "retrieval_profile.event_bias", label: "Event Bias", kind: "number", min: 0, max: 1, step: 0.05 },
    ],
    output: [
      { path: "output.tts.voice", label: "TTS Voice", kind: "text" },
      { path: "output.mode", label: "出力モード", kind: "select", options: ["ui_only", "ui_and_tts"] },
      { path: "integrations.notify_route", label: "通知経路", kind: "select", options: ["ui_only", "line", "discord"] },
      { path: "integrations.line.channel_access_token", label: "LINE トークン", kind: "password" },
      { path: "integrations.line.to_user_id", label: "LINE 宛先", kind: "text" },
      { path: "integrations.discord.bot_token", label: "Discord トークン", kind: "password" },
      { path: "integrations.discord.channel_id", label: "Discord チャンネル", kind: "text" },
    ],
  };
  const DIRECT_DESCRIPTORS = [
    { key: "runtime.idle_tick_ms", label: "Idle Tick (ms)", kind: "integer", min: 250, max: 60000, step: 250 },
    { key: "runtime.long_cycle_min_interval_ms", label: "Long Cycle (ms)", kind: "integer", min: 1000, max: 300000, step: 1000 },
    { key: "sensors.microphone.enabled", label: "マイク入力", kind: "boolean" },
    { key: "sensors.camera.enabled", label: "カメラ入力", kind: "boolean" },
    { key: "output.tts.enabled", label: "ブラウザ TTS", kind: "boolean" },
    { key: "integrations.sns.enabled", label: "SNS 連携", kind: "boolean" },
    { key: "integrations.line.enabled", label: "外部通知", kind: "boolean" },
  ];

  // Block: Runtime state
  let stream = null;
  let micRecognition = null;
  let statusTimerId = 0;
  let latestStatusSnapshot = null;
  let latestEditorSnapshot = null;
  let editorDraft = null;
  let activeSettingsTab = "behavior";
  const draftMessages = new Map();

  // Block: Application startup
  async function start() {
    installEventHandlers();
    updateSendEnabledState();
    connectStream();
    await refreshStatusSnapshot();
    await loadSettingsEditorSnapshot();
    statusTimerId = window.setInterval(() => {
      void refreshStatusSnapshot();
    }, 5000);
  }

  // Block: Event registration
  function installEventHandlers() {
    chatForm.addEventListener("submit", handleChatSubmit);
    chatInput.addEventListener("input", handleComposerInput);
    chatInput.addEventListener("keydown", handleComposerKeyDown);
    cancelButton.addEventListener("click", handleCancel);
    settingsButton.addEventListener("click", () => {
      void openSettingsPanel();
    });
    settingsCloseButton.addEventListener("click", closeSettingsPanel);
    settingsReloadButton.addEventListener("click", () => {
      void reloadSettingsPanel();
    });
    settingsSaveButton.addEventListener("click", () => {
      void handleSettingsSave();
    });
    settingsDummyButton.addEventListener("click", () => {
      appendNotice("settings_dummy", "このボタンはまだダミーです");
    });
    for (const button of settingsTabButtons) {
      button.addEventListener("click", () => {
        const tabKey = String(button.dataset.settingsTab || "");
        if (!SETTINGS_TAB_KEYS.includes(tabKey)) {
          appendError("設定タブの定義が不正です");
          return;
        }
        activeSettingsTab = tabKey;
        applySettingsTabState();
      });
    }
    micButton.addEventListener("click", () => {
      void handleMicClick();
    });
    cameraButton.addEventListener("click", () => {
      appendNotice("camera_dummy", "カメラ UI はまだダミーです");
    });
    window.addEventListener("beforeunload", stopStream);
  }

  // Block: Composer handlers
  function handleComposerInput() {
    autoResizeComposer();
    updateSendEnabledState();
  }

  function handleComposerKeyDown(event) {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    event.preventDefault();
    if (sendButton.disabled) {
      return;
    }
    chatForm.requestSubmit();
  }

  // Block: Stream lifecycle
  function connectStream() {
    stopStream();
    connectionText.textContent = "接続中...";
    stream = new EventSource("/api/chat/stream?channel=browser_chat");
    stream.addEventListener("open", () => {
      connectionText.textContent = "SSE 接続中";
    });
    stream.addEventListener("status", (event) => {
      const payload = parsePayload(event.data);
      if (payload === null) {
        return;
      }
      handleStatusEvent(payload);
    });
    stream.addEventListener("token", (event) => {
      const payload = parsePayload(event.data);
      if (payload === null) {
        return;
      }
      handleTokenEvent(payload);
    });
    stream.addEventListener("message", (event) => {
      const payload = parsePayload(event.data);
      if (payload === null) {
        return;
      }
      handleMessageEvent(payload);
    });
    stream.addEventListener("notice", (event) => {
      const payload = parsePayload(event.data);
      if (payload === null) {
        return;
      }
      handleNoticeEvent(payload);
    });
    stream.addEventListener("error", (event) => {
      if (typeof event.data === "string" && event.data) {
        const payload = parsePayload(event.data);
        if (payload !== null) {
          handleErrorEvent(payload);
          return;
        }
      }
      connectionText.textContent = "SSE 再接続中...";
    });
  }

  function stopStream() {
    if (stream === null) {
      return;
    }
    stream.close();
    stream = null;
  }

  // Block: Chat requests
  async function handleChatSubmit(event) {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text) {
      return;
    }
    try {
      await submitChatText(text);
      chatInput.value = "";
      autoResizeComposer();
      updateSendEnabledState();
      chatInput.focus();
    } catch (error) {
      appendError(`送信に失敗しました: ${error.message}`);
    }
  }

  async function handleCancel() {
    cancelButton.disabled = true;
    try {
      stopBrowserSpeech();
      const response = await fetch("/api/chat/cancel", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({}),
      });
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload));
      }
      appendNotice("cancel_requested", "停止要求を送りました");
    } catch (error) {
      appendError(`停止に失敗しました: ${error.message}`);
    } finally {
      cancelButton.disabled = false;
    }
  }

  async function submitChatText(text) {
    const response = await fetch("/api/chat/input", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        channel: "browser_chat",
        input_kind: "chat_message",
        text,
      }),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(readErrorMessage(payload));
    }
    appendMessage({
      role: "user",
      text,
      messageId: String(payload.input_id),
      isDraft: false,
    });
  }

  // Block: Settings panel actions
  async function openSettingsPanel() {
    chatPanel.classList.add("hidden");
    chatForm.classList.add("hidden");
    settingsPanel.classList.remove("hidden");
    await Promise.all([refreshStatusSnapshot(), loadSettingsEditorSnapshot()]);
  }

  function closeSettingsPanel() {
    settingsPanel.classList.add("hidden");
    chatPanel.classList.remove("hidden");
    chatForm.classList.remove("hidden");
  }

  async function reloadSettingsPanel() {
    await Promise.all([refreshStatusSnapshot(), loadSettingsEditorSnapshot()]);
  }

  async function handleSettingsSave() {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    settingsSaveButton.disabled = true;
    try {
      const response = await fetch("/api/settings/editor", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(editorDraft),
      });
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload));
      }
      applyEditorSnapshot(payload);
      settingsStatus.textContent = "設定を保存しました";
      await refreshStatusSnapshot();
    } catch (error) {
      settingsStatus.textContent = `保存失敗: ${error.message}`;
    } finally {
      settingsSaveButton.disabled = false;
    }
  }

  // Block: Mic input
  async function handleMicClick() {
    if (micRecognition !== null) {
      micRecognition.stop();
      return;
    }
    let runtimeProjection;
    try {
      runtimeProjection = requireRuntimeProjection();
    } catch (error) {
      appendError(`マイク入力を開始できません: ${error.message}`);
      return;
    }
    if (readBooleanRuntimeProjection(runtimeProjection, "sensors.microphone.enabled") !== true) {
      appendError("マイク入力は無効です");
      return;
    }
    if (typeof window.SpeechRecognition !== "function") {
      appendError("このブラウザでは音声入力が使えません");
      return;
    }
    const recognition = new window.SpeechRecognition();
    micRecognition = recognition;
    recognition.lang = "ja-JP";
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;
    setMicListeningState(true);
    recognition.addEventListener("result", (event) => {
      void handleMicResult(event);
    });
    recognition.addEventListener("error", (event) => {
      const errorCode = event && typeof event.error === "string" ? event.error : "unknown";
      appendError(`音声入力に失敗しました: ${errorCode}`);
    });
    recognition.addEventListener("end", () => {
      micRecognition = null;
      setMicListeningState(false);
    });
    recognition.start();
  }

  async function handleMicResult(event) {
    if (!event.results || !event.results[0] || !event.results[0][0]) {
      appendError("音声入力の結果が不正です");
      return;
    }
    const transcript = String(event.results[0][0].transcript).trim();
    if (!transcript) {
      appendError("音声入力が空です");
      return;
    }
    try {
      await submitChatText(transcript);
      chatInput.value = "";
      autoResizeComposer();
      updateSendEnabledState();
      chatInput.focus();
    } catch (error) {
      appendError(`音声入力の送信に失敗しました: ${error.message}`);
    }
  }

  // Block: Snapshot loading
  async function refreshStatusSnapshot() {
    try {
      const response = await fetch("/api/status");
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload));
      }
      latestStatusSnapshot = payload;
      statusJson.textContent = formatJson(payload);
      updateRuntimeChip(payload);
    } catch (error) {
      runtimeText.textContent = `状態取得に失敗しました: ${error.message}`;
      if (!settingsPanel.classList.contains("hidden")) {
        statusJson.textContent = runtimeText.textContent;
      }
    }
  }

  async function loadSettingsEditorSnapshot() {
    try {
      const response = await fetch("/api/settings/editor");
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload));
      }
      applyEditorSnapshot(payload);
    } catch (error) {
      settingsStatus.textContent = `設定読込に失敗しました: ${error.message}`;
      appendError(`設定読込に失敗しました: ${error.message}`);
    }
  }

  function applyEditorSnapshot(snapshot) {
    validateEditorSnapshot(snapshot);
    latestEditorSnapshot = snapshot;
    editorDraft = {
      editor_state: cloneJson(snapshot.editor_state),
      preset_catalogs: cloneJson(snapshot.preset_catalogs),
    };
    settingsStatus.textContent = "サーバ正本を読込済み";
    renderSettingsEditor();
  }

  function validateEditorSnapshot(snapshot) {
    if (!isObject(snapshot)) {
      throw new Error("設定スナップショットが不正です");
    }
    if (!isObject(snapshot.editor_state)) {
      throw new Error("editor_state が不正です");
    }
    if (!isObject(snapshot.preset_catalogs)) {
      throw new Error("preset_catalogs が不正です");
    }
    if (!isObject(snapshot.runtime_projection)) {
      throw new Error("runtime_projection が不正です");
    }
  }

  // Block: Settings editor rendering
  function renderSettingsEditor() {
    if (editorDraft === null || latestEditorSnapshot === null) {
      return;
    }
    renderPresetCard("behavior", "振る舞いプリセット", settingsBehaviorCard);
    renderPresetCard("llm", "会話プリセット", settingsLlmCard);
    renderPresetCard("memory", "記憶プリセット", settingsMemoryCard);
    renderPresetCard("output", "出力プリセット", settingsOutputCard);
    renderDirectValuesCard();
    settingsJson.textContent = formatJson(latestEditorSnapshot.runtime_projection);
    if (latestStatusSnapshot !== null) {
      statusJson.textContent = formatJson(latestStatusSnapshot);
    }
    applySettingsTabState();
    attachSettingsEditorHandlers();
    updateSettingsDirtyState();
  }

  function renderPresetCard(kind, title, container) {
    const presetEntries = readPresetEntries(kind);
    const activePresetId = readActivePresetId(kind);
    const activePreset = requirePresetEntry(kind, activePresetId);
    const selectOptions = presetEntries
      .filter((entry) => entry.archived !== true || entry.preset_id === activePresetId)
      .map((entry) => {
        const selected = entry.preset_id === activePresetId ? " selected" : "";
        const archivedTag = entry.archived === true ? " (archived)" : "";
        return `<option value="${escapeHtml(entry.preset_id)}"${selected}>${escapeHtml(entry.preset_name)}${archivedTag}</option>`;
      })
      .join("");
    const fieldsHtml = PRESET_DESCRIPTORS[kind]
      .map((descriptor) => renderPresetField(kind, activePreset.payload, descriptor))
      .join("");
    container.innerHTML = `
      <div class="settings-card-title">${escapeHtml(title)}</div>
      <div class="settings-grid">
        <label class="settings-field">
          <span class="settings-label">使用プリセット</span>
          <select class="settings-input" data-active-preset-kind="${escapeHtml(kind)}">${selectOptions}</select>
        </label>
        <label class="settings-field">
          <span class="settings-label">プリセット名</span>
          <input class="settings-input" type="text" value="${escapeHtml(activePreset.preset_name)}" data-preset-name-kind="${escapeHtml(kind)}" />
        </label>
      </div>
      <div class="settings-grid">${fieldsHtml}</div>
    `;
  }

  function renderPresetField(kind, payload, descriptor) {
    const rawValue = readNestedValue(payload, descriptor.path);
    const path = escapeHtml(descriptor.path);
    const label = escapeHtml(descriptor.label);
    if (descriptor.kind === "select") {
      const optionsHtml = descriptor.options
        .map((optionValue) => {
          const selected = rawValue === optionValue ? " selected" : "";
          return `<option value="${escapeHtml(optionValue)}"${selected}>${escapeHtml(optionValue)}</option>`;
        })
        .join("");
      return `
        <label class="settings-field">
          <span class="settings-label">${label}</span>
          <select class="settings-input" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string">${optionsHtml}</select>
        </label>
      `;
    }
    if (descriptor.kind === "password") {
      return `
        <label class="settings-field">
          <span class="settings-label">${label}</span>
          <input class="settings-input" type="password" value="${escapeHtml(requireString(rawValue, descriptor.path))}" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string" />
        </label>
      `;
    }
    if (descriptor.kind === "text") {
      return `
        <label class="settings-field">
          <span class="settings-label">${label}</span>
          <input class="settings-input" type="text" value="${escapeHtml(requireString(rawValue, descriptor.path))}" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string" />
        </label>
      `;
    }
    const numberValue = requireNumber(rawValue, descriptor.path);
    const step = descriptor.step ?? 1;
    const minAttr = descriptor.min !== undefined ? ` min="${descriptor.min}"` : "";
    const maxAttr = descriptor.max !== undefined ? ` max="${descriptor.max}"` : "";
    return `
      <label class="settings-field">
        <span class="settings-label">${label}</span>
        <input class="settings-input" type="number" value="${String(numberValue)}" step="${String(step)}"${minAttr}${maxAttr} data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="${escapeHtml(descriptor.kind)}" />
      </label>
    `;
  }

  function renderDirectValuesCard() {
    const directValues = requireDirectValues();
    const fieldsHtml = DIRECT_DESCRIPTORS
      .map((descriptor) => renderDirectField(directValues, descriptor))
      .join("");
    settingsDirectCard.innerHTML = `
      <div class="settings-card-title">システム direct 値</div>
      <div class="settings-grid">${fieldsHtml}</div>
    `;
  }

  function renderDirectField(directValues, descriptor) {
    const value = directValues[descriptor.key];
    if (descriptor.kind === "boolean") {
      return `
        <label class="settings-check">
          <input type="checkbox" ${value === true ? "checked" : ""} data-direct-key="${escapeHtml(descriptor.key)}" data-value-kind="boolean" />
          <span>${escapeHtml(descriptor.label)}</span>
        </label>
      `;
    }
    const numberValue = descriptor.kind === "integer"
      ? requireInteger(value, descriptor.key)
      : requireNumber(value, descriptor.key);
    const minAttr = descriptor.min !== undefined ? ` min="${descriptor.min}"` : "";
    const maxAttr = descriptor.max !== undefined ? ` max="${descriptor.max}"` : "";
    return `
      <label class="settings-field">
        <span class="settings-label">${escapeHtml(descriptor.label)}</span>
        <input class="settings-input" type="number" value="${String(numberValue)}" step="${String(descriptor.step ?? 1)}"${minAttr}${maxAttr} data-direct-key="${escapeHtml(descriptor.key)}" data-value-kind="${escapeHtml(descriptor.kind)}" />
      </label>
    `;
  }

  function attachSettingsEditorHandlers() {
    const activePresetInputs = settingsPanel.querySelectorAll("[data-active-preset-kind]");
    for (const element of activePresetInputs) {
      element.addEventListener("change", handleActivePresetChange);
    }
    const presetNameInputs = settingsPanel.querySelectorAll("[data-preset-name-kind]");
    for (const element of presetNameInputs) {
      element.addEventListener("input", handlePresetNameChange);
    }
    const presetValueInputs = settingsPanel.querySelectorAll("[data-preset-kind][data-preset-path]");
    for (const element of presetValueInputs) {
      element.addEventListener("input", handlePresetFieldChange);
      element.addEventListener("change", handlePresetFieldChange);
    }
    const directValueInputs = settingsPanel.querySelectorAll("[data-direct-key]");
    for (const element of directValueInputs) {
      element.addEventListener("input", handleDirectFieldChange);
      element.addEventListener("change", handleDirectFieldChange);
    }
  }

  function handleActivePresetChange(event) {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    const element = event.currentTarget;
    const kind = String(element.dataset.activePresetKind || "");
    if (!SETTINGS_PRESET_KINDS.includes(kind)) {
      appendError("プリセット種別が不正です");
      return;
    }
    writeActivePresetId(kind, String(element.value));
    renderSettingsEditor();
  }

  function handlePresetNameChange(event) {
    const element = event.currentTarget;
    const kind = String(element.dataset.presetNameKind || "");
    const presetEntry = requireActivePresetEntry(kind);
    presetEntry.preset_name = String(element.value);
    updateSettingsDirtyState();
  }

  function handlePresetFieldChange(event) {
    const element = event.currentTarget;
    const kind = String(element.dataset.presetKind || "");
    const path = String(element.dataset.presetPath || "");
    const valueKind = String(element.dataset.valueKind || "");
    const presetEntry = requireActivePresetEntry(kind);
    writeNestedValue(presetEntry.payload, path, readInputValue(element, valueKind));
    updateSettingsDirtyState();
  }

  function handleDirectFieldChange(event) {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    const element = event.currentTarget;
    const key = String(element.dataset.directKey || "");
    const valueKind = String(element.dataset.valueKind || "");
    editorDraft.editor_state.direct_values[key] = readInputValue(element, valueKind);
    updateSettingsDirtyState();
  }

  function updateSettingsDirtyState() {
    if (editorDraft === null || latestEditorSnapshot === null) {
      return;
    }
    const currentCanonical = JSON.stringify(editorDraft);
    const serverCanonical = JSON.stringify({
      editor_state: latestEditorSnapshot.editor_state,
      preset_catalogs: latestEditorSnapshot.preset_catalogs,
    });
    settingsStatus.textContent = currentCanonical === serverCanonical
      ? "保存済み"
      : "未保存の変更があります";
  }

  function applySettingsTabState() {
    for (const button of settingsTabButtons) {
      const isActive = String(button.dataset.settingsTab || "") === activeSettingsTab;
      button.classList.toggle("active", isActive);
    }
    for (const page of settingsPages) {
      const isActive = String(page.dataset.settingsPage || "") === activeSettingsTab;
      page.classList.toggle("hidden", !isActive);
    }
  }

  // Block: Stream payload handlers
  function handleStatusEvent(payload) {
    const label = typeof payload.label === "string" ? payload.label : "状態更新";
    runtimeText.textContent = label;
  }

  function handleTokenEvent(payload) {
    const messageId = requireString(payload.message_id, "token.message_id");
    const chunk = requireString(payload.text, "token.text");
    let messageNode = draftMessages.get(messageId);
    if (messageNode === undefined) {
      messageNode = appendMessage({
        role: "assistant",
        text: "",
        messageId,
        isDraft: true,
      });
      draftMessages.set(messageId, messageNode);
    }
    const bubble = messageNode.querySelector(".bubble");
    bubble.textContent += chunk;
    scrollToBottom();
  }

  function handleMessageEvent(payload) {
    const messageId = requireString(payload.message_id, "message.message_id");
    const role = requireString(payload.role, "message.role");
    const text = requireString(payload.text, "message.text");
    let messageNode = draftMessages.get(messageId);
    if (messageNode === undefined) {
      messageNode = appendMessage({
        role,
        text,
        messageId,
        isDraft: false,
      });
    } else {
      const bubble = messageNode.querySelector(".bubble");
      const label = messageNode.querySelector(".bubble-time");
      bubble.textContent = text;
      label.textContent = buildMetaLabel(role);
      label.classList.remove("empty");
      draftMessages.delete(messageId);
    }
    speakMessageText(text);
  }

  function handleNoticeEvent(payload) {
    const noticeCode = requireString(payload.notice_code, "notice.notice_code");
    const text = typeof payload.label === "string" && payload.label
      ? payload.label
      : noticeCode;
    appendNotice(noticeCode, text);
  }

  function handleErrorEvent(payload) {
    const message = typeof payload.message === "string" && payload.message
      ? payload.message
      : "処理中にエラーが発生しました";
    appendError(message);
  }

  // Block: UI message rendering
  function appendMessage({ role, text, messageId, isDraft }) {
    const row = document.createElement("div");
    row.className = `bubble-row ${role === "user" ? "right" : "left"}`;
    row.dataset.messageId = messageId;

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = buildMetaLabel(role);
    if (isDraft) {
      meta.classList.add("empty");
    }

    row.appendChild(bubble);
    row.appendChild(meta);
    chatScroll.appendChild(row);
    scrollToBottom();
    return row;
  }

  function appendNotice(code, text) {
    const row = document.createElement("div");
    row.className = "bubble-row left";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = buildMetaLabel(`notice:${code}`);

    row.appendChild(bubble);
    row.appendChild(meta);
    chatScroll.appendChild(row);
    scrollToBottom();
  }

  function appendError(text) {
    const row = document.createElement("div");
    row.className = "bubble-row left";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    bubble.style.borderColor = "#aa3d52";
    bubble.style.color = "#6b1022";

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = buildMetaLabel("error");

    row.appendChild(bubble);
    row.appendChild(meta);
    chatScroll.appendChild(row);
    scrollToBottom();
  }

  function buildMetaLabel(role) {
    if (role === "user") {
      return "user";
    }
    if (role === "assistant") {
      return "assistant";
    }
    if (role.startsWith("notice:")) {
      return role.slice("notice:".length);
    }
    return role;
  }

  // Block: Runtime status rendering
  function updateRuntimeChip(statusPayload) {
    if (!isObject(statusPayload) || !isObject(statusPayload.runtime)) {
      throw new Error("status payload が不正です");
    }
    const runtime = statusPayload.runtime;
    if (runtime.is_running === true) {
      runtimeText.textContent = "人格ランタイム稼働中";
      return;
    }
    runtimeText.textContent = "人格ランタイム停止中";
  }

  // Block: Composer helpers
  function autoResizeComposer() {
    chatInput.style.height = "auto";
    chatInput.style.height = `${chatInput.scrollHeight}px`;
  }

  function updateSendEnabledState() {
    sendButton.disabled = chatInput.value.trim().length === 0;
  }

  function scrollToBottom() {
    chatScroll.scrollTop = chatScroll.scrollHeight;
  }

  // Block: Browser speech output
  function speakMessageText(text) {
    if (!text) {
      return;
    }
    if (!("speechSynthesis" in window)) {
      return;
    }
    if (latestEditorSnapshot === null) {
      return;
    }
    try {
      const runtimeProjection = requireRuntimeProjection();
      if (readBooleanRuntimeProjection(runtimeProjection, "output.tts.enabled") !== true) {
        return;
      }
      if (readStringRuntimeProjection(runtimeProjection, "output.mode") !== "ui_and_tts") {
        return;
      }
      const voiceName = readStringRuntimeProjection(runtimeProjection, "output.tts.voice");
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "ja-JP";
      const voices = window.speechSynthesis.getVoices();
      const selectedVoice = voices.find((voice) => voice.name === voiceName);
      if (selectedVoice) {
        utterance.voice = selectedVoice;
      }
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    } catch (error) {
      appendError(`TTS を開始できません: ${error.message}`);
    }
  }

  function stopBrowserSpeech() {
    if (!("speechSynthesis" in window)) {
      return;
    }
    window.speechSynthesis.cancel();
  }

  function setMicListeningState(isListening) {
    micButton.classList.toggle("listening", isListening);
  }

  // Block: Settings draft helpers
  function readPresetEntries(kind) {
    if (editorDraft === null) {
      throw new Error("設定ドラフトが未初期化です");
    }
    const entries = editorDraft.preset_catalogs[kind];
    if (!Array.isArray(entries)) {
      throw new Error(`${kind} preset_catalogs が不正です`);
    }
    return entries;
  }

  function readActivePresetId(kind) {
    if (editorDraft === null) {
      throw new Error("設定ドラフトが未初期化です");
    }
    const key = `active_${kind}_preset_id`;
    return requireString(editorDraft.editor_state[key], key);
  }

  function writeActivePresetId(kind, presetId) {
    if (editorDraft === null) {
      throw new Error("設定ドラフトが未初期化です");
    }
    const key = `active_${kind}_preset_id`;
    editorDraft.editor_state[key] = presetId;
  }

  function requireActivePresetEntry(kind) {
    const presetId = readActivePresetId(kind);
    return requirePresetEntry(kind, presetId);
  }

  function requirePresetEntry(kind, presetId) {
    const entry = readPresetEntries(kind).find((candidate) => String(candidate.preset_id) === presetId);
    if (entry === undefined) {
      throw new Error(`${kind} のアクティブプリセットが見つかりません`);
    }
    if (!isObject(entry.payload)) {
      throw new Error(`${kind} の payload が不正です`);
    }
    return entry;
  }

  function requireDirectValues() {
    if (editorDraft === null || !isObject(editorDraft.editor_state)) {
      throw new Error("direct_values が未初期化です");
    }
    const directValues = editorDraft.editor_state.direct_values;
    if (!isObject(directValues)) {
      throw new Error("direct_values が不正です");
    }
    return directValues;
  }

  function requireRuntimeProjection() {
    if (latestEditorSnapshot === null || !isObject(latestEditorSnapshot.runtime_projection)) {
      throw new Error("runtime_projection が未取得です");
    }
    return latestEditorSnapshot.runtime_projection;
  }

  function readNestedValue(root, path) {
    if (isObject(root) && path in root) {
      return root[path];
    }
    const segments = path.split(".");
    let current = root;
    for (const segment of segments) {
      if (!isObject(current) || !(segment in current)) {
        throw new Error(`${path} が不正です`);
      }
      current = current[segment];
    }
    return current;
  }

  function writeNestedValue(root, path, value) {
    if (isObject(root) && path in root) {
      root[path] = value;
      return;
    }
    const segments = path.split(".");
    let current = root;
    for (let index = 0; index < segments.length - 1; index += 1) {
      const segment = segments[index];
      if (!isObject(current[segment])) {
        current[segment] = {};
      }
      current = current[segment];
    }
    current[segments[segments.length - 1]] = value;
  }

  // Block: Value parsing
  function readInputValue(element, valueKind) {
    if (valueKind === "boolean") {
      return element.checked === true;
    }
    if (valueKind === "integer") {
      const value = Number.parseInt(element.value, 10);
      if (!Number.isInteger(value)) {
        throw new Error("整数入力が不正です");
      }
      return value;
    }
    if (valueKind === "number") {
      const value = Number.parseFloat(element.value);
      if (!Number.isFinite(value)) {
        throw new Error("数値入力が不正です");
      }
      return value;
    }
    return String(element.value);
  }

  // Block: JSON helpers
  async function readJson(response) {
    const text = await response.text();
    if (!text) {
      return {};
    }
    const payload = JSON.parse(text);
    if (!isObject(payload)) {
      throw new Error("JSON 応答が不正です");
    }
    return payload;
  }

  function parsePayload(data) {
    try {
      const payload = JSON.parse(data);
      if (!isObject(payload)) {
        throw new Error("payload must be object");
      }
      return payload;
    } catch (error) {
      appendError(`SSE payload の解釈に失敗しました: ${error.message}`);
      return null;
    }
  }

  function readErrorMessage(payload) {
    if (!isObject(payload) || typeof payload.message !== "string" || !payload.message) {
      return "不明なエラー";
    }
    return payload.message;
  }

  function formatJson(value) {
    return JSON.stringify(value, null, 2);
  }

  function cloneJson(value) {
    return JSON.parse(JSON.stringify(value));
  }

  // Block: Type helpers
  function isObject(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
  }

  function requireString(value, label) {
    if (typeof value !== "string") {
      throw new Error(`${label} が文字列ではありません`);
    }
    return value;
  }

  function requireNumber(value, label) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(`${label} が数値ではありません`);
    }
    return value;
  }

  function requireInteger(value, label) {
    if (!Number.isInteger(value)) {
      throw new Error(`${label} が整数ではありません`);
    }
    return value;
  }

  function readBooleanRuntimeProjection(runtimeProjection, key) {
    const value = runtimeProjection[key];
    if (typeof value !== "boolean") {
      throw new Error(`${key} が boolean ではありません`);
    }
    return value;
  }

  function readStringRuntimeProjection(runtimeProjection, key) {
    const value = runtimeProjection[key];
    if (typeof value !== "string") {
      throw new Error(`${key} が string ではありません`);
    }
    return value;
  }

  // Block: HTML escape
  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll("\"", "&quot;");
  }

  // Block: Startup invocation
  void start();
})();
