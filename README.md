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
[`docx/03-API接口/API接口定义+M1-M4.yaml`](docx/03-API接口/API接口定义+M1-M4.yaml)
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
