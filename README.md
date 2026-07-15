# CampusPilot

学生生活一站式社区 AI 助手。

## M4 后端本地运行

```powershell
Copy-Item .env.example .env
cd backend
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload
```

`.env.example` 只包含本地演示值和密钥占位符。真实的 JWT、DeepSeek 等密钥只写入本机 `.env`，不得提交。当前 `/health/live` 不读取配置或访问外部依赖。

本地 HTTP 演示使用 `REFRESH_COOKIE_SECURE=false`；生产环境必须在本机部署配置中显式设置为 `true`，否则浏览器不会以 Secure 属性保存 Refresh Cookie。

存活检查：`GET http://localhost:8000/health/live`

就绪检查：`GET http://localhost:8000/health/ready`。该接口只在请求时探测
PostgreSQL 和 Redis；依赖缺失或不可用返回统一的 `503 SERVICE_NOT_READY`，
不会在导入应用或存活检查时建立连接。当前仓库没有 Chroma 适配器，响应会明确标记
`chroma: up / not configured`，真实 PostgreSQL、Redis 和 Chroma 联调需在后续环境完成。

API 使用全局 CORS：仅允许 `FRONTEND_ORIGIN`（自动兼容末尾 `/`），允许凭据以及
`Authorization`、`Content-Type`、`X-Request-Id`、`Idempotency-Key` 请求头。刷新和登出
仍额外校验 `Origin`，不因全局 CORS 放宽 Cookie 会话安全边界。

## M5 Agent 运行时配置

M5 的 Router、Agent、Approval、Tool、Reranker 和模型/数据集对象根目录均通过
`.env` 注入；完整非敏感模板见 `.env.example`。默认采用规则/本地 Router 置信度
`0.80`、最多 6 步和 3 个专业 Agent，MCP 默认关闭。路径配置只作为未来运行时的
对象根目录，本任务不会自动创建目录、加载模型或启动训练。DeepSeek API Key 仍只从
环境变量读取，不写入数据库、日志或配置响应。

这些配置只在显式构造 M5 运行时服务时读取；导入应用和调用 `/health/live` 不会因此
加载模型、连接数据库或访问外部服务。

可恢复运行时使用独立的 `AGENT_CHECKPOINT_SECRET` 对短期 Checkpoint 做认证加密，
不得与 JWT 或外部 API 密钥复用；`AGENT_CHECKPOINT_TTL_SECONDS` 默认 3600 秒。
密钥只在显式构造持久化运行时组件时读取，不写入数据库、日志或对象表示。

Agent Run 的启动、恢复和取消使用 PostgreSQL 事务 Outbox。HTTP 事务提交成功即保证命令
已持久化，Worker 可在崩溃后重新领取；Redis 通知仅用于降低唤醒延迟，不可用时继续按
`AGENT_RUNTIME_POLL_SECONDS` 轮询数据库。

Agent Run 事件使用 `GET /api/v1/agent-runs/{run_id}/stream` 下行 SSE。客户端可在
`Last-Event-ID` 传入上次收到的数字 sequence 进行重放；非法或超前游标返回
`409 AGENT_EVENT_CURSOR_INVALID`。SSE 不接收审批，审批仍调用对应的 HTTP 接口。

数据集上传写入 `DATASET_ARTIFACT_ROOT` 下的隔离区，默认保留 3600 秒且最大 100 MiB。
仅服务端生成的对象键进入数据库和 API；原始文件名不会作为磁盘路径。

训练 API 当前是 P0 数据库队列骨架：创建只返回 `queued`，不会启动 GPU 或真实微调。
允许的本地基座模型由 `LOCAL_TRAINING_BASE_MODELS` 配置；DeepSeek API 模型不会进入本地训练。

模型注册只保存受控对象键和哈希；响应会移除密钥、Token和Secret配置。模型激活要求已有
成功评估，`complex_generation` 始终保留DeepSeek活动兜底。

评估 API 提供 `/api/v1/evaluations` 的分页、创建、详情和 2～5 项结果比较。创建仅登记
`queued` 任务并要求 `Idempotency-Key`，不会在 HTTP 请求内启动 GPU、DeepSeek 或本地
模型；只有冻结、校验有效且无敏感数据的数据集版本可以引用。当前真实执行由后续可插拔
Worker 接入，不能把排队成功解释为评估已完成。

运行当前后端测试：

```powershell
cd backend
python -m pytest
```

## M4 管理接口

当前后端已提供用户/角色/权限、敏感词、审核案件、审计日志、业务配置和看板接口，
均使用 OpenAPI 中的 `x-permissions` 权限码、统一成功/错误信封和 `X-Request-Id`。
写操作按契约使用幂等键或乐观锁；配置只允许 `editable=true` 项更新，敏感快照会递归脱敏。
接口路径、请求字段和稳定错误码以
[`docx/deliverables/openapi.yaml`](docx/deliverables/openapi.yaml)
为准。

## M4 数据库迁移

迁移从仓库根目录 `.env` 的 `DATABASE_URL` 读取 PostgreSQL 连接。进入 `backend` 后执行：

```powershell
# 不连接数据库，仅生成待执行 SQL
python -m alembic upgrade head --sql

# PostgreSQL 可用时执行升级或降级
python -m alembic upgrade head
python -m alembic downgrade base
```

当前开发机没有 Docker/PostgreSQL，仅完成了 Alembic 离线 SQL 验证；真实空库升降级仍需在可用环境中执行。

## M4 演示账号种子

在 PostgreSQL 已完成迁移且本机 `.env` 已配置后，临时注入演示密码并执行种子命令：

```powershell
cd backend
$env:DEMO_SEED_PASSWORD = Read-Host "Set a local demo seed password"
python -m app.scripts.seed_demo
Remove-Item Env:DEMO_SEED_PASSWORD
```

命令会重复收敛到预定义的系统角色、权限、角色权限和 6 个演示账号。密码只在运行时读取并写入 Argon2id 哈希，命令输出不会显示密码。

本仓库当前交付范围为后端与契约文档；前端和 Docker Compose 尚未创建。没有 Docker、
PostgreSQL 或 Redis 的机器只能执行模拟依赖、离线迁移 SQL 和 pytest，不能宣称真实空库
升级/降级或就绪探针集成验证已完成。
