const SESSION_KEY = "ugc-intake-session-id";
const stages = ["narrative", "voice", "editorial", "references", "asset", "timeline", "render"];
const stageLabels = {
  narrative: "脚本",
  voice: "声音",
  editorial: "画面规划",
  references: "参考图",
  asset: "镜头 / 素材",
  timeline: "时间线",
  render: "成片",
};
const trackLabels = {
  beats: "Beat",
  audio: "声音",
  text: "口播文本",
  visuals: "画面素材",
  presentation: "展示方式",
  captions: "字幕",
  overlays: "标注",
};

const state = {
  sessionId: localStorage.getItem(SESSION_KEY),
  payload: null,
  projects: [],
  busy: false,
  pendingReply: false,
  requestId: 0,
  pollTimer: null,
  feedbackTarget: null,
  stageViews: {},
  timeline: null,
  playhead: 0,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, (c) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "'": "&#39;",
  '"': "&quot;",
}[c]));

const api = async (path, options = {}) => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败：${response.status}`);
  }
  return response.json();
};

const toast = (message) => {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 3200);
};

function nextRequest() {
  state.requestId += 1;
  return state.requestId;
}

function applyPayload(payload, requestId) {
  if (requestId !== undefined && requestId !== state.requestId) return;
  state.pendingReply = false;
  state.payload = payload;
  if (payload.session_id) {
    state.sessionId = payload.session_id;
    localStorage.setItem(SESSION_KEY, payload.session_id);
  }
  renderChat();
  renderGate();
  renderRail();
  syncProjectSelect();
  setBusy(false);
  if (payload.gate?.kind === "running") startPolling();
  else stopPolling();
}

function setBusy(busy) {
  state.busy = busy;
  $("#sendButton").disabled = busy;
  $("#composerInput").disabled = busy;
  $("#continueButton").disabled = busy || state.payload?.gate?.kind === "running";
}

function transcript() {
  const payload = state.payload || {};
  return (payload.messages || [])
    .map((item) => ({
      role: item.role === "user" ? "user" : "agent",
      content: item.content,
      created_at: item.created_at || "",
    }))
    .filter((item) => item.content);
}

function renderChat() {
  const root = $("#chatThread");
  const items = transcript();
  if (!items.length) {
    root.innerHTML = `<div class="chat-empty"><h2>先说你想做什么视频</h2><p>主题、观众、平台和时长说清楚后，我会开工。每做完一步都会停下来问你：可以继续，还是先改。</p></div>`;
    return;
  }
  const bubbles = items.map((item) => {
    const who = item.role === "user" ? "你" : item.role === "studio" ? "制作台" : "助手";
    const variant = item.role === "user" ? "user" : item.role === "studio" ? "notice" : "agent";
    return `<article class="bubble ${variant}"><span class="bubble-meta">${who}</span>${escapeHtml(item.content)}</article>`;
  });
  const thinking = thinkingCopy();
  if (thinking) {
    bubbles.push(`<article class="bubble notice thinking">${thinking}</article>`);
  }
  root.innerHTML = `<div class="chat-log">${bubbles.join("")}</div>`;
  if (root.scrollHeight - root.clientHeight > 8) {
    root.scrollTop = root.scrollHeight;
  }
}

function thinkingCopy() {
  if (state.pendingReply) return "正在想…";
  return "";
}

function renderGate() {
  const gate = state.payload?.gate;
  const bar = $("#gateBar");
  const show = gate && (gate.kind === "review" || gate.kind === "done" || gate.kind === "failed");
  bar.classList.toggle("hidden", !show);
  if (!show) return;
  $("#gateQuestion").textContent = gate.question || "";
  const nextLabel = stageLabels[gate.next_stage];
  $("#continueButton").textContent = gate.kind === "done"
    ? "就这样"
    : nextLabel
      ? `可以，继续做${nextLabel}`
      : "可以，继续下一步";
  $("#continueButton").classList.toggle("hidden", gate.kind === "failed");
}

function renderRail() {
  const production = state.payload?.production || { stages: [] };
  const hint = $("#railHint");
  const current = production.current_stage;
  const pipeline = production.pipeline || [];
  const visible = (production.stages || []).filter((stage) => stage.required);
  if (!production.project_key) {
    hint.textContent = "开工之后，会按体裁显示对应步骤。";
    $("#stageRail").innerHTML = `<p class="muted">还没有项目产物。</p>`;
    return;
  }
  const flow = pipeline.length
    ? pipeline.map((stage) => stageLabels[stage] || stage).join(" → ")
    : "";
  const here = current ? `当前停在「${stageLabels[current] || current}」` : "制作进行中";
  hint.textContent = flow ? `${flow}。${here}。` : `${here}。`;
  const blocks = visible.map((stage) => {
    const skipped = !stage.required;
    const isCurrent = stage.stage === current;
    const currentClass = isCurrent ? "current" : "";
    const items = stage.items || [];
    const body = renderStageItems(items, skipped, isCurrent);
    return `<section class="stage-block ${skipped ? "skipped" : ""} ${currentClass}">
      <div class="stage-block-head">
        <strong>${escapeHtml(stage.label || stageLabels[stage.stage])}</strong>
        <span class="status-pill ${stage.status}">${escapeHtml(stage.status)}${stage.user_approved ? " · 已确认" : ""}</span>
      </div>
      ${body}
    </section>`;
  }).join("");
  $("#stageRail").innerHTML = blocks;
}

const kindLabels = {
  utterance: "口播",
  beat: "规划",
  shot: "镜头",
  audio: "配音",
  clip: "剪辑",
  script_segment: "口播",
  planned_beat: "规划",
  audio_segment: "配音",
  voice_segment: "声音设计",
  realized_beat: "实际 Beat",
  visual_requirement: "画面需求",
  claim: "主张",
  asset: "素材",
  shot_video: "镜头视频",
  visual_resolution: "素材决策",
  timeline_clip: "剪辑",
  caption: "字幕",
  rendered_media: "成片",
  cast: "人物",
  character: "人物",
  prop: "道具",
  place: "场景",
  section: "段落",
  scene: "场",
  step: "步骤",
  action: "动作",
};

function renderStageItems(items, skipped, isCurrent) {
  if (!items.length) {
    return `<p class="muted">${skipped ? "此路线不需要这一步。" : "还没有产物。"}</p>`;
  }
  const groups = [];
  const indexByBeat = new Map();
  for (const item of items) {
    const key = item.beat_id || "_";
    if (!indexByBeat.has(key)) {
      indexByBeat.set(key, groups.length);
      groups.push({ beatId: key, items: [] });
    }
    groups[indexByBeat.get(key)].items.push(item);
  }
  const list = `<div class="item-list">${groups.map((group) => {
    const label = group.items.length > 1
      ? `<p class="prod-group-label">${escapeHtml(group.beatId)}</p>`
      : "";
    return `<div class="prod-group">${label}${group.items.map(itemMarkup).join("")}</div>`;
  }).join("")}</div>`;
  if (!isCurrent && items.length > 6) {
    return `<details class="stage-more"><summary>共 ${items.length} 条，点开查看</summary>${list}</details>`;
  }
  return list;
}

function itemMarkup(item) {
  const kind = kindLabels[item.kind] || item.kind;
  return `<article class="prod-item">
    <p class="prod-kind">${escapeHtml(kind)}</p>
    <h3>${escapeHtml(item.title || item.kind)}</h3>
    ${mediaMarkup(item)}
    <p>${escapeHtml(item.summary || "")}</p>
  </article>`;
}

function mediaMarkup(item) {
  if (!item.media_url) return "";
  const path = item.media_url.toLowerCase();
  if (/\.(png|jpg|jpeg|webp|gif)$/.test(path)) {
    return `<img class="artifact-media visual-media" loading="lazy" src="${escapeHtml(item.media_url)}" alt="${escapeHtml(item.title || "")}" />`;
  }
  if (/\.(mp4|webm|mov)$/.test(path)) {
    return `<video class="artifact-media visual-media" controls preload="metadata" src="${escapeHtml(item.media_url)}"></video>`;
  }
  if (/\.(wav|mp3|m4a|ogg)$/.test(path)) {
    return `<audio controls preload="none" src="${escapeHtml(item.media_url)}"></audio>`;
  }
  return "";
}

function syncProjectSelect() {
  const select = $("#projectSelect");
  if (!select) return;
  select.value = state.payload?.project_key || "";
}

async function ensureSession() {
  if (state.sessionId) {
    try {
      applyPayload(await api(`/api/intake/sessions/${encodeURIComponent(state.sessionId)}`));
      return;
    } catch {
      localStorage.removeItem(SESSION_KEY);
      state.sessionId = null;
    }
  }
  applyPayload(await api("/api/intake/sessions", { method: "POST" }));
}

async function loadProjects() {
  state.projects = await api("/api/projects");
  $("#projectSelect").innerHTML = `<option value="">打开已有项目</option>${
    state.projects.map((item) => `<option value="${escapeHtml(item.path_key)}">${escapeHtml(item.project_name)}</option>`).join("")
  }`;
  syncProjectSelect();
}

async function sendMessage(text) {
  const trimmed = text.trim();
  if (!trimmed || !state.sessionId || state.busy) return;
  const previous = state.payload;
  const now = new Date().toISOString();
  state.payload = {
    ...previous,
    messages: [...(previous?.messages || []), { role: "user", content: trimmed, created_at: now }],
  };
  const requestId = nextRequest();
  state.pendingReply = true;
  setBusy(true);
  $("#composerInput").value = "";
  renderChat();
  try {
    applyPayload(await api(
      `/api/intake/sessions/${encodeURIComponent(state.sessionId)}/messages`,
      { method: "POST", body: JSON.stringify({ message: trimmed }) },
    ), requestId);
  } catch (error) {
    if (requestId !== state.requestId) return;
    state.payload = previous;
    state.pendingReply = false;
    setBusy(false);
    renderChat();
    $("#composerInput").value = trimmed;
    toast(error.message);
  }
}

async function continueProduction() {
  if (!state.sessionId || state.busy) return;
  if (state.payload?.gate?.kind === "running") return;
  const requestId = nextRequest();
  setBusy(true);
  try {
    applyPayload(await api(
      `/api/intake/sessions/${encodeURIComponent(state.sessionId)}/continue`,
      { method: "POST", body: "{}" },
    ), requestId);
  } catch (error) {
    if (requestId !== state.requestId) return;
    setBusy(false);
    toast(error.message);
  }
}

function startPolling() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(async () => {
    if (!state.sessionId) return;
    const sessionId = state.sessionId;
    const requestId = state.requestId;
    try {
      const payload = await api(`/api/intake/sessions/${encodeURIComponent(sessionId)}`);
      if (requestId !== state.requestId || payload.session_id !== state.sessionId) return;
      applyPayload(payload, requestId);
    } catch {
      if (requestId === state.requestId) stopPolling();
    }
  }, 2000);
}

function stopPolling() {
  if (!state.pollTimer) return;
  clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function attachProject(projectKey) {
  if (!projectKey || !state.sessionId) return;
  if (state.payload?.project_key === projectKey) return;
  const select = $("#projectSelect");
  const requestId = nextRequest();
  stopPolling();
  state.pendingReply = false;
  if (select) select.disabled = true;
  try {
    applyPayload(await api(
      `/api/intake/sessions/${encodeURIComponent(state.sessionId)}/attach`,
      { method: "POST", body: JSON.stringify({ project_key: projectKey }) },
    ), requestId);
  } catch (error) {
    if (requestId === state.requestId) {
      toast(error.message);
      syncProjectSelect();
    }
  } finally {
    if (select) select.disabled = false;
  }
}

async function newSession() {
  const requestId = nextRequest();
  stopPolling();
  state.pendingReply = false;
  localStorage.removeItem(SESSION_KEY);
  state.sessionId = null;
  applyPayload(await api("/api/intake/sessions", { method: "POST" }), requestId);
}

function formatTime(ms = 0) {
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  const milli = Math.floor(ms % 1000);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milli).padStart(3, "0")}`;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
}

