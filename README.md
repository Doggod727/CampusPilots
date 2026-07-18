# CampusPilot

学生生活一站式社区 AI 助手：校园服务、社区互助、知识库问答与多智能体能力的一站式平台。

- 后端：Python 3.12 + FastAPI + SQLAlchemy 2.0 异步（PostgreSQL）+ Redis + Chroma + 本地 BGE 向量模型 + DeepSeek API（固定 `deepseek-v4-pro`）。
- 契约：`docx/deliverables/openapi.yaml`（136 个 operationId，Redocly lint 通过）。
- 状态：M1–M5 后端全部完成并在真实环境（PostgreSQL 17、Redis、Chroma、BGE、DeepSeek、四川大学公开数据）验证通过；跨模块冒烟 68/0；全量测试 800+。前端与 Docker Compose 开发中。

## 前置环境（本机已就绪）

- conda 环境：`D:\anaconda\envs\campuspilot`（Python 3.12，已安装 `.[dev]`、`.[ai]`、`.[modelops]`）。
- PostgreSQL 17 便携实例：`E:\CampusPilotServices\PostgreSQL`（`pg_ctl -D data start`）。
- Redis：本机 6379（Memurai 或便携实例）。
- Chroma / BGE / 模型与数据目录：均位于 `E:\CampusPilotServices\`（路径见 `.env`）。

## 配置

```powershell
Copy-Item .env.example .env   # 首次
```

`.env.example` 只含演示值；真实密钥（JWT、DeepSeek、INTERNAL_TOOL_SECRET、AGENT_CHECKPOINT_SECRET、社区加密）各自独立，只写本机 `.env`（已被 gitignore，严禁提交）。

## 迁移与种子

```powershell
cd backend
python -m alembic upgrade head     # 真实迁移（读取根 .env）
$env:DEMO_SEED_PASSWORD = '<本机演示口令>'
python -m app.scripts.seed_demo    # 演示账号 + 四川大学真实公开数据（幂等）
python -m app.scripts.seed_ai_knowledge
python -m app.scripts.seed_agent_platform
```

## 启动（API + 四个 Worker）

```powershell
pwsh -File scripts\start-dev.ps1   # 一键启动：PostgreSQL + Redis + API + 全部 Worker（幂等，日志在 logs/）
pwsh -File scripts\status-dev.ps1  # 状态检查：进程 + /health/ready
pwsh -File scripts\stop-dev.ps1    # 幂等停止应用进程（不动共享 PG/Redis）
```

或手动（`backend/` 下）：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m app.scripts.runtime_worker       # Agent 运行时
python -m app.scripts.ingestion_worker     # 文档入库
python -m app.scripts.training_worker      # 真实 LoRA 训练（MODELOPS_EXECUTION_MODE=local）
python -m app.scripts.evaluation_worker    # 真实评估（五类 Provider）
```

## 验证

```powershell
curl http://127.0.0.1:8000/health/live     # 存活（无依赖）
curl http://127.0.0.1:8000/health/ready    # 就绪（按需探测 PG/Redis/Chroma）
pwsh -File scripts\smoke.ps1               # 跨模块冒烟（68 项）
cd backend; python -m pytest               # 全量测试
```

专项真实环境探针（可重复）：`scripts/verify-runtime-outbox-concurrency.ps1`、`verify-runtime-checkpoint-recovery.ps1`、`verify-runtime-rate-limits.ps1`、`verify-deepseek-provider-faults.ps1`、`verify-m5-runtime-acceptance.ps1`、`verify-modelops-integration.ps1`。

## 说明

- ModelOps：`MODELOPS_EXECUTION_MODE=local` 时训练/评估真实执行（LoRA 支持 CPU/CUDA，QLoRA 需 CUDA+bitsandbytes；评估含 RAG/Agent/Tool/Model/System 五类 Provider）；`disabled`（默认）时评估任务稳定失败、不产生伪造指标。
- 演示数据：校区、部门、指南、知识库文档来自四川大学官网公开快照（`backend/app/scripts/data/scu/README.md` 含溯源与哈希）；电费为明确 Mock（`is_simulated=true`）。
- 前端与 Docker Compose 开发中，尚未交付。
