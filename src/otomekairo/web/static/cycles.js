// 判断専用画面。inspection の cycle 一覧と詳細を読む。
// token はブラウザへ渡さず、同一 origin の server-held 認可だけを使う。

const CYCLE_LIST_LIMIT = 40;

const TRIGGER_KIND_LABELS = {
  user_message: "会話入力",
  wake: "起床",
  background_thinking: "定期思考",
  autonomous_run: "自律実行",
  capability_result: "能力結果",
  conversation: "会話入力",
};

const RESULT_KIND_LABELS = {
  speech: "発話",
  noop: "見送り",
  capability_request: "能力要求",
  internal_failure: "失敗",
  pending_intent: "保留意図",
};

const STAGE_ORDER = [
  ["input_trace", "入力"],
  ["world_state_trace", "世界状態"],
  ["activity_trace", "活動"],
  ["recall_trace", "想起"],
  ["decision_trace", "判断"],
  ["result_trace", "結果"],
  ["memory_trace", "記憶"],
];

const state = {
  cycles: [],
  selectedCycleId: null,
  trace: null,
  cognitive: null,
  loadingList: false,
  loadingTrace: false,
  activeTab: "overview",
};

function element(id) {
  const node = document.getElementById(id);
  if (!node) {
    throw new Error(`missing element: ${id}`);
  }
  return node;
}

