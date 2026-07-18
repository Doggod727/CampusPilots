# AGENTS.md

> 面向 AI 编码代理的项目说明。阅读本文即可了解仓库全貌；细节以代码与 `docx/deliverables/` 中的契约为准。

## 项目概览

- CampusPilot（学生生活一站式社区 AI 助手）：为学生提供校园服务、社区互助、知识库问答与多智能体能力的一站式平台。
- 当前交付范围：**FastAPI 后端**（`backend/`）与**契约/设计文档**（`docx/deliverables/`）。前端与 Docker Compose 尚未创建。
- 技术栈：Python ≥ 3.12、FastAPI、Pydantic v2 + pydantic-settings、SQLAlchemy 2.0 异步（asyncpg）+ Alembic、Redis、PyJWT、argon2-cffi、cryptography、httpx、pypdf；可选 `ai` 依赖组为 chromadb 与 sentence-transformers。
- 文档、注释以中文为主；提交信息使用英文（如 `feat(m3): wire real community tool adapters`）。仓库托管于 GitHub（Doggod727/CampusPilot），默认分支 `main`。

## 仓库布局

- `backend/` — 唯一可执行代码（Python 包 `campuspilot-backend`）
  - `app/main.py` — FastAPI 入口 `create_app()`，注册全部路由与中间件。
  - `app/core/` — `config.py`（Pydantic Settings，读取仓库根目录 `.env`）、`errors.py`（`AppError` 与统一异常处理）、`request_id.py`（`X-Request-Id` 中间件）。
  - `app/shared/responses.py` — 统一成功/错误信封模型。
  - `app/infrastructure/database.py` — 异步引擎与会话工厂，需显式构造。
  - `app/modules/` — 五个业务模块（见下节）。
  - `app/scripts/` — 种子与 Worker CLI（见“常用命令”）。
  - `migrations/` — Alembic 迁移（`0001`–`0009`，始终保持唯一 Head）。
  - `tests/` — 145 个 pytest 文件，无 `conftest.py`；`fixtures/` 存放冻结评测集。
- `docx/deliverables/` — 需求分析、概要设计、详细设计（M1–M5 五个 Part）、`openapi.yaml`（**API 契约事实源**）、`redocly.yaml`（lint 配置）、`sql/` 设计 SQL、`00-M5重构-本地覆盖与迁移说明.md`。
- `.env.example` — 配置模板，只含本地演示值；真实密钥只写本机 `.env`（已被 `.gitignore` 排除）。
- `todo.md` — 任务看板：固定开发规则、契约与设计差异记录、各 Issue 完成记录与显式待办。
- `DESIGN.md` — 面向未来前端的设计令牌分析（Cursor 风格），与后端无直接耦合。

## 模块划分（`app/modules/`）

- `platform`（M4 公共基础与平台治理）：认证/JWT/Refresh Cookie、RBAC、用户管理、敏感词、审核案件、审计日志、业务配置、看板、`/health/live` 与 `/health/ready`。
- `campus_service`（M2 校园服务中心）：部门、服务指南与材料清单、工单状态机、电费查询/充值、办事进度。
- `community`（M3 校园社区与互助）：话题/帖子/评论/反应/举报、匿名身份、活动与报名、失物招领与认领交接、联系方式加密（`encryption.py`）。
- `ai_knowledge`（M1 AI 与知识库）：知识库/文档生命周期、异步入库（解析、确定性切分、向量索引）、授权 RAG 检索、会话与消息、同步 Chat + SSE、引用与反馈。
- `agent_platform`（M5 智能体与模型工程）：Agent/Tool 目录、Run 编排（`orchestration/`：Router/Supervisor/Runtime）、Tool 网关（`tool_gateway/`：目录、执行器与各业务适配器）、审批、Checkpoint、Run 事件 SSE、内部 Tool 端点（`internal_tools.py`）、数据集、真实训练 Worker（`training_worker.py`，LoRA/QLoRA）、模型注册、五类真实评估 Provider（`evaluation_providers.py`）与评估 Worker。

## 运行时架构要点

