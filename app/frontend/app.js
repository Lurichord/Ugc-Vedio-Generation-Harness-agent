const SESSION_KEY = "ugc-intake-session-id";
const PANE_KEY = "ugc-studio-pane";
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
const formatLabels = {
  drama: "短剧",
  tutorial: "教程",
  explainer: "讲解",
};
const statusLabels = {
  pending: "未开始",
  ready: "待跑",
  running: "进行中",
  passed: "已通过",
  failed: "失败",
  stale: "已过期",
  blocked: "阻塞",
  locked: "锁定",
  needs_user_input: "待确认",
  needs_revision: "待修改",
  not_required: "不需要",
  completed: "已完成",
  queued: "排队中",
};
const sourceLabels = {
  intake: "对话派发",
  studio: "制作台",
  trajectory: "历史结账",
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
  pane: sessionStorage.getItem(PANE_KEY) || "chat",
  runFilter: "all",
  folds: new Set(),
  catalog: { skills: [] },
  catalogSkill: null,
};

const PROBLEMS = {
  problems: "string[] · 上一轮这层的问题；首次调用不要填",
};

const agentCatalog = [
  {
    kind: "SKILL",
    group: "对话 Intake",
    items: [
      {
        name: "clarify-brief",
        description: "用户还在说要做什么片子。看 working_brief 缺什么，把口头说法收成制作能用的字段，一次只问一件事。",
        params: { name: '"clarify-brief"（经 skill.activate）' },
      },
      {
        name: "start-project",
        description: "用户明确要开做。确认 working_brief 够制作使用后派 harness.start_project。",
        params: { name: '"start-project"（经 skill.activate）' },
      },
      {
        name: "inspect-graph",
        description: "用户要改规格：谁出场、干什么、说什么。先读精简依赖图；换人/换物/换地文案先 identity.update，再 layer=spec 派 harness.repair。只改图或成片走 revise-media。",
        params: { name: '"inspect-graph"（经 skill.activate）' },
      },
      {
        name: "revise-segment",
        description: "用户要改某一段规格。先对上内容，再读精简依赖图判断改哪些节点，最后 layer=spec 派 harness.repair。",
        params: { name: '"revise-segment"（经 skill.activate）' },
      },
      {
        name: "revise-media",
        description: "用户要改参考图、某一镜成片观感或音色，不改剧情。对上地址后 layer=asset 或 layer=voice 派 harness.repair。",
        params: { name: '"revise-media"（经 skill.activate）' },
      },
      {
        name: "user-revises",
        description: "用户改口换主题、时长、体裁或目标。对照更新后的 brief，确认整支重做还是只改一段。",
        params: { name: '"user-revises"（经 skill.activate）' },
      },
      {
        name: "inspect-progress",
        description: "用户问做到哪了。看 progress 里各阶段状态，用一句话告诉用户。",
        params: { name: '"inspect-progress"（经 skill.activate）' },
      },
    ],
  },
  {
    kind: "SKILL",
    group: "制作阶段",
    items: [
      {
        name: "generate-identity",
        description: "短剧/教程身份参考图。必须自己写 prompt；修片时先读上次 generation_prompt，再按 instruction 改。",
        params: { name: '"generate-identity"（经 skill.activate，agent=asset_stage）' },
      },
      {
        name: "generate-shot",
        description: "短剧/教程出镜。必须自己写 prompt；修片时先读上次导演句和配图，再按 instruction 改。",
        params: { name: '"generate-shot"（经 skill.activate，agent=asset_stage）' },
      },
    ],
  },
  {
    kind: "TOOL",
    group: "通用",
    items: [
      {
        name: "skill.activate",
        description: "读入一篇 skill 做法说明书。激活不等于办事。名称只能包含小写字母、数字和连字符。",
        params: {
          name: "string · 技能名，如 clarify-brief / start-project / revise-media / generate-shot",
        },
      },
    ],
  },
  {
    kind: "TOOL",
    group: "对话 Intake",
    items: [
      {
        name: "brief.update",
        description: "部分更新 working_brief。只填要改的字段，没改的键不要带。点头确认、选择题、改口时用。不等于开工。",
        params: {
          topic: "string",
          production_mode: "explainer | drama | tutorial",
          video_profile: "string",
          target: "{ platform, duration_target_ms, aspect_ratio, language }",
          audience: "{ description, knowledge_level: beginner|general|expert }",
          communication: "{ goal, tone[], creator_persona }",
        },
      },
      {
        name: "description.list_outline",
        description: "列出 structure 大纲：id 与标题/命题，不含正文。",
        params: {},
      },
      {
        name: "description.get_element",
        description: "读取一个 description 元素，ref 如 beat:b3。",
        params: { ref: "string · 元素 ref，如 beat:b3、shot:shot_a4" },
      },
      {
        name: "identity.update",
        description: "按内容改已有身份，id 不变。ref 可以是图上的 cast:/prop:/place:，也可以是当前名字。不要新建、不要改 binds。",
        params: {
          patches: "[{ ref, name?, description?, appearance? }] · 至少一条，必须有 ref",
        },
      },
      {
        name: "identity.create",
        description: "新增身份根（cast/prop/place）。要画进现有镜再 shot.bind；要新开一镜再 shot.create。",
        params: {
          kind: "cast | prop | place",
          name: "string · 显示名",
          description: "string · 这是谁/什么",
          appearance: "string · 可选，出参考图时用",
          role: "narrator | performer，默认 performer",
        },
      },
      {
        name: "shot.create",
        description: "派 narrative task 写一镜新 shot。只声明锚点、binds、插在哪一镜后和 instruction。",
        params: {
          anchor_ref: "string · 如 scene:s2、step:st3",
          instruction: "string · 这一镜要拍什么",
          binds: "string[] · 可选，如 cast:e1",
          after_ref: "string · 可选，如 shot:shot_a3",
        },
      },
      {
        name: "shot.bind",
        description: "把已有身份追加到现有镜的 binds，只加不换不删。改完用返回的 shot_refs 做 harness.repair。",
        params: {
          shot_refs: "string[] · 如 shot:shot_a4",
          identity_ref: "string · 已有身份 ref 或当前名字",
        },
      },
      {
        name: "graph.remove",
        description: "删除身份或镜头节点。身份若还有 shot dependents 会失败。",
        params: { ref: "string · 如 prop:p1 或 shot:shot_a4" },
      },
      {
        name: "harness.list_graph",
        description: "读取精简依赖图。无参返回内容根；around_ref 返回该节点、邻居和下游 shot/audio。节点带 dependents_by_agent。不含 hash。",
        params: { around_ref: "string · 可选，聚焦节点、邻居和下游 shot/audio" },
      },
      {
        name: "harness.start_project",
        description: "按当前 working_brief 开工。缺 topic 或 goal 会失败。",
        params: {},
      },
      {
        name: "harness.inspect",
        description: "从磁盘刷新 progress 和已落账的 intent。",
        params: {},
      },
      {
        name: "harness.repair",
        description: "把圈中节点交给一个制作 agent。layer=spec 改规格；layer=asset 重出参考图或成片；layer=voice 重配。一次不要混 shot 和 audio。",
        params: {
          target_refs: "string[] · 画面：身份 ref 或 shot:…；配音：audio:…",
          instruction: "string · 这一轮要改成什么样",
          layer: "spec | asset | voice · 默认 spec",
        },
      },
    ],
  },
  {
    kind: "TOOL",
    group: "叙事",
    items: [
      {
        name: "narrative.write_identities",
        description: "只改身份册、不改镜头和中间层。首跑不要单独调用。",
        params: { identities: "身份册 JSON" },
      },
      {
        name: "narrative.write_sections",
        description: "讲解第一层：身份册 + Hook/Body/Close。",
        params: { sections: "SectionPlanArtifact JSON" },
      },
      {
        name: "narrative.write_beats",
        description: "讲解第二层。前置：已 write_sections。",
        params: { beats: "BeatPlanArtifact JSON" },
      },
      {
        name: "narrative.write_script",
        description: "讲解第三层。前置：已 write_beats。",
        params: { script: "ScriptArtifact JSON" },
      },
      {
        name: "narrative.write_world",
        description: "短剧第一层：世界和身份册。",
        params: { world: "DramaWorldArtifact JSON" },
      },
      {
        name: "narrative.write_story",
        description: "短剧第二层。前置：已 write_world。",
        params: { story: "DramaStoryArtifact JSON" },
      },
      {
        name: "narrative.write_actions",
        description: "短剧第三层。前置：已 write_story。",
        params: { actions: "DramaActionPlanArtifact JSON" },
      },
      {
        name: "narrative.write_definition",
        description: "教程第一层：成品和材料身份册。",
        params: { definition: "TutorialDefinitionArtifact JSON" },
      },
      {
        name: "narrative.write_procedure",
        description: "教程第二层。前置：已 write_definition。",
        params: { procedure: "TutorialProcedureArtifact JSON" },
      },
      {
        name: "narrative.write_explanations",
        description: "教程第三层。前置：已 write_procedure。可空。",
        params: { explanations: "TutorialScriptArtifact JSON" },
      },
      {
        name: "narrative.write_shots",
        description: "写 ProductionShot。首跑须已有上一层；修片段只交改过的镜。",
        params: { shots: "镜头列表", identities: "仅补交身份册", merge: "默认 true" },
      },
      {
        name: "narrative.list_state",
        description: "看当前各层是否已写、身份册和镜表骨架。",
        params: {},
      },
      {
        name: "narrative.submit_candidate",
        description: "身份册和全片镜头都齐了再交。有中间层就用中间层。",
        params: {},
      },
    ],
  },
  {
    kind: "TOOL",
    group: "配音",
    items: [
      {
        name: "voice.select_voice",
        description: "从 2.0 讲解音色表选出旁白音色。没有指定 voice_id 时必须先调用，不要自己编音色；已指定时可跳过。要换音色就再调一次。",
        params: PROBLEMS,
      },
      {
        name: "voice.create_plan",
        description: "根据已批准的叙事脚本生成配音计划：写发声人、语速、停顿与逐段语气。需要先选定音色。",
        params: PROBLEMS,
      },
      {
        name: "audio.synthesize_narration",
        description: "按配音计划合成全部音频段，拼接完整旁白并产出字级时间戳与 RealizedBeat。需要先有配音计划。",
        params: PROBLEMS,
      },
      {
        name: "voice.submit_candidate",
        description: "配音候选完整后提交；缺少音频时会返回可修复错误。",
        params: PROBLEMS,
      },
    ],
  },
  {
    kind: "TOOL",
    group: "画面规划",
    items: [
      {
        name: "editorial.create_plan",
        description: "生成主张映射和逐 Beat 的 A-roll/B-roll 视觉需求计划；任务带 critic 问题清单时自动以修复模式生成。",
        params: PROBLEMS,
      },
      {
        name: "editorial.submit_candidate",
        description: "编辑部计划完整后提交候选。",
        params: PROBLEMS,
      },
    ],
  },
  {
    kind: "TOOL",
    group: "镜头 / 素材",
    items: [
      {
        name: "asset.list_identities",
        description: "列出范围内身份、是否已有参考图、上次 generation_prompt。",
        params: PROBLEMS,
      },
      {
        name: "asset.generate_identity",
        description: "为指定身份生成一张竖屏参考图。必须自己写 prompt。",
        params: {
          ref: "string · 身份 ref",
          prompt: "string · 发给 Seedream 的参考图提示词",
          problems: "string[] · 可选",
        },
      },
      {
        name: "asset.list_shots",
        description: "列出范围内各镜是否已有成片、上次导演句和配图，以及可用参考图 materials。",
        params: PROBLEMS,
      },
      {
        name: "asset.generate_shot",
        description: "生成一镜 AI 视频。必须自己写 prompt；references 数组顺序就是图片1、图片2。",
        params: {
          shot_id: "string",
          prompt: "string · 发给 Seedance 的完整导演句",
          references: "[{ id, description }] · id 来自 list_shots.materials",
          problems: "string[] · 可选",
        },
      },
      {
        name: "asset.list_talking_heads",
        description: "列出待办口播镜、旁白、接镜关系和 materials。TTS 参考音频由 generate 自动带上。",
        params: PROBLEMS,
      },
      {
        name: "asset.generate_talking_head",
        description: "为指定口播镜生成 Seedance 视频。必须自己写 prompt；只用 reference_image。",
        params: {
          visual_request_id: "string",
          prompt: "string · 发给 Seedance 的完整导演句",
          references: "[{ id, description }] · id 来自 list_talking_heads.materials",
          problems: "string[] · 可选",
        },
      },
      {
        name: "asset.acquire_requirement",
        description: "处理下一个待办 VisualRequirement：按 first-success 规则获取素材并给出 resolution。",
        params: PROBLEMS,
      },
      {
        name: "asset.prepare_image",
        description: "图像修复模式：处理下一个待办 asset_id，产出 1080x1920 的竖屏预处理图。",
        params: PROBLEMS,
      },
      {
        name: "asset.submit_candidate",
        description: "待办清空或范围内每镜都有成片后提交；未完成时返回可修复错误。",
        params: PROBLEMS,
      },
    ],
  },
  {
    kind: "TOOL",
    group: "时间线",
    items: [
      {
        name: "timeline.compose",
        description: "把配音、编辑部计划和素材合成为以音频为时钟的时间线候选；修复任务自动只合并 scope 内的 beat。",
        params: PROBLEMS,
      },
      {
        name: "timeline.sequence_ai_video_shots",
        description: "短剧/教程：按 Narrative 顺序把 AI 成片接起来，不替换片内音频。",
        params: {},
      },
      {
        name: "timeline.submit_candidate",
        description: "时间线候选完整后提交。",
        params: PROBLEMS,
      },
    ],
  },
  {
    kind: "TOOL",
    group: "成片",
    items: [
      {
        name: "render.execute",
        description: "把已批准的时间线渲染为最终与预览 MP4 并探测输出指标。",
        params: PROBLEMS,
      },
      {
        name: "render.native_audio_shot_timeline",
        description: "短剧/教程：渲染 Shot 时间线，保留每段 AI 视频的原声。",
        params: {},
      },
      {
        name: "render.submit_candidate",
        description: "渲染输出就绪后提交候选。",
        params: PROBLEMS,
      },
    ],
  },
  {
    kind: "TOOL",
    group: "局部修复",
    items: [
      {
        name: "repair.submit_candidate",
        description: "提交圈中 shot 的补丁。只交 editable 的 shot。",
        params: {
          patches: '[{ ref: string, value: object }]',
        },
      },
    ],
  },
];

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
  renderStatePage();
  renderRunsPage();
  syncProjectSelect();
  setBusy(false);
  if (payload.gate?.kind === "running") startPolling();
  else stopPolling();
}

