# UGC Beat Studio

这是现有 `ugc_harness` 内核的 Web 工作台。所有 Web 代码都位于 `app/`，不修改内核包。

主界面是对话：左侧像 Codex 一样聊天收意图、派制作；右侧实时展示已经完成的脚本、声音、镜头和成片。每一个阶段做完都会停下来问你：可以继续下一步，还是先改。

## 功能

- 意图层对话：主题、观众、平台、时长收齐后，由 intake 开工（只跑 Narrative，不会自动连跑后面的阶段）。
- 每完成一个阶段，聊天里出现确认栏。回复「可以 / 一样 / 继续」或点按钮，才会批准并跑下一阶段。
- 右侧栏目按阶段列出当前产物（口播、配音、shot 视频、时间线、成片），生成过程中会轮询刷新。
- 反馈仍可绑到具体依赖图节点，走现有局部修复。时间线和 Task 历史放在右侧抽屉里。

## 启动

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r app\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.backend.main:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000`。

模型、TTS、素材生成和渲染继续读取仓库现有 `.env` 配置。

## 数据职责

| 数据 | 来源 |
|---|---|
| 对话 memory | `app/data/intake/<session>.json` |
| 阶段门禁 / 制作台通知 | `app/data/intake/<session>.workspace.json` |
| 阶段执行状态 | `outputs/<项目>/harness/project_state.json` |
| 领域产物 | `outputs/<项目>/*_artifact.json` |
| 人工批准与反馈 | `app/data/projects/<项目目录>/` |

## 主要接口

- `POST /api/intake/sessions`
- `GET /api/intake/sessions/{session_id}`
- `POST /api/intake/sessions/{session_id}/messages`
- `POST /api/intake/sessions/{session_id}/continue`
- `POST /api/intake/sessions/{session_id}/attach`
- `GET /api/projects`
- `GET /api/projects/{project_key}/stages/{stage}`
- `POST /api/projects/{project_key}/stages/{stage}/run`
- `POST /api/projects/{project_key}/stages/{stage}/approve`
- `GET /api/projects/{project_key}/tasks`
- `GET /api/projects/{project_key}/timeline`
