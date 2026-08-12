// ログ専用画面。/ui/api/logs/stream を購読し、CocoroConsole ログビューアー相当の表示を行う。
// token はブラウザへ渡さず、同一 origin の server-held 認可だけを使う。

const MAX_DISPLAYED_LOGS = 200;
const LEVEL_PRIORITY = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
};

const state = {
  allLogs: [],
  pendingLogs: [],
  componentSelected: new Map(),
  levelFilter: "INFO",
  socket: null,
  reconnectTimer: null,
  unloading: false,
  componentPopupOpen: false,
};

function element(id) {
  const node = document.getElementById(id);
  if (!node) {
    throw new Error(`missing element: ${id}`);
  }
  return node;
}

function websocketUrl(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

function normalizeLevel(level) {
  const normalized = String(level || "INFO").trim().toUpperCase();
  if (normalized === "WARN") {
    return "WARNING";
  }
  if (normalized in LEVEL_PRIORITY) {
    return normalized;
  }
  return "INFO";
}

function normalizeComponent(component) {
  const value = String(component || "").trim();
  return value || "(unknown)";
}

function formatTime(ts) {
  if (typeof ts !== "string" || !ts) {
    return "--:--:--";
  }
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) {
    return ts.slice(11, 19) || ts;
  }
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  const millis = String(date.getMilliseconds()).padStart(3, "0");
  return `${hours}:${minutes}:${seconds}.${millis}`;
}

function isAutoScrollEnabled() {
  return element("auto-scroll").checked;
}

function passesFilters(log) {
  if (state.levelFilter) {
    const logPriority = LEVEL_PRIORITY[log.level] ?? -1;
    const filterPriority = LEVEL_PRIORITY[state.levelFilter] ?? 0;
    if (logPriority < filterPriority) {
      return false;
    }
  }
  if (state.componentSelected.size > 0 && !state.componentSelected.get(log.component)) {
    return false;
  }
  return true;
}

function ensureComponent(component) {
  if (state.componentSelected.has(component)) {
    return false;
  }
  // 新規コンポーネントは既定で表示対象にする。
  state.componentSelected.set(component, true);
  return true;
}

function updateComponentSummary() {
  const total = state.componentSelected.size;
  let selected = 0;
  for (const enabled of state.componentSelected.values()) {
    if (enabled) {
      selected += 1;
    }
  }
  element("component-filter-summary").textContent = `${selected} / ${total} 選択`;
}

function renderComponentFilters() {
  const list = element("component-filter-list");
  list.replaceChildren();
  const components = Array.from(state.componentSelected.keys()).sort((a, b) => a.localeCompare(b, "ja"));
  for (const component of components) {
    const label = document.createElement("label");
    label.className = "log-component-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.componentSelected.get(component) === true;
    checkbox.addEventListener("change", () => {
      state.componentSelected.set(component, checkbox.checked);
      updateComponentSummary();
      renderLogRows({ preserveScroll: !isAutoScrollEnabled() });
    });
    const text = document.createElement("span");
    text.textContent = component;
    text.title = component;
    label.append(checkbox, text);
    list.append(label);
  }
  updateComponentSummary();
}

function setConnectionStatus(text, kind = "") {
  const node = element("connection-status");
  node.textContent = text;
  node.className = kind ? `status ${kind}` : "status";
}

function setStatusText(text) {
  element("status-text").textContent = text;
}

function updateLogCount() {
  const total = state.allLogs.length;
  const visible = state.allLogs.filter(passesFilters).length;
  element("log-count").textContent = (
    total === visible
      ? `総件数: ${total}`
      : `表示中: ${visible} / 総件数: ${total}`
  );
}

function createLogRow(log) {
  const row = document.createElement("tr");
  row.className = `log-row log-level-${log.level.toLowerCase()}`;
  row.dataset.component = log.component;

  const timeCell = document.createElement("td");
  timeCell.className = "log-col-time";
  timeCell.textContent = formatTime(log.ts);

  const levelCell = document.createElement("td");
  levelCell.className = "log-col-level";
  levelCell.textContent = log.level;

  const componentCell = document.createElement("td");
  componentCell.className = "log-col-component";
  componentCell.textContent = log.component;
  componentCell.title = log.component;

  const messageCell = document.createElement("td");
  messageCell.className = "log-col-message";
  messageCell.title = log.msg;
  const messageText = document.createElement("div");
  messageText.className = "log-message-text";
  messageText.textContent = log.msg;
  messageCell.append(messageText);

  row.append(timeCell, levelCell, componentCell, messageCell);
  return row;
}

