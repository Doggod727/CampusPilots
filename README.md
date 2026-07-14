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

运行当前后端测试：

```powershell
cd backend
python -m pytest
```

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