function productionRunning() {
  return state.payload?.gate?.kind === "running";
}

function composerLocked() {
  return state.busy || productionRunning();
}

function setBusy(busy) {
  state.busy = busy;
  const locked = composerLocked();
  const send = $("#sendButton");
  const input = $("#composerInput");
  send.disabled = locked;
  input.disabled = locked;
  send.classList.toggle("is-locked", locked);
  input.classList.toggle("is-locked", locked);
  $("#continueButton").disabled = locked;
  if (productionRunning()) {
    input.placeholder = "这一步还在制作，完成后才能发送";
  }
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

function showPane(pane) {
  const next = ["chat", "state", "runs"].includes(pane) ? pane : "chat";
  state.pane = next;
  sessionStorage.setItem(PANE_KEY, next);
  $$(".nav-tab").forEach((button) => {
    const active = button.dataset.pane === next;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("#pane-chat")?.classList.toggle("hidden", next !== "chat");
  $("#pane-state")?.classList.toggle("hidden", next !== "state");
  $("#pane-runs")?.classList.toggle("hidden", next !== "runs");
  if (next === "state") renderStatePage();
  if (next === "runs") {
    renderCatalog();
    renderRunsPage();
  }
}

function projectStateView() {
  return state.payload?.project_state || {};
}

function runLogView() {
  return state.payload?.run_log || { items: [] };
}

const planeLabels = {
  schema_version: "schema_version",
  context: "context",
  creative: "creative",
  production: "production",
  evaluation: "evaluation",
  dependency_graph: "dependency_graph",
  trajectory: "trajectory",
};
const fieldLabels = {
  project_id: "project_id",
  state_version: "state_version",
  available_models: "available_models",
  available_tools: "available_tools",
  constraints: "constraints",
  brief: "brief",
  intent: "intent",
  world: "world",
  structure: "structure",
  voice: "voice",
  shots: "shots",
  timeline: "timeline",
  deliverable: "deliverable",
  elements: "elements",
  stages: "stages",
  graph_version: "graph_version",
  nodes: "nodes",
  phases: "phases",
  cast: "cast",
  props: "props",
  places: "places",
  claims: "claims",
};

function renderStatePage() {
  const hint = $("#stateHint");
  const board = $("#stateBoard");
  if (!hint || !board) return;
  const view = projectStateView();
  if (!view.project_key) {
    hint.textContent = "开工之后，这里列出 ProjectState 各层字段。视频和图片只显示 id。";
    board.innerHTML = `<p class="muted">还没有 ProjectState。先在对话里开工，或打开已有项目。</p>`;
    return;
  }
  const format = formatLabels[view.format_id] || view.format_id || "项目";
  const here = view.current_stage
    ? `当前停在「${stageLabels[view.current_stage] || view.current_stage}」`
    : "制作进行中";
  hint.textContent = `${format} · ${here} · state v${view.state_version ?? 0}。下面是 ProjectState 全部字段；视频 / 图片只保留 id。`;
  const production = view.production || {};
  const graph = view.graph || {};
  const chips = [
    view.topic ? escapeHtml(view.topic) : "",
    production.shots ? `${production.shots} 镜` : "",
    production.cast ? `人物 ${production.cast}` : "",
    production.places ? `场景 ${production.places}` : "",
    production.props ? `道具 ${production.props}` : "",
    graph.node_count ? `图 ${graph.node_count} 节点` : "",
  ].filter(Boolean);
  const required = (view.stages || []).filter((stage) => stage.required);
  const steps = required.map((stage) => {
    const classes = ["state-step", stage.current ? "current" : ""].filter(Boolean).join(" ");
    return `<li class="${classes}">
      <strong>${escapeHtml(stage.label)}</strong>
      <span class="status-pill ${stage.status}">${escapeHtml(statusLabels[stage.status] || stage.status)}</span>
    </li>`;
  }).join("");
  const planes = view.planes || {};
  const planeOrder = ["schema_version", "context", "creative", "production", "evaluation", "dependency_graph", "trajectory"];
  const present = planeOrder.filter((name) => name in planes);
  const toc = present.map((name) => `<a href="#plane-${escapeHtml(name)}">${escapeHtml(planeLabels[name] || name)}</a>`).join("");
  const body = present.map((name) => `
    <section class="state-plane" id="plane-${escapeHtml(name)}">
      <h2>${escapeHtml(planeLabels[name] || name)}</h2>
      ${typeof planes[name] === "object" && planes[name] && !Array.isArray(planes[name])
        ? renderPlaneFolds(planes[name], name)
        : renderStateValue(planes[name], name, name)}
    </section>
  `).join("");
  board.innerHTML = `
    <div class="state-meta">${chips.map((item) => `<span class="state-chip">${item}</span>`).join("")}</div>
    ${view.promise ? `<p class="muted">${escapeHtml(view.promise)}</p>` : ""}
    <ol class="state-pipeline">${steps}</ol>
    <nav class="state-toc">${toc}</nav>
    <div class="state-document">${body}</div>
  `;
}

const bulkyKeys = new Set([
  "world", "structure", "voice", "shots", "timeline", "deliverable",
  "brief", "intent", "elements", "stages", "nodes", "phases",
  "cast", "props", "places", "claims", "scenes", "actions", "steps",
  "sections", "beats", "clips", "utterances", "spec", "visual",
  "payload", "task", "agent_result", "audience", "communication",
  "target", "presentation", "available_models", "constraints",
  "media", "audio", "publish", "evaluation", "transition", "graph_update",
  "tasks",
]);

function isBulkyItem(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length >= 5);
}

function isGroupFold(name, value) {
  if (Array.isArray(value)) {
    if (!value.some((item) => item && typeof item === "object")) return false;
    return bulkyKeys.has(name) || value.some(isBulkyItem);
  }
  return Boolean(value && typeof value === "object" && bulkyKeys.has(name));
}

function foldCollectionItems(name, value) {
  return bulkyKeys.has(name) || value.some(isBulkyItem);
}

function renderFoldBlock(path, title, summary, innerHtml) {
  const open = state.folds.has(path);
  return `<section class="state-fold${open ? " open" : ""}">
    <button type="button" class="state-fold-toggle" data-state-fold="${escapeHtml(path)}" aria-expanded="${open}">
      <span class="state-fold-name">${escapeHtml(title)}</span>
      <span class="state-fold-meta">${escapeHtml(summary)}</span>
      <span class="state-fold-mark">${open ? "收起" : "展开"}</span>
    </button>
    <div class="state-fold-body">${innerHtml}</div>
  </section>`;
}

function toggleFold(event) {
  const toggle = event.target.closest("[data-state-fold]");
  if (!toggle) return false;
  const name = toggle.dataset.stateFold;
  const fold = toggle.closest(".state-fold");
  if (!fold) return false;
  const open = !fold.classList.contains("open");
  fold.classList.toggle("open", open);
  toggle.setAttribute("aria-expanded", String(open));
  const mark = toggle.querySelector(".state-fold-mark");
  if (mark) mark.textContent = open ? "收起" : "展开";
  if (open) state.folds.add(name);
  else state.folds.delete(name);
  return true;
}

function renderPlaneFolds(value, plane) {
  if (value == null || typeof value !== "object" || Array.isArray(value)) {
    return renderStateValue(value, plane, plane);
  }
  const forceAll = plane === "production";
  const plain = [];
  const folds = [];
  for (const [name, item] of Object.entries(value)) {
    const path = `${plane}.${name}`;
    const fold = forceAll || isGroupFold(name, item);
    if (fold) {
      folds.push(renderFoldBlock(
        path,
        fieldLabels[name] || name,
        foldSummary(name, item),
        renderStateValue(item, name, path),
      ));
      continue;
    }
    plain.push(`
      <div class="state-field">
        <dt>${escapeHtml(fieldLabels[name] || name)}</dt>
        <dd>${renderStateValue(item, name, path)}</dd>
      </div>
    `);
  }
  return [
    plain.length ? `<dl class="state-fields">${plain.join("")}</dl>` : "",
    folds.length ? `<div class="state-folds">${folds.join("")}</div>` : "",
  ].join("");
}

function foldSummary(name, value) {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value !== "object") return String(value);
  if (name === "world") {
    const parts = [];
    if (value.cast?.length) parts.push(`人物 ${value.cast.length}`);
    if (value.props?.length) parts.push(`道具 ${value.props.length}`);
    if (value.places?.length) parts.push(`场景 ${value.places.length}`);
    if (value.claims?.length) parts.push(`主张 ${value.claims.length}`);
    return parts.join(" · ") || "{}";
  }
  if (name === "structure") {
    const units = value.scenes || value.steps || value.sections || [];
    const count = Array.isArray(units) ? units.length : 0;
    return [value.kind, count ? `${count} 段` : ""].filter(Boolean).join(" · ") || "{}";
  }
  if (name === "voice") {
    const utterances = value.utterances?.length || 0;
    return utterances ? `utterance ${utterances}` : "已填写";
  }
  if (name === "timeline") {
    const clips = value.clips?.length || 0;
    return clips ? `${clips} clip` : "{}";
  }
  if (name === "deliverable") return value.video_file || "{}";
  if (name === "brief" || name === "intent") {
    return value.topic || value.project_name || value.format_id || `${Object.keys(value).length} 个字段`;
  }
  if (name === "elements" || name === "nodes") return `${Object.keys(value).length} 个`;
  if (name === "phases") return Object.keys(value).join(" · ") || "{}";
  if (name === "stages") {
    return Object.entries(value).map(([key, item]) => `${key}:${item}`).join(" · ");
  }
  const keys = Object.keys(value);
  return keys.length ? `${keys.length} 个字段` : "{}";
}

function cardSummary(value) {
  if (Array.isArray(value)) return `${value.length} 项`;
  if (value == null || value === "") return "—";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value.tasks)) return `${value.tasks.length} 条 task`;
  return String(
    value.status
    || value.purpose
    || value.kind
    || value.role
    || value.task_kind
    || value.shot_kind
    || value.spec?.purpose
    || value.spec?.shot_id
    || value.task?.goal
    || value.description
    || ""
  ).replace(/\s+/g, " ").slice(0, 56);
}

