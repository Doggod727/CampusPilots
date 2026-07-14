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