- PostgreSQL 是事实源；Chroma 只保存可重建向量索引；Redis 用于限流和 Worker 唤醒通知，不可用时按 `AGENT_RUNTIME_POLL_SECONDS` 轮询数据库。
- DeepSeek API 是唯一外部 LLM，仅接受 `deepseek-v4-pro` 且显式关闭 Thinking；结构化路由、ToolCall 与最终回答均做严格 Schema 校验。
- Agent Run 的启动/恢复/取消使用 PostgreSQL 事务 Outbox；事件经 `GET /api/v1/agent-runs/{run_id}/stream` SSE 下行，支持 `Last-Event-ID` 重放。
- 惰性启动纪律：导入应用与调用 `/health/live` 不读取配置、不连接数据库/Redis/外部服务；配置只在显式构造运行时组件或启动 Worker 进程时读取。
- 持久化运行时装配（`agent_platform/composition.py`）已为 M1 知识、M2 校园服务/电费、M3 社区、M4 治理接入全部 14 个真实 Tool Handler（Mock 仅兜底目录外未覆盖工具，#192 验收零漂移）。
- ModelOps 真实执行：`MODELOPS_EXECUTION_MODE=local` 时 `training_worker` 执行真实 LoRA（CPU/CUDA，产物与 SHA-256 落 E 盘 `artifacts/`）、`evaluation_worker` 执行五类真实 Provider（RAG/Agent/Tool/Model/System，指标来自真实执行）；`disabled`（默认）时评估任务稳定 `EVALUATION_PROVIDER_UNAVAILABLE` 失败，不产生伪造指标；Fake Evaluator 仅存于 `tests/fake_evaluators.py`。

## 常用命令

以下命令除注明外均在仓库根目录/`backend/` 下以 PowerShell 执行：

```powershell
Copy-Item .env.example .env                 # 首次：创建本机配置
cd backend
python -m pip install -e ".[dev]"           # 安装运行时 + 测试依赖
python -m pip install -e ".[ai]"            # 可选：Chroma/Embedding（生产启动前）
python -m pip install -e ".[modelops]"      # 可选：torch/transformers/peft/accelerate（真实训练/评估）
pwsh -File scripts/start-dev.ps1            # 一键启动：PostgreSQL + Redis + API + 全部 Worker
pwsh -File scripts/smoke.ps1                # 跨模块冒烟（真实环境，68 项）
python -m uvicorn app.main:app --reload     # 启动 API（http://localhost:8000）
python -m pytest                            # 全量测试（pyproject 已配 testpaths/-q）
python -m compileall app                    # 编译检查
python -m alembic upgrade head --sql        # 离线生成迁移 SQL（不需要数据库）
python -m alembic upgrade head              # 真实升级（需 PostgreSQL；读取根 .env 的 DATABASE_URL）
python -m alembic downgrade base            # 真实降级
python -m app.scripts.runtime_worker        # M5 Agent 运行时 Worker
python -m app.scripts.evaluation_worker     # M5 评估 Worker
python -m app.scripts.training_worker       # M5 真实训练 Worker（需 [modelops] 依赖）
python -m app.scripts.ingestion_worker      # M1 文档入库 Worker
python -m app.scripts.seed_demo             # M4 演示账号种子（先设 DEMO_SEED_PASSWORD）
python -m app.scripts.seed_ai_knowledge     # M1 知识种子
python -m app.scripts.seed_agent_platform   # M5 目录种子
```

真实环境专项探针（`scripts/`，可重复执行，要求无同类 Worker 在线）：`verify-runtime-outbox-concurrency.ps1`（并发领取）、`verify-runtime-checkpoint-recovery.ps1`（崩溃恢复）、`verify-runtime-rate-limits.ps1`（双维度限流）、`verify-deepseek-provider-faults.ps1`（Provider 故障矩阵）、`verify-m5-runtime-acceptance.ps1`（M5 总验收）、`verify-modelops-integration.ps1`（ModelOps 全链）。

健康检查：`GET /health/live`（无任何依赖）；`GET /health/ready`（仅在请求时探测 PostgreSQL/Redis/Chroma）。

## 代码与契约约定