function taskMarkup(item) {
  const when = item.recorded_at || item.created_at;
  const title = item.task_kind ? `${item.task_kind} · ${item.task_id}` : item.event_type;
  const status = item.status || item.event_type;
  return `<article class="task-card"><time class="task-time">${escapeHtml(formatDate(when))}</time><div><strong>${escapeHtml(title || "Task")}</strong><div class="task-meta">${escapeHtml(item.goal || item.message || "")}</div></div><span class="status-pill ${status}">${escapeHtml(status || "")}</span></article>`;
}

const GRAPH_COLUMNS = [
  { id: "world", label: "人物 / 场景", kinds: ["cast", "character", "prop", "place"] },
  { id: "structure", label: "结构", kinds: ["section", "scene", "step", "action"] },
  { id: "beat", label: "Beat", kinds: ["beat", "planned_beat", "realized_beat"] },
  { id: "script", label: "口播", kinds: ["utterance", "script_segment"] },
  { id: "voice", label: "声音", kinds: ["audio", "audio_segment", "voice_segment"] },
  { id: "visual", label: "镜头", kinds: ["shot", "visual_requirement", "asset", "shot_video", "visual_resolution"] },
  { id: "cut", label: "剪辑", kinds: ["clip", "timeline_clip", "caption", "timeline_overlay", "timeline_transform"] },
  { id: "out", label: "成片", kinds: ["rendered_media", "render"] },
];
const GRAPH_COL_W = 196;
const GRAPH_NODE_W = 168;
const GRAPH_NODE_H = 72;
const GRAPH_GAP = 12;
const GRAPH_HEADER = 28;
const GRAPH_PAD = 18;

