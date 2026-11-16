const PROJECT_KEY = "ugc-project-id";
const PANE_KEY = "ugc-studio-pane";

const ui = {
  projectSelect: document.getElementById("projectSelect"),
  newSessionButton: document.getElementById("newSessionButton"),
  chatThread: document.getElementById("chatThread"),
  composer: document.getElementById("composer"),
  composerInput: document.getElementById("composerInput"),
  sendButton: document.getElementById("sendButton"),
  stateBoard: document.getElementById("stateBoard"),
  runBoard: document.getElementById("runBoard"),
  toast: document.getElementById("toast"),
};

const app = {
  projectId: localStorage.getItem(PROJECT_KEY),
  pane: sessionStorage.getItem(PANE_KEY) || "chat",
  sending: false,
  interrupt: null,
};

let toastTimer = 0;

function toast(text, ms = 8000) {
  ui.toast.textContent = text;
  ui.toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => ui.toast.classList.remove("show"), ms);
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch {
    throw new Error("后端连不上。先确认 studio 服务还在跑，再试一次。");
  }
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function setPane(name) {
  app.pane = name;
  sessionStorage.setItem(PANE_KEY, name);
  document.querySelectorAll(".nav-tab").forEach((tab) => {
    const active = tab.dataset.pane === name;
    tab.classList.toggle("active", active);
    if (active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  document.getElementById("pane-chat").classList.toggle("hidden", name !== "chat");
  document.getElementById("pane-state").classList.toggle("hidden", name !== "state");
  document.getElementById("pane-runs").classList.toggle("hidden", name !== "runs");
  if (name === "state") loadState();
  if (name === "runs") loadRun();
}

function projectLabel(project) {
  return project.topic || project.project_id.slice(0, 8);
}

async function refreshProjects() {
  const data = await api("/api/projects");
  ui.projectSelect.replaceChildren();
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "打开已有项目";
  ui.projectSelect.append(blank);
  for (const project of data.projects) {
    const option = document.createElement("option");
    option.value = project.project_id;
    option.textContent = projectLabel(project);
    ui.projectSelect.append(option);
  }
  ui.projectSelect.value = app.projectId || "";
}

async function ensureProject() {
  if (app.projectId) return app.projectId;
  const created = await api("/api/projects", { method: "POST" });
  app.projectId = created.project_id;
  localStorage.setItem(PROJECT_KEY, app.projectId);
  await refreshProjects();
  return app.projectId;
}

async function openProject(projectId) {
  app.projectId = projectId;
  if (projectId) localStorage.setItem(PROJECT_KEY, projectId);
  else localStorage.removeItem(PROJECT_KEY);
  ui.projectSelect.value = projectId || "";
  await loadChat();
  if (app.pane === "state") await loadState();
  if (app.pane === "runs") await loadRun();
}

function emptyChat(text) {
  ui.chatThread.innerHTML = `<div class="chat-empty"><p>${text}</p></div>`;
}

function chatLog() {
  let log = ui.chatThread.querySelector(".chat-log");
  if (!log) {
    ui.chatThread.replaceChildren();
    log = document.createElement("div");
    log.className = "chat-log";
    ui.chatThread.append(log);
  }
  return log;
}

function appendBubble(role, content, extraClass = "") {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role} ${extraClass}`.trim();
  bubble.textContent = content;
  chatLog().append(bubble);
  ui.chatThread.scrollTop = ui.chatThread.scrollHeight;
  return bubble;
}

async function loadChat() {
  if (!app.projectId) {
    emptyChat("说你想做什么片子。发送后会自动建一个项目。");
    return;
  }
  try {
    const data = await api(`/api/projects/${app.projectId}/messages`);
    if (!data.messages.length) {
      emptyChat("这个项目还没有对白。");
      return;
    }
    ui.chatThread.replaceChildren();
    for (const message of data.messages) {
      appendBubble(message.role === "user" ? "user" : "assistant", message.content);
    }
    if (data.interrupt) showReview(data.interrupt);
    else {
      app.interrupt = null;
      setSending(false);
    }
  } catch (error) {
    if (String(error.message).includes("unknown project")) {
      app.projectId = null;
      localStorage.removeItem(PROJECT_KEY);
      emptyChat("说你想做什么片子。发送后会自动建一个项目。");
      await refreshProjects();
      return;
    }
    toast(error.message);
  }
}

function fieldList(entries) {
  const dl = document.createElement("dl");
  dl.className = "state-fields";
  for (const [key, value] of entries) {
    const row = document.createElement("div");
    row.className = "state-field";
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.append(renderValue(value, key));
    row.append(dt, dd);
    dl.append(row);
  }
  return dl;
}

function renderValue(value, key = "") {
  if (value == null || value === "") return document.createTextNode("空");
  if (Array.isArray(value)) {
    if (!value.length) return document.createTextNode("[]");
    const list = document.createElement("div");
    list.className = "state-list";
    value.forEach((item, index) => {
      const card = document.createElement("div");
      card.className = "state-card";
      const title = document.createElement("h3");
      title.textContent = item.shot_id || item.entity_id || item.media_id || `#${index + 1}`;
      card.append(title, renderValue(item));
      list.append(card);
    });
    return list;
  }
  if (typeof value === "object") {
    const fragment = document.createDocumentFragment();
    fragment.append(fieldList(Object.entries(value)));
    if (key === "media" || value.kind === "image") {
      const mediaId = value.media_id || key;
      if (app.projectId && (value.kind || "").startsWith("image")) {
        const img = document.createElement("img");
        img.className = "media-preview";
        img.alt = mediaId;
        img.src = `/api/projects/${app.projectId}/media/${mediaId}`;
        img.addEventListener("error", () => img.remove());
        fragment.append(img);
      }
    }
    return fragment;
  }
  return document.createTextNode(String(value));
}