- 统一信封：成功 `SuccessResponse{code,message,data,request_id,timestamp}`；错误 `ErrorResponse{code,message,details,request_id,timestamp}`；领域错误抛 `AppError`，由全局异常处理器统一转换。
- 所有请求经过 `RequestIdMiddleware` 生成/校验并回传 `X-Request-Id`（中间件注册在 CORS 之后，预检响应也带关联头）。
- `docx/deliverables/openapi.yaml` 是契约事实源：接口路径、请求字段、稳定错误码以其为准；**已发布 operationId 不得改变**；权限用 `x-permissions` 权限码声明。Redocly lint 配置在同目录，保留 5 条既有非阻断警告。
- 写操作按契约使用 `Idempotency-Key` 幂等键或乐观锁版本号；业务配置仅 `editable=true` 项可更新，敏感快照递归脱敏。
- CORS 仅放行 `FRONTEND_ORIGIN`（自动兼容末尾 `/`）；`/api/v1/auth/refresh` 与 `/logout` 额外校验 `Origin`，不因全局 CORS 放宽 Cookie 会话边界。
- 实现中发现的 OpenAPI/状态码/字段差异，先记录到 `todo.md` 的“契约与设计差异”，模块收尾时统一修订受影响文档。
- 开发流程（`todo.md` 固定规则）：一次只完成一个小型任务；每个任务开 GitHub Issue 记录问题、范围与验收标准并在提交中关联，验证推送后关闭；每个模块使用同名分支（`m1`…`m5`）与同一个持续交付 PR（未完成保持 Draft，验收后转 Ready）。

## 测试说明

- 策略：FastAPI `TestClient` + dependency override + Stub/Fake/确定性内存端口，不依赖真实 PostgreSQL、Redis、DeepSeek 或 GPU；RAG 评测集冻结于 `tests/fixtures/`。
- 验收惯例（见 `todo.md` 各 Issue 记录）：全量 pytest、`compileall`、OpenAPI lint、Alembic 唯一 Head 与离线升降级全部通过后才算完成。
- 本机环境（2026-07 起稳定可用）：conda 环境 `campuspilot`（`D:\anaconda\envs\campuspilot`，Python 3.12，已装 `.[dev]`/`.[ai]`/`.[modelops]`）；PostgreSQL 17 便携实例 `E:\CampusPilotServices\PostgreSQL`；Redis 本机 6379；Chroma/BGE/模型与数据目录均在 `E:\CampusPilotServices\`。依赖钉版：`fastapi>=0.115,<0.137`（≥0.137 会破坏契约测试路由遍历，勿升级）；Windows 需 `tzdata`（已在依赖中声明）。
- 当前全量测试 `808 passed`（2026-07-18）；跨模块冒烟 68/0；五个真实环境专项探针全部通过。

## 安全注意事项

- 真实密钥只写本机 `.env`；配置模型用 `SecretStr` 保护。JWT、DeepSeek、`INTERNAL_TOOL_SECRET`、`AGENT_CHECKPOINT_SECRET` 必须各自独立、禁止复用；密钥不写入数据库、日志或响应。
- 密码使用 Argon2id 哈希；生产必须显式 `REFRESH_COOKIE_SECURE=true`（本地演示为 `false`）。
- 内部 Tool 端点 `POST /internal/v1/tools/{tool_name}:invoke` 只接受独立 Bearer 服务凭证（不接受用户 JWT），且必须携带 `Idempotency-Key` 并绑定已有 Agent Run/Step；R2/R3 Tool 首次调用返回 202 审批摘要，批准后的相同调用只消费一次审批。
- 文件上传（知识库/数据集/模型制品）写入隔离根目录，只保存服务端生成的对象键与哈希，原始文件名不作磁盘路径，并有路径穿越/符号链接防护与大小上限。
- M3 联系方式等敏感数据使用 `COMMUNITY_DATA_ENCRYPTION_KEY` 加密；审计、日志与 Agent 轨迹只保存脱敏参数摘要。
- Agent Run 与内部 Tool 入口按用户/IP 双维度限流（生产 Redis、测试内存端口），超限统一 `429 RATE_LIMITED` + `Retry-After`。

## 已知边界与待办

- 真实环境验证已完成：真实空库迁移升→降→升、`/health/ready` 集成、M1 端到端（上传→入库→检索→REST/SSE→Tool）、M5 Outbox 并发/Checkpoint 恢复/限流/Provider 故障矩阵、M5 总验收与 ModelOps 全链（见 `todo.md` 各 Issue 记录与 `scripts/verify-*.ps1`）。
- ModelOps 执行边界：QLoRA 需 CUDA+bitsandbytes（本机仅 CPU，稳定拒绝）；本地模型评估当前仅 LoRA 产物前向损失对比；Agent Run 启动恢复保真（>1000 字输入与 mode/context 未入恢复状态）与审批到期协调为显式待办。
- 前端与 Docker Compose 未创建（后续批次）；`DESIGN.md` 为未来前端的设计参考。
