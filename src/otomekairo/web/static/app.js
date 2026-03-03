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
  const attachments = document.getElementById("attachments");
  const settingsButton = document.getElementById("btn-settings");
  const settingsPanel = document.getElementById("settings-panel");
  const settingsDummyButton = document.getElementById("btn-settings-dummy");
  const settingsReloadButton = document.getElementById("btn-settings-reload");
  const settingsSaveButton = document.getElementById("btn-settings-save");
  const settingsCloseButton = document.getElementById("btn-settings-close");
  const llmDefaultModelInput = document.getElementById("setting-llm-default-model");
  const llmTemperatureInput = document.getElementById("setting-llm-temperature");
  const runtimeIdleTickInput = document.getElementById("setting-runtime-idle-tick-ms");
  const outputTtsEnabledInput = document.getElementById("setting-output-tts-enabled");
  const outputTtsVoiceInput = document.getElementById("setting-output-tts-voice");
  const integrationsLineEnabledInput = document.getElementById("setting-integrations-line-enabled");
  const settingsJson = document.getElementById("settings-json");
  const statusJson = document.getElementById("status-json");
  const connectionText = document.getElementById("connection-text");
  const runtimeText = document.getElementById("runtime-text");

  // Block: Runtime state
  let stream = null;
  let micRecognition = null;
  const draftMessages = new Map();
  const pendingCameraAttachments = [];
  let statusTimerId = 0;
  let latestSettings = null;

  // Block: Startup
  function start() {
    installEventHandlers();
    updateSendEnabledState();
    connectStream();
    void refreshSnapshots();
    statusTimerId = window.setInterval(() => {
      void refreshSnapshots();
    }, 5000);
  }

  // Block: Event handlers
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
    if (settingsDummyButton !== null) {
      settingsDummyButton.addEventListener("click", () => {
        appendNotice("settings_dummy", "このボタンはまだダミーです");
      });
    }
    micButton.addEventListener("click", () => {
      void handleMicClick();
    });
    cameraButton.addEventListener("click", () => {
      void handleCameraCapture();
    });
    window.addEventListener("beforeunload", stopStream);
  }

  // Block: Composer input
  function handleComposerInput() {
    autoResizeComposer();
    updateSendEnabledState();
  }

  // Block: Composer keydown
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

  // Block: Stream connect
  function connectStream() {
    stopStream();
    connectionText.textContent = "接続中...";
    stream = new EventSource("/api/chat/stream?channel=browser_chat");
    stream.addEventListener("open", () => {
      connectionText.textContent = "SSE 接続中";
    });
    stream.addEventListener("status", (event) => {
      const payload = parsePayload(event.data);
      if (!payload) {
        return;
      }
      handleStatusEvent(payload);
    });
    stream.addEventListener("token", (event) => {
      const payload = parsePayload(event.data);
      if (!payload) {
        return;
      }
      handleTokenEvent(payload);
    });
    stream.addEventListener("message", (event) => {
      const payload = parsePayload(event.data);
      if (!payload) {
        return;
      }
      handleMessageEvent(payload);
    });
    stream.addEventListener("notice", (event) => {
      const payload = parsePayload(event.data);
      if (!payload) {
        return;
      }
      handleNoticeEvent(payload);
    });
    stream.addEventListener("error", (event) => {
      if (event.data) {
        const payload = parsePayload(event.data);
        if (payload) {
          handleErrorEvent(payload);
          return;
        }
      }
      connectionText.textContent = "SSE 再接続中...";
    });
  }

  // Block: Stream stop
  function stopStream() {
    if (stream === null) {
      return;
    }
    stream.close();
    stream = null;
  }

  // Block: Chat submit
  async function handleChatSubmit(event) {
    event.preventDefault();
    const text = chatInput.value.trim();
    if (!text && pendingCameraAttachments.length === 0) {
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

  // Block: Cancel submit
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

  // Block: Settings open
  async function openSettingsPanel() {
    chatPanel.classList.add("hidden");
    chatForm.classList.add("hidden");
    settingsPanel.classList.remove("hidden");
    await refreshSnapshots();
    try {
      syncEditableSettingsFromSnapshot();
    } catch (error) {
      appendError(`設定表示に失敗しました: ${error.message}`);
    }
  }

  // Block: Settings close
  function closeSettingsPanel() {
    settingsPanel.classList.add("hidden");
    chatPanel.classList.remove("hidden");
    chatForm.classList.remove("hidden");
  }

  // Block: Settings reload
  async function reloadSettingsPanel() {
    await refreshSnapshots();
    try {
      syncEditableSettingsFromSnapshot();
    } catch (error) {
      appendError(`設定再読込に失敗しました: ${error.message}`);
    }
  }

  // Block: Settings save
  async function handleSettingsSave() {
    let requestedSettings;
    let effectiveSettings;
    try {
      effectiveSettings = requireEffectiveSettings();
      requestedSettings = collectEditableSettings();
    } catch (error) {
      appendError(`設定保存に失敗しました: ${error.message}`);
      return;
    }
    const changedEntries = Object.entries(requestedSettings).filter(([key, value]) => !Object.is(effectiveSettings[key], value));
    if (changedEntries.length === 0) {
      appendNotice("settings_no_changes", "変更はありません");
      return;
    }
    settingsSaveButton.disabled = true;
    try {
      for (const [key, requestedValue] of changedEntries) {
        const response = await fetch("/api/settings/overrides", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            key,
            requested_value: requestedValue,
            apply_scope: "runtime",
          }),
        });
        const payload = await readJson(response);
        if (!response.ok) {
          throw new Error(readErrorMessage(payload));
        }
      }
      appendNotice("settings_saved", `${changedEntries.length} 件の設定変更を受け付けました`);
      await refreshSnapshots();
    } catch (error) {
      appendError(`設定保存に失敗しました: ${error.message}`);
    } finally {
      settingsSaveButton.disabled = false;
    }
  }

  // Block: Mic click
  async function handleMicClick() {
    if (micRecognition !== null) {
      micRecognition.stop();
      return;
    }
    let effectiveSettings;
    try {
      effectiveSettings = requireEffectiveSettings();
      if (readBooleanSetting(effectiveSettings, "sensors.microphone.enabled") !== true) {
        throw new Error("マイク入力は無効です");
      }
    } catch (error) {
      appendError(`音声入力を開始できません: ${error.message}`);
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

  // Block: Mic result
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

  // Block: Camera capture
  async function handleCameraCapture() {
    cameraButton.disabled = true;
    try {
      const response = await fetch("/api/camera/capture", {
        method: "POST",
      });
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload));
      }
      appendCameraAttachment(payload);
      appendNotice("camera_captured", "カメラ画像を取得しました");
    } catch (error) {
      appendError(`カメラ画像の取得に失敗しました: ${error.message}`);
    } finally {
      cameraButton.disabled = false;
    }
  }

  // Block: Snapshot refresh
  async function refreshSnapshots() {
    try {
      const [statusResponse, settingsResponse] = await Promise.all([
        fetch("/api/status"),
        fetch("/api/settings"),
      ]);
      const statusPayload = await readJson(statusResponse);
      const settingsPayload = await readJson(settingsResponse);
      if (!statusResponse.ok) {
        throw new Error(readErrorMessage(statusPayload));
      }
      if (!settingsResponse.ok) {
        throw new Error(readErrorMessage(settingsPayload));
      }
      latestSettings = settingsPayload;
      statusJson.textContent = formatJson(statusPayload);
      settingsJson.textContent = formatJson(settingsPayload);
      updateRuntimeChip(statusPayload);
      connectionText.textContent = "SSE 接続中";
    } catch (error) {
      connectionText.textContent = "状態取得失敗";
      runtimeText.textContent = `状態取得に失敗しました: ${error.message}`;
      if (!settingsPanel.classList.contains("hidden")) {
        statusJson.textContent = runtimeText.textContent;
      }
    }
  }

  // Block: Settings form sync
  function syncEditableSettingsFromSnapshot() {
    const effectiveSettings = requireEffectiveSettings();
    llmDefaultModelInput.value = readStringSetting(effectiveSettings, "llm.default_model");
    llmTemperatureInput.value = String(readNumberSetting(effectiveSettings, "llm.temperature"));
    runtimeIdleTickInput.value = String(readIntegerSetting(effectiveSettings, "runtime.idle_tick_ms"));
    outputTtsEnabledInput.checked = readBooleanSetting(effectiveSettings, "output.tts.enabled");
    outputTtsVoiceInput.value = readStringSetting(effectiveSettings, "output.tts.voice");
    integrationsLineEnabledInput.checked = readBooleanSetting(effectiveSettings, "integrations.line.enabled");
  }

  // Block: Status event handler
  function handleStatusEvent(payload) {
    const label = typeof payload.label === "string" ? payload.label : "状態更新";
    runtimeText.textContent = label;
  }

  // Block: Token event handler
  function handleTokenEvent(payload) {
    const messageId = String(payload.message_id || "");
    if (!messageId) {
      return;
    }
    const chunk = typeof payload.text === "string" ? payload.text : "";
    let messageNode = draftMessages.get(messageId);
    if (!messageNode) {
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

  // Block: Message event handler
  function handleMessageEvent(payload) {
    const messageId = String(payload.message_id || "");
    const text = typeof payload.text === "string" ? payload.text : "";
    let messageNode = draftMessages.get(messageId);
    if (!messageNode) {
      messageNode = appendMessage({
        role: String(payload.role || "assistant"),
        text,
        messageId,
        isDraft: false,
      });
    } else {
      const bubble = messageNode.querySelector(".bubble");
      bubble.textContent = text;
      const label = messageNode.querySelector(".bubble-time");
      label.textContent = buildMetaLabel(String(payload.role || "assistant"));
      label.classList.remove("empty");
      draftMessages.delete(messageId);
    }
    speakMessageText(text);
    scrollToBottom();
  }

  // Block: Notice event handler
  function handleNoticeEvent(payload) {
    const text = typeof payload.text === "string" ? payload.text : "通知";
    appendNotice(String(payload.notice_code || "notice"), text);
  }

  // Block: Error event handler
  function handleErrorEvent(payload) {
    const text = typeof payload.text === "string" ? payload.text : "エラーが発生しました";
    appendError(text);
  }

  // Block: Message append
  function appendMessage({ role, text, messageId, isDraft }) {
    const rowRole = role === "user" ? "user" : "ai";
    const row = document.createElement("div");
    row.className = `bubble-row ${rowRole}`;
    if (messageId) {
      row.dataset.messageId = messageId;
    }

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    if (isDraft) {
      meta.classList.add("empty");
    }
    meta.textContent = isDraft ? "" : buildMetaLabel(role, isDraft);

    const bubble = document.createElement("div");
    bubble.className = `bubble ${rowRole}`;
    bubble.textContent = text;
    if (rowRole === "user") {
      row.append(meta, bubble);
    } else {
      row.append(bubble, meta);
    }
    chatScroll.appendChild(row);
    scrollToBottom();
    return row;
  }

  // Block: Notice append
  function appendNotice(code, text) {
    const row = document.createElement("div");
    row.className = "bubble-row ai";
    row.dataset.noticeCode = String(code);

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = buildNoticeMetaLabel(String(code));

    const bubble = document.createElement("div");
    bubble.className = "bubble ai";
    bubble.textContent = text;

    row.append(bubble, meta);
    chatScroll.appendChild(row);
    scrollToBottom();
  }

  // Block: Error append
  function appendError(text) {
    const row = document.createElement("div");
    row.className = "bubble-row ai";

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = "エラー";

    const bubble = document.createElement("div");
    bubble.className = "bubble ai";
    bubble.textContent = text;

    row.append(bubble, meta);
    chatScroll.appendChild(row);
    scrollToBottom();
  }

  // Block: Camera attachment append
  function appendCameraAttachment(payload) {
    const attachment = buildPendingCameraAttachment(payload);
    const item = document.createElement("div");
    item.className = "attachment";
    item.dataset.captureId = attachment.captureId;

    const thumbWrap = document.createElement("a");
    thumbWrap.className = "attachment-thumb-wrap";
    thumbWrap.href = attachment.imageUrl;
    thumbWrap.target = "_blank";
    thumbWrap.rel = "noreferrer";

    const image = document.createElement("img");
    image.className = "attachment-thumb";
    image.src = attachment.imageUrl;
    image.alt = attachment.captureId;
    thumbWrap.appendChild(image);

    const removeButton = document.createElement("button");
    removeButton.className = "attachment-remove";
    removeButton.type = "button";
    removeButton.setAttribute("aria-label", "画像を閉じる");
    removeButton.textContent = "×";
    removeButton.addEventListener("click", () => {
      removePendingCameraAttachment(attachment.captureId);
      item.remove();
      updateSendEnabledState();
    });

    item.append(thumbWrap, removeButton);
    attachments.appendChild(item);
    pendingCameraAttachments.push(attachment);
    updateSendEnabledState();
  }

  // Block: Camera attachment build
  function buildPendingCameraAttachment(payload) {
    const captureId = payload && typeof payload.capture_id === "string" ? payload.capture_id.trim() : "";
    const imageUrl = payload && typeof payload.image_url === "string" ? payload.image_url.trim() : "";
    if (!captureId || !imageUrl) {
      throw new Error("カメラ応答が不正です");
    }
    return {
      attachmentKind: "camera_still_image",
      captureId,
      imageUrl,
    };
  }

  // Block: Camera attachment remove
  function removePendingCameraAttachment(captureId) {
    const targetCaptureId = String(captureId || "");
    const attachmentIndex = pendingCameraAttachments.findIndex((attachment) => attachment.captureId === targetCaptureId);
    if (attachmentIndex === -1) {
      return;
    }
    pendingCameraAttachments.splice(attachmentIndex, 1);
  }

  // Block: Camera attachment clear
  function clearPendingCameraAttachments() {
    pendingCameraAttachments.length = 0;
    attachments.replaceChildren();
  }

  // Block: Runtime chip update
  function updateRuntimeChip(statusPayload) {
    const runtime = statusPayload.runtime || {};
    const taskState = statusPayload.task_state || {};
    const runningText = runtime.is_running ? "ランタイム稼働中" : "ランタイム停止中";
    const activeCount = Number(taskState.active_task_count || 0);
    const waitingCount = Number(taskState.waiting_task_count || 0);
    runtimeText.textContent = `${runningText} / active:${activeCount} waiting:${waitingCount}`;
  }

  // Block: Payload parse
  function parsePayload(text) {
    try {
      return JSON.parse(text);
    } catch (_error) {
      appendError("受信データの解析に失敗しました");
      return null;
    }
  }

  // Block: Response parse
  async function readJson(response) {
    const text = await response.text();
    if (!text) {
      return {};
    }
    return JSON.parse(text);
  }

  // Block: API error message
  function readErrorMessage(payload) {
    if (payload && typeof payload === "object" && typeof payload.message === "string" && payload.message.trim()) {
      return payload.message;
    }
    throw new Error("エラー応答が不正です");
  }

  // Block: Chat send helper
  async function submitChatText(text) {
    const messageText = String(text).trim();
    const outgoingAttachments = pendingCameraAttachments.map((attachment) => ({
      attachment_kind: attachment.attachmentKind,
      capture_id: attachment.captureId,
    }));
    if (!messageText && outgoingAttachments.length === 0) {
      throw new Error("空のメッセージは送信できません");
    }
    sendButton.disabled = true;
    try {
      const requestBody = {};
      if (messageText) {
        requestBody.text = messageText;
      }
      if (outgoingAttachments.length > 0) {
        requestBody.attachments = outgoingAttachments;
      }
      const response = await fetch("/api/chat/input", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      });
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload));
      }
      const userText = buildUserMessageEchoText({
        text: messageText,
        attachmentCount: outgoingAttachments.length,
      });
      appendMessage({
        role: "user",
        text: userText,
        messageId: payload && "input_id" in payload ? String(payload.input_id) : "",
        isDraft: false,
      });
      clearPendingCameraAttachments();
    } finally {
      sendButton.disabled = false;
      updateSendEnabledState();
    }
  }

  // Block: User message echo
  function buildUserMessageEchoText({ text, attachmentCount }) {
    const normalizedText = String(text || "").trim();
    const normalizedAttachmentCount = Number(attachmentCount || 0);
    if (normalizedText && normalizedAttachmentCount > 0) {
      return `${normalizedText}\n[画像 ${normalizedAttachmentCount} 枚]`;
    }
    if (normalizedText) {
      return normalizedText;
    }
    return `[画像 ${normalizedAttachmentCount} 枚]`;
  }

  // Block: Meta label
  function buildMetaLabel(role, isDraft = false) {
    if (role === "user") {
      return "あなた";
    }
    if (isDraft) {
      return "OtomeKairo（生成中）";
    }
    return "OtomeKairo";
  }

  // Block: Notice meta label
  function buildNoticeMetaLabel(code) {
    if (code === "browse_queued") {
      return "検索タスク";
    }
    if (code === "browse_completed") {
      return "検索結果";
    }
    if (code === "settings_saved" || code === "settings_no_changes") {
      return "設定";
    }
    if (code === "cancel_requested") {
      return "停止";
    }
    if (code === "camera_captured") {
      return "カメラ";
    }
    if (code === "settings_dummy") {
      return "ダミー";
    }
    return "通知";
  }

  // Block: JSON format
  function formatJson(value) {
    return JSON.stringify(value, null, 2);
  }

  // Block: Scroll helper
  function scrollToBottom() {
    chatScroll.scrollTop = chatScroll.scrollHeight;
  }

  // Block: Effective settings access
  function requireEffectiveSettings() {
    if (!latestSettings || typeof latestSettings !== "object") {
      throw new Error("設定スナップショットが未取得です");
    }
    const effectiveSettings = latestSettings.effective_settings;
    if (!effectiveSettings || typeof effectiveSettings !== "object") {
      throw new Error("有効設定が不正です");
    }
    return effectiveSettings;
  }

  // Block: String setting read
  function readStringSetting(effectiveSettings, key) {
    if (!(key in effectiveSettings) || typeof effectiveSettings[key] !== "string") {
      throw new Error(`${key} が文字列ではありません`);
    }
    const value = effectiveSettings[key].trim();
    if (!value) {
      throw new Error(`${key} が空です`);
    }
    return value;
  }

  // Block: Number setting read
  function readNumberSetting(effectiveSettings, key) {
    if (!(key in effectiveSettings) || typeof effectiveSettings[key] !== "number" || !Number.isFinite(effectiveSettings[key])) {
      throw new Error(`${key} が数値ではありません`);
    }
    return effectiveSettings[key];
  }

  // Block: Integer setting read
  function readIntegerSetting(effectiveSettings, key) {
    const value = readNumberSetting(effectiveSettings, key);
    if (!Number.isInteger(value)) {
      throw new Error(`${key} が整数ではありません`);
    }
    return value;
  }

  // Block: Boolean setting read
  function readBooleanSetting(effectiveSettings, key) {
    if (!(key in effectiveSettings) || typeof effectiveSettings[key] !== "boolean") {
      throw new Error(`${key} が真偽値ではありません`);
    }
    return effectiveSettings[key];
  }

  // Block: Settings collect
  function collectEditableSettings() {
    const llmDefaultModel = llmDefaultModelInput.value.trim();
    if (!llmDefaultModel) {
      throw new Error("LLM モデルは必須です");
    }
    const llmTemperature = Number(llmTemperatureInput.value);
    if (!Number.isFinite(llmTemperature) || llmTemperature < 0 || llmTemperature > 2) {
      throw new Error("Temperature は 0.0 以上 2.0 以下で入力してください");
    }
    const runtimeIdleTick = Number(runtimeIdleTickInput.value);
    if (!Number.isInteger(runtimeIdleTick) || runtimeIdleTick < 250 || runtimeIdleTick > 60000) {
      throw new Error("Idle Tick は 250 以上 60000 以下の整数で入力してください");
    }
    const outputTtsVoice = outputTtsVoiceInput.value.trim();
    if (!outputTtsVoice) {
      throw new Error("TTS Voice は必須です");
    }
    return {
      "llm.default_model": llmDefaultModel,
      "llm.temperature": llmTemperature,
      "runtime.idle_tick_ms": runtimeIdleTick,
      "output.tts.enabled": outputTtsEnabledInput.checked,
      "output.tts.voice": outputTtsVoice,
      "integrations.line.enabled": integrationsLineEnabledInput.checked,
    };
  }

  // Block: Mic state
  function setMicListeningState(isListening) {
    micButton.classList.toggle("listening", isListening);
  }

  // Block: Composer resize
  function autoResizeComposer() {
    chatInput.style.height = "auto";
    chatInput.style.height = `${Math.min(chatInput.scrollHeight, 120)}px`;
  }

  // Block: Send state
  function updateSendEnabledState() {
    sendButton.disabled = chatInput.value.trim() === "" && pendingCameraAttachments.length === 0;
  }

  // Block: Browser speech
  function speakMessageText(text) {
    let effectiveSettings;
    try {
      effectiveSettings = requireEffectiveSettings();
      if (readBooleanSetting(effectiveSettings, "output.tts.enabled") !== true) {
        return;
      }
    } catch (error) {
      appendError(`TTS を開始できません: ${error.message}`);
      return;
    }
    if (!("speechSynthesis" in window) || typeof window.SpeechSynthesisUtterance !== "function") {
      appendError("このブラウザでは TTS が使えません");
      return;
    }
    const messageText = String(text || "").trim();
    if (!messageText) {
      return;
    }
    let requestedVoice;
    try {
      requestedVoice = readStringSetting(effectiveSettings, "output.tts.voice");
    } catch (error) {
      appendError(`TTS を開始できません: ${error.message}`);
      return;
    }
    const utterance = new window.SpeechSynthesisUtterance(messageText);
    if (requestedVoice !== "default") {
      const voices = window.speechSynthesis.getVoices();
      const matchedVoice = voices.find((voice) => voice.name === requestedVoice);
      if (!matchedVoice) {
        appendError(`指定された TTS voice が見つかりません: ${requestedVoice}`);
        return;
      }
      utterance.voice = matchedVoice;
    }
    stopBrowserSpeech();
    window.speechSynthesis.speak(utterance);
  }

  // Block: Browser speech stop
  function stopBrowserSpeech() {
    if (!("speechSynthesis" in window)) {
      return;
    }
    window.speechSynthesis.cancel();
  }

  // Block: Start application
  start();
})();