function graphNodeLabel(node) {
  const title = (node.title || "").trim();
  if (title) return title;
  const ref = node.ref || "";
  return ref.includes(":") ? ref.slice(ref.indexOf(":") + 1) : ref;
}

function compareGraphNodes(a, b) {
  const ao = Number(a.order);
  const bo = Number(b.order);
  const aHas = Number.isFinite(ao);
  const bHas = Number.isFinite(bo);
  if (aHas && bHas && ao !== bo) return ao - bo;
  if (aHas !== bHas) return aHas ? -1 : 1;
  return String(a.ref).localeCompare(String(b.ref));
}

function layoutGraph(nodes) {
  const columns = GRAPH_COLUMNS.map((column) => ({ ...column, nodes: [] }));
  const other = { id: "other", label: "其他", kinds: [], nodes: [] };
  const byKind = new Map();
  for (const column of columns) {
    for (const kind of column.kinds) byKind.set(kind, column);
  }
  for (const node of nodes) {
    (byKind.get(node.kind) || other).nodes.push(node);
  }
  for (const column of columns) {
    if (column.id === "visual") {
      column.nodes.sort(compareGraphNodes);
    } else {
      column.nodes.sort((a, b) => String(a.ref).localeCompare(String(b.ref)));
    }
  }
  other.nodes.sort((a, b) => String(a.ref).localeCompare(String(b.ref)));
  const used = columns.filter((column) => column.nodes.length);
  if (other.nodes.length) used.push(other);
  const positions = new Map();
  used.forEach((column, columnIndex) => {
    const x = GRAPH_PAD + columnIndex * GRAPH_COL_W;
    column.nodes.forEach((node, row) => {
      positions.set(node.ref, {
        x,
        y: GRAPH_PAD + GRAPH_HEADER + row * (GRAPH_NODE_H + GRAPH_GAP),
        node,
      });
    });
  });
  const tallest = Math.max(1, ...used.map((column) => column.nodes.length));
  return {
    columns: used,
    positions,
    width: GRAPH_PAD * 2 + used.length * GRAPH_COL_W,
    height: GRAPH_PAD * 2 + GRAPH_HEADER + tallest * (GRAPH_NODE_H + GRAPH_GAP),
  };
}

