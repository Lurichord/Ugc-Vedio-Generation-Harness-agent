const SESSION_KEY = "ugc-intake-session-id";
const stages = ["narrative", "voice", "editorial", "asset", "timeline", "render"];
const stageLabels = {
  narrative: "脚本",
  voice: "声音",
  editorial: "画面规划",
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

function applyPayload(payload) {
  state.payload = payload;
  if (payload.session_id) {
    state.sessionId = payload.session_id;
    localStorage.setItem(SESSION_KEY, payload.session_id);
  }
  renderChat();
  renderGate();
  renderRail();
  syncProjectSelect();
  setBusy(payload.gate?.kind === "running");
  if (payload.gate?.kind === "running") startPolling();
  else stopPolling();
}

function setBusy(busy) {
  state.busy = busy;
  $("#sendButton").disabled = busy;
  $("#composerInput").disabled = busy;
  $("#continueButton").disabled = busy && state.payload?.gate?.kind === "running";
}

function transcript() {
  const payload = state.payload || {};
  const items = [
    ...(payload.messages || []).map((item) => ({
      role: item.role === "user" ? "user" : "agent",
      content: item.content,
      created_at: item.created_at || "",
    })),
    ...(payload.notices || []).map((item) => ({
      role: item.role === "user" ? "user" : "studio",
      content: item.content,
      created_at: item.created_at || "",
    })),
  ].filter((item) => item.content);
  items.sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)));
  return items;
}

function renderChat() {
  const root = $("#chatThread");
  const items = transcript();
  if (!items.length) {
    root.innerHTML = `<div class="chat-empty"><h2>先说你想做什么视频</h2><p>主题、观众、平台和时长说清楚后，我会开工。每做完一步都会停下来问你：可以继续，还是先改。</p></div>`;
    return;
  }
  root.innerHTML = items.map((item) => {
    const who = item.role === "user" ? "你" : item.role === "studio" ? "制作台" : "助手";
    return `<article class="bubble ${item.role}"><span class="bubble-meta">${who}</span>${escapeHtml(item.content)}</article>`;
  }).join("");
  if (state.busy && state.payload?.gate?.kind === "running") {
    root.insertAdjacentHTML("beforeend", `<article class="bubble studio thinking">正在生成，右侧会更新已完成内容…</article>`);
  }
  root.scrollTop = root.scrollHeight;
}

function renderGate() {
  const gate = state.payload?.gate;
  const bar = $("#gateBar");
  const show = gate && (gate.kind === "review" || gate.kind === "done" || gate.kind === "failed");
  bar.classList.toggle("hidden", !show);
  if (!show) return;
  $("#gateQuestion").textContent = gate.question || "";
  $("#continueButton").textContent = gate.kind === "done" ? "就这样" : "可以，继续下一步";
  $("#continueButton").classList.toggle("hidden", gate.kind === "failed");
}

function renderRail() {
  const production = state.payload?.production || { stages: [] };
  const hint = $("#railHint");
  const current = production.current_stage;
  if (!production.project_key) {
    hint.textContent = "开工之后，脚本、声音、镜头会写在这里。";
    $("#stageRail").innerHTML = `<p class="muted">还没有项目产物。</p>`;
    return;
  }
  hint.textContent = current ? `当前停在「${stageLabels[current] || current}」。` : "制作进行中。";
  const blocks = (production.stages || []).map((stage) => {
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
  script_segment: "口播",
  planned_beat: "规划",
  shot: "镜头",
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
  const key = state.payload?.project_key || "";
  if (key) $("#projectSelect").value = key;
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
  if (!text.trim() || !state.sessionId || state.busy) return;
  setBusy(true);
  $("#composerInput").value = "";
  try {
    applyPayload(await api(
      `/api/intake/sessions/${encodeURIComponent(state.sessionId)}/messages`,
      { method: "POST", body: JSON.stringify({ message: text }) },
    ));
  } catch (error) {
    toast(error.message);
    setBusy(false);
  }
}

async function continueProduction() {
  if (!state.sessionId) return;
  setBusy(true);
  try {
    applyPayload(await api(
      `/api/intake/sessions/${encodeURIComponent(state.sessionId)}/continue`,
      { method: "POST", body: "{}" },
    ));
  } catch (error) {
    toast(error.message);
    setBusy(false);
  }
}

function startPolling() {
  if (state.pollTimer) return;
  state.pollTimer = setInterval(async () => {
    if (!state.sessionId) return;
    try {
      applyPayload(await api(`/api/intake/sessions/${encodeURIComponent(state.sessionId)}`));
    } catch {
      stopPolling();
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
  try {
    applyPayload(await api(
      `/api/intake/sessions/${encodeURIComponent(state.sessionId)}/attach`,
      { method: "POST", body: JSON.stringify({ project_key: projectKey }) },
    ));
  } catch (error) {
    toast(error.message);
  }
}

async function newSession() {
  stopPolling();
  localStorage.removeItem(SESSION_KEY);
  state.sessionId = null;
  applyPayload(await api("/api/intake/sessions", { method: "POST" }));
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

async function openDrawer(kind) {
  const key = state.payload?.project_key;
  if (!key) {
    toast("还没有项目");
    return;
  }
  const title = kind === "timeline" ? "统一媒体时间线" : "Task 历史";
  $("#drawerTitle").textContent = title;
  const body = $("#drawerBody");
  body.innerHTML = "<p class='muted'>加载中…</p>";
  $("#drawer").showModal();
  try {
    if (kind === "tasks") {
      const data = await api(`/api/projects/${encodeURIComponent(key)}/tasks`);
      body.innerHTML = `<div class="task-list">${(data.chronological || []).map(taskMarkup).join("") || "<p class='muted'>还没有 Task。</p>"}</div>`;
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