async function apiRequest(path) {
  const response = await fetch(path);
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

function setStatus(text, kind = "") {
  const node = element("status-text");
  node.textContent = text;
  node.className = kind ? `status ${kind}` : "status";
}

function displayValue(value) {
  if (value == null || value === "") {
    return "";
  }
  return String(value);
}

function labelTrigger(kind) {
  return TRIGGER_KIND_LABELS[kind] || displayValue(kind) || "不明";
}

function labelResult(kind) {
  return RESULT_KIND_LABELS[kind] || displayValue(kind) || "不明";
}

function formatTime(ts) {
  if (typeof ts !== "string" || !ts) {
    return "—";
  }
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) {
    return ts;
  }
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${month}/${day} ${hours}:${minutes}:${seconds}`;
}

function firstNonEmpty(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function pickString(obj, key) {
  if (!obj || typeof obj !== "object") {
    return "";
  }
  const value = obj[key];
  return typeof value === "string" ? value : "";
}

function prettyJson(value) {
  if (value == null) {
    return "（なし）";
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function queryCycleId() {
  const params = new URLSearchParams(window.location.search);
  const cycleId = params.get("cycle_id");
  return cycleId && cycleId.trim() ? cycleId.trim() : null;
}

function setActiveTab(tabId) {
  state.activeTab = tabId;
  for (const button of document.querySelectorAll(".cycle-tab")) {
    const active = button.dataset.tab === tabId;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  }
  for (const panel of document.querySelectorAll(".cycle-tab-panel")) {
    panel.hidden = panel.dataset.panel !== tabId;
  }
}

function createOverviewSection(title, lines) {
  const filtered = lines.filter((line) => typeof line === "string" && line.trim());
  if (!filtered.length) {
    return null;
  }
  const section = document.createElement("section");
  section.className = "cycle-overview-section";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const body = document.createElement("pre");
  body.className = "cycle-overview-body";
  body.textContent = filtered.join("\n");
  section.append(heading, body);
  return section;
}

function buildOverviewSections(trace, cognitive) {
  const summary = asObject(trace.cycle_summary) || {};
  const inputTrace = asObject(trace.input_trace) || {};
  const decisionTrace = asObject(trace.decision_trace) || {};
  const resultTrace = asObject(trace.result_trace) || {};
  const recallTrace = asObject(trace.recall_trace) || {};
  const worldTrace = asObject(trace.world_state_trace) || {};
  const activityTrace = asObject(trace.activity_trace) || {};
  const memoryTrace = asObject(trace.memory_trace) || {};
  const compact = asObject(resultTrace.trigger_compact_summary) || {};
  const entrySummary = asObject(compact.entry_summary) || {};
  const decisionSummary = asObject(compact.decision_summary) || {};
  const resultSummary = asObject(compact.result_summary) || {};
  const factResolution = asObject(recallTrace.fact_resolution_trace) || {};
  const cognitiveObj = asObject(cognitive) || {};

  const conclusion = [
    `開始: ${formatTime(summary.started_at)}`,
    `きっかけ: ${labelTrigger(summary.trigger_kind)}`,
    `結果: ${labelResult(summary.result_kind)}${summary.failed === true ? "（失敗）" : ""}`,
  ];
  const speech = firstNonEmpty(pickString(resultTrace, "speech_summary"), pickString(resultSummary, "speech_summary"), pickString(summary, "outcome_summary"));
  const noop = firstNonEmpty(pickString(resultTrace, "noop_reason_summary"), pickString(resultSummary, "noop_reason_summary"));
  const failure = firstNonEmpty(pickString(resultTrace, "internal_failure_summary"), pickString(resultSummary, "internal_failure_summary"));
  if (speech) {
    conclusion.push(`発話: ${speech}`);
  }
  if (noop) {
    conclusion.push(`見送り理由: ${noop}`);
  }
  if (failure) {
    conclusion.push(`失敗理由: ${failure}`);
  }

  const inputLines = [];
  const inputText = firstNonEmpty(
    pickString(asObject(compact.current_input_summary), "text"),
    pickString(entrySummary, "input_summary"),
    pickString(inputTrace, "input_summary"),
    pickString(summary, "input_summary"),
    pickString(decisionTrace, "current_context_summary"),
  );
  if (inputText) {
    inputLines.push(`入力: ${inputText}`);
  }
  const addition = firstNonEmpty(
    pickString(inputTrace, "input_context_addition_summary"),
    pickString(decisionTrace, "input_context_addition_summary"),
  );
  if (addition) {
    inputLines.push(`追加文脈: ${addition}`);
  }

  const reasonLines = [];
  const reason = firstNonEmpty(
    pickString(decisionTrace, "reason_summary"),
    pickString(decisionSummary, "reason_summary"),
    pickString(summary, "reason_summary"),
  );
  if (reason) {
    reasonLines.push(`理由: ${reason}`);
  }
  const persona = pickString(decisionTrace, "persona_summary");
  if (persona) {
    reasonLines.push(`人格: ${persona}`);
  }
  const memorySummary = pickString(decisionTrace, "memory_summary");
  if (memorySummary) {
    reasonLines.push(`記憶集合: ${memorySummary}`);
  }

  const evidenceLines = [];
  const evidenceStatus = pickString(factResolution, "result_status");
  if (evidenceStatus) {
    evidenceLines.push(`状態: ${evidenceStatus}`);
  }
  const missingReason = pickString(factResolution, "missing_reason");
  if (missingReason) {
    evidenceLines.push(`未解決: ${missingReason}`);
  }
  const speechGuidance = pickString(factResolution, "speech_guidance");
  if (speechGuidance) {
    evidenceLines.push(`発話方針: ${speechGuidance}`);
  }

  const recallLines = [];
  for (const key of ["recall_summary", "selected_sections_summary", "pack_summary"]) {
    const value = pickString(recallTrace, key);
    if (value) {
      recallLines.push(value);
    }
  }

  const cognitiveLines = [];
  if (asObject(cognitiveObj.foreground_selection)) {
    cognitiveLines.push("前景化あり（詳細 JSON タブで確認）");
  }
  if (asObject(cognitiveObj.workspace_context_summary)) {
    cognitiveLines.push("候補盤面あり（詳細 JSON タブで確認）");
  }

  const worldLines = [];
  for (const key of ["summary", "world_state_summary", "sanitized_context_summary"]) {
    const value = pickString(worldTrace, key);
    if (value) {
      worldLines.push(value);
      break;
    }
  }

  const activityLines = [];
  for (const key of ["summary", "activity_summary"]) {
    const value = pickString(activityTrace, key);
    if (value) {
      activityLines.push(value);
      break;
    }
  }

  const memoryLines = [];
  for (const key of ["summary", "memory_update_summary", "postprocess_summary"]) {
    const value = pickString(memoryTrace, key);
    if (value) {
      memoryLines.push(value);
    }
  }

  return [
    createOverviewSection("この判断の結論", conclusion),
    createOverviewSection("入力と状況", inputLines),
    createOverviewSection("判断理由", reasonLines),
    createOverviewSection("根拠", evidenceLines),
    createOverviewSection("想起", recallLines),
    createOverviewSection("前景化と派生 view", cognitiveLines),
    createOverviewSection("世界状態", worldLines),
    createOverviewSection("活動", activityLines),
    createOverviewSection("記憶反映", memoryLines),
  ].filter(Boolean);
}

function renderOverview() {
  const container = element("overview-sections");
  if (!state.trace) {
    container.replaceChildren();
    const empty = document.createElement("p");
    empty.className = "cycle-empty";
    empty.textContent = "cycle を選択してください。";
    container.append(empty);
    return;
  }
  const sections = buildOverviewSections(state.trace, state.cognitive);
  if (!sections.length) {
    const empty = document.createElement("p");
    empty.className = "cycle-empty";
    empty.textContent = "表示できる要点がありません。段階または詳細 JSON を見てください。";
    container.replaceChildren(empty);
    return;
  }
  container.replaceChildren(...sections);
}

function renderStages() {
  const container = element("stage-sections");
  container.replaceChildren();
  if (!state.trace) {
    const empty = document.createElement("p");
    empty.className = "cycle-empty";
    empty.textContent = "cycle を選択してください。";
    container.append(empty);
    return;
  }
  for (const [key, label] of STAGE_ORDER) {
    const details = document.createElement("details");
    details.className = "cycle-stage";
    if (key === "decision_trace" || key === "result_trace") {
      details.open = true;
    }
    const summary = document.createElement("summary");
    summary.textContent = label;
    const pre = document.createElement("pre");
    pre.className = "cycle-json";
    pre.textContent = prettyJson(state.trace[key] || {});
    details.append(summary, pre);
    container.append(details);
  }
}

function renderJson() {
  element("json-trace").textContent = prettyJson(state.trace);
  element("json-cognitive").textContent = prettyJson(state.cognitive);
}

function renderDetailChrome(cycleId, summary) {
  element("selected-cycle-title").textContent = cycleId || "cycle 未選択";
  if (!summary) {
    element("selected-cycle-meta").textContent = "";
    return;
  }
  const failed = summary.failed === true ? "はい" : "いいえ";
  element("selected-cycle-meta").textContent = [
    `開始: ${formatTime(summary.started_at)}`,
    `きっかけ: ${labelTrigger(summary.trigger_kind)}`,
    `結果: ${labelResult(summary.result_kind)}`,
    `失敗: ${failed}`,
  ].join(" / ");
}

function renderCycleList() {
  const list = element("cycle-list");
  list.replaceChildren();
  if (!state.cycles.length) {
    const empty = document.createElement("p");
    empty.className = "cycle-empty";
    empty.textContent = "記録済みの判断はまだありません。";
    list.append(empty);
    return;
  }
  for (const cycle of state.cycles) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cycle-list-item";
    if (cycle.cycle_id === state.selectedCycleId) {
      button.classList.add("is-selected");
    }
    if (cycle.failed === true) {
      button.classList.add("is-failed");
    }
    button.dataset.cycleId = cycle.cycle_id;

    const time = document.createElement("div");
    time.className = "cycle-list-time";
    time.textContent = formatTime(cycle.started_at);

    const title = document.createElement("div");
    title.className = "cycle-list-title";
    title.textContent = `${labelTrigger(cycle.trigger_kind)} · ${labelResult(cycle.result_kind)}`;

    const body = document.createElement("div");
    body.className = "cycle-list-body";
    const input = displayValue(cycle.input_summary);
    const outcome = displayValue(cycle.outcome_summary);
    if (input && outcome) {
      body.textContent = `${input} → ${outcome}`;
    } else {
      body.textContent = input || outcome || "（要約なし）";
    }

    button.append(time, title, body);
    button.addEventListener("click", () => {
      selectCycle(cycle.cycle_id);
    });
    list.append(button);
  }
}

function clearDetail(message) {
  state.trace = null;
  state.cognitive = null;
  renderDetailChrome(state.selectedCycleId, null);
  element("overview-sections").replaceChildren();
  const empty = document.createElement("p");
  empty.className = "cycle-empty";
  empty.textContent = message;
  element("overview-sections").append(empty);
  element("stage-sections").replaceChildren(empty.cloneNode(true));
  element("json-trace").textContent = "";
  element("json-cognitive").textContent = "";
}

async function loadTrace(cycleId) {
  if (!cycleId || state.loadingTrace) {
    return;
  }
  state.loadingTrace = true;
  element("reload-trace").disabled = true;
  setStatus(`読み込み中… ${cycleId}`, "processing");
  try {
    const encoded = encodeURIComponent(cycleId);
    const [trace, cognitive] = await Promise.all([
      apiRequest(`/ui/api/inspection/cycles/${encoded}`),
      apiRequest(`/ui/api/inspection/cycles/${encoded}/cognitive-context`),
    ]);
    state.selectedCycleId = cycleId;
    state.trace = trace;
    state.cognitive = cognitive;
    const summary = asObject(trace.cycle_summary) || state.cycles.find((item) => item.cycle_id === cycleId) || null;
    renderDetailChrome(cycleId, summary);
    renderOverview();
    renderStages();
    renderJson();
    renderCycleList();
    setStatus(`表示中: ${cycleId}`);
    const url = new URL(window.location.href);
    url.searchParams.set("cycle_id", cycleId);
    window.history.replaceState({}, "", url);
  } catch (error) {
    clearDetail(error.message || "読み込みに失敗しました。");
    setStatus(error.message || "読み込みに失敗しました。", "error");
  } finally {
    state.loadingTrace = false;
    element("reload-trace").disabled = false;
  }
}

async function selectCycle(cycleId) {
  if (state.selectedCycleId === cycleId && state.trace) {
    return;
  }
  state.selectedCycleId = cycleId;
  renderCycleList();
  await loadTrace(cycleId);
}

async function loadCycleList({ preserveSelection }) {
  if (state.loadingList) {
    return;
  }
  state.loadingList = true;
  element("refresh-cycles").disabled = true;
  setStatus("一覧を読み込み中…", "processing");
  try {
    const data = await apiRequest(`/ui/api/inspection/cycle-summaries?limit=${CYCLE_LIST_LIMIT}`);
    state.cycles = Array.isArray(data.cycle_summaries) ? data.cycle_summaries : [];
    renderCycleList();

    const preferred = preserveSelection
      ? state.selectedCycleId || queryCycleId()
      : queryCycleId() || state.selectedCycleId;
    const inList = preferred && state.cycles.some((cycle) => cycle.cycle_id === preferred);
    if (inList) {
      await selectCycle(preferred);
      setStatus(`一覧 ${state.cycles.length} 件`);
      return;
    }
    if (preferred && !inList) {
      // 一覧に無い cycle でも詳細 API で開ける。
      state.selectedCycleId = preferred;
      renderCycleList();
      await loadTrace(preferred);
      setStatus(`一覧外の cycle を表示中（一覧 ${state.cycles.length} 件）`);
      return;
    }
    if (state.cycles.length) {
      await selectCycle(state.cycles[0].cycle_id);
      setStatus(`一覧 ${state.cycles.length} 件`);
      return;
    }
    state.selectedCycleId = null;
    clearDetail("記録済みの判断はまだありません。");
    setStatus("cycle がありません");
  } catch (error) {
    setStatus(error.message || "一覧の読み込みに失敗しました。", "error");
    clearDetail("一覧の読み込みに失敗しました。");
  } finally {
    state.loadingList = false;
    element("refresh-cycles").disabled = false;
  }
}

function bindUi() {
  element("refresh-cycles").addEventListener("click", () => {
    loadCycleList({ preserveSelection: true });
  });
  element("reload-trace").addEventListener("click", () => {
    if (!state.selectedCycleId) {
      setStatus("cycle を選択してください。", "error");
      return;
    }
    loadTrace(state.selectedCycleId);
  });
  for (const button of document.querySelectorAll(".cycle-tab")) {
    button.addEventListener("click", () => setActiveTab(button.dataset.tab));
  }
}

bindUi();
setActiveTab("overview");
loadCycleList({ preserveSelection: false });