function renderStateValue(value, key, path = key) {
  if (value == null || value === "") return `<p class="muted">—</p>`;
  if (typeof value === "boolean" || typeof value === "number") {
    return `<p>${escapeHtml(String(value))}</p>`;
  }
  if (typeof value === "string") return `<p>${escapeHtml(value)}</p>`;
  if (Array.isArray(value)) {
    if (!value.length) return `<p class="muted">[]</p>`;
    if (value.every((item) => item == null || typeof item !== "object")) {
      return `<p>${value.map((item) => escapeHtml(String(item))).join(" · ") || "—"}</p>`;
    }
    if (foldCollectionItems(key, value)) {
      return `<div class="state-folds">${value.map((item, index) => {
        const title = objectTitle(item) || `${fieldLabels[key] || key || "item"} ${index + 1}`;
        const itemPath = `${path}.${title || index}`;
        return renderFoldBlock(itemPath, title, cardSummary(item), renderStateFields(item, itemPath));
      }).join("")}</div>`;
    }
    return `<div class="state-list">${value.map((item, index) => {
      const title = objectTitle(item) || `${fieldLabels[key] || key || "item"} ${index + 1}`;
      return `<article class="state-card"><h3>${escapeHtml(title)}</h3>${renderStateFields(item, `${path}.${index}`)}</article>`;
    }).join("")}</div>`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value);
    if (!keys.length) return `<p class="muted">{}</p>`;
    const named = keys.every((name) => value[name] && typeof value[name] === "object" && !Array.isArray(value[name]));
    if (named && (key === "nodes" || key === "phases" || key === "elements")) {
      return `<div class="state-folds">${keys.map((name) => {
        const itemPath = `${path}.${name}`;
        return renderFoldBlock(itemPath, name, cardSummary(value[name]), renderStateFields(value[name], itemPath));
      }).join("")}</div>`;
    }
    return renderStateFields(value, path);
  }
  return `<p>${escapeHtml(String(value))}</p>`;
}