function graphEdgePath(x1, y1, x2, y2) {
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}

function graphVersionLabel(data) {
  const selected = data.history_version;
  if (selected == null) return `当前 · graph v${data.graph_version ?? 0}`;
  return `历史 ${selected} · graph v${data.graph_version ?? 0}`;
}

function renderGraphVersions(data) {
  const history = data.history || [];
  if (!history.length) return "";
  const selected = data.history_version;
  const latest = history[history.length - 1]?.version;
  const active = selected == null ? latest : selected;
  const chips = history.map((item) => {
    const isActive = Number(item.version) === Number(active);
    return `<button type="button" class="graph-version${isActive ? " is-active" : ""}" data-graph-version="${escapeHtml(String(item.version))}">${escapeHtml(String(item.version))}</button>`;
  }).join("");
  return `<div class="graph-versions" role="tablist" aria-label="依赖图历史">${chips}</div>`;
}

function renderGraph(data) {
  const nodes = data.nodes || [];
  const versionBar = renderGraphVersions(data);
  if (!nodes.length) {
    return `<div class="graph-panel">${versionBar}<p class='muted'>还没有依赖图。开工写出脚本后会出现。</p></div>`;
  }
  const layout = layoutGraph(nodes);
  const cy = GRAPH_NODE_H / 2;
  const edges = [];
  for (const node of nodes) {
    for (const parent of node.depends_on || []) {
      const from = layout.positions.get(parent);
      const to = layout.positions.get(node.ref);
      if (!from || !to) continue;
      edges.push({
        from: parent,
        to: node.ref,
        d: graphEdgePath(from.x + GRAPH_NODE_W, from.y + cy, to.x, to.y + cy),
      });
    }
  }
  const labels = layout.columns.map((column, index) => (
    `<span class="graph-col-label" style="left:${GRAPH_PAD + index * GRAPH_COL_W}px;width:${GRAPH_NODE_W}px">${escapeHtml(column.label)}</span>`
  )).join("");
  const nodeButtons = nodes.map((node) => {
    const pos = layout.positions.get(node.ref);
    const title = graphNodeLabel(node);
    const preview = (node.summary || "").trim();
    const second = preview && preview !== title ? preview : "";
    return `<button type="button" class="graph-node ${escapeHtml(node.status || "current")}${node.locked ? " is-locked" : ""}" data-ref="${escapeHtml(node.ref)}" style="left:${pos.x}px;top:${pos.y}px">
      <small>${escapeHtml(kindLabels[node.kind] || node.kind)}</small>
      <strong>${escapeHtml(title)}</strong>
      ${second ? `<span class="graph-preview">${escapeHtml(second)}</span>` : ""}
    </button>`;
  }).join("");
  const paths = edges.map((edge) => (
    `<path data-from="${escapeHtml(edge.from)}" data-to="${escapeHtml(edge.to)}" d="${edge.d}" fill="none"></path>`
  )).join("");
  const kindLegend = Object.entries(data.kinds || {}).map(([kind, count]) => (
    `<span>${escapeHtml(kindLabels[kind] || kind)} ${count}</span>`
  )).join("");
  return `<div class="graph-panel">
    ${renderGraphVersions(data)}
    <div class="graph-meta">
      <span>${escapeHtml(graphVersionLabel(data))} · ${nodes.length} 个节点</span>
      <div class="graph-legend">${kindLegend}</div>
    </div>
    <div class="graph-workspace">
      <div class="graph-scroll">
        <div class="graph-board" style="width:${layout.width}px;height:${layout.height}px">
          <svg class="graph-edges" width="${layout.width}" height="${layout.height}" viewBox="0 0 ${layout.width} ${layout.height}">${paths}</svg>
          ${labels}
          ${nodeButtons}
        </div>
      </div>
      <aside class="graph-inspector" id="graphInspector">
        <p class="muted">点一个节点，看这段讲什么、依赖谁。</p>
      </aside>
    </div>
  </div>`;
}

