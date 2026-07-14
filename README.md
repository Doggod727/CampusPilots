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