function renderStateFields(value, path = "") {
  if (value == null || typeof value !== "object" || Array.isArray(value)) {
    return renderStateValue(value, path, path);
  }
  const rows = Object.entries(value).map(([name, item]) => {
    const childPath = path ? `${path}.${name}` : name;
    if (isGroupFold(name, item)) {
      return `<div class="state-field state-field-fold">
        ${renderFoldBlock(
          childPath,
          fieldLabels[name] || name,
          foldSummary(name, item),
          renderStateValue(item, name, childPath),
        )}
      </div>`;
    }
    return `<div class="state-field">
      <dt>${escapeHtml(fieldLabels[name] || name)}</dt>
      <dd>${renderStateValue(item, name, childPath)}</dd>
    </div>`;
  }).join("");
  return `<dl class="state-fields">${rows}</dl>`;
}

function objectTitle(value) {
  if (!value || typeof value !== "object") return "";
  const spec = value.spec && typeof value.spec === "object" ? value.spec : {};
  const task = value.task && typeof value.task === "object" ? value.task : {};
  return String(
    value.name
    || value.character_id
    || value.prop_id
    || value.place_id
    || value.claim_id
    || value.action_id
    || value.shot_id
    || spec.shot_id
    || value.scene_id
    || value.step_id
    || value.section_id
    || value.utterance_id
    || value.clip_id
    || value.task_id
    || task.task_id
    || value.ref
    || value.goal
    || task.goal
    || ""
  );
}