function graphInspectorMarkup(node) {
  if (!node) return `<p class="muted">点一个节点，看这段讲什么、依赖谁。</p>`;
  const list = (refs) => refs.length
    ? refs.map((ref) => `<button type="button" class="graph-link" data-ref="${escapeHtml(ref)}">${escapeHtml(ref)}</button>`).join("")
    : `<span class="muted">无</span>`;
  const details = (node.details || []).map((item) => (
    `<div class="graph-detail"><dt>${escapeHtml(item.label)}</dt><dd>${escapeHtml(item.text)}</dd></div>`
  )).join("");
  const title = (node.title || "").trim() || node.ref;
  return `<p class="eyebrow">${escapeHtml(kindLabels[node.kind] || node.kind)}</p>
    <h3>${escapeHtml(title)}</h3>
    <code class="graph-ref">${escapeHtml(node.ref)}</code>
    <p class="graph-inspector-status"><span class="status-pill ${escapeHtml(node.status || "")}">${escapeHtml(node.status || "")}</span>${node.locked ? "<span class='status-pill'>已锁定</span>" : ""}</p>
    ${details || `<p class="graph-body">${escapeHtml(node.summary || "还没有写入这段内容。")}</p>`}
    <small>依赖</small>
    <div class="graph-ref-list">${list(node.depends_on || [])}</div>
    <small>被依赖</small>
    <div class="graph-ref-list">${list(node.dependents || [])}</div>`;
}