function plane(title, value) {
  const section = document.createElement("section");
  section.className = "state-plane";
  const heading = document.createElement("h2");
  heading.textContent = title;
  section.append(heading, renderValue(value, title));
  return section;
}

async function loadState() {
  if (!app.projectId) {
    ui.stateBoard.innerHTML = `<p class="muted">还没有项目。</p>`;
    return;
  }
  const state = await api(`/api/projects/${app.projectId}/state`);
  const meta = document.createElement("div");
  meta.className = "state-meta";
  meta.innerHTML = `
    <span class="state-chip">revision ${state.revision}</span>
    <span class="state-chip">${state.outline?.video_type || "未选片种"}</span>
    <span class="state-chip">${state.deliverable ? "已有成片" : "尚无成片"}</span>
  `;
  const documentEl = document.createElement("div");
  documentEl.className = "state-document";
  documentEl.append(
    plane("outline", state.outline),
    plane("story", state.story),
    plane("shot_design", state.shot_design),
    plane("media", state.media),
    plane("deliverable", state.deliverable),
  );
  ui.stateBoard.replaceChildren(meta, documentEl);
}

async function loadRun() {
  if (!app.projectId) {
    ui.runBoard.innerHTML = `<p class="muted">还没有项目。</p>`;
    return;
  }
  const data = await api(`/api/projects/${app.projectId}/run`);
  if (!data.turns.length) {
    ui.runBoard.innerHTML = `<p class="muted">还没有运行记录。</p>`;
    return;
  }
  const list = document.createElement("div");
  list.className = "run-list";
  for (const turn of data.turns) {
    const card = document.createElement("article");
    card.className = "run-card";
    const head = document.createElement("div");
    head.className = "run-card-head";
    const heading = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = turn.user;
    const meta = document.createElement("p");
    meta.className = "run-meta";
    meta.textContent = `${turn.turn_id} · ${turn.steps.length} step`;
    heading.append(title, meta);
    head.append(heading);
    card.append(head);
    for (const step of turn.steps) {
      const block = document.createElement("div");
      block.className = "run-step";
      const title = document.createElement("p");
      title.className = "run-meta";
      title.textContent = `step ${step.index}`;
      block.append(title);
      if (step.assistant) {
        const assistant = document.createElement("p");
        assistant.textContent = step.assistant;
        block.append(assistant);
      }
      if (step.tool_calls.length) {
        const pre = document.createElement("pre");
        pre.className = "run-json";
        pre.textContent = JSON.stringify(
          { tool_calls: step.tool_calls, tool_results: step.tool_results },
          null,
          2,
        );
        block.append(pre);
      }
      card.append(block);
    }
    list.append(card);
  }
  ui.runBoard.replaceChildren(list);
}

function setSending(busy) {
  app.sending = busy;
  const locked = busy || Boolean(app.interrupt);
  ui.sendButton.disabled = locked;
  ui.composerInput.disabled = locked;
}