function renderRunsPage() {
  const hint = $("#runHint");
  const filters = $("#runFilters");
  const board = $("#runBoard");
  if (!hint || !filters || !board) return;
  const items = runLogView().items || [];
  const turns = runLogView().intake_turns || [];
  const key = state.payload?.project_key;
  if (!key && !items.length && !turns.length) {
    hint.textContent = "对话里每说一句，这里会记下 intake 当时的消息上下文和调用的工具。开工后还会加上 Generic 的 task。";
    filters.innerHTML = "";
    board.innerHTML = `<p class="muted">还没有运行记录。</p>`;
    return;
  }
  const stagesInLog = [...new Set(items.map((item) => item.stage))];
  const filter = stagesInLog.includes(state.runFilter) ? state.runFilter : "all";
  state.runFilter = filter;
  const visible = filter === "all" ? items : items.filter((item) => item.stage === filter);
  hint.textContent = `Generic ${items.length} 条，intake ${turns.length} 轮。参考图 / 出片会展开当时发给 agent 的 system（说明书、skill 目录、上次组装）和本拍 user turn。`;
  const chips = items.length
    ? [`<button type="button" class="run-filter ${filter === "all" ? "active" : ""}" data-run-filter="all">全部</button>`]
      .concat(stagesInLog.map((stage) => {
        const active = filter === stage ? "active" : "";
        return `<button type="button" class="run-filter ${active}" data-run-filter="${escapeHtml(stage)}">${escapeHtml(stageLabels[stage] || stage)}</button>`;
      }))
    : [];
  filters.innerHTML = chips.join("");
  const generic = visible.length
    ? `<div class="run-list">${visible.slice().reverse().map(runMarkup).join("")}</div>`
    : (items.length ? `<p class="muted">这一步还没有 Generic task。</p>` : "");
  board.innerHTML = `${generic}${intakeTurnsMarkup(turns)}`;
}