function bindGraph(root, data, projectKey) {
  const panel = root.querySelector(".graph-panel") || root;
  const byRef = new Map((data.nodes || []).map((node) => [node.ref, node]));
  const inspector = root.querySelector("#graphInspector");
  const select = (ref) => {
    const node = byRef.get(ref) || null;
    const related = new Set(node ? [node.ref, ...(node.depends_on || []), ...(node.dependents || [])] : []);
    root.querySelectorAll(".graph-node").forEach((button) => {
      const id = button.dataset.ref;
      button.classList.toggle("is-focus", Boolean(node) && id === node.ref);
      button.classList.toggle("is-related", Boolean(node) && related.has(id) && id !== node.ref);
      button.classList.toggle("is-dim", Boolean(node) && !related.has(id));
    });
    root.querySelectorAll(".graph-edges path").forEach((path) => {
      const active = Boolean(node) && (path.dataset.from === node.ref || path.dataset.to === node.ref);
      path.classList.toggle("is-active", active);
      path.classList.toggle("is-dim", Boolean(node) && !active);
    });
    if (inspector) inspector.innerHTML = graphInspectorMarkup(node);
  };
  panel.addEventListener("click", async (event) => {
    const versionButton = event.target.closest("[data-graph-version]");
    if (versionButton && projectKey) {
      event.preventDefault();
      const history = data.history || [];
      const latest = history[history.length - 1]?.version;
      const version = Number(versionButton.dataset.graphVersion);
      const query = version === Number(latest) ? "" : `?version=${encodeURIComponent(String(version))}`;
      try {
        const next = await api(`/api/projects/${encodeURIComponent(projectKey)}/graph${query}`);
        root.innerHTML = renderGraph(next);
        bindGraph(root, next, projectKey);
      } catch (error) {
        toast(error.message);
      }
      return;
    }
    const target = event.target.closest("[data-ref]");
    if (target?.dataset.ref) {
      select(target.dataset.ref);
      return;
    }
    if (!event.target.closest(".graph-inspector")) select(null);
  });
}

async function openDrawer(kind) {
  const key = state.payload?.project_key;
  if (!key) {
    toast("还没有项目");
    return;
  }
  const titles = {
    timeline: "统一媒体时间线",
    tasks: "Task 历史",
    graph: "依赖图",
  };
  $("#drawerTitle").textContent = titles[kind] || "详情";
  const body = $("#drawerBody");
  body.innerHTML = "<p class='muted'>加载中…</p>";
  $("#drawer").showModal();
  try {
    if (kind === "tasks") {
      const data = await api(`/api/projects/${encodeURIComponent(key)}/tasks`);
      body.innerHTML = `<div class="task-list">${(data.chronological || []).map(taskMarkup).join("") || "<p class='muted'>还没有 Task。</p>"}</div>`;
      return;
    }
    if (kind === "graph") {
      const data = await api(`/api/projects/${encodeURIComponent(key)}/graph`);
      body.innerHTML = renderGraph(data);
      bindGraph(body, data, key);
      return;
    }
    const timeline = await api(`/api/projects/${encodeURIComponent(key)}/timeline`);
    const duration = Math.max(1, timeline.duration_ms);
    body.innerHTML = `<div class="timeline-panel panel" style="box-shadow:none">
      <div class="timeline-ruler">${Array.from({ length: 7 }, (_, index) => `<span class="tick" style="left:${index / 6 * 100}%">${formatTime(duration * index / 6).slice(0, -4)}</span>`).join("")}</div>
      <div class="timeline-tracks">${Object.entries(timeline.tracks || {}).map(([track, items]) => `<div class="track-row"><div class="track-label">${trackLabels[track] || track}</div><div class="track-canvas">${(items || []).map((item) => `<button class="timeline-item" style="left:${item.start_ms / duration * 100}%;width:${Math.max(0.7, (item.end_ms - item.start_ms) / duration * 100)}%">${escapeHtml(item.label)}</button>`).join("")}</div></div>`).join("")}</div>
    </div>`;
  } catch (error) {
    body.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

$("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  await sendMessage($("#composerInput").value);
});

$("#composerInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});

$("#continueButton").addEventListener("click", continueProduction);
$("#reviseButton").addEventListener("click", () => {
  $("#gateBar").classList.add("hidden");
  $("#composerInput").focus();
  $("#composerInput").placeholder = "说说要改哪一段、怎么改";
});
$("#newSessionButton").addEventListener("click", () => newSession().catch((error) => toast(error.message)));
$("#projectSelect").addEventListener("change", (event) => attachProject(event.target.value));
$("#projectSelect").addEventListener("input", (event) => attachProject(event.target.value));
$$("[data-drawer]").forEach((node) => node.addEventListener("click", () => openDrawer(node.dataset.drawer)));
$$("[data-close-drawer]").forEach((node) => node.addEventListener("click", () => $("#drawer").close()));
$$("[data-close-dialog]").forEach((node) => node.addEventListener("click", () => $("#feedbackDialog").close()));

async function boot() {
  await loadProjects().catch((error) => toast(error.message));
  await ensureSession().catch((error) => toast(error.message));
}

boot();
