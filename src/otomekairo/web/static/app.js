(() => {
  "use strict";

  // Block: DOM references
  const chatScroll = document.getElementById("chat-scroll");
  const chatPanel = document.getElementById("chat-panel");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendButton = document.getElementById("btn-send");
  const cancelButton = document.getElementById("btn-cancel");
  const cameraButton = document.getElementById("btn-camera");
  const attachments = document.getElementById("attachments");
  const settingsButton = document.getElementById("btn-settings");
  const settingsPanel = document.getElementById("settings-panel");
  const settingsOkButton = document.getElementById("btn-settings-ok");
  const settingsCancelButton = document.getElementById("btn-settings-cancel");
  const settingsApplyButton = document.getElementById("btn-settings-apply");
  const settingsCharacterCard = document.getElementById("settings-character-card");
  const settingsBehaviorCard = document.getElementById("settings-behavior-card");
  const settingsConversationCard = document.getElementById("settings-conversation-card");
  const settingsMemoryCard = document.getElementById("settings-memory-card");
  const settingsMotionCard = document.getElementById("settings-motion-card");
  const settingsSystemCard = document.getElementById("settings-system-card");
  const settingsCameraCard = document.getElementById("settings-camera-card");
  const settingsStatus = document.getElementById("settings-status");
  const settingsTabButtons = Array.from(document.querySelectorAll("[data-settings-tab]"));
  const settingsPages = Array.from(document.querySelectorAll("[data-settings-page]"));
  const connectionText = document.getElementById("connection-text");
  const runtimeText = document.getElementById("runtime-text");
  const runtimeSummaryText = document.getElementById("runtime-summary-text");
  const retrievalSummaryText = document.getElementById("retrieval-summary-text");
  const personaUpdateSummaryText = document.getElementById("persona-update-summary-text");

  // Block: Settings schema
  const SETTINGS_TAB_KEYS = ["character", "behavior", "conversation", "memory", "motion", "system"];
  const PRESET_COLLECTION_CONFIG = {
    character: {
      listKey: "character_presets",
      activeKey: "active_character_preset_id",
      idPrefix: "preset_character",
      baseName: "新規キャラクター",
    },
    behavior: {
      listKey: "behavior_presets",
      activeKey: "active_behavior_preset_id",
      idPrefix: "preset_behavior",
      baseName: "振る舞い",
    },
    conversation: {
      listKey: "conversation_presets",
      activeKey: "active_conversation_preset_id",
      idPrefix: "preset_conversation",
      baseName: "LLM",
    },
    memory: {
      listKey: "memory_presets",
      activeKey: "active_memory_preset_id",
      idPrefix: "preset_memory",
      baseName: "記憶",
    },
    motion: {
      listKey: "motion_presets",
      activeKey: "active_motion_preset_id",
      idPrefix: "preset_motion",
      baseName: "モーション",
    },
  };
  const TTS_PROVIDER_LABELS = {
    "aivis-cloud": "Aivis Cloud API",
    voicevox: "VOICEVOX/SHAREVOX/AivisSpeech",
    "style-bert-vits2": "Style-Bert-VITS2",
  };
  const STT_PROVIDER_LABELS = {
    amivoice: "AmiVoice",
  };
  const TTS_ADVANCED_SECTION_TITLES = {
    "aivis-cloud": "Aivis Cloud API詳細設定",
    voicevox: "VOICEVOX詳細設定",
    "style-bert-vits2": "Style-Bert-VITS2 詳細設定",
  };
  const BEHAVIOR_OPTION_LABELS = {
    "behavior.response_pace": {
      careful: "慎重",
      balanced: "標準",
      quick: "迅速",
    },
    "behavior.proactivity_level": {
      low: "低い",
      medium: "標準",
      high: "高い",
    },
    "behavior.browse_preference": {
      avoid: "控えめ",
      balanced: "標準",
      prefer: "積極的",
    },
    "behavior.notify_preference": {
      quiet: "静かめ",
      balanced: "標準",
      proactive: "積極的",
    },
    "behavior.speech_style": {
      gentle: "やわらかめ",
      neutral: "標準",
      firm: "はっきり",
    },
    "behavior.verbosity_bias": {
      short: "短め",
      balanced: "標準",
      detailed: "詳しめ",
    },
  };
  const BEHAVIOR_PROMPT_DESCRIPTORS = [
    { path: "behavior.second_person_label", label: "ユーザーの呼び方", kind: "text" },
    { path: "behavior.system_prompt", label: "振る舞い本文", kind: "textarea", rows: 12 },
  ];
  const BEHAVIOR_ADDON_DESCRIPTORS = [
    { path: "behavior.addon_prompt", label: "追加プロンプト", kind: "textarea", rows: 6 },
  ];
  const BEHAVIOR_DESCRIPTORS = [
    {
      path: "behavior.response_pace",
      label: "応答ペース",
      kind: "select",
      options: ["careful", "balanced", "quick"],
      optionLabels: BEHAVIOR_OPTION_LABELS["behavior.response_pace"],
    },
    {
      path: "behavior.proactivity_level",
      label: "自発性",
      kind: "select",
      options: ["low", "medium", "high"],
      optionLabels: BEHAVIOR_OPTION_LABELS["behavior.proactivity_level"],
    },
    {
      path: "behavior.browse_preference",
      label: "検索傾向",
      kind: "select",
      options: ["avoid", "balanced", "prefer"],
      optionLabels: BEHAVIOR_OPTION_LABELS["behavior.browse_preference"],
    },
    {
      path: "behavior.notify_preference",
      label: "通知傾向",
      kind: "select",
      options: ["quiet", "balanced", "proactive"],
      optionLabels: BEHAVIOR_OPTION_LABELS["behavior.notify_preference"],
    },
    {
      path: "behavior.speech_style",
      label: "話し方",
      kind: "select",
      options: ["gentle", "neutral", "firm"],
      optionLabels: BEHAVIOR_OPTION_LABELS["behavior.speech_style"],
    },
    {
      path: "behavior.verbosity_bias",
      label: "詳細さ",
      kind: "select",
      options: ["short", "balanced", "detailed"],
      optionLabels: BEHAVIOR_OPTION_LABELS["behavior.verbosity_bias"],
    },
  ];
  const CHARACTER_MATERIAL_DESCRIPTORS = [
    {
      path: "character.material.convert_unlit_to_mtoon",
      label: "UnlitをMToonに変換する（正常に表示されないときに有効にしてみてください）",
      kind: "boolean",
    },
    {
      path: "character.material.enable_shadow_off",
      label: "指定したメッシュに影を落とさない（顔などを想定）",
      kind: "boolean",
    },
    {
      path: "character.material.shadow_off_meshes",
      label: "メッシュ名",
      kind: "text",
      disabledWhenPath: "character.material.enable_shadow_off",
      disabledWhenValue: false,
    },
  ];
  const CHARACTER_TTS_COMMON_DESCRIPTORS = [
    { path: "speech.tts.enabled", label: "TTSを使用する", kind: "boolean" },
    {
      path: "speech.tts.provider",
      label: "エンジン",
      kind: "select",
      options: ["aivis-cloud", "voicevox", "style-bert-vits2"],
      optionLabels: TTS_PROVIDER_LABELS,
    },
  ];
  const CHARACTER_TTS_PROVIDER_DESCRIPTORS = {
    "aivis-cloud": [
      { path: "speech.tts.aivis_cloud.api_key", label: "API Key", kind: "password", clipboardActions: true },
      { path: "speech.tts.aivis_cloud.endpoint_url", label: "Endpoint URL", kind: "text" },
      { path: "speech.tts.aivis_cloud.model_uuid", label: "Model UUID", kind: "text" },
      { path: "speech.tts.aivis_cloud.speaker_uuid", label: "Speaker UUID", kind: "text" },
      { path: "speech.tts.aivis_cloud.style_id", label: "Style ID", kind: "integer", min: 0, max: 999999, step: 1 },
      { path: "speech.tts.aivis_cloud.use_ssml", label: "SSML を使う", kind: "boolean" },
      { path: "speech.tts.aivis_cloud.language", label: "言語", kind: "text" },
      { path: "speech.tts.aivis_cloud.speaking_rate", label: "Speaking Rate", kind: "number", min: 0.25, max: 4.0, step: 0.05 },
      { path: "speech.tts.aivis_cloud.emotional_intensity", label: "Emotional Intensity", kind: "number", min: 0.0, max: 2.0, step: 0.05 },
      { path: "speech.tts.aivis_cloud.tempo_dynamics", label: "Tempo Dynamics", kind: "number", min: 0.0, max: 2.0, step: 0.05 },
      { path: "speech.tts.aivis_cloud.pitch", label: "Pitch", kind: "number", min: -1.0, max: 1.0, step: 0.05 },
      { path: "speech.tts.aivis_cloud.volume", label: "Volume", kind: "number", min: 0.0, max: 2.0, step: 0.05 },
      { path: "speech.tts.aivis_cloud.output_format", label: "音声フォーマット", kind: "select", options: ["wav", "mp3", "ogg", "aac", "flac"] },
    ],
    voicevox: [
      { path: "speech.tts.voicevox.endpoint_url", label: "Endpoint URL", kind: "text" },
      { path: "speech.tts.voicevox.speaker_id", label: "Speaker ID", kind: "integer", min: 0, max: 999999, step: 1 },
      { path: "speech.tts.voicevox.speed_scale", label: "話速", kind: "number", min: 0.5, max: 2.0, step: 0.05 },
      { path: "speech.tts.voicevox.pitch_scale", label: "音高", kind: "number", min: -0.15, max: 0.15, step: 0.01 },
      { path: "speech.tts.voicevox.intonation_scale", label: "抑揚", kind: "number", min: 0.0, max: 2.0, step: 0.05 },
      { path: "speech.tts.voicevox.volume_scale", label: "音量", kind: "number", min: 0.0, max: 2.0, step: 0.05 },
      { path: "speech.tts.voicevox.pre_phoneme_length", label: "発話前無音", kind: "number", min: 0.0, max: 1.5, step: 0.05 },
      { path: "speech.tts.voicevox.post_phoneme_length", label: "発話後無音", kind: "number", min: 0.0, max: 1.5, step: 0.05 },
      { path: "speech.tts.voicevox.output_sampling_rate", label: "サンプリングレート", kind: "integer", min: 8000, max: 48000, step: 1000 },
      { path: "speech.tts.voicevox.output_stereo", label: "ステレオ出力", kind: "boolean" },
    ],
    "style-bert-vits2": [
      { path: "speech.tts.style_bert_vits2.endpoint_url", label: "Endpoint URL", kind: "text" },
      { path: "speech.tts.style_bert_vits2.model_name", label: "Model Name", kind: "text" },
      { path: "speech.tts.style_bert_vits2.model_id", label: "Model ID", kind: "integer", min: 0, max: 999999, step: 1 },
      { path: "speech.tts.style_bert_vits2.speaker_name", label: "Speaker Name", kind: "text" },
      { path: "speech.tts.style_bert_vits2.speaker_id", label: "Speaker ID", kind: "integer", min: 0, max: 999999, step: 1 },
      { path: "speech.tts.style_bert_vits2.style", label: "Style", kind: "text" },
      { path: "speech.tts.style_bert_vits2.style_weight", label: "Style Weight", kind: "number", min: 0.0, max: 10.0, step: 0.05 },
      { path: "speech.tts.style_bert_vits2.sdp_ratio", label: "SDP Ratio", kind: "number", min: 0.0, max: 1.0, step: 0.05 },
      { path: "speech.tts.style_bert_vits2.noise", label: "Noise", kind: "number", min: 0.0, max: 10.0, step: 0.05 },
      { path: "speech.tts.style_bert_vits2.noise_w", label: "Noise W", kind: "number", min: 0.0, max: 10.0, step: 0.05 },
      { path: "speech.tts.style_bert_vits2.length", label: "Length", kind: "number", min: 0.25, max: 4.0, step: 0.05 },
      { path: "speech.tts.style_bert_vits2.language", label: "言語", kind: "select", options: ["JP", "EN", "ZH"] },
      { path: "speech.tts.style_bert_vits2.auto_split", label: "自動分割", kind: "boolean" },
      { path: "speech.tts.style_bert_vits2.split_interval", label: "分割間隔", kind: "number", min: 0.0, max: 30.0, step: 0.1 },
      { path: "speech.tts.style_bert_vits2.assist_text", label: "Assist Text", kind: "text" },
      { path: "speech.tts.style_bert_vits2.assist_text_weight", label: "Assist Weight", kind: "number", min: 0.0, max: 10.0, step: 0.05 },
    ],
  };
  const CHARACTER_STT_DESCRIPTORS = [
    { path: "speech.stt.enabled", label: "STTを使用する", kind: "boolean" },
    {
      path: "speech.stt.provider",
      label: "エンジン",
      kind: "select",
      options: ["amivoice"],
      optionLabels: STT_PROVIDER_LABELS,
    },
    { path: "speech.stt.wake_word", label: "Wake Word", kind: "text" },
    { path: "speech.stt.language", label: "言語", kind: "text" },
    { path: "speech.stt.amivoice.profile_id", label: "Profile ID", kind: "text" },
    { path: "speech.stt.amivoice.api_key", label: "API Key", kind: "password", clipboardActions: true },
  ];
  const CONVERSATION_LLM_DESCRIPTORS = [
    { path: "llm.model", label: "LLM モデル (provider/model)", kind: "text" },
    { path: "llm.api_key", label: "LLM API キー", kind: "password", clipboardActions: true },
    { path: "llm.base_url", label: "LLM Base URL（任意）", kind: "text" },
    { path: "llm.temperature", label: "Temperature", kind: "number", min: 0, max: 2, step: 0.1 },
    { path: "llm.max_output_tokens", label: "最大出力トークン", kind: "integer", min: 256, max: 8192, step: 1 },
    {
      path: "llm.reasoning_effort",
      label: "Reasoning Effort",
      kind: "select",
      options: ["", "low", "medium", "high"],
      optionLabels: {
        "": "未指定",
        low: "low",
        medium: "medium",
        high: "high",
      },
    },
    { path: "llm.reply_web_search_enabled", label: "最終応答でWeb検索を許可する", kind: "boolean" },
    { path: "llm.max_turns_window", label: "会話履歴ターン数", kind: "integer", min: 1, max: 200, step: 1 },
  ];
  const CONVERSATION_VISION_DESCRIPTORS = [
    { path: "llm.image_model", label: "画像LLMモデル", kind: "text" },
    { path: "llm.image_api_key", label: "画像LLM API キー", kind: "password", clipboardActions: true },
    { path: "llm.image_base_url", label: "画像LLM Base URL（任意）", kind: "text" },
    { path: "llm.max_output_tokens_vision", label: "画像最大出力トークン", kind: "integer", min: 256, max: 8192, step: 1 },
    { path: "llm.image_timeout_seconds", label: "画像タイムアウト(秒)", kind: "integer", min: 1, max: 600, step: 1 },
  ];
  const MEMORY_EMBEDDING_DESCRIPTORS = [
    { path: "llm.embedding_model", label: "埋め込みモデル (provider/model)", kind: "text" },
    { path: "llm.embedding_api_key", label: "埋め込み API キー", kind: "password", clipboardActions: true },
    { path: "llm.embedding_base_url", label: "埋め込み Base URL（任意）", kind: "text" },
    { path: "memory.embedding_dimension", label: "Embedding 次元数", kind: "integer", min: 1, max: 8192, step: 1 },
  ];
  const MEMORY_RETRIEVAL_DESCRIPTORS = [
    { path: "memory.similar_episodes_limit", label: "類似エピソード上限", kind: "integer", min: 1, max: 512, step: 1 },
    { path: "memory.max_inject_tokens", label: "最大注入トークン", kind: "integer", min: 256, max: 32768, step: 1 },
    { path: "runtime.context_budget_tokens", label: "文脈上限", kind: "integer", min: 1024, max: 32768, step: 1 },
    { path: "retrieval_profile.semantic_top_k", label: "Semantic Top K", kind: "integer", min: 1, max: 64, step: 1 },
    { path: "retrieval_profile.recent_window_limit", label: "Recent Window", kind: "integer", min: 1, max: 20, step: 1 },
    { path: "retrieval_profile.fact_bias", label: "Fact Bias", kind: "number", min: 0, max: 1, step: 0.05 },
    { path: "retrieval_profile.summary_bias", label: "Summary Bias", kind: "number", min: 0, max: 1, step: 0.05 },
    { path: "retrieval_profile.event_bias", label: "Event Bias", kind: "number", min: 0, max: 1, step: 0.05 },
  ];
  const SYSTEM_NOTIFY_DESCRIPTORS = [
    {
      key: "integrations.notify_route",
      label: "通知経路",
      kind: "select",
      options: ["ui_only", "discord"],
      optionLabels: {
        ui_only: "UI only",
        discord: "Discord",
      },
    },
    { key: "integrations.discord.bot_token", label: "Discord トークン", kind: "password", clipboardActions: true },
    { key: "integrations.discord.channel_id", label: "Discord チャンネル", kind: "text" },
  ];
  const CHARACTER_TTS_PROVIDER_BASIC_PATHS = {
    "aivis-cloud": [
      "speech.tts.aivis_cloud.api_key",
      "speech.tts.aivis_cloud.endpoint_url",
      "speech.tts.aivis_cloud.model_uuid",
      "speech.tts.aivis_cloud.speaker_uuid",
      "speech.tts.aivis_cloud.style_id",
    ],
    voicevox: [
      "speech.tts.voicevox.endpoint_url",
      "speech.tts.voicevox.speaker_id",
    ],
    "style-bert-vits2": [
      "speech.tts.style_bert_vits2.endpoint_url",
      "speech.tts.style_bert_vits2.model_name",
      "speech.tts.style_bert_vits2.model_id",
      "speech.tts.style_bert_vits2.speaker_name",
      "speech.tts.style_bert_vits2.speaker_id",
    ],
  };
  const CHARACTER_TTS_PROVIDER_ADVANCED_PATHS = {
    "aivis-cloud": [
      "speech.tts.aivis_cloud.use_ssml",
      "speech.tts.aivis_cloud.language",
      "speech.tts.aivis_cloud.speaking_rate",
      "speech.tts.aivis_cloud.emotional_intensity",
      "speech.tts.aivis_cloud.tempo_dynamics",
      "speech.tts.aivis_cloud.pitch",
      "speech.tts.aivis_cloud.volume",
      "speech.tts.aivis_cloud.output_format",
    ],
    voicevox: [
      "speech.tts.voicevox.speed_scale",
      "speech.tts.voicevox.pitch_scale",
      "speech.tts.voicevox.intonation_scale",
      "speech.tts.voicevox.volume_scale",
      "speech.tts.voicevox.pre_phoneme_length",
      "speech.tts.voicevox.post_phoneme_length",
      "speech.tts.voicevox.output_sampling_rate",
      "speech.tts.voicevox.output_stereo",
    ],
    "style-bert-vits2": [
      "speech.tts.style_bert_vits2.style",
      "speech.tts.style_bert_vits2.style_weight",
      "speech.tts.style_bert_vits2.language",
      "speech.tts.style_bert_vits2.sdp_ratio",
      "speech.tts.style_bert_vits2.noise",
      "speech.tts.style_bert_vits2.noise_w",
      "speech.tts.style_bert_vits2.length",
      "speech.tts.style_bert_vits2.auto_split",
      "speech.tts.style_bert_vits2.split_interval",
      "speech.tts.style_bert_vits2.assist_text",
      "speech.tts.style_bert_vits2.assist_text_weight",
    ],
  };
  const SYSTEM_RUNTIME_DESCRIPTORS = [
    { key: "runtime.idle_tick_ms", label: "Idle Tick (ms)", kind: "integer", min: 250, max: 60000, step: 250 },
    { key: "runtime.long_cycle_min_interval_ms", label: "Long Cycle (ms)", kind: "integer", min: 1000, max: 300000, step: 1000 },
    { key: "sensors.microphone.enabled", label: "マイク入力", kind: "boolean" },
    { key: "sensors.camera.enabled", label: "カメラ入力", kind: "boolean" },
    { key: "integrations.sns.enabled", label: "SNS 連携", kind: "boolean" },
  ];
  const MOTION_ANIMATION_TYPE_LABELS = {
    0: "Standing",
    1: "SittingFloor",
    2: "LyingDown",
  };
  const CAMERA_FIELD_KEYS = ["is_enabled", "display_name", "host", "username", "password"];

  // Block: Runtime state
  let stream = null;
  const pendingCameraAttachments = [];
  let statusTimerId = 0;
  let latestEditorSnapshot = null;
  let editorDraft = null;
  let activeSettingsTab = "character";
  let localDraftIdCounter = 0;
  const draftMessages = new Map();
  let activeSpeechAudio = null;

  // Block: Application startup
  async function start() {
    installEventHandlers();
    updateSendEnabledState();
    connectStream();
    await refreshStatusSnapshot();
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
    settingsOkButton.addEventListener("click", () => {
      void handleSettingsOk();
    });
    settingsCancelButton.addEventListener("click", handleSettingsCancel);
    settingsApplyButton.addEventListener("click", () => {
      void handleSettingsApply();
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
    cameraButton.addEventListener("click", () => {
      void handleCameraCapture();
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

  async function handleCancel() {
    cancelButton.disabled = true;
    try {
      stopActiveSpeechAudio();
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
    const normalizedText = String(text).trim();
    const outgoingAttachments = pendingCameraAttachments.map((attachment) => ({
      attachment_kind: attachment.attachmentKind,
      capture_id: attachment.captureId,
    }));
    if (!normalizedText && outgoingAttachments.length === 0) {
      throw new Error("空のメッセージは送信できません");
    }
    const response = await fetch("/api/chat/input", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ...(normalizedText ? { text: normalizedText } : {}),
        ...(outgoingAttachments.length > 0 ? { attachments: outgoingAttachments } : {}),
      }),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      throw new Error(readErrorMessage(payload));
    }
    appendMessage({
      role: "user",
      text: buildUserMessageEchoText({
        text: normalizedText,
        attachmentCount: outgoingAttachments.length,
      }),
      messageId: requireString(payload.input_id, "chat.input_id"),
      isDraft: false,
    });
    clearPendingCameraAttachments();
  }

  // Block: Settings panel actions
  async function openSettingsPanel() {
    chatPanel.classList.add("hidden");
    chatForm.classList.add("hidden");
    settingsPanel.classList.remove("hidden");
    await loadSettingsEditorSnapshot();
  }

  function closeSettingsPanel() {
    settingsPanel.classList.add("hidden");
    chatPanel.classList.remove("hidden");
    chatForm.classList.remove("hidden");
  }

  // Block: Settings save helpers
  async function handleSettingsOk() {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    if (isSettingsDraftDirty() !== true) {
      closeSettingsPanel();
      return;
    }
    await saveSettingsEditor(true);
  }

  function handleSettingsCancel() {
    discardSettingsDraft();
    closeSettingsPanel();
  }

  async function handleSettingsApply() {
    await saveSettingsEditor(false);
  }

  async function saveSettingsEditor(closeAfterSave) {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    settingsOkButton.disabled = true;
    settingsCancelButton.disabled = true;
    settingsApplyButton.disabled = true;
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
      if (closeAfterSave === true) {
        closeSettingsPanel();
      }
    } catch (error) {
      settingsStatus.textContent = `保存失敗: ${error.message}`;
    } finally {
      settingsOkButton.disabled = false;
      settingsCancelButton.disabled = false;
      settingsApplyButton.disabled = false;
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

  // Block: Snapshot loading
  async function refreshStatusSnapshot() {
    try {
      const response = await fetch("/api/status");
      const payload = await readJson(response);
      if (!response.ok) {
        throw new Error(readErrorMessage(payload));
      }
      updateRuntimeChip(payload);
    } catch (error) {
      runtimeText.textContent = `状態取得に失敗しました: ${error.message}`;
      runtimeSummaryText.textContent = "状態取得に失敗しました";
      runtimeSummaryText.title = "";
      retrievalSummaryText.textContent = "状態未取得";
      retrievalSummaryText.title = "";
      personaUpdateSummaryText.textContent = "状態未取得";
      personaUpdateSummaryText.title = "";
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
    editorDraft = buildEditorDraft(snapshot);
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
    if (!Array.isArray(snapshot.character_presets)) {
      throw new Error("character_presets が不正です");
    }
    if (!Array.isArray(snapshot.behavior_presets)) {
      throw new Error("behavior_presets が不正です");
    }
    if (!Array.isArray(snapshot.conversation_presets)) {
      throw new Error("conversation_presets が不正です");
    }
    if (!Array.isArray(snapshot.memory_presets)) {
      throw new Error("memory_presets が不正です");
    }
    if (!Array.isArray(snapshot.motion_presets)) {
      throw new Error("motion_presets が不正です");
    }
    if (!Array.isArray(snapshot.camera_connections)) {
      throw new Error("camera_connections が不正です");
    }
    if (!isObject(snapshot.runtime_projection)) {
      throw new Error("runtime_projection が不正です");
    }
  }

  function buildEditorDraft(snapshot) {
    return {
      editor_state: cloneJson(snapshot.editor_state),
      character_presets: cloneJson(snapshot.character_presets),
      behavior_presets: cloneJson(snapshot.behavior_presets),
      conversation_presets: cloneJson(snapshot.conversation_presets),
      memory_presets: cloneJson(snapshot.memory_presets),
      motion_presets: cloneJson(snapshot.motion_presets),
      camera_connections: cloneJson(snapshot.camera_connections),
    };
  }

  // Block: Settings editor rendering
  function renderSettingsEditor() {
    if (editorDraft === null || latestEditorSnapshot === null) {
      return;
    }
    renderCharacterPresetCard();
    renderBehaviorPresetCard();
    renderConversationPresetCard();
    renderMemoryPresetCard();
    renderMotionPresetCard();
    renderSystemValuesCard();
    renderCameraConnectionsCard();
    applySettingsTabState();
    attachSettingsEditorHandlers();
    updateSettingsDirtyState();
  }

  // Block: Behavior preset rendering
  function renderBehaviorPresetCard() {
    const activePresetId = readActivePresetId("behavior");
    const activePreset = requirePresetEntry("behavior", activePresetId);
    const selectOptions = buildPresetSelectOptions(readPresetEntries("behavior"), activePresetId);
    settingsBehaviorCard.innerHTML = `
      <div class="settings-card-title">振る舞い設定</div>
      <div class="settings-stack">
        ${renderSettingsGroup(
      "振る舞いプロンプト",
      "呼び方と振る舞い指示をここで管理します。",
      [
        renderPresetSelectionFields("behavior", activePreset, selectOptions),
        BEHAVIOR_PROMPT_DESCRIPTORS
          .map((descriptor) => renderPresetField("behavior", activePreset.payload, descriptor))
          .join(""),
      ].join(""),
    )}
        ${renderSettingsGroup(
      "追加プロンプト（任意）",
      "VRMの表情指定や会話の文字数制限などを入れてください。記憶更新処理の人格には使用しません。",
      BEHAVIOR_ADDON_DESCRIPTORS
        .map((descriptor) => renderPresetField("behavior", activePreset.payload, descriptor))
        .join(""),
    )}
        ${renderSettingsGroup(
      "行動傾向",
      "OtomeKairo 独自の傾向設定です。会話方針と認知判断に反映します。",
      BEHAVIOR_DESCRIPTORS
        .map((descriptor) => renderPresetField("behavior", activePreset.payload, descriptor))
        .join(""),
    )}
      </div>
    `;
  }

  // Block: Conversation preset rendering
  function renderConversationPresetCard() {
    const activePresetId = readActivePresetId("conversation");
    const activePreset = requirePresetEntry("conversation", activePresetId);
    const selectOptions = buildPresetSelectOptions(readPresetEntries("conversation"), activePresetId);
    settingsConversationCard.innerHTML = `
      <div class="settings-card-title">LLM設定</div>
      <div class="settings-stack">
        ${renderSettingsGroup(
      "プリセット選択",
      "システムプロンプトは「振る舞い」タブで設定します。",
      renderPresetSelectionToolbar("conversation", selectOptions),
    )}
        ${renderSettingsGroup(
      "基本設定",
      "",
      renderPresetNameField("conversation", activePreset.preset_name),
    )}
        ${renderSettingsGroup(
      "LLM設定",
      "",
      CONVERSATION_LLM_DESCRIPTORS
        .map((descriptor) => renderPresetField("conversation", activePreset.payload, descriptor))
        .join(""),
    )}
        ${renderSettingsGroup(
      "画像認識LLM設定",
      "",
      CONVERSATION_VISION_DESCRIPTORS
        .map((descriptor) => renderPresetField("conversation", activePreset.payload, descriptor))
        .join(""),
    )}
      </div>
    `;
  }

  // Block: Memory preset rendering
  function renderMemoryPresetCard() {
    const activePresetId = readActivePresetId("memory");
    const activePreset = requirePresetEntry("memory", activePresetId);
    const selectOptions = buildPresetSelectOptions(readPresetEntries("memory"), activePresetId);
    settingsMemoryCard.innerHTML = `
      <div class="settings-card-title">記憶設定</div>
      <div class="settings-stack">
        ${renderSettingsGroup(
      "プリセット選択",
      "",
      renderPresetSelectionToolbar("memory", selectOptions),
    )}
        ${renderSettingsGroup(
      "Embedding設定",
      "※ 記憶検索と文脈組み立てに使う設定です。",
      [
        renderPresetNameField("memory", activePreset.preset_name),
        MEMORY_EMBEDDING_DESCRIPTORS
          .map((descriptor) => renderPresetField("memory", activePreset.payload, descriptor))
          .join(""),
      ].join(""),
    )}
        ${renderSettingsGroup(
      "記憶検索設定",
      "",
      MEMORY_RETRIEVAL_DESCRIPTORS
        .map((descriptor) => renderPresetField("memory", activePreset.payload, descriptor))
        .join(""),
    )}
      </div>
    `;
  }

  // Block: Motion preset rendering
  function renderMotionPresetCard() {
    const activePresetId = readActivePresetId("motion");
    const activePreset = requirePresetEntry("motion", activePresetId);
    const selectOptions = buildPresetSelectOptions(readPresetEntries("motion"), activePresetId);
    const animations = Array.isArray(activePreset.payload.animations)
      ? activePreset.payload.animations
      : [];
    settingsMotionCard.innerHTML = `
      <div class="settings-card-title">モーション設定</div>
      <div class="settings-stack">
        ${renderSettingsGroup(
      "アニメーションセット選択",
      "",
      renderPresetSelectionToolbar("motion", selectOptions),
    )}
        ${renderSettingsGroup(
      "基本設定",
      "",
      [
        renderPresetNameField("motion", activePreset.preset_name),
        renderPresetField("motion", activePreset.payload, {
          path: "motion.posture_change_loop_count_standing",
          label: "立ち姿勢ループ回数",
          kind: "integer",
          min: 1,
          max: 9999,
          step: 1,
        }),
        renderPresetField("motion", activePreset.payload, {
          path: "motion.posture_change_loop_count_sitting_floor",
          label: "座り姿勢ループ回数",
          kind: "integer",
          min: 1,
          max: 9999,
          step: 1,
        }),
      ].join(""),
    )}
        ${renderSettingsGroup(
      "アニメーションリスト",
      "",
      renderMotionAnimationEditor(animations),
    )}
      </div>
    `;
  }

  function renderMotionAnimationEditor(animations) {
    const rowsHtml = animations.map((animation, index) => `
      <tr>
        <td><input class="settings-input" type="text" value="${escapeHtml(requireString(animation.display_name, "motion.display_name"))}" data-motion-index="${String(index)}" data-motion-field="display_name" /></td>
        <td>
          <select class="settings-input" data-motion-index="${String(index)}" data-motion-field="animation_type" data-value-kind="integer">
            ${Object.entries(MOTION_ANIMATION_TYPE_LABELS)
        .map(([value, label]) => `<option value="${escapeHtml(value)}"${Number(animation.animation_type) === Number(value) ? " selected" : ""}>${escapeHtml(label)}</option>`)
        .join("")}
          </select>
        </td>
        <td><input class="settings-input" type="text" value="${escapeHtml(requireString(animation.animation_name, "motion.animation_name"))}" data-motion-index="${String(index)}" data-motion-field="animation_name" /></td>
        <td><input type="checkbox" ${animation.is_enabled === true ? "checked" : ""} data-motion-index="${String(index)}" data-motion-field="is_enabled" data-value-kind="boolean" /></td>
        <td><button class="settings-btn settings-btn-small danger" type="button" data-motion-action="remove" data-motion-index="${String(index)}">削除</button></td>
      </tr>
    `).join("");
    return `
      <div class="settings-table-wrap">
        <table class="settings-table">
          <thead>
            <tr>
              <th>表示名</th>
              <th>種別</th>
              <th>アニメーション名</th>
              <th>有効</th>
              <th></th>
            </tr>
          </thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
      <div class="settings-actions">
        <button class="settings-btn settings-btn-small" type="button" data-motion-action="add">追加</button>
      </div>
    `;
  }

  // Block: Character preset rendering
  function renderCharacterPresetCard() {
    const activePresetId = readActivePresetId("character");
    const activePreset = requirePresetEntry("character", activePresetId);
    const selectOptions = buildPresetSelectOptions(readPresetEntries("character"), activePresetId);
    settingsCharacterCard.innerHTML = `
      <div class="settings-card-title">キャラクター設定</div>
      <div class="settings-stack">
        ${renderSettingsGroup(
      "キャラクター選択",
      "LLM(AI)を使う場合は振る舞い / 会話 / 記憶タブも設定してください。",
      renderPresetSelectionToolbar("character", selectOptions),
    )}
        ${renderSettingsGroup(
      "基本設定",
      "",
      [
        renderPresetNameField("character", activePreset.preset_name),
        renderVrmFileField("character", activePreset.payload),
        renderIndentedNote("未指定の場合はVRM表示/音声出力が無効になります。"),
      ].join(""),
    )}
        ${renderSettingsGroup(
      "マテリアル・影設定",
      "",
      CHARACTER_MATERIAL_DESCRIPTORS
        .map((descriptor) => renderPresetField("character", activePreset.payload, descriptor))
        .join(""),
    )}
        ${renderCharacterSpeechGroup(activePreset.payload)}
        ${renderCharacterSttGroup(activePreset.payload)}
      </div>
    `;
  }

  function renderCharacterSpeechGroup(payload) {
    const provider = requireCharacterTtsProvider(payload);
    const providerDescriptors = CHARACTER_TTS_PROVIDER_DESCRIPTORS[provider];
    if (!Array.isArray(providerDescriptors)) {
      throw new Error(`未対応の TTS プロバイダです: ${provider}`);
    }
    const basicFieldsHtml = filterDescriptorList(providerDescriptors, CHARACTER_TTS_PROVIDER_BASIC_PATHS[provider])
      .map((descriptor) => renderPresetField("character", payload, descriptor))
      .join("");
    const advancedFieldsHtml = filterDescriptorList(providerDescriptors, CHARACTER_TTS_PROVIDER_ADVANCED_PATHS[provider])
      .map((descriptor) => renderPresetField("character", payload, descriptor))
      .join("");
    return renderSettingsGroup(
      "音声合成",
      "",
      [
        CHARACTER_TTS_COMMON_DESCRIPTORS
          .map((descriptor) => renderPresetField("character", payload, descriptor))
          .join(""),
        renderSettingsSubsection(`${TTS_PROVIDER_LABELS[provider]} 設定`, basicFieldsHtml),
        renderSettingsExpander(TTS_ADVANCED_SECTION_TITLES[provider], advancedFieldsHtml),
      ].join(""),
    );
  }

  function renderCharacterSttGroup(payload) {
    return renderSettingsGroup(
      "音声認識",
      "",
      [
        renderPresetField("character", payload, CHARACTER_STT_DESCRIPTORS[0]),
        renderPresetField("character", payload, CHARACTER_STT_DESCRIPTORS[1]),
        renderPresetField("character", payload, CHARACTER_STT_DESCRIPTORS[2]),
        renderIndentedNote("起動ワードです。空欄の場合は常時待受します。複数指定する場合はカンマ区切りです。"),
        renderPresetField("character", payload, CHARACTER_STT_DESCRIPTORS[3]),
        renderPresetField("character", payload, CHARACTER_STT_DESCRIPTORS[4]),
        renderIndentedNote("単語登録を使う場合に入力してください。エンジンは -a-general を使います。"),
        renderPresetField("character", payload, CHARACTER_STT_DESCRIPTORS[5]),
      ].join(""),
    );
  }

  // Block: Preset selection rendering
  function renderPresetSelectionFields(kind, activePreset, selectOptions) {
    return `
      ${renderPresetSelectionToolbar(kind, selectOptions)}
      ${renderPresetNameField(kind, activePreset.preset_name)}
    `;
  }

  // Block: Preset selection helpers
  function buildPresetSelectOptions(presetEntries, activePresetId) {
    return presetEntries
      .filter((entry) => entry.archived !== true || entry.preset_id === activePresetId)
      .map((entry) => {
        const selected = entry.preset_id === activePresetId ? " selected" : "";
        const archivedTag = entry.archived === true ? " (archived)" : "";
        return `<option value="${escapeHtml(entry.preset_id)}"${selected}>${escapeHtml(entry.preset_name)}${archivedTag}</option>`;
      })
      .join("");
  }

  function renderPresetSelectionToolbar(kind, selectOptions) {
    return `
      <div class="settings-preset-toolbar">
        <select class="settings-input" data-active-preset-kind="${escapeHtml(kind)}">${selectOptions}</select>
        <button class="settings-btn settings-btn-small" type="button" data-preset-action="add" data-preset-action-kind="${escapeHtml(kind)}">追加</button>
        <button class="settings-btn settings-btn-small" type="button" data-preset-action="duplicate" data-preset-action-kind="${escapeHtml(kind)}">複製</button>
        <button class="settings-btn settings-btn-small danger" type="button" data-preset-action="archive" data-preset-action-kind="${escapeHtml(kind)}">削除</button>
      </div>
    `;
  }

  function renderPresetNameField(kind, presetName) {
    return renderRowField(
      "プリセット名",
      `<input class="settings-input" type="text" value="${escapeHtml(presetName)}" data-preset-name-kind="${escapeHtml(kind)}" />`,
    );
  }

  function renderVrmFileField(kind, payload) {
    const value = requireString(readNestedValue(payload, "character.vrm_file_path"), "character.vrm_file_path");
    return renderRowField(
      "VRMファイル",
      `
        <span class="settings-inline-actions">
          <input class="settings-input" type="text" value="${escapeHtml(value)}" data-preset-kind="${escapeHtml(kind)}" data-preset-path="character.vrm_file_path" data-value-kind="string" />
          <button class="settings-btn settings-btn-small" type="button" data-preset-prompt-kind="${escapeHtml(kind)}" data-preset-prompt-path="character.vrm_file_path" data-preset-prompt-title="VRMファイル" data-preset-prompt-message="VRMファイルのパスを入力してください">開く...</button>
        </span>
      `,
    );
  }

  function renderIndentedNote(text) {
    return `<div class="settings-note settings-note-indented">${escapeHtml(text).replaceAll("\n", "<br />")}</div>`;
  }

  function renderStaticCheckRow(label, checked) {
    return `
      <label class="settings-check-row">
        <input type="checkbox" ${checked ? "checked" : ""} disabled />
        <span class="settings-check-text">${escapeHtml(label)}</span>
      </label>
    `;
  }

  // Block: Group rendering
  function renderSettingsGroup(title, description, bodyHtml) {
    const descriptionHtml = description
      ? `<div class="settings-note">${escapeHtml(description)}</div>`
      : "";
    return `
      <section class="settings-group">
        <div class="settings-group-title">${escapeHtml(title)}</div>
        <div class="settings-group-body">
          ${descriptionHtml}
          <div class="settings-stack">${bodyHtml}</div>
        </div>
      </section>
    `;
  }

  // Block: Group helper rendering
  function renderSettingsSubsection(title, bodyHtml) {
    if (!bodyHtml) {
      return "";
    }
    return `
      <div class="settings-subsection">
        <div class="settings-subsection-title">${escapeHtml(title)}</div>
        <div class="settings-stack">${bodyHtml}</div>
      </div>
    `;
  }

  function renderSettingsExpander(title, bodyHtml) {
    if (!bodyHtml) {
      return "";
    }
    return `
      <details class="settings-expander">
        <summary class="settings-expander-summary">${escapeHtml(title)}</summary>
        <div class="settings-expander-body">
          <div class="settings-stack">${bodyHtml}</div>
        </div>
      </details>
    `;
  }

  // Block: Character TTS helpers
  function requireCharacterTtsProvider(payload) {
    const provider = requireString(readNestedValue(payload, "speech.tts.provider"), "speech.tts.provider");
    if (!(provider in CHARACTER_TTS_PROVIDER_DESCRIPTORS) || !(provider in TTS_PROVIDER_LABELS)) {
      throw new Error(`未対応の TTS プロバイダです: ${provider}`);
    }
    return provider;
  }

  function filterDescriptorList(descriptors, allowedPaths) {
    return descriptors.filter((descriptor) => allowedPaths.includes(descriptor.path));
  }

  function renderPresetField(kind, payload, descriptor) {
    const rawValue = readNestedValue(payload, descriptor.path);
    const path = escapeHtml(descriptor.path);
    const label = escapeHtml(descriptor.label);
    const isDisabled = descriptor.disabledWhenPath !== undefined
      && readNestedValue(payload, descriptor.disabledWhenPath) === descriptor.disabledWhenValue;
    const disabledAttr = isDisabled ? " disabled" : "";
    if (descriptor.kind === "boolean") {
      return `
        <label class="settings-check-row">
          <input type="checkbox" ${rawValue === true ? "checked" : ""} data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="boolean" />
          <span class="settings-check-text">${label}</span>
        </label>
      `;
    }
    if (descriptor.kind === "select") {
      const optionsHtml = descriptor.options
        .map((optionValue) => {
          const selected = rawValue === optionValue ? " selected" : "";
          const optionLabel = isObject(descriptor.optionLabels) && typeof descriptor.optionLabels[optionValue] === "string"
            ? descriptor.optionLabels[optionValue]
            : optionValue;
          return `<option value="${escapeHtml(optionValue)}"${selected}>${escapeHtml(optionLabel)}</option>`;
        })
        .join("");
      return renderRowField(
        label,
        `<select class="settings-input" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string"${disabledAttr}>${optionsHtml}</select>`,
      );
    }
    if (descriptor.kind === "password") {
      if (descriptor.clipboardActions === true) {
        return renderRowField(
          label,
          `
            <span class="settings-inline-actions">
              <input class="settings-input" type="text" value="${escapeHtml(requireString(rawValue, descriptor.path))}" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string"${disabledAttr} />
              <button class="settings-btn settings-btn-small" type="button" data-preset-clipboard-action="copy" data-preset-clipboard-kind="${escapeHtml(kind)}" data-preset-clipboard-path="${path}">コピー</button>
              <button class="settings-btn settings-btn-small" type="button" data-preset-clipboard-action="paste" data-preset-clipboard-kind="${escapeHtml(kind)}" data-preset-clipboard-path="${path}">上書き貼付け</button>
            </span>
          `,
        );
      }
      return renderRowField(
        label,
        `<input class="settings-input" type="text" value="${escapeHtml(requireString(rawValue, descriptor.path))}" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string"${disabledAttr} />`,
      );
    }
    if (descriptor.kind === "text") {
      return renderRowField(
        label,
        `<input class="settings-input" type="text" value="${escapeHtml(requireString(rawValue, descriptor.path))}" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string"${disabledAttr} />`,
      );
    }
    if (descriptor.kind === "textarea") {
      const rows = Number.isInteger(descriptor.rows) && descriptor.rows > 0
        ? descriptor.rows
        : 6;
      return renderRowField(
        label,
        `<textarea class="settings-input settings-textarea" rows="${String(rows)}" data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="string"${disabledAttr}>${escapeHtml(requireString(rawValue, descriptor.path))}</textarea>`,
      );
    }
    const numberValue = descriptor.kind === "integer"
      ? requireInteger(rawValue, descriptor.path)
      : requireNumber(rawValue, descriptor.path);
    const step = descriptor.step ?? 1;
    const minAttr = descriptor.min !== undefined ? ` min="${descriptor.min}"` : "";
    const maxAttr = descriptor.max !== undefined ? ` max="${descriptor.max}"` : "";
    return renderRowField(
      label,
      `<input class="settings-input settings-input-number" type="number" value="${String(numberValue)}" step="${String(step)}"${minAttr}${maxAttr} data-preset-kind="${escapeHtml(kind)}" data-preset-path="${path}" data-value-kind="${escapeHtml(descriptor.kind)}"${disabledAttr} />`,
    );
  }

  function renderRowField(label, controlHtml) {
    return `
      <label class="settings-row-field">
        <span class="settings-row-label">${escapeHtml(label)}</span>
        <span class="settings-row-control">${controlHtml}</span>
      </label>
    `;
  }

  function renderSystemValuesCard() {
    const systemValues = requireSystemValues();
    const notifyRoute = requireNotifyRoute(systemValues);
    const notifyFieldsHtml = SYSTEM_NOTIFY_DESCRIPTORS
      .filter((descriptor) => notifyRoute === "discord" || descriptor.key === "integrations.notify_route")
      .map((descriptor) => renderSystemField(systemValues, descriptor))
      .join("");
    const runtimeFieldsHtml = SYSTEM_RUNTIME_DESCRIPTORS
      .map((descriptor) => renderSystemField(systemValues, descriptor))
      .join("");
    settingsSystemCard.innerHTML = `
      <div class="settings-card-title">システム設定</div>
      ${renderSettingsGroup(
      "通知",
      "",
      notifyFieldsHtml,
    )}
      ${renderSettingsGroup(
      "運用設定",
      "ランタイムの運用値をここで調整します。",
      runtimeFieldsHtml,
    )}
    `;
  }

  function renderSystemField(systemValues, descriptor) {
    const value = systemValues[descriptor.key];
    if (descriptor.kind === "boolean") {
      return `
        <label class="settings-check-row">
          <input type="checkbox" ${value === true ? "checked" : ""} data-system-key="${escapeHtml(descriptor.key)}" data-value-kind="boolean" />
          <span class="settings-check-text">${escapeHtml(descriptor.label)}</span>
        </label>
      `;
    }
    if (descriptor.kind === "select") {
      const optionsHtml = descriptor.options
        .map((optionValue) => {
          const optionLabel = isObject(descriptor.optionLabels) && typeof descriptor.optionLabels[optionValue] === "string"
            ? descriptor.optionLabels[optionValue]
            : optionValue;
          const selected = value === optionValue ? " selected" : "";
          return `<option value="${escapeHtml(optionValue)}"${selected}>${escapeHtml(optionLabel)}</option>`;
        })
        .join("");
      return renderRowField(
        descriptor.label,
        `<select class="settings-input" data-system-key="${escapeHtml(descriptor.key)}" data-value-kind="string">${optionsHtml}</select>`,
      );
    }
    if (descriptor.kind === "password" && descriptor.clipboardActions === true) {
      return renderRowField(
        descriptor.label,
        `
          <span class="settings-inline-actions">
            <input class="settings-input" type="text" value="${escapeHtml(requireString(value, descriptor.key))}" data-system-key="${escapeHtml(descriptor.key)}" data-value-kind="string" />
            <button class="settings-btn settings-btn-small" type="button" data-system-clipboard-action="copy" data-system-key="${escapeHtml(descriptor.key)}">コピー</button>
            <button class="settings-btn settings-btn-small" type="button" data-system-clipboard-action="paste" data-system-key="${escapeHtml(descriptor.key)}">上書き貼付け</button>
          </span>
        `,
      );
    }
    if (descriptor.kind === "password" || descriptor.kind === "text") {
      return renderRowField(
        descriptor.label,
        `<input class="settings-input" type="text" value="${escapeHtml(requireString(value, descriptor.key))}" data-system-key="${escapeHtml(descriptor.key)}" data-value-kind="string" />`,
      );
    }
    const numberValue = descriptor.kind === "integer"
      ? requireInteger(value, descriptor.key)
      : requireNumber(value, descriptor.key);
    const minAttr = descriptor.min !== undefined ? ` min="${descriptor.min}"` : "";
    const maxAttr = descriptor.max !== undefined ? ` max="${descriptor.max}"` : "";
    return renderRowField(
      descriptor.label,
      `<input class="settings-input settings-input-number" type="number" value="${String(numberValue)}" step="${String(descriptor.step ?? 1)}"${minAttr}${maxAttr} data-system-key="${escapeHtml(descriptor.key)}" data-value-kind="${escapeHtml(descriptor.kind)}" />`,
    );
  }

  function renderCameraConnectionsCard() {
    const cameraConnections = readCameraConnections();
    const rowsHtml = cameraConnections
      .map((cameraConnection) => {
        return `
          <tr class="settings-table-row">
            <td class="settings-table-cell-select">
              <input
                class="settings-table-check"
                type="checkbox"
                data-camera-id="${escapeHtml(cameraConnection.camera_connection_id)}"
                data-camera-field="is_enabled"
                data-value-kind="boolean"
                ${cameraConnection.is_enabled === true ? "checked" : ""}
              />
            </td>
            <td>
              <input class="settings-input settings-table-input" type="text" value="${escapeHtml(cameraConnection.display_name)}" data-camera-id="${escapeHtml(cameraConnection.camera_connection_id)}" data-camera-field="display_name" />
            </td>
            <td>
              <input class="settings-input settings-table-input" type="text" value="${escapeHtml(cameraConnection.host)}" data-camera-id="${escapeHtml(cameraConnection.camera_connection_id)}" data-camera-field="host" />
            </td>
            <td>
              <input class="settings-input settings-table-input" type="text" value="${escapeHtml(cameraConnection.username)}" data-camera-id="${escapeHtml(cameraConnection.camera_connection_id)}" data-camera-field="username" />
            </td>
            <td>
              <input class="settings-input settings-table-input" type="text" value="${escapeHtml(cameraConnection.password)}" data-camera-id="${escapeHtml(cameraConnection.camera_connection_id)}" data-camera-field="password" />
            </td>
            <td class="settings-table-cell-action">
              <button class="settings-btn settings-btn-small danger" type="button" data-camera-action="remove" data-camera-id="${escapeHtml(cameraConnection.camera_connection_id)}">削除</button>
            </td>
          </tr>
        `;
      })
      .join("");
    const tableBodyHtml = rowsHtml || `
      <tr class="settings-table-row-empty">
        <td class="settings-table-empty" colspan="6">カメラ接続はまだありません。</td>
      </tr>
    `;
    settingsCameraCard.innerHTML = `
      <div class="settings-card-title">カメラ接続</div>
      ${renderSettingsGroup(
      "接続一覧",
      "AI に使わせる接続だけ「使用」をオンにしてください。追加は一覧の末尾へ行を足します。",
      `
          <div class="settings-table-wrap">
            <table class="settings-table">
              <thead>
                <tr>
                  <th>使用</th>
                  <th>表示名</th>
                  <th>IP アドレス</th>
                  <th>アカウント</th>
                  <th>パスワード</th>
                  <th>削除</th>
                </tr>
              </thead>
              <tbody>${tableBodyHtml}</tbody>
            </table>
          </div>
          <div class="settings-actions">
            <button class="settings-btn settings-btn-small" type="button" data-camera-action="add">追加</button>
          </div>
        `,
    )}
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
    const presetActionButtons = settingsPanel.querySelectorAll("[data-preset-action][data-preset-action-kind]");
    for (const element of presetActionButtons) {
      element.addEventListener("click", handlePresetAction);
    }
    const presetValueInputs = settingsPanel.querySelectorAll("[data-preset-kind][data-preset-path]");
    for (const element of presetValueInputs) {
      element.addEventListener("input", handlePresetFieldChange);
      element.addEventListener("change", handlePresetFieldChange);
    }
    const presetPromptButtons = settingsPanel.querySelectorAll("[data-preset-prompt-kind][data-preset-prompt-path]");
    for (const element of presetPromptButtons) {
      element.addEventListener("click", handlePresetPromptPathAction);
    }
    const presetClipboardButtons = settingsPanel.querySelectorAll("[data-preset-clipboard-action][data-preset-clipboard-kind][data-preset-clipboard-path]");
    for (const element of presetClipboardButtons) {
      element.addEventListener("click", (event) => {
        void handlePresetClipboardAction(event);
      });
    }
    const systemValueInputs = settingsPanel.querySelectorAll("[data-system-key]");
    for (const element of systemValueInputs) {
      element.addEventListener("input", handleSystemFieldChange);
      element.addEventListener("change", handleSystemFieldChange);
    }
    const systemClipboardButtons = settingsPanel.querySelectorAll("[data-system-clipboard-action][data-system-key]");
    for (const element of systemClipboardButtons) {
      element.addEventListener("click", (event) => {
        void handleSystemClipboardAction(event);
      });
    }
    const motionActionButtons = settingsPanel.querySelectorAll("[data-motion-action]");
    for (const element of motionActionButtons) {
      element.addEventListener("click", handleMotionAction);
    }
    const motionFieldInputs = settingsPanel.querySelectorAll("[data-motion-index][data-motion-field]");
    for (const element of motionFieldInputs) {
      element.addEventListener("input", handleMotionFieldChange);
      element.addEventListener("change", handleMotionFieldChange);
    }
    const cameraFieldInputs = settingsPanel.querySelectorAll("[data-camera-id][data-camera-field]");
    for (const element of cameraFieldInputs) {
      element.addEventListener("input", handleCameraFieldChange);
      element.addEventListener("change", handleCameraFieldChange);
    }
    const cameraActionButtons = settingsPanel.querySelectorAll("[data-camera-action]");
    for (const element of cameraActionButtons) {
      element.addEventListener("click", handleCameraAction);
    }
  }

  function handleActivePresetChange(event) {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    const element = event.currentTarget;
    const kind = String(element.dataset.activePresetKind || "");
    if (!(kind in PRESET_COLLECTION_CONFIG)) {
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

  function handlePresetAction(event) {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    const element = event.currentTarget;
    const kind = String(element.dataset.presetActionKind || "");
    const action = String(element.dataset.presetAction || "");
    if (!(kind in PRESET_COLLECTION_CONFIG)) {
      appendError("プリセット種別が不正です");
      return;
    }
    try {
      if (action === "add") {
        addPreset(kind);
        renderSettingsEditor();
        return;
      }
      if (action === "duplicate") {
        duplicateActivePreset(kind);
        renderSettingsEditor();
        return;
      }
      if (action === "archive") {
        archiveActivePreset(kind);
        renderSettingsEditor();
        return;
      }
      appendError("プリセット操作が不正です");
    } catch (error) {
      appendError(`プリセット操作に失敗しました: ${error.message}`);
    }
  }

  function handlePresetFieldChange(event) {
    const element = event.currentTarget;
    const kind = String(element.dataset.presetKind || "");
    const path = String(element.dataset.presetPath || "");
    const valueKind = String(element.dataset.valueKind || "");
    const presetEntry = requireActivePresetEntry(kind);
    writeNestedValue(presetEntry.payload, path, readInputValue(element, valueKind));
    if (
      (kind === "character" && (path === "speech.tts.provider" || path === "character.material.enable_shadow_off"))
      || (kind === "motion" && path.startsWith("motion."))
    ) {
      renderSettingsEditor();
      return;
    }
    updateSettingsDirtyState();
  }

  function handlePresetPromptPathAction(event) {
    const element = event.currentTarget;
    const kind = String(element.dataset.presetPromptKind || "");
    const path = String(element.dataset.presetPromptPath || "");
    const title = String(element.dataset.presetPromptTitle || "設定");
    const message = String(element.dataset.presetPromptMessage || "値を入力してください");
    const presetEntry = requireActivePresetEntry(kind);
    const currentValue = readNestedValue(presetEntry.payload, path);
    const nextValue = window.prompt(`${title}\n${message}`, typeof currentValue === "string" ? currentValue : "");
    if (nextValue === null) {
      return;
    }
    writeNestedValue(presetEntry.payload, path, nextValue);
    renderSettingsEditor();
  }

  async function handlePresetClipboardAction(event) {
    const element = event.currentTarget;
    const action = String(element.dataset.presetClipboardAction || "");
    const kind = String(element.dataset.presetClipboardKind || "");
    const path = String(element.dataset.presetClipboardPath || "");
    const presetEntry = requireActivePresetEntry(kind);
    try {
      if (action === "copy") {
        const currentValue = readNestedValue(presetEntry.payload, path);
        if (typeof currentValue !== "string") {
          appendError("コピー対象が文字列ではありません");
          return;
        }
        await navigator.clipboard.writeText(currentValue);
        settingsStatus.textContent = "クリップボードへコピーしました";
        return;
      }
      if (action === "paste") {
        const clipboardText = await navigator.clipboard.readText();
        writeNestedValue(presetEntry.payload, path, clipboardText);
        renderSettingsEditor();
        settingsStatus.textContent = "クリップボードから貼り付けました";
        return;
      }
      appendError("クリップボード操作が不正です");
    } catch (error) {
      appendError(`クリップボード操作に失敗しました: ${error.message}`);
    }
  }

  function handleSystemFieldChange(event) {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    const element = event.currentTarget;
    const key = String(element.dataset.systemKey || "");
    const valueKind = String(element.dataset.valueKind || "");
    editorDraft.editor_state.system_values[key] = readInputValue(element, valueKind);
    if (key === "integrations.notify_route") {
      renderSettingsEditor();
      return;
    }
    updateSettingsDirtyState();
  }

  async function handleSystemClipboardAction(event) {
    const element = event.currentTarget;
    const action = String(element.dataset.systemClipboardAction || "");
    const key = String(element.dataset.systemKey || "");
    const systemValues = requireSystemValues();
    try {
      if (action === "copy") {
        const currentValue = systemValues[key];
        if (typeof currentValue !== "string") {
          appendError("コピー対象が文字列ではありません");
          return;
        }
        await navigator.clipboard.writeText(currentValue);
        settingsStatus.textContent = "クリップボードへコピーしました";
        return;
      }
      if (action === "paste") {
        systemValues[key] = await navigator.clipboard.readText();
        renderSettingsEditor();
        settingsStatus.textContent = "クリップボードから貼り付けました";
        return;
      }
      appendError("クリップボード操作が不正です");
    } catch (error) {
      appendError(`クリップボード操作に失敗しました: ${error.message}`);
    }
  }

  function handleMotionAction(event) {
    const element = event.currentTarget;
    const action = String(element.dataset.motionAction || "");
    const activeMotionPreset = requireActivePresetEntry("motion");
    if (!Array.isArray(activeMotionPreset.payload.animations)) {
      activeMotionPreset.payload.animations = [];
    }
    if (action === "add") {
      activeMotionPreset.payload.animations.push({
        display_name: `モーション ${activeMotionPreset.payload.animations.length + 1}`,
        animation_type: 0,
        animation_name: `animation_${activeMotionPreset.payload.animations.length + 1}`,
        is_enabled: true,
      });
      renderSettingsEditor();
      return;
    }
    if (action === "remove") {
      const index = Number.parseInt(String(element.dataset.motionIndex || ""), 10);
      if (!Number.isInteger(index) || index < 0 || index >= activeMotionPreset.payload.animations.length) {
        appendError("モーション行が不正です");
        return;
      }
      activeMotionPreset.payload.animations.splice(index, 1);
      renderSettingsEditor();
      return;
    }
    appendError("モーション操作が不正です");
  }

  function handleMotionFieldChange(event) {
    const element = event.currentTarget;
    const index = Number.parseInt(String(element.dataset.motionIndex || ""), 10);
    const fieldName = String(element.dataset.motionField || "");
    const valueKind = String(element.dataset.valueKind || "string");
    const activeMotionPreset = requireActivePresetEntry("motion");
    if (!Array.isArray(activeMotionPreset.payload.animations)) {
      throw new Error("motion.animations が不正です");
    }
    if (!Number.isInteger(index) || index < 0 || index >= activeMotionPreset.payload.animations.length) {
      appendError("モーション行が不正です");
      return;
    }
    activeMotionPreset.payload.animations[index][fieldName] = readInputValue(element, valueKind);
    updateSettingsDirtyState();
  }

  // Block: Camera selection handlers
  function handleCameraFieldChange(event) {
    const element = event.currentTarget;
    const cameraConnectionId = String(element.dataset.cameraId || "");
    const fieldName = String(element.dataset.cameraField || "");
    const valueKind = String(element.dataset.valueKind || "string");
    if (!CAMERA_FIELD_KEYS.includes(fieldName)) {
      appendError("カメラ項目が不正です");
      return;
    }
    try {
      const cameraConnection = requireCameraConnection(cameraConnectionId);
      cameraConnection[fieldName] = readInputValue(element, valueKind);
      cameraConnection.updated_at = Date.now();
      updateSettingsDirtyState();
    } catch (error) {
      appendError(`カメラ項目の更新に失敗しました: ${error.message}`);
    }
  }

  function handleCameraAction(event) {
    if (editorDraft === null) {
      appendError("設定ドラフトが未初期化です");
      return;
    }
    const element = event.currentTarget;
    const action = String(element.dataset.cameraAction || "");
    try {
      if (action === "add") {
        addCameraConnection();
        renderSettingsEditor();
        return;
      }
      if (action === "remove") {
        removeCameraConnection(String(element.dataset.cameraId || ""));
        renderSettingsEditor();
        return;
      }
    } catch (error) {
      appendError(`カメラ操作に失敗しました: ${error.message}`);
      return;
    }
    appendError("カメラ操作が不正です");
  }

  function discardSettingsDraft() {
    if (latestEditorSnapshot === null) {
      return;
    }
    editorDraft = buildEditorDraft(latestEditorSnapshot);
  }

  function isSettingsDraftDirty() {
    if (editorDraft === null || latestEditorSnapshot === null) {
      return false;
    }
    return JSON.stringify(editorDraft) !== JSON.stringify(buildEditorDraft(latestEditorSnapshot));
  }

  function updateSettingsDirtyState() {
    settingsStatus.textContent = isSettingsDraftDirty() === true
      ? "未保存の変更があります"
      : "保存済み";
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
    const bubbleText = messageNode.querySelector(".bubble-text");
    if (!(bubbleText instanceof HTMLElement)) {
      throw new Error("bubble-text が見つかりません");
    }
    bubbleText.textContent += chunk;
    scrollToBottom();
  }

  function handleMessageEvent(payload) {
    const messageId = requireString(payload.message_id, "message.message_id");
    const role = requireString(payload.role, "message.role");
    const text = requireString(payload.text, "message.text");
    const audioUrl = readOptionalString(payload.audio_url);
    let messageNode = draftMessages.get(messageId);
    if (messageNode === undefined) {
      messageNode = appendMessage({
        role,
        text,
        messageId,
        isDraft: false,
      });
    } else {
      const bubbleText = messageNode.querySelector(".bubble-text");
      const meta = messageNode.querySelector(".bubble-time");
      if (!(bubbleText instanceof HTMLElement) || !(meta instanceof HTMLElement)) {
        throw new Error("message node が不正です");
      }
      bubbleText.textContent = text;
      meta.textContent = buildMetaLabel(role);
      meta.classList.remove("empty");
      draftMessages.delete(messageId);
    }
    if (audioUrl !== null) {
      playRemoteSpeechAudio(audioUrl);
    }
  }

  function handleNoticeEvent(payload) {
    const noticeCode = requireString(payload.notice_code, "notice.notice_code");
    const text = typeof payload.text === "string" && payload.text.length > 0
      ? payload.text
      : noticeCode;
    appendNotice(noticeCode, text);
  }

  function handleErrorEvent(payload) {
    const message = typeof payload.message === "string" && payload.message.length > 0
      ? payload.message
      : "処理中にエラーが発生しました";
    appendError(message);
  }

  // Block: Message rendering
  function appendMessage({ role, text, messageId, isDraft }) {
    const normalizedRole = role === "user" ? "user" : "assistant";
    const row = document.createElement("div");
    row.className = `bubble-row ${normalizedRole === "user" ? "user" : "ai"}`;
    row.dataset.messageId = messageId;

    const bubble = document.createElement("div");
    bubble.className = `bubble ${normalizedRole === "user" ? "user" : "ai"}`;

    const bubbleText = document.createElement("div");
    bubbleText.className = "bubble-text";
    bubbleText.textContent = text;
    bubble.appendChild(bubbleText);

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = buildMetaLabel(normalizedRole);
    if (isDraft === true) {
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
    row.className = "bubble-row ai";

    const bubble = document.createElement("div");
    bubble.className = "bubble ai";

    const bubbleText = document.createElement("div");
    bubbleText.className = "bubble-text";
    bubbleText.textContent = text;
    bubble.appendChild(bubbleText);

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = buildMetaLabel(`notice:${code}`);

    row.appendChild(bubble);
    row.appendChild(meta);
    chatScroll.appendChild(row);
    scrollToBottom();
  }

  function appendError(text) {
    if (!settingsPanel.classList.contains("hidden")) {
      settingsStatus.textContent = text;
    }
    const row = document.createElement("div");
    row.className = "bubble-row ai";

    const bubble = document.createElement("div");
    bubble.className = "bubble ai";
    bubble.style.borderColor = "#aa3d52";
    bubble.style.color = "#6b1022";

    const bubbleText = document.createElement("div");
    bubbleText.className = "bubble-text";
    bubbleText.textContent = text;
    bubble.appendChild(bubbleText);

    const meta = document.createElement("div");
    meta.className = "bubble-time";
    meta.textContent = buildMetaLabel("error");

    row.appendChild(bubble);
    row.appendChild(meta);
    chatScroll.appendChild(row);
    scrollToBottom();
  }

  // Block: Camera attachments
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

  function removePendingCameraAttachment(captureId) {
    const targetCaptureId = String(captureId || "");
    const attachmentIndex = pendingCameraAttachments.findIndex((attachment) => attachment.captureId === targetCaptureId);
    if (attachmentIndex === -1) {
      return;
    }
    pendingCameraAttachments.splice(attachmentIndex, 1);
  }

  function clearPendingCameraAttachments() {
    pendingCameraAttachments.length = 0;
    attachments.replaceChildren();
    updateSendEnabledState();
  }

  // Block: Chat UI helpers
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

  function updateRuntimeChip(statusPayload) {
    if (!isObject(statusPayload) || !isObject(statusPayload.runtime) || !isObject(statusPayload.self_state)) {
      throw new Error("status payload が不正です");
    }
    const runtime = statusPayload.runtime;
    const selfState = statusPayload.self_state;
    applyStatusSummary(runtimeSummaryText, buildRuntimeSummary(runtime));
    applyStatusSummary(retrievalSummaryText, buildRetrievalSummary(runtime.last_retrieval));
    applyStatusSummary(personaUpdateSummaryText, buildPersonaUpdateSummary(selfState.last_persona_update));
    if (runtime.is_running === true) {
      runtimeText.textContent = "人格ランタイム稼働中";
      return;
    }
    runtimeText.textContent = "人格ランタイム停止中";
  }

  // Block: Status summary rendering
  function applyStatusSummary(element, summary) {
    if (!(element instanceof HTMLElement)) {
      throw new Error("status summary element が見つかりません");
    }
    element.textContent = summary.text;
    element.title = summary.title;
  }

  function buildRuntimeSummary(runtime) {
    const parts = [runtime.is_running === true ? "稼働中" : "停止中"];
    if (Number.isInteger(runtime.last_commit_id)) {
      parts.push(`commit ${String(runtime.last_commit_id)}`);
    }
    if (typeof runtime.last_cycle_id === "string" && runtime.last_cycle_id.length > 0) {
      parts.push(runtime.last_cycle_id);
    }
    return {
      text: parts.join(" / "),
      title: parts.join("\n"),
    };
  }

  function buildRetrievalSummary(lastRetrieval) {
    if (!isObject(lastRetrieval)) {
      return { text: "まだありません", title: "" };
    }
    const createdAt = requireInteger(lastRetrieval.created_at, "runtime.last_retrieval.created_at");
    const mode = requireString(lastRetrieval.mode, "runtime.last_retrieval.mode");
    const cycleId = requireString(lastRetrieval.cycle_id, "runtime.last_retrieval.cycle_id");
    const queries = readStringArray(lastRetrieval.queries, "runtime.last_retrieval.queries");
    const selectedCounts = requireCountMap(lastRetrieval.selected_counts, "runtime.last_retrieval.selected_counts");
    const totalCount = Object.values(selectedCounts).reduce((total, count) => total + count, 0);
    return {
      text: `${formatStatusTimestamp(createdAt)} / ${mode} / ${summarizeQueries(queries)} / 合計 ${String(totalCount)} 件（${summarizeSelectedCounts(selectedCounts)}）`,
      title: [
        `cycle: ${cycleId}`,
        `queries: ${queries.length > 0 ? queries.join(" / ") : "なし"}`,
        `selected: ${formatSelectedCounts(selectedCounts)}`,
      ].join("\n"),
    };
  }

  function buildPersonaUpdateSummary(lastPersonaUpdate) {
    if (!isObject(lastPersonaUpdate)) {
      return { text: "まだありません", title: "" };
    }
    const createdAt = requireInteger(lastPersonaUpdate.created_at, "self_state.last_persona_update.created_at");
    const reason = requireString(lastPersonaUpdate.reason, "self_state.last_persona_update.reason");
    const updatedTraits = readUpdatedTraits(lastPersonaUpdate.updated_traits, "self_state.last_persona_update.updated_traits");
    const evidenceEventIds = readStringArray(
      lastPersonaUpdate.evidence_event_ids,
      "self_state.last_persona_update.evidence_event_ids",
    );
    const traitSummary = updatedTraits.length > 0
      ? summarizeTraitUpdates(updatedTraits)
      : reason;
    return {
      text: `${formatStatusTimestamp(createdAt)} / ${traitSummary}`,
      title: [
        `reason: ${reason}`,
        `traits: ${updatedTraits.length > 0 ? formatTraitUpdates(updatedTraits) : "なし"}`,
        `evidence: ${evidenceEventIds.length > 0 ? evidenceEventIds.join(", ") : "なし"}`,
      ].join("\n"),
    };
  }

  // Block: Status summary helpers
  function summarizeQueries(queries) {
    if (queries.length === 0) {
      return "query なし";
    }
    const firstQuery = clipText(queries[0], 36);
    if (queries.length === 1) {
      return `「${firstQuery}」`;
    }
    return `「${firstQuery}」ほか ${String(queries.length - 1)} 件`;
  }

  function summarizeTraitUpdates(updatedTraits) {
    const visibleTraits = updatedTraits.slice(0, 3).map((trait) => {
      const delta = requireNumber(trait.delta, `updated_traits.${trait.trait_name}.delta`);
      return `${requireString(trait.trait_name, "updated_traits.trait_name")} ${formatSignedDecimal(delta)}`;
    });
    if (updatedTraits.length > 3) {
      visibleTraits.push(`他 ${String(updatedTraits.length - 3)} 件`);
    }
    return visibleTraits.join(", ");
  }

  function formatSelectedCounts(selectedCounts) {
    return Object.entries(selectedCounts)
      .map(([key, value]) => `${key}=${String(value)}`)
      .join(", ");
  }

  function summarizeSelectedCounts(selectedCounts) {
    const labelMap = {
      working_memory_items: "作業",
      episodic_items: "エピ",
      semantic_items: "意味",
      affective_items: "感情",
      relationship_items: "関係",
      reflection_items: "反省",
      recent_event_window: "直近",
    };
    return Object.entries(labelMap)
      .map(([key, label]) => `${label}${String(requireInteger(selectedCounts[key] ?? 0, `selected_counts.${key}`))}`)
      .join(" / ");
  }

  function formatTraitUpdates(updatedTraits) {
    return updatedTraits
      .map((trait) => {
        const traitName = requireString(trait.trait_name, "updated_traits.trait_name");
        const before = requireNumber(trait.before, `updated_traits.${traitName}.before`);
        const after = requireNumber(trait.after, `updated_traits.${traitName}.after`);
        const delta = requireNumber(trait.delta, `updated_traits.${traitName}.delta`);
        return `${traitName}: ${before.toFixed(2)} -> ${after.toFixed(2)} (${formatSignedDecimal(delta)})`;
      })
      .join(", ");
  }

  function formatStatusTimestamp(timestampMs) {
    return new Date(timestampMs).toLocaleString("ja-JP", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatSignedDecimal(value) {
    return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
  }

  function clipText(text, maxLength) {
    if (text.length <= maxLength) {
      return text;
    }
    return `${text.slice(0, maxLength - 3)}...`;
  }

  function readStringArray(value, label) {
    if (!Array.isArray(value)) {
      throw new Error(`${label} が配列ではありません`);
    }
    return value.map((item, index) => requireString(item, `${label}[${String(index)}]`));
  }

  function requireCountMap(value, label) {
    if (!isObject(value)) {
      throw new Error(`${label} が object ではありません`);
    }
    const counts = {};
    for (const [key, rawValue] of Object.entries(value)) {
      counts[key] = requireInteger(rawValue, `${label}.${key}`);
    }
    return counts;
  }

  function readUpdatedTraits(value, label) {
    if (!Array.isArray(value)) {
      throw new Error(`${label} が配列ではありません`);
    }
    return value.map((item, index) => {
      if (!isObject(item)) {
        throw new Error(`${label}[${String(index)}] が object ではありません`);
      }
      return item;
    });
  }

  function autoResizeComposer() {
    chatInput.style.height = "auto";
    chatInput.style.height = `${chatInput.scrollHeight}px`;
  }

  function updateSendEnabledState() {
    sendButton.disabled = chatInput.value.trim().length === 0 && pendingCameraAttachments.length === 0;
  }

  function scrollToBottom() {
    chatScroll.scrollTop = chatScroll.scrollHeight;
  }

  // Block: Remote audio playback
  function playRemoteSpeechAudio(audioUrl) {
    if (typeof audioUrl !== "string" || audioUrl.length === 0) {
      return;
    }
    stopActiveSpeechAudio();
    const audio = new Audio(audioUrl);
    activeSpeechAudio = audio;
    audio.addEventListener("ended", () => {
      if (activeSpeechAudio === audio) {
        activeSpeechAudio = null;
      }
    });
    audio.addEventListener("error", () => {
      if (activeSpeechAudio === audio) {
        activeSpeechAudio = null;
      }
      appendError("音声再生に失敗しました");
    });
    const playPromise = audio.play();
    if (playPromise && typeof playPromise.catch === "function") {
      playPromise.catch((error) => {
        if (activeSpeechAudio === audio) {
          activeSpeechAudio = null;
        }
        appendError(`音声再生を開始できません: ${error.message}`);
      });
    }
  }

  function stopActiveSpeechAudio() {
    if (activeSpeechAudio === null) {
      return;
    }
    activeSpeechAudio.pause();
    activeSpeechAudio.currentTime = 0;
    activeSpeechAudio = null;
  }

  // Block: Settings draft helpers
  function readPresetEntries(kind) {
    if (editorDraft === null) {
      throw new Error("設定ドラフトが未初期化です");
    }
    const config = requirePresetCollectionConfig(kind);
    const entries = editorDraft[config.listKey];
    if (!Array.isArray(entries)) {
      throw new Error(`${config.listKey} が不正です`);
    }
    return entries;
  }

  function readActivePresetId(kind) {
    if (editorDraft === null) {
      throw new Error("設定ドラフトが未初期化です");
    }
    const config = requirePresetCollectionConfig(kind);
    return requireString(editorDraft.editor_state[config.activeKey], config.activeKey);
  }

  function writeActivePresetId(kind, presetId) {
    if (editorDraft === null) {
      throw new Error("設定ドラフトが未初期化です");
    }
    const config = requirePresetCollectionConfig(kind);
    editorDraft.editor_state[config.activeKey] = presetId;
  }

  function requireActivePresetEntry(kind) {
    return requirePresetEntry(kind, readActivePresetId(kind));
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

  // Block: Draft ID helpers
  function buildDraftEntityId(prefix) {
    localDraftIdCounter += 1;
    return `${prefix}_${Date.now()}_${localDraftIdCounter}`;
  }

  function addPreset(kind) {
    const config = requirePresetCollectionConfig(kind);
    const presetEntries = readPresetEntries(kind);
    const presetId = buildDraftEntityId(config.idPrefix);
    const nextSortOrder = presetEntries.length === 0
      ? 10
      : Math.max(...presetEntries.map((entry) => requireInteger(entry.sort_order, `${kind}.sort_order`))) + 10;
    presetEntries.push({
      preset_id: presetId,
      preset_name: `${config.baseName} ${visiblePresetCount(kind) + 1}`,
      archived: false,
      sort_order: nextSortOrder,
      updated_at: Date.now(),
      payload: buildDefaultPresetPayload(kind),
    });
    writeActivePresetId(kind, presetId);
    updateSettingsDirtyState();
  }

  function duplicateActivePreset(kind) {
    const config = requirePresetCollectionConfig(kind);
    const presetEntries = readPresetEntries(kind);
    const activePreset = requireActivePresetEntry(kind);
    const presetId = buildDraftEntityId(config.idPrefix);
    const nextSortOrder = presetEntries.length === 0
      ? 10
      : Math.max(...presetEntries.map((entry) => requireInteger(entry.sort_order, `${kind}.sort_order`))) + 10;
    presetEntries.push({
      preset_id: presetId,
      preset_name: `${activePreset.preset_name} のコピー`,
      archived: false,
      sort_order: nextSortOrder,
      updated_at: Date.now(),
      payload: cloneJson(activePreset.payload),
    });
    writeActivePresetId(kind, presetId);
    updateSettingsDirtyState();
  }

  function archiveActivePreset(kind) {
    const presetEntries = readPresetEntries(kind);
    const activePresetId = readActivePresetId(kind);
    const visibleEntries = presetEntries.filter((entry) => entry.archived !== true);
    if (visibleEntries.length <= 1) {
      throw new Error("最後のプリセットは削除できません");
    }
    const activePreset = requireActivePresetEntry(kind);
    activePreset.archived = true;
    activePreset.updated_at = Date.now();
    const nextActiveEntry = visibleEntries.find((entry) => String(entry.preset_id) !== activePresetId);
    if (nextActiveEntry === undefined) {
      throw new Error("切り替え先のプリセットが見つかりません");
    }
    writeActivePresetId(kind, String(nextActiveEntry.preset_id));
    updateSettingsDirtyState();
  }

  function buildDefaultPresetPayload(kind) {
    if (latestEditorSnapshot !== null) {
      const config = requirePresetCollectionConfig(kind);
      const snapshotEntries = latestEditorSnapshot[config.listKey];
      if (Array.isArray(snapshotEntries) && snapshotEntries.length > 0) {
        const templateEntry = snapshotEntries.find((entry) => entry.archived !== true && isObject(entry.payload))
          || snapshotEntries.find((entry) => isObject(entry.payload));
        if (templateEntry !== undefined) {
          return cloneJson(templateEntry.payload);
        }
      }
    }
    return cloneJson(requireActivePresetEntry(kind).payload);
  }

  function visiblePresetCount(kind) {
    return readPresetEntries(kind).filter((entry) => entry.archived !== true).length;
  }

  // Block: System draft helpers
  function requireSystemValues() {
    if (editorDraft === null || !isObject(editorDraft.editor_state)) {
      throw new Error("system_values が未初期化です");
    }
    const systemValues = editorDraft.editor_state.system_values;
    if (!isObject(systemValues)) {
      throw new Error("system_values が不正です");
    }
    return systemValues;
  }

  function requireNotifyRoute(systemValues) {
    const notifyRoute = requireString(systemValues["integrations.notify_route"], "integrations.notify_route");
    if (notifyRoute !== "ui_only" && notifyRoute !== "discord") {
      throw new Error(`未対応の通知経路です: ${notifyRoute}`);
    }
    return notifyRoute;
  }

  // Block: Camera draft helpers
  function readCameraConnections() {
    if (editorDraft === null) {
      throw new Error("設定ドラフトが未初期化です");
    }
    if (!Array.isArray(editorDraft.camera_connections)) {
      throw new Error("camera_connections が不正です");
    }
    return editorDraft.camera_connections;
  }

  function requireCameraConnection(cameraConnectionId) {
    const requiredCameraConnectionId = requireString(cameraConnectionId, "camera_connection_id");
    const cameraConnection = readCameraConnections()
      .find((candidate) => String(candidate.camera_connection_id) === requiredCameraConnectionId);
    if (cameraConnection === undefined || !isObject(cameraConnection)) {
      throw new Error("カメラ接続が見つかりません");
    }
    return cameraConnection;
  }

  function addCameraConnection() {
    const cameraConnections = readCameraConnections();
    const nextSortOrder = cameraConnections.length === 0
      ? 10
      : Math.max(...cameraConnections.map((cameraConnection) => requireInteger(cameraConnection.sort_order, "camera_connection.sort_order"))) + 10;
    const cameraConnectionId = buildDraftEntityId("cam");
    const nextIndex = cameraConnections.length + 1;
    const nowMs = Date.now();
    cameraConnections.push({
      camera_connection_id: cameraConnectionId,
      is_enabled: false,
      display_name: `カメラ ${nextIndex}`,
      host: "",
      username: "",
      password: "",
      sort_order: nextSortOrder,
      updated_at: nowMs,
    });
    updateSettingsDirtyState();
  }

  function removeCameraConnection(cameraConnectionId) {
    const requiredCameraConnectionId = requireString(cameraConnectionId, "camera_connection_id");
    requireCameraConnection(requiredCameraConnectionId);
    editorDraft.camera_connections = readCameraConnections()
      .filter((cameraConnection) => String(cameraConnection.camera_connection_id) !== requiredCameraConnectionId);
    updateSettingsDirtyState();
  }

  function requirePresetCollectionConfig(kind) {
    const config = PRESET_COLLECTION_CONFIG[kind];
    if (!isObject(config)) {
      throw new Error(`未対応のプリセット種別です: ${kind}`);
    }
    return config;
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

  function readOptionalString(value) {
    if (typeof value !== "string" || value.length === 0) {
      return null;
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
