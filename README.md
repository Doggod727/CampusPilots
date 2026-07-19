# CampusPilot

学生生活一站式社区 AI 助手：校园服务、社区互助、知识库问答与多智能体能力的一站式平台。

- 后端：Python 3.12 + FastAPI + SQLAlchemy 2.0 异步（PostgreSQL）+ Redis + Chroma + 本地 BGE 向量模型 + DeepSeek API（固定 `deepseek-v4-pro`）。
- 前端：Vue 3 + TypeScript + Vite + Pinia + Element Plus，API 全部由 `docx/deliverables/openapi.yaml`（136 个 operationId）自动生成，禁止手写请求。
- 状态：M1–M5 后端与全量前端已完成；后端 `809 passed`、跨模块冒烟 `68/0`、前端单测 `98 passed`，真实浏览器与 Docker Compose 联调通过。

## 方式一：Docker Compose（推荐演示）

前置：Docker Desktop；仓库根 `.env` 提供密钥（见下文“配置”）；BGE 模型权重只在宿主持有，启动前设置其位置：

```powershell
$env:MODEL_HOST_DIR = 'E:\CampusPilotServices'   # 内含 bge-small-zh-v1.5\；或将模型放入 deploy\models\
docker compose up -d --build                      # 首次构建并启动全部服务
docker compose ps                                 # migrate 一次性任务完成(Exited 0)后 api 转 healthy
```

访问 `http://localhost:8080`（可用 `WEB_PORT` 改端口）。包含：web（Nginx 托管前端 + 反代 REST/上传/SSE）、api、runtime/ingestion/training/evaluation 四个 Worker、PostgreSQL 17、Redis；`migrate` 服务自动完成迁移与演示种子。Chroma、上传文件、训练产物均在命名卷持久化；镜像构建不下载模型权重。

```powershell
docker compose logs -f api
docker compose down            # 停止；加 -v 清空数据卷
```

## 方式二：Conda 本机启动

前置环境（本机已就绪）：conda 环境 `D:\anaconda\envs\campuspilot`（Python 3.12，已装 `.[dev]`/`.[ai]`/`.[modelops]`）；PostgreSQL 17 便携实例 `E:\CampusPilotServices\PostgreSQL`；Redis 6379；Chroma/BGE/数据目录在 `E:\CampusPilotServices\`。

```powershell
Copy-Item .env.example .env    # 首次；真实密钥只写本机 .env（已 gitignore）
cd backend
python -m alembic upgrade head
$env:DEMO_SEED_PASSWORD = '<本机演示口令>'
python -m app.scripts.seed_demo; python -m app.scripts.seed_ai_knowledge; python -m app.scripts.seed_agent_platform
cd ..
pwsh -File scripts\start-dev.ps1    # 一键启动 PostgreSQL + Redis + API + 全部 Worker（幂等，日志在 logs/）
pwsh -File scripts\status-dev.ps1   # 状态检查；stop-dev.ps1 幂等停止
```

前端（`frontend/`，需 pnpm）：

```powershell
cd frontend
pnpm install
pnpm dev          # http://localhost:5173，代理到 127.0.0.1:8000
```

## 验证

```powershell
curl http://127.0.0.1:8000/health/live     # 存活（无依赖）
curl http://127.0.0.1:8000/health/ready    # 就绪（按需探测 PG/Redis/Chroma）
pwsh -File scripts\smoke.ps1               # 跨模块冒烟（68 项）
cd backend; python -m pytest               # 后端全量测试
cd frontend
pnpm generate:api && git diff --exit-code src/api/generated   # 契约零漂移
pnpm typecheck; pnpm lint; pnpm test:unit; pnpm build; pnpm check:guards
$env:PLAYWRIGHT_BROWSERS_PATH='E:\CampusPilotServices\playwright-browsers' # 本机已有浏览器时
pnpm exec playwright test                                          # 真实后端浏览器验收
```

真实环境专项探针（可重复）：`scripts/verify-runtime-outbox-concurrency.ps1`、`verify-runtime-checkpoint-recovery.ps1`、`verify-runtime-rate-limits.ps1`、`verify-deepseek-provider-faults.ps1`、`verify-m5-runtime-acceptance.ps1`、`verify-modelops-integration.ps1`。

## 演示账号

种子口令为 `DEMO_SEED_PASSWORD`（Compose 默认 `CampusPilot-Demo-2026!`）：

| 账号 | 用途 |
| --- | --- |
| admin01 | 超级管理员（用户/角色/敏感词/审核/审计/配置） |
| knowledge01 | 知识库管理、文档入库 |
| service01 | 工单处理 |
| community01 | 社区运营（话题/活动/审核） |
| student01 / student02 | 学生（问答、工单、社区、电费、失物招领） |

## 说明

- ModelOps：`MODELOPS_EXECUTION_MODE=local` 时训练/评估真实执行（LoRA 支持 CPU/CUDA，QLoRA 需 CUDA+bitsandbytes；评估含 RAG/Agent/Tool/Model/System 五类 Provider）；`disabled` 时评估任务稳定失败、不产生伪造指标。
- 演示数据：校区、部门、指南、知识库文档来自四川大学官网公开快照（`backend/app/scripts/data/scu/README.md` 含溯源与哈希）；电费为明确 Mock（`is_simulated=true`）。
- 前端安全约束：Access Token 仅驻留内存，Refresh Token 只走 HttpOnly Cookie；业务代码禁止 localStorage/sessionStorage/IndexedDB 与手写 API 路径（`pnpm check:guards` 强制）。
- 已知环境边界：QLoRA 需要 CUDA + bitsandbytes；本机 CPU 仅验收 LoRA。真实校园事项进度和电费支付仍是明确 Mock，不会伪装为生产能力。