function catalogSections() {
  const skills = state.catalog?.skills || [];
  const grouped = new Map();
  for (const item of skills) {
    const group = item.group || "技能";
    if (!grouped.has(group)) grouped.set(group, []);
    grouped.get(group).push({
      name: item.name,
      description: item.description,
      body: item.body,
      params: item.agent ? { agent: item.agent } : {},
    });
  }
  const skillSections = [...grouped.entries()].map(([group, items]) => ({
    kind: "SKILL",
    group,
    items,
  }));
  if (!skillSections.length) {
    skillSections.push(...agentCatalog.filter((section) => section.kind === "SKILL"));
  }
  return [
    ...skillSections,
    ...agentCatalog.filter((section) => section.kind === "TOOL"),
  ];
}

async function loadCatalog() {
  try {
    state.catalog = await api("/api/catalog");
  } catch {
    state.catalog = { skills: [] };
  }
  renderCatalog();
}

function renderCatalog() {
  const board = $("#catalogBoard");
  if (!board) return;
  board.innerHTML = catalogSections().map(catalogGroupMarkup).join("");
}

function catalogGroupMarkup(section) {
  return `<section class="catalog-group">
    <p class="eyebrow">${escapeHtml(section.kind)}</p>
    <h2>${escapeHtml(section.group)}</h2>
    <div class="catalog-list">${section.items.map((item) => catalogItemMarkup(item, section.kind)).join("")}</div>
  </section>`;
}

