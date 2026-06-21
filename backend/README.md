# 后端 MVP 骨架

本目录实现 `FastAPI + LangGraph + PostgreSQL + Redis/Valkey + LiveKit` 的生产级 MVP 后端基础骨架。运行路径默认 Provider 为腾讯云 `ASR`、腾讯云 `TTS`、阿里云 `LLM`，并保留配置化切换能力。当前已接入并真实冒烟验证阿里云百炼 OpenAI-compatible `chat/completions`、腾讯云 `TextToVoice`、腾讯云短音频 `SentenceRecognition` 和腾讯云实时 ASR WebSocket stream 边界；`mock` Provider 仅用于 `APP_ENV=test` 自动化测试，不作为本地演示或生产运行路径，非测试环境的 Provider 列表不会暴露 `mock` 作为可选运行供应商。

## 本地运行

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

打开：

- `GET http://localhost:8000/health`
- `POST http://localhost:8000/api/v1/sessions`
- `POST http://localhost:8000/api/v1/agent/turn`
- `POST http://localhost:8000/api/v1/agent/audio-turn`
- `POST http://localhost:8000/api/v1/sessions/{session_id}/voice-worker`
- `POST http://localhost:8000/api/v1/provider-configs/smoke`

默认 `AUTO_START_VOICE_WORKER=true`，后端创建会话后会在当前 FastAPI 进程内自动启动对应的 `Voice Worker`，订阅该会话的 LiveKit 音轨并进入腾讯云实时 ASR 链路。也可以关闭自动启动后手动运行单会话 `Voice Worker`：

```bash
python3 -m backend.app.worker.livekit_voice_worker --session-id <session_id>
```

多实例或生产部署建议使用外部 worker 模式：

```bash
VOICE_WORKER_LAUNCH_MODE=external uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
python3 -m backend.app.worker.voice_worker_runner --worker-id worker-1
```

该模式下后端创建会话时只写入 `voice_worker_job`，独立 worker runner 领取 `queued` job 后运行实时语音链路，并把 `voice_worker.job_claimed`、`voice_worker.job_completed` 或 `voice_worker.job_failed` 写入事件日志。job 带有租约和心跳，`VOICE_WORKER_JOB_LEASE_SECONDS` 控制租约时长，`VOICE_WORKER_HEARTBEAT_INTERVAL_SECONDS` 控制续约频率，`VOICE_WORKER_MAX_ATTEMPTS` 控制最大领取次数。Docker Compose 也提供可选 profile：

```bash
VOICE_WORKER_LAUNCH_MODE=external docker compose --profile external-worker up
```

外部 worker 队列状态可通过 API 查询：

```bash
curl "http://localhost:8000/api/v1/voice-worker/jobs/status?limit=20"
```

该接口返回 `queued/running/failed/completed/cancelled` 数量、`expired_running_jobs`、`heartbeat_stale_jobs`、最近 job 和最近失败 job，用于部署探针、人工排障和后续监控告警接入。

密钥推荐通过环境变量注入：`TENCENT_CLOUD_SECRET_ID`、`TENCENT_CLOUD_SECRET_KEY`、`ALIBABA_CLOUD_API_KEY`。本地开发也可以临时读取 `docs/密钥/` 下的文件；该目录不应提交到版本库。

`LiveKit` 地址有两个配置：

- `LIVEKIT_URL`：后端和 `Voice Worker` 访问 `LiveKit` 的地址，本地 Docker 默认是 `ws://livekit:7880`。
- `PUBLIC_LIVEKIT_URL`：返回给浏览器的地址，本地 Docker 默认是 `ws://127.0.0.1:7880`。

如果浏览器报 `could not establish signal connection: Failed to fetch`，通常是 `PUBLIC_LIVEKIT_URL` 被配置成了容器内 hostname。

## 生产配置门禁

当 `APP_ENV=production` 或 `APP_ENV=staging` 时，后端启动会执行 `validate_runtime_configuration()`：

- `LIVEKIT_API_KEY` 不能使用 `devkey`。
- `LIVEKIT_API_SECRET` 不能使用 `secret`、`test-secret`、`dev-secret`，且长度必须至少 32 字符。
- `DEFAULT_ASR_PROVIDER`、`DEFAULT_TTS_PROVIDER`、`DEFAULT_LLM_PROVIDER` 不能设置为 `mock`。
- `TENCENT_CLOUD_SECRET_FILE` 和 `ALIBABA_CLOUD_SECRET_FILE` 必须清空，密钥必须通过环境变量或 Secret Manager 注入。
- `SESSION_PERSIST_AUDIO` 必须为 `false`，`ALLOW_TTS_TEXT_ONLY_FALLBACK` 必须为 `false`。
- 如果 LiveKit SDK 或签名配置异常，生产环境不会生成 `local-placeholder.*` token，而是抛出 `runtime.configuration_invalid`。

