# CampusPilot

学生生活一站式社区 AI 助手。

## M4 后端本地运行

```powershell
cd backend
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload
```

存活检查：`GET http://localhost:8000/health/live`

运行当前后端测试：

```powershell
cd backend
python -m pytest
```