function catalogItemMarkup(item, kind) {
  if (kind === "SKILL" && item.body) return catalogSkillMarkup(item);
  const params = item.params || {};
  const keys = Object.keys(params);
  const body = keys.length
    ? `<dl class="catalog-params">${keys.map((key) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(params[key])}</dd></div>`).join("")}</dl>`
    : `<p class="muted">无参</p>`;
  return `<article class="catalog-card">
    <h3><code>${escapeHtml(item.name)}</code></h3>
    <p>${escapeHtml(item.description)}</p>
    ${body}
  </article>`;
}

function catalogSkillMarkup(item) {
  const open = state.catalogSkill === item.name;
  return `<article class="catalog-card catalog-skill${open ? " is-open" : ""}">
    <button type="button" class="catalog-skill-toggle" data-catalog-skill="${escapeHtml(item.name)}" aria-expanded="${open}">
      <span>
        <h3><code>${escapeHtml(item.name)}</code></h3>
        <p>${escapeHtml(item.description || "")}</p>
      </span>
      <span class="catalog-skill-mark">${open ? "收起" : "查看全文"}</span>
    </button>
    ${open ? `<pre class="catalog-skill-body">${escapeHtml(item.body)}</pre>` : ""}
  </article>`;
}

function runMarkup(item) {
  const when = formatDate(item.finished_at || item.created_at);
  const source = sourceLabels[item.source] || item.source;
  const status = statusLabels[item.status] || item.status;
  const refs = [...(item.target_refs || []), ...(item.shot_ids || [])].slice(0, 6);
    const actions = (item.actions || []).map((action, index) => {
      const skill = action.skill ? ` · skill ${action.skill}` : "";
      const ms = action.duration_ms ? ` · ${action.duration_ms}ms` : "";
      const head = `${escapeHtml(action.tool)}${escapeHtml(skill)} · ${escapeHtml(action.result)}${ms}${action.reason ? ` · ${escapeHtml(action.reason)}` : ""}`;
      const args = action.arguments && Object.keys(action.arguments).length
        ? renderFoldBlock(
          `run:${item.run_id}:args:${index}`,
          "调用参数",
          argumentSummary(action.arguments),
          jsonMarkup(action.arguments),
        )
        : "";
      return `<li class="run-action"><div class="run-action-head">${head}</div>${args}</li>`;
    }).join("");
    const opening = runOpeningMessages(item);
    const context = item.context && Object.keys(item.context).length
      ? renderFoldBlock(
        `run:${item.run_id}:context`,
        "注入上下文",
        argumentSummary(item.context),
        jsonMarkup(item.context),
      )
      : "";
    return `<article class="run-card">
    <div class="run-card-head">
      <div>
        <h3>${escapeHtml(item.goal || item.task_id || "Task")}</h3>
        <p class="run-meta">${escapeHtml(source)} · ${escapeHtml(item.label || stageLabels[item.stage] || item.stage)} · ${escapeHtml(when)}</p>
        ${item.received ? `<p class="run-received"><span>接收</span>${escapeHtml(item.received)}</p>` : item.instruction ? `<p class="run-received"><span>接收</span>${escapeHtml(item.instruction)}</p>` : ""}
        ${refs.length ? `<p class="run-meta">${refs.map(escapeHtml).join(" · ")}</p>` : ""}
      </div>
      <span class="status-pill ${item.status}">${escapeHtml(status)}${item.critic_passed == null ? "" : item.critic_passed ? " · Critic 通过" : " · Critic 未过"}</span>
    </div>
    ${messagesMarkup(opening, `run:${item.run_id}:messages`)}
    ${context}
    ${actions ? `<ol class="run-actions">${actions}</ol>` : ""}
    ${terminalMarkup(item.lines)}
    ${item.error ? `<p class="run-meta">${escapeHtml(item.error)}</p>` : ""}
  </article>`;
}

function intakeTurnsMarkup(turns) {
  const cards = (turns || []).slice().reverse().map(intakeTurnMarkup).join("");
  return `<section class="intake-log">
    <div class="page-header">
      <p class="eyebrow">INTAKE</p>
      <h2>Intake 记录</h2>
      <p class="muted">每一轮对话 intake 当时看到的消息上下文，以及它调用的工具 / skill。终端原文在卡片底部。</p>
    </div>
    ${cards || `<p class="muted">还没有 intake 记录。在对话里发一句后会出现。</p>`}
  </section>`;
}

function intakeTurnMarkup(item) {
  const tools = (item.tools || []).map((call, index) => {
    const skill = call.skill ? `skill · ${call.skill}` : call.tool;
    const ok = call.result_ok == null ? "" : call.result_ok ? " · ok" : " · failed";
    const head = `${escapeHtml(skill)}${escapeHtml(ok)}${call.error ? ` · ${escapeHtml(call.error)}` : ""}`;
    const args = call.arguments && Object.keys(call.arguments).length
      ? renderFoldBlock(
        `intake:${item.turn_id}:args:${index}`,
        "调用参数",
        argumentSummary(call.arguments),
        jsonMarkup(call.arguments),
      )
      : "";
    return `<li class="run-action"><div class="run-action-head">${head}</div>${args}</li>`;
  }).join("");
  return `<article class="run-card">
    <div class="run-card-head">
      <div>
        <h3>Intake 回合</h3>
        <p class="run-meta">${escapeHtml(formatDate(item.created_at))}</p>
        <p class="run-received"><span>接收</span>${escapeHtml(item.received || "")}</p>
        ${item.reply ? `<p class="run-meta">回复：${escapeHtml(item.reply)}</p>` : ""}
      </div>
    </div>
    ${messagesMarkup(intakeTurnMessages(item), `intake:${item.turn_id}:messages`)}
    ${tools ? `<ol class="run-actions">${tools}</ol>` : `<p class="run-meta">这一轮没有调工具。</p>`}
    ${terminalMarkup(item.lines)}
  </article>`;
}

function intakeTurnMessages(item) {
  if (item.messages && item.messages.length) return item.messages;
  const all = (state.payload?.messages || []).filter((message) => message.content);
  const cutoff = item.created_at || "";
  let sliced = cutoff
    ? all.filter((message) => !message.created_at || message.created_at <= cutoff)
    : all;
  const last = sliced[sliced.length - 1];
  if (last && last.role === "agent" && last.content === item.reply) {
    sliced = sliced.slice(0, -1);
  }
  return sliced.map((message) => ({
    role: message.role === "agent" ? "assistant" : message.role,
    content: message.content,
  }));
}

function runOpeningMessages(item) {
  if (Array.isArray(item.messages) && item.messages.length) return item.messages;
  const pack = item.context?.asset_context || item.context?.references_context;
  if (!pack || typeof pack !== "object") return [];
  const messages = [];
  if (pack.memory && Object.keys(pack.memory).length) {
    messages.push({
      role: "system",
      content: JSON.stringify(pack.memory, null, 2),
    });
  }
  if (pack.turn && Object.keys(pack.turn).length) {
    messages.push({
      role: "user",
      content: JSON.stringify(pack.turn, null, 2),
    });
  }
  return messages;
}

function messagesMarkup(messages, foldId) {
  if (!messages || !messages.length) return "";
  const inner = messages.map((item) => {
    const role = item.role || "user";
    const label = role === "system"
      ? "system"
      : role === "assistant" || role === "agent"
        ? "assistant"
        : "user";
    return `<article class="run-message">
      <span class="run-message-role">${escapeHtml(label)}</span>
      <pre class="run-message-body">${escapeHtml(item.content || "")}</pre>
    </article>`;
  }).join("");
  return renderFoldBlock(
    foldId,
    "消息上下文",
    `${messages.length} 条`,
    `<div class="run-messages">${inner}</div>`,
  );
}

function terminalMarkup(lines) {
  if (!lines || !lines.length) return "";
  return `<pre class="run-terminal">${lines.map((line) => escapeHtml(line)).join("\n")}</pre>`;
}

function jsonMarkup(value) {
  return `<pre class="run-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
}