上线前可先做不启动服务的配置校验：

```bash
python3 -m backend.app.worker.runtime_config_check
```

该命令只读取当前环境变量和 `Settings`，不会连接数据库、不会调用腾讯云或阿里云。生产-like 环境中它会检查 `TENCENT_CLOUD_SECRET_ID`、`TENCENT_CLOUD_SECRET_KEY`、`ALIBABA_CLOUD_API_KEY` 是否真实存在且不是占位值。仓库同时提供 `docker-compose.production.yml` 和 `.env.production.example` 作为部署模板；生产模板不挂载 `docs/密钥/`，应由平台 Secret Manager 或环境变量注入密钥。

上线前还可以运行生产准入 gate：

```bash
python3 -m backend.app.worker.production_release_gate
python3 -m backend.app.worker.production_release_gate --run-provider-smoke --include-realtime-asr
```

默认模式只读取证据文件和验收产物，不消耗云 API；第二条命令会现场复验阿里云 `LLM`、腾讯云 `TTS`、腾讯云短音频 `ASR` 和腾讯云实时 `ASR`。若真人麦克风验收产物缺失，gate 会返回失败，不能作为生产可交付结论。

## 真实云 Provider 冒烟

不启动后端服务、不依赖 `PostgreSQL` 的本地 smoke：

```bash
python3 -m backend.app.worker.provider_smoke
python3 -m backend.app.worker.provider_smoke --include-realtime-asr
```

覆盖范围：

- 阿里云 `qwen-plus`：OpenAI-compatible `chat/completions`。
- 腾讯云 `TextToVoice`：返回 `audio/wav`。
- 腾讯云短音频 `SentenceRecognition`：识别 TTS 生成的测试语音。
- 腾讯云实时 ASR WebSocket：将 TTS 生成的 16k mono WAV PCM 发送到实时 ASR，并读取最终转写。

已验证结果：

- `python3 -m backend.app.worker.provider_smoke`：阿里云 LLM、腾讯云 TTS、腾讯云短音频 ASR 真实调用成功。
- `python3 -m backend.app.worker.provider_smoke --include-realtime-asr`：腾讯云实时 ASR 返回 8 条消息，最终文本为“这是一轮云服务语音接入冒烟测试。”。

## LiveKit E2E 冒烟

启动 `postgres`、`redis`、`livekit` 和后端后，可运行合成 caller 冒烟：

```bash
python3 -m backend.app.worker.livekit_e2e_smoke --timeout-seconds 60
```

该脚本会创建真实后端会话，使用返回的 caller token 加入 LiveKit，发布腾讯云 TTS 生成的 16k mono WAV 音轨，然后等待后端事件：

- `transcript.final`
- `voice_worker.agent_audio_published`

已验证结果：

- 合成 caller 音轨发布成功。
- `Voice Worker` 经腾讯云实时 ASR 得到 `transcript.final`：“这是一轮云服务语音接入冒烟测试。”。
- Agent 完成回复并发布 LiveKit `agent-voice` 音轨。
- `Voice Worker` 会通过 LiveKit Data Channel 向前端镜像 `transcript.final`、`agent.reply.preparing` 和音轨发布成功后的 `agent.reply.answered` 标准事件；播报失败时会镜像 `session.error`。
- smoke 完成后会调用会话结束接口。

## 浏览器 Web Voice 冒烟

启动 `postgres`、`redis`、`livekit`、后端和前端后，可运行浏览器 fake microphone smoke：

```bash
cd frontend
VITE_SESSION_API_BASE_URL=http://127.0.0.1:8000 WEB_VOICE_APP_URL=http://127.0.0.1:5173 npm run smoke:web-voice
```

该脚本会使用腾讯云 TTS 生成测试 WAV，作为 Chrome fake microphone 输入，真实走 `Browser -> LiveKit -> Voice Worker -> Tencent Realtime ASR -> Agent -> Tencent TTS -> LiveKit Data Channel -> Vue UI`。脚本断言页面曾观察到：

