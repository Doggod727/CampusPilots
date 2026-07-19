# 模型权重挂载目录

Compose 默认把本目录以只读方式挂载为容器的 `/models`。**镜像构建不下载任何模型权重**。

使用前将所需模型放入本目录（或启动前用 `MODEL_HOST_DIR` 指向其父目录）：

- `bge-small-zh-v1.5/` — RAG 嵌入模型（sentence-transformers 完整目录）

本机现有模型位于 `E:/CampusPilotServices/bge-small-zh-v1.5`，可直接：

```powershell
docker compose up -d --build   # 先设置环境变量 MODEL_HOST_DIR=E:/CampusPilotServices
```

模型文件体积大，切勿提交到 Git。