function argumentSummary(value) {
  if (value == null) return "—";
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value !== "object") return String(value);
  const keys = Object.keys(value);
  if (!keys.length) return "{}";
  if (value.name) return String(value.name);
  if (value.task?.goal) return String(value.task.goal);
  if (value.memory) {
    const identities = Array.isArray(value.memory.identities) ? value.memory.identities.length : 0;
    const shots = Array.isArray(value.memory.shots) ? value.memory.shots.length : 0;
    return `上次组装 ${identities} 身份 / ${shots} 镜`;
  }
  return keys.slice(0, 4).join(", ");
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
  if (!trimmed || !state.sessionId || composerLocked()) return;
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
  if (!state.sessionId || composerLocked()) return;
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
$$(".nav-tab").forEach((node) => node.addEventListener("click", () => showPane(node.dataset.pane)));
$("#stateBoard")?.addEventListener("click", (event) => {
  if (toggleFold(event)) return;
  const openRuns = event.target.closest("[data-open-runs]")?.dataset.openRuns;
  if (openRuns) {
    state.runFilter = openRuns;
    showPane("runs");
  }
});
$("#runBoard")?.addEventListener("click", (event) => {
  toggleFold(event);
});
$("#runFilters")?.addEventListener("click", (event) => {
  const filter = event.target.closest("[data-run-filter]")?.dataset.runFilter;
  if (!filter) return;
  state.runFilter = filter;
  renderRunsPage();
});
$("#catalogBoard")?.addEventListener("click", (event) => {
  const name = event.target.closest("[data-catalog-skill]")?.dataset.catalogSkill;
  if (!name) return;
  state.catalogSkill = state.catalogSkill === name ? null : name;
  renderCatalog();
});

async function boot() {
  showPane(state.pane);
  await Promise.all([
    loadCatalog().catch((error) => toast(error.message)),
    loadProjects().catch((error) => toast(error.message)),
  ]);
  await ensureSession().catch((error) => toast(error.message));
}

boot();
