# customer-agent

营销业扩通用客服 Agent Web Voice MVP。

当前仓库包含：

- `backend/`：`FastAPI + LangGraph + PostgreSQL + Valkey + LiveKit` 后端 MVP 骨架。
- `frontend/`：`Vue 3 + TypeScript + Vite + Pinia + LiveKit JS SDK` 浏览器模拟通话界面。

说明：项目内部 PRD、工程方案、UI/UX 和情报资料保留在本地 `docs/`，该目录已配置为不上传 GitHub。

## 已确认技术栈

- 前端使用 `Vue 3 + TypeScript + Vite`。
- 实时音频确认使用 `LiveKit`，浏览器内模拟通话走 `WebRTC`。
- 后端使用 `FastAPI`，Agent 工作流使用 `LangGraph`。
- 生产默认 Provider：腾讯云 `ASR`、腾讯云 `TTS`、阿里云 `LLM`，并支持配置化切换。
- 已接入并真实冒烟验证阿里云 OpenAI-compatible `LLM`、腾讯云 `TextToVoice`、腾讯云短音频 `SentenceRecognition` 和腾讯云实时 ASR WebSocket；运行路径默认使用真实 Provider，`mock` 仅用于 `APP_ENV=test` 自动化测试，非测试环境的 Provider 列表不会暴露 `mock` 作为可选运行供应商。
- 持久化只保存转写、回答和事件日志，不保存原始音频。

本地 Docker 运行时，`LIVEKIT_URL` 是后端/worker 容器内访问地址，默认 `ws://livekit:7880`；`PUBLIC_LIVEKIT_URL` 是返回给浏览器的公开地址，默认 `ws://127.0.0.1:7880`。如果页面出现 `could not establish signal connection: Failed to fetch`，优先检查 `PUBLIC_LIVEKIT_URL` 是否是浏览器可访问地址。

## 本地开发

后端：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# 运行路径保持 DEFAULT_*_PROVIDER=tencent_cloud/alibaba_cloud。
docker compose up -d postgres redis livekit
alembic -c backend/alembic.ini upgrade head
uvicorn backend.app.main:app --reload
```

前端：

```bash
cd frontend
npm install
VITE_SESSION_API_BASE_URL=http://127.0.0.1:8000 npm run dev
```

真实 Provider 冒烟验证：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/provider-configs/smoke
```

该接口会真实调用阿里云 LLM、腾讯云 TTS 和腾讯云短音频 ASR，只返回文本、音频类型和音频字节数，不返回或保存音频内容。

不依赖后端服务或数据库的本地真实云 smoke：

```bash
python3 -m backend.app.worker.provider_smoke
python3 -m backend.app.worker.provider_smoke --include-realtime-asr
```

第二条命令会使用腾讯云 TTS 生成的 16k mono WAV PCM 调用腾讯云实时 ASR WebSocket，验证 `TTS -> Realtime ASR` 的真实云链路。

## 验证

```bash
python3 -m pytest
cd frontend && npm run build
docker compose config
```

生产 readiness 巡检：

```bash
curl http://127.0.0.1:8000/api/v1/runtime/readiness
```

该接口不调用外部云服务、不返回密钥，只检查数据库查询、运行配置门禁、当前启用 Provider 是否为真实供应商，以及 `Voice Worker` 队列是否存在过期租约、心跳停滞或失败 job。`/health` 只表示后端进程存活，不能替代 readiness 巡检。

生产-like 配置离线校验：

```bash
python3 -m backend.app.worker.runtime_config_check
```

该命令不启动 API、不连接数据库、不调用腾讯云或阿里云；只校验 `APP_ENV=production/staging` 下是否使用强 `LiveKit` 配置、真实 Provider、环境变量/Secret 注入密钥、禁用本地密钥文件、禁用原始音频持久化和 text-only TTS fallback。`.env.production.example` 只是模板，占位值预期会被该命令拦截；复制为 `.env.production` 并由部署平台注入真实密钥后再执行校验。`docker-compose.production.yml` 会在 `backend` 与 `voice-worker` 启动前先执行该校验。

生产准入 gate：

```bash
python3 -m backend.app.worker.production_release_gate
python3 -m backend.app.worker.production_release_gate --run-provider-smoke --include-realtime-asr
```

该 gate 汇总运行配置、非测试环境 `mock` 暴露情况、生产 Compose 模板、真实 Provider smoke 证据、浏览器真实链路 smoke 证据和真人麦克风验收产物。默认只读取已有证据文件；带 `--run-provider-smoke` 时会真实调用腾讯云和阿里云。只有所有 gate 都通过才返回退出码 `0`。

生产指标出口：

```bash
curl http://127.0.0.1:8000/api/v1/runtime/metrics
```

该接口返回 Prometheus 文本格式指标，覆盖运行环境、配置违规数量、当前启用 Provider、会话数量、对话轮次数、错误事件数量和 `Voice Worker` 队列状态；指标不包含密钥、用户文本、会话 ID 或原始音频。

Prometheus 告警规则：