- `transcript.final`
- `agent.reply.preparing`
- `agent.reply.answered`
- “这是一轮云服务语音接入冒烟测试”

已验证结果：

- 浏览器 smoke 会话 `a6c7f2b9-5c08-4172-8397-89ae42a81d31` 通过，页面观察到上述 3 类标准事件和转写文本片段。
- smoke 输出会对 LiveKit WebSocket token 做脱敏；当前 `livekit/livekit-server:v1.10` 与 `livekit-client@2.19.2` 已完成兼容验证，浏览器 smoke 输出 `console.error_count=0`。

## 真人麦克风验收

启动 `postgres`、`redis`、`livekit`、后端和前端后，可运行：

```bash
cd frontend
VITE_SESSION_API_BASE_URL=http://127.0.0.1:8000 WEB_VOICE_APP_URL=http://127.0.0.1:5173 npm run acceptance:manual-mic
```

该脚本会打开 headed Chrome，使用真实麦克风完成一轮人工验收，并把客观事件和主观确认写入 `output/manual-mic-acceptance/manual-mic-acceptance-*.json`。验收记录不保存原始音频，适合用于后续调参和版本准入。

## 测试

```bash
pytest
```

测试默认使用内存 `SQLite`，不会依赖本地 `PostgreSQL`、`Redis` 或 `LiveKit` 进程。

## 当前缺口

- 当前 `agent/turn` 已可真实调用阿里云 LLM 并合成腾讯云 TTS，但 TTS 音频暂不入库，只记录合成结果元数据。
- 当前 `audio-turn` 已支持短音频 `ASR -> LLM -> TTS` HTTP 闭环；实时通话默认由后端自动启动会话级 `Voice Worker`，订阅 LiveKit 音轨并通过腾讯云实时 ASR WebSocket 持续推流。
- `audio-turn` 已接入 `Turn Manager` 低置信度确认：当 ASR 返回 `confidence` 低于 `TURN_LOW_CONFIDENCE_THRESHOLD` 时，后端记录 `turn.low_confidence`，并生成确认话术，不直接调用 LLM 推进业务回答。
- `LiveKitVoiceWorker` 已接入沉默超时提示第一版：超过 `TURN_SILENCE_TIMEOUT_SECONDS` 未观察到新活动时，后端记录 `turn.silence_timeout` 并通过 Data Channel 向前端发送可恢复提示。
- `LiveKitVoiceWorker` 已接入播报中打断的轻量 VAD 门槛：只有连续达到 `TURN_BARGE_IN_MIN_VOICE_FRAMES`、且 PCM RMS 高于 `TURN_BARGE_IN_RMS_THRESHOLD` 的 16k mono 用户音频帧才会触发 `agent.reply.interrupted`，避免短噪声或回声直接中断 TTS。
- `LiveKitVoiceWorker` 已接入误打断恢复第一版：如果 `agent.reply.interrupted` 后在 `TURN_INTERRUPTION_RECOVERY_TIMEOUT_SECONDS` 内没有形成新的 `transcript.final`，会记录并镜像 `agent.reply.interrupt_recovered`，前端回到继续聆听状态。
- `LiveKit` token 签发有真实 JWT 路径；本地开发允许占位 fallback，生产-like 环境禁止生成 placeholder token。
- 当前已实现 `Voice Worker` 入会准备接口、真实 LiveKit room 连接、音频 track 订阅事件监听、音频帧元数据记录、腾讯云实时 ASR 持续 stream sink、音轨结束时 flush 最终 ASR 消息、最终转写触发 Agent、标准事件 Data Channel 镜像，以及 TTS `wav` 发布为 LiveKit `agent-voice` 音轨的可测路径；浏览器 fake microphone smoke 已验证前端能看到真实转写、准备回答和已回答事件。
- 已新增 DB-backed `voice_worker_job` 与独立 `voice_worker_runner`，生产可配置为外部 worker 领取任务；job 已具备租约、心跳、过期重领和最大尝试次数。更复杂的分布式锁、worker 心跳监控面板和告警仍需按部署环境继续加固。
- 已新增 `GET /api/v1/voice-worker/jobs/status`，可查看外部 worker 队列数量、过期租约、心跳停滞和最近失败；这只是运维状态 API，正式监控告警仍需接入部署环境。
- 已提供 `npm run acceptance:manual-mic` 真人麦克风验收入口；尚未在当前仓库看到一份通过的人工验收 JSON 记录。打断阈值、恢复时间窗和沉默策略仍需基于现场验收继续调参。
