(() => {
  "use strict";

  // Block: DOM references
  const chatScroll = document.getElementById("chat-scroll");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendButton = document.getElementById("btn-send");
  const cancelButton = document.getElementById("btn-cancel");
  const micButton = document.getElementById("btn-mic");
  const cameraButton = document.getElementById("btn-camera");
  const settingsButton = document.getElementById("btn-settings");
  const settingsPanel = document.getElementById("settings-panel");
  const settingsReloadButton = document.getElementById("btn-settings-reload");
  const settingsSaveButton = document.getElementById("btn-settings-save");
  const settingsCloseButton = document.getElementById("btn-settings-close");
  const settingsJson = document.getElementById("settings-json");
  const statusJson = document.getElementById("status-json");
  const connectionText = document.getElementById("connection-text");
  const runtimeText = document.getElementById("runtime-text");

  // Block: Runtime state
  let stream = null;
  const draftMessages = new Map();
  let statusTimerId = 0;
  let latestSettings = null;

  // Block: Startup
  function start() {
    installEventHandlers();
    connectStream();
    void refreshSnapshots();
    statusTimerId = window.setInterval(() => {
      void refreshSnapshots();
    }, 5000);
  }

  // Block: Event handlers
  function installEventHandlers() {
    chatForm.addEventListener("submit", handleChatSubmit);
    cancelButton.addEventListener("click", handleCancel);
    settingsButton.addEventListener("click", () => {
      void openSettingsPanel();
    });
    settingsCloseButton.addEventListener("click", closeSettingsPanel);
    settingsReloadButton.addEventListener("click", () => {
      void refreshSnapshots();
    });
    settingsSaveButton.addEventListener("click", () => {
      appendNotice("settings_dummy", "設定保存 UI はまだダミーです");
    });
    micButton.addEventListener("click", () => {
      appendNotice("mic_dummy", "音声入力 UI はまだダミーです");
    });
    cameraButton.addEventListener("click", () => {
      appendNotice("camera_dummy", "カメラ UI はまだダミーです");
    });
    window.addEventListener("beforeunload", stopStream);
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
    if (!text) {
      return;
    }
    sendButton.disabled = true;
    try {
      const response = await fetch("/api/chat/input", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ text }),
      });
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(payload.message || "送信に失敗しました");
      }
      appendMessage({
        role: "user",
        text,
        messageId: String(payload.input_id || ""),
        isDraft: false,
      });
      chatInput.value = "";
      chatInput.focus();
    } catch (error) {
      appendError(`送信に失敗しました: ${error.message}`);
    } finally {
      sendButton.disabled = false;
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
        throw new Error(payload.message || "停止に失敗しました");
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
    settingsPanel.classList.remove("hidden");
    await refreshSnapshots();
  }

  // Block: Settings close
  function closeSettingsPanel() {
    settingsPanel.classList.add("hidden");
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
        throw new Error(statusPayload.message || "状態取得に失敗しました");
      }
      if (!settingsResponse.ok) {
        throw new Error(settingsPayload.message || "設定取得に失敗しました");
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
      bubble.classList.remove("is-draft");
      const meta = messageNode.querySelector(".meta");
      meta.textContent = buildMetaLabel(String(payload.role || "assistant"));
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
    const row = document.createElement("div");
    row.className = `message-row ${role}`;
    if (messageId) {
      row.dataset.messageId = messageId;
    }

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (isDraft) {
      bubble.classList.add("is-draft");
    }
    bubble.textContent = text;

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = buildMetaLabel(role, isDraft);

    row.append(meta, bubble);
    chatScroll.appendChild(row);
    scrollToBottom();
    return row;
  }

  // Block: Notice append
  function appendNotice(code, text) {
    const row = document.createElement("div");
    row.className = "message-row notice";
    row.dataset.noticeCode = String(code);

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;

    row.appendChild(bubble);
    chatScroll.appendChild(row);
    scrollToBottom();
  }

  // Block: Error append
  function appendError(text) {
    const row = document.createElement("div");
    row.className = "message-row error";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;

    row.appendChild(bubble);
    chatScroll.appendChild(row);
    scrollToBottom();
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

  // Block: JSON format
  function formatJson(value) {
    return JSON.stringify(value, null, 2);
  }

  // Block: Scroll helper
  function scrollToBottom() {
    chatScroll.scrollTop = chatScroll.scrollHeight;
  }

  // Block: Browser speech
  function speakMessageText(text) {
    const effectiveSettings = latestSettings && typeof latestSettings === "object"
      ? latestSettings.effective_settings
      : null;
    if (!effectiveSettings || effectiveSettings["output.tts.enabled"] !== true) {
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
    const requestedVoice = String(effectiveSettings["output.tts.voice"] || "").trim();
    if (!requestedVoice) {
      appendError("TTS voice 設定が不正です");
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