```text
docs/engineering/ops/prometheus-alerts.yml
```

规则覆盖生产配置违规、误启用 `mock` Provider、`Voice Worker` 租约过期、心跳停滞、job 失败、近期语音链路错误和长时间无活跃会话。该文件位于本地 `docs/`，不随 GitHub 提交上传。

浏览器真实链路 smoke：

```bash
docker compose up -d postgres redis livekit
alembic -c backend/alembic.ini upgrade head
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
cd frontend
VITE_SESSION_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1
VITE_SESSION_API_BASE_URL=http://127.0.0.1:8000 WEB_VOICE_APP_URL=http://127.0.0.1:5173 npm run smoke:web-voice
```

该 smoke 会用腾讯云 TTS 生成测试 WAV，作为 Chrome fake microphone 输入，验证浏览器页面可观察到 `transcript.final`、`agent.reply.preparing`、`agent.reply.answered` 和测试转写文本。当前本地 `livekit/livekit-server:v1.10` 与 `livekit-client@2.19.2` 已完成兼容验证，浏览器 smoke 输出 `console.error_count=0`。

真人麦克风验收：

```bash
docker compose up -d postgres redis livekit
alembic -c backend/alembic.ini upgrade head
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
cd frontend
VITE_SESSION_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1
VITE_SESSION_API_BASE_URL=http://127.0.0.1:8000 WEB_VOICE_APP_URL=http://127.0.0.1:5173 npm run acceptance:manual-mic
```

该验收会打开真实浏览器和真实麦克风，由人工完成一轮语音咨询、听 Agent 播报并尝试播报中打断。脚本会把页面观察到的标准事件和人工勾选结果写入 `output/manual-mic-acceptance/`，不保存原始音频。

## 生产配置门禁

当 `APP_ENV=production` 或 `APP_ENV=staging` 时，后端启动会校验生产安全边界：

- `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` 必须替换开发默认值，且 secret 至少 32 字符。
- `DEFAULT_ASR_PROVIDER`、`DEFAULT_TTS_PROVIDER`、`DEFAULT_LLM_PROVIDER` 不允许为 `mock`。
- 腾讯云、阿里云密钥必须通过环境变量或 Secret Manager 注入，不允许依赖 `docs/密钥/` 本地文件。
- 不允许保存原始音频，不允许静默降级为 text-only TTS fallback。
- LiveKit token 签发失败时不会生成 placeholder token，而是启动/调用失败并暴露配置错误。

## Turn Manager 第一版

后端已接入低置信度确认策略：当 ASR Provider 返回的 `confidence` 低于
`TURN_LOW_CONFIDENCE_THRESHOLD` 时，系统会记录 `turn.low_confidence` 事件，并先生成“请用户确认或重说”的可播报话术，不直接调用 LLM 推进业务回答。没有返回 `confidence` 的实时 ASR 消息会保持原有回答链路。
后端也已接入沉默超时提示第一版：`TURN_SILENCE_TIMEOUT_SECONDS` 超时后会记录 `turn.silence_timeout`，并向前端发送可恢复的 `session.error` 提示用户继续说或结束通话。
播报中打断也已接入第一版：Agent 播报期间如果观察到连续高于 `TURN_BARGE_IN_RMS_THRESHOLD` 的用户音频帧，后端会停止继续发布后续 TTS frame，记录并镜像 `agent.reply.interrupted`；`TURN_BARGE_IN_MIN_VOICE_FRAMES` 用于降低短噪声或回声误打断。
如果打断后在 `TURN_INTERRUPTION_RECOVERY_TIMEOUT_SECONDS` 内没有形成新的 `transcript.final`，后端会记录并镜像 `agent.reply.interrupt_recovered`，前端恢复到继续聆听状态。

## Voice Worker 运行模式

默认 `VOICE_WORKER_LAUNCH_MODE=in_process`，适合本地单进程演示：后端创建会话后在当前 FastAPI 进程内启动对应 `Voice Worker`。

生产或多实例部署应使用 `VOICE_WORKER_LAUNCH_MODE=external`：后端只写入 `voice_worker_job` 和 `voice_worker.start_requested` 事件，独立 worker 进程通过队列领取任务并运行实时语音链路。
外部 worker job 带有租约和心跳：`VOICE_WORKER_JOB_LEASE_SECONDS` 控制租约时长，`VOICE_WORKER_HEARTBEAT_INTERVAL_SECONDS` 控制续约频率，`VOICE_WORKER_MAX_ATTEMPTS` 控制失败或租约过期后的最大领取次数。
可通过 `GET /api/v1/voice-worker/jobs/status` 查看外部 worker 队列健康，包括各状态数量、过期租约、心跳停滞 job、最近 job 和最近失败 job。

```bash
VOICE_WORKER_LAUNCH_MODE=external uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
python3 -m backend.app.worker.voice_worker_runner --worker-id worker-1
```

Docker Compose 可选启动独立 worker：

```bash
VOICE_WORKER_LAUNCH_MODE=external docker compose --profile external-worker up
```
