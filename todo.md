# CampusPilot 开发任务

## 固定开发规则

- 一次只完成一个小型任务。
- 每次完成任务并验证通过后，在本文档标记完成情况。
- 开发模块前，先创建并推送对应名称的远端分支，例如 M3 使用 `m3`。
- 严格按当前任务范围实现，不做需求外扩展或过度兜底。
- 每个小型任务使用 GitHub Issue 记录问题、范围和验收标准，并在提交中关联。

## 当前模块

- M4：公共基础与平台治理
- 开发分支：`m4`

## 已完成

- [x] [#1 M4：建立后端骨架与存活检查](https://github.com/Doggod727/CampusPilot/issues/1)（2026-07-14）
  - 建立 Python 3.12 + FastAPI 最小后端骨架。
  - 实现 Request-Id 生成、校验和响应回传。
  - 实现匿名 `GET /health/live` 及 OpenAPI 规定的统一响应。
  - 添加启动说明与自动化测试；`3 passed`。
- [x] [#2 M4：建立环境配置基线](https://github.com/Doggod727/CampusPilot/issues/2)（2026-07-14）
  - 使用 Pydantic Settings 统一读取并校验环境变量。
  - 使用 SecretStr 保护 JWT 与 DeepSeek 密钥，Token 时长限制为正整数。
  - 添加根目录 `.env.example` 和本地配置说明。
  - 保持 `/health/live` 不依赖配置或外部服务；全部自动化测试 `8 passed`。
- [x] [#3 M4：统一异常响应信封](https://github.com/Doggod727/CampusPilot/issues/3)（2026-07-14）
  - 增加领域 AppError 与 OpenAPI 扁平错误响应模型。
  - 统一处理领域异常、请求校验、HTTP 错误和未知异常。
  - 错误响应统一回传 Request-Id，且不泄露原始校验输入或内部异常文本。
  - 全部自动化测试 `12 passed`，Python 编译检查通过。

## 待办

- [ ] 下一项小型任务尚未选择。