function showReview(interrupt) {
  app.interrupt = interrupt;
  document.querySelector(".bubble.review")?.remove();
  const args = interrupt.args || {};
  const card = document.createElement("div");
  card.className = "bubble assistant review";
  const title = document.createElement("strong");
  title.textContent = "这一镜要生成视频，先看一眼再出片。";
  const meta = document.createElement("p");
  meta.className = "review-meta";
  meta.textContent = interrupt.description || "";
  const fields = document.createElement("div");
  fields.className = "review-fields";
  fields.innerHTML = `
    <label>mode
      <select name="mode">
        <option value="ref2va">ref2va</option>
        <option value="fl2va">fl2va</option>
      </select>
    </label>
    <label>时长（秒）
      <input name="duration" type="number" min="1" step="1" />
    </label>
    <label>prompt
      <textarea name="prompt"></textarea>
    </label>
    <label>拒绝原因（可选）
      <input name="reason" type="text" />
    </label>
  `;
  fields.querySelector("[name=mode]").value = args.mode || "ref2va";
  fields.querySelector("[name=duration]").value = args.duration_seconds ?? 8;
  fields.querySelector("[name=prompt]").value = args.prompt || "";
  const actions = document.createElement("div");
  actions.className = "review-actions";
  const approve = document.createElement("button");
  approve.className = "primary";
  approve.type = "button";
  approve.textContent = "通过";
  const edit = document.createElement("button");
  edit.className = "secondary";
  edit.type = "button";
  edit.textContent = "用修改后的生成";
  const reject = document.createElement("button");
  reject.className = "danger";
  reject.type = "button";
  reject.textContent = "拒绝";
  actions.append(approve, edit, reject);
  const status = document.createElement("p");
  status.className = "review-status";
  status.hidden = true;
  card.append(title, meta, fields, actions, status);
  chatLog().append(card);
  ui.chatThread.scrollTop = ui.chatThread.scrollHeight;
  setSending(false);
  approve.addEventListener("click", () =>
    resumeInterrupt({ type: "approve" }),
  );
  edit.addEventListener("click", () =>
    resumeInterrupt({
      type: "edit",
      edited_action: {
        name: "video-gen",
        args: {
          ...args,
          mode: fields.querySelector("[name=mode]").value,
          duration_seconds: Number(fields.querySelector("[name=duration]").value),
          prompt: fields.querySelector("[name=prompt]").value,
        },
      },
    }),
  );
  reject.addEventListener("click", () => {
    const message = fields.querySelector("[name=reason]").value.trim();
    resumeInterrupt(
      message ? { type: "reject", message } : { type: "reject" },
    );
  });
}

function setReviewBusy(busy, text = "") {
  const card = document.querySelector(".bubble.review");
  if (!card) return;
  card.querySelectorAll(".review-actions button").forEach((button) => {
    button.disabled = busy;
  });
  const status = card.querySelector(".review-status");
  if (!status) return;
  status.hidden = !text;
  status.textContent = text;
}

async function resumeInterrupt(decision) {
  if (!app.projectId) {
    toast("没有打开的项目");
    return;
  }
  if (!app.interrupt) {
    toast("没有待确认的镜头，正在重新加载");
    await loadChat();
    return;
  }
  if (app.sending) {
    toast("上一镜还在生成，请等这条确认消失，不要刷新");
    return;
  }
  const interruptId = app.interrupt.id;
  setSending(true);
  setReviewBusy(true, "已经提交，正在出片。这一步通常要几分钟，请不要刷新。");
  try {
    const result = await api(`/api/projects/${app.projectId}/messages/resume`, {
      method: "POST",
      body: JSON.stringify({ interrupt_id: interruptId, decision }),
    });
    document.querySelector(".bubble.review")?.remove();
    app.interrupt = null;
    if (result.interrupt) {
      showReview(result.interrupt);
      toast("还要再确认一镜。点通过之后会开始生成，请等几分钟。");
    } else {
      appendBubble("assistant", result.message.content);
    }
    try {
      await refreshProjects();
    } catch (error) {
      toast(error.message);
    }
    if (app.pane === "state") await loadState();
    if (app.pane === "runs") await loadRun();
  } catch (error) {
    toast(error.message);
    setReviewBusy(false, "");
    try {
      await loadChat();
    } catch {
      /* keep the review card */
    }
  } finally {
    setSending(false);
    ui.composerInput.focus();
  }
}

async function sendMessage(text) {
  if (!text || app.sending) return;
  setSending(true);
  try {
    await ensureProject();
    if (!ui.chatThread.querySelector(".chat-log")) {
      ui.chatThread.replaceChildren();
    }
    appendBubble("user", text);
    const thinking = appendBubble("assistant", "正在想…", "thinking");
    ui.composerInput.value = "";
    const result = await api(`/api/projects/${app.projectId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content: text }),
    });
    thinking.remove();
    if (result.interrupt) showReview(result.interrupt);
    else {
      app.interrupt = null;
      appendBubble("assistant", result.message.content);
    }
    try {
      await refreshProjects();
    } catch (error) {
      toast(error.message);
    }
    if (app.pane === "state") await loadState();
    if (app.pane === "runs") await loadRun();
  } catch (error) {
    document.querySelector(".bubble.thinking")?.remove();
    toast(error.message);
  } finally {
    setSending(false);
    ui.composerInput.focus();
  }
}

document.querySelectorAll(".nav-tab").forEach((tab) => {
  tab.addEventListener("click", () => setPane(tab.dataset.pane));
});

ui.projectSelect.addEventListener("change", async () => {
  const projectId = ui.projectSelect.value;
  if (!projectId) return;
  await openProject(projectId);
});

ui.newSessionButton.addEventListener("click", async () => {
  const created = await api("/api/projects", { method: "POST" });
  await openProject(created.project_id);
  app.interrupt = null;
  emptyChat("新对话已开始。说你想做什么片子。");
  setSending(false);
  setPane("chat");
});

ui.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(ui.composerInput.value.trim());
});

ui.composerInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(ui.composerInput.value.trim());
  }
});

setPane(app.pane);
refreshProjects().catch((error) => toast(error.message));
loadChat().catch((error) => toast(error.message));