function renderLogRows({ preserveScroll = false } = {}) {
  const scroll = element("log-scroll");
  const savedTop = scroll.scrollTop;
  const tbody = element("log-rows");
  const fragment = document.createDocumentFragment();
  for (const log of state.allLogs) {
    if (!passesFilters(log)) {
      continue;
    }
    fragment.append(createLogRow(log));
  }
  tbody.replaceChildren(fragment);
  updateLogCount();
  if (preserveScroll) {
    scroll.scrollTop = savedTop;
    return;
  }
  if (isAutoScrollEnabled()) {
    scroll.scrollTop = scroll.scrollHeight;
  }
}

function trimAllLogs() {
  while (state.allLogs.length > MAX_DISPLAYED_LOGS) {
    state.allLogs.shift();
  }
}

function appendLogsToView(logs, { scrollToLatest }) {
  if (!logs.length) {
    return;
  }
  let componentsChanged = false;
  for (const log of logs) {
    if (ensureComponent(log.component)) {
      componentsChanged = true;
    }
    state.allLogs.push(log);
  }
  trimAllLogs();
  if (componentsChanged) {
    renderComponentFilters();
  }
  renderLogRows({ preserveScroll: !scrollToLatest });
  const latest = logs[logs.length - 1];
  setStatusText(`最新ログ: ${formatTime(latest.ts)} [${latest.level}] ${latest.component}`);
}

function parseLogEntry(entry) {
  if (!entry || typeof entry !== "object") {
    return null;
  }
  return {
    ts: typeof entry.ts === "string" ? entry.ts : "",
    level: normalizeLevel(entry.level),
    component: normalizeComponent(entry.logger),
    msg: typeof entry.msg === "string" ? entry.msg : String(entry.msg ?? ""),
  };
}

function ingestPayload(raw) {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return;
  }

  const entries = Array.isArray(payload) ? payload : [payload];
  const logs = [];
  for (const entry of entries) {
    const log = parseLogEntry(entry);
    if (log) {
      logs.push(log);
    }
  }
  if (!logs.length) {
    return;
  }

  if (!isAutoScrollEnabled()) {
    state.pendingLogs.push(...logs);
    while (state.pendingLogs.length > MAX_DISPLAYED_LOGS) {
      state.pendingLogs.shift();
    }
    setStatusText("自動スクロール停止中");
    return;
  }

  appendLogsToView(logs, { scrollToLatest: true });
}

function connectLogStream() {
  if (state.unloading || state.socket) {
    return;
  }
  const socket = new WebSocket(websocketUrl("/ui/api/logs/stream"));
  state.socket = socket;
  setConnectionStatus("接続中", "processing");

  socket.addEventListener("open", () => {
    setConnectionStatus("接続済み");
    setStatusText("ログ受信中");
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data !== "string") {
      return;
    }
    ingestPayload(event.data);
  });
  socket.addEventListener("error", () => socket.close());
  socket.addEventListener("close", () => {
    if (state.socket === socket) {
      state.socket = null;
    }
    if (state.unloading) {
      return;
    }
    setConnectionStatus("再接続中", "processing");
    window.clearTimeout(state.reconnectTimer);
    state.reconnectTimer = window.setTimeout(connectLogStream, 3000);
  });
}

function clearLogs() {
  state.allLogs = [];
  state.pendingLogs = [];
  state.componentSelected.clear();
  renderComponentFilters();
  renderLogRows();
  setStatusText("ログクリア");
}

function onAutoScrollChanged() {
  if (!isAutoScrollEnabled()) {
    setStatusText("自動スクロール停止中");
    return;
  }
  if (state.pendingLogs.length === 0) {
    setStatusText("自動スクロール再開");
    renderLogRows({ preserveScroll: false });
    return;
  }
  const pending = state.pendingLogs.splice(0, state.pendingLogs.length);
  appendLogsToView(pending, { scrollToLatest: true });
}

function setComponentPopupOpen(open) {
  state.componentPopupOpen = open;
  const popup = element("component-filter-popup");
  const toggle = element("component-filter-toggle");
  popup.hidden = !open;
  toggle.setAttribute("aria-expanded", open ? "true" : "false");
}

function bindUi() {
  element("level-filter").addEventListener("change", (event) => {
    state.levelFilter = event.target.value || "";
    renderLogRows({ preserveScroll: !isAutoScrollEnabled() });
  });
  element("clear-logs").addEventListener("click", clearLogs);
  element("auto-scroll").addEventListener("change", onAutoScrollChanged);
  element("component-filter-toggle").addEventListener("click", (event) => {
    event.stopPropagation();
    setComponentPopupOpen(!state.componentPopupOpen);
  });
  document.addEventListener("click", (event) => {
    if (!state.componentPopupOpen) {
      return;
    }
    const root = element("component-filter-popup").parentElement;
    if (root && !root.contains(event.target)) {
      setComponentPopupOpen(false);
    }
  });
  window.addEventListener("beforeunload", () => {
    state.unloading = true;
    window.clearTimeout(state.reconnectTimer);
    state.socket?.close();
  });
}

bindUi();
updateComponentSummary();
connectLogStream();
