# CampusPilot — Jira 导入清单 (V2.0 M5 优先)

> **Jira 项目**：`scu2026-A-R-01-学生生活一站式社区AI助手 (NAILONG)`
>
> **设计基线**：需求分析说明书 V2.1 / 概要设计说明书 V1.0 / OpenAPI V0.5.0
>
> **开发周期**：2 个 Sprint（Sprint 1: 6 天 + Sprint 2: 6 天），共 12 天
>
> **团队**：4 人 × 7h/天 = 28h/天，总工时 336h
>
> **已实现基础**：M4 后端 58 个 Issue 已完成（认证、RBAC、用户管理、敏感词、审核、审计、配置、看板、Health、CORS），M2 电费服务已完成

---

## 史诗（Epic）— 7 个

| 标题 | 问题类型 | 优先级 | 史诗名称 | 描述 |
|------|----------|--------|----------|------|
| M5 智能体与模型工程平台 | 史诗 | 最高 | M5 智能体与模型工程平台 | M5 是系统的统一交互与编排中心，负责意图路由、Supervisor 编排、Tool 注册/执行/确认、多智能体协作、模型网关（DeepSeek + 本地模型）、数据集/训练/模型注册/评估闭环。所有 M1–M4 通过 Tool 契约接入 M5，不直接写入其他模块数据表。 |
| M1 AI 与知识库 Tool 提供 | 史诗 | 高 | M1 AI 与知识库 Tool 提供 | M1 保留知识库、文档、会话和反馈全部需求，首期优先提供预置知识的检索与 RAG 问答能力，通过 `knowledge.search` 和 `knowledge.answer` 两个 P0 Tool 接入 M5。完整知识管理后台降级为 P1。 |
| M2 校园服务中心 Tool 提供 | 史诗 | 高 | M2 校园服务中心 Tool 提供 | M2 首期优先提供 5 个事件 Tool：办事指南查询、报修创建/查询、电费余额查询、模拟充值申请。完整校园服务门户和管理后台降级为 P1。电费领域 Service 已先行实现。 |
| M3 校园社区与互助 Tool 提供 | 史诗 | 高 | M3 校园社区与互助 Tool 提供 | M3 首期优先提供 4 个事件 Tool：活动搜索/报名、失物发布/匹配查询。通用帖子/评论/点赞后台降级为 P1。 |
| M4 平台治理 M5 兼容 | 史诗 | 高 | M4 平台治理 M5 兼容 | 在已完成的 M4 认证/RBAC/审核/审计/配置基础上，新增 M5 所需的 4 个治理 Tool 适配器、M5 权限码/角色/配置种子、scope CHECK 扩展和兼容迁移脚本。 |
| 基础设施与部署 | 史诗 | 高 | 基础设施与部署 | M5 新增 Agent Platform 数据库迁移（SQL 009/012/013）、Chroma/Redis 服务搭建、Docker Compose 联调环境、种子数据完善。 |
| 联调集成与测试 | 史诗 | 中 | 联调集成与测试 | M5 与 M1–M4 真实 Tool 对接、多智能体 P0 闭环联调、端到端测试、安全护栏验证、Sprint Review。 |

---

## 问题（Issue）— 共 50 个

### Sprint 1（第 1–6 天）：M5 基础设施 + M1/M2/M3 服务 + Tool 契约冻结

---

### 一、M5 智能体与模型工程平台（14 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-M5-001 M5 数据库迁移与 Agent Platform Schema | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 覃焓 | 14h |
| CP-M5-002 Agent Platform ORM 与 Repository 层 | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 覃焓 | 14h |
| CP-M5-003 ToolDefinition / ToolRegistry 契约与注册中心 | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 张讯毓 | 14h |
| CP-M5-004 ToolExecutor 执行引擎（权限/审批/幂等/超时） | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 张讯毓 | 21h |
| CP-M5-005 ApprovalService 写操作确认服务 | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 覃焓 | 14h |
| CP-M5-006 ModelGateway 模型网关（DeepSeek 集成） | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 王一林 | 14h |
| CP-M5-007 GuardrailService 安全护栏（输入/工具/输出） | 故事 | 高 | M5 智能体与模型工程平台 | 1 | 覃焓 | 10h |
| CP-M5-008 TraceService 执行轨迹记录 | 故事 | 高 | M5 智能体与模型工程平台 | 1 | 覃焓 | 10h |
| CP-M5-009 AgentRegistry 与 6 个 Agent 定义 | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 李欢 | 10h |
| CP-M5-010 RouterService 意图路由（规则+DeepSeek 回退） | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 李欢 | 14h |
| CP-M5-011 Supervisor 编排服务（路由→规划→执行→聚合） | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 李欢 | 21h |
| CP-M5-012 LangGraph GraphRuntime 状态图运行时 | 故事 | 最高 | M5 智能体与模型工程平台 | 1 | 李欢 | 21h |
| CP-M5-013 DatasetService 数据集版本管理 | 故事 | 高 | M5 智能体与模型工程平台 | 1 | 王一林 | 10h |
| CP-M5-014 ModelRegistry + Evaluation 模型注册与评估 | 故事 | 高 | M5 智能体与模型工程平台 | 1 | 王一林 | 14h |

### 二、M1 AI 与知识库 Tool 提供（5 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-M1-001 M1 知识库 Schema 迁移与种子知识导入 | 故事 | 高 | M1 AI 与知识库 Tool 提供 | 1 | 王一林 | 10h |
| CP-M1-002 EmbeddingProvider 与 Chroma VectorStore | 故事 | 高 | M1 AI 与知识库 Tool 提供 | 1 | 王一林 | 14h |
| CP-M1-003 RetrieverService 授权检索与引用映射 | 故事 | 高 | M1 AI 与知识库 Tool 提供 | 1 | 王一林 | 14h |
| CP-M1-004 RAGAnswerService 带来源上下文 DeepSeek 问答 | 故事 | 高 | M1 AI 与知识库 Tool 提供 | 1 | 王一林 | 14h |
| CP-M1-005 M1 Tool 适配器（knowledge.search / knowledge.answer） | 故事 | 高 | M1 AI 与知识库 Tool 提供 | 1 | 王一林 | 10h |

### 三、M2 校园服务中心 Tool 提供（6 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-M2-001 ServiceGuide 办事指南查询服务 | 故事 | 高 | M2 校园服务中心 Tool 提供 | 1 | 张讯毓 | 14h |
| CP-M2-002 WorkOrder 报修工单领域服务与状态机 | 故事 | 高 | M2 校园服务中心 Tool 提供 | 1 | 张讯毓 | 21h |
| CP-M2-003 WorkOrder 工单仓储与权限策略 | 故事 | 高 | M2 校园服务中心 Tool 提供 | 1 | 张讯毓 | 14h |
| CP-M2-004 M2 电费路由接口完善（REST API） | 故事 | 高 | M2 校园服务中心 Tool 提供 | 1 | 张讯毓 | 7h |
| CP-M2-005 M2 Tool 适配器 — 办事指南与报修（3 个 Tool） | 故事 | 高 | M2 校园服务中心 Tool 提供 | 1 | 张讯毓 | 14h |
| CP-M2-006 M2 Tool 适配器 — 电费（2 个 Tool） | 故事 | 高 | M2 校园服务中心 Tool 提供 | 1 | 张讯毓 | 7h |

### 四、M3 校园社区与互助 Tool 提供（5 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-M3-001 M3 社区 Schema 迁移与种子数据 | 故事 | 高 | M3 校园社区与互助 Tool 提供 | 1 | 李欢 | 7h |
| CP-M3-002 CampusEvent 校园活动领域服务 | 故事 | 高 | M3 校园社区与互助 Tool 提供 | 1 | 李欢 | 14h |
| CP-M3-003 LostFound 失物招领领域服务与匹配算法 | 故事 | 高 | M3 校园社区与互助 Tool 提供 | 1 | 李欢 | 21h |
| CP-M3-004 M3 Tool 适配器 — 活动（2 个 Tool） | 故事 | 高 | M3 校园社区与互助 Tool 提供 | 1 | 李欢 | 7h |
| CP-M3-005 M3 Tool 适配器 — 失物（2 个 Tool） | 故事 | 高 | M3 校园社区与互助 Tool 提供 | 1 | 李欢 | 7h |

### 五、M4 平台治理 M5 兼容（3 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-M4-M5-001 M5 兼容迁移（SQL 009：CHECK 扩展/权限/角色/配置） | 任务 | 最高 | M4 平台治理 M5 兼容 | 1 | 覃焓 | 7h |
| CP-M4-M5-002 M4 治理 Tool 适配器（check_content / authorize / audit） | 故事 | 高 | M4 平台治理 M5 兼容 | 1 | 覃焓 | 14h |
| CP-M4-M5-003 AgentConfig Adapter 与前端种子账号 Token 更新 | 任务 | 高 | M4 平台治理 M5 兼容 | 1 | 覃焓 | 7h |

### 六、基础设施与部署（2 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-INFRA-001 M5 迁移执行链验证（SQL 009–013 Alembic 迁移） | 任务 | 最高 | 基础设施与部署 | 1 | 覃焓 | 7h |
| CP-INFRA-002 Chroma/Redis 服务搭建与 Docker Compose 更新 | 任务 | 高 | 基础设施与部署 | 1 | 覃焓 | 7h |

---

### Sprint 2（第 7–12 天）：多智能体闭环 + API + 联调 + 测试

---

### 七、M5 智能体与模型工程平台 — API 与前端（7 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-M5-101 AgentRun REST API（创建/查询/取消/SSE 流式） | 故事 | 最高 | M5 智能体与模型工程平台 | 2 | 李欢 | 21h |
| CP-M5-102 Approval REST API（确认卡片/决策/过期） | 故事 | 最高 | M5 智能体与模型工程平台 | 2 | 覃焓 | 10h |
| CP-M5-103 Agent/Tool Catalog 查询 API | 故事 | 高 | M5 智能体与模型工程平台 | 2 | 李欢 | 7h |
| CP-M5-104 ModelOps API（数据集/训练/模型/评估接口） | 故事 | 高 | M5 智能体与模型工程平台 | 2 | 王一林 | 14h |
| CP-M5-105 5 个专业 Agent 实现（Knowledge/Service/Community/Governance/ModelOps） | 故事 | 最高 | M5 智能体与模型工程平台 | 2 | 李欢 | 21h |
| CP-M5-106 前端 Agent 工作台页面（对话/流式进度/确认卡片/轨迹） | 故事 | 高 | M5 智能体与模型工程平台 | 2 | 李欢 | 21h |
| CP-M5-107 前端 ModelOps 管理页面（数据集/模型/评估） | 故事 | 中 | M5 智能体与模型工程平台 | 2 | 王一林 | 14h |

### 八、M1/M2/M3 真实 Tool 对接与 REST API（4 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-INT-001 M1 知识问答 SSE 流式接口 + 真实 Tool 接入 M5 | 故事 | 最高 | M1 AI 与知识库 Tool 提供 | 2 | 王一林 | 14h |
| CP-INT-002 M2 报修/电费真实 Tool 接入 M5 + 前端工单页面 | 故事 | 最高 | M2 校园服务中心 Tool 提供 | 2 | 张讯毓 | 14h |
| CP-INT-003 M3 活动/失物真实 Tool 接入 M5 | 故事 | 最高 | M3 校园社区与互助 Tool 提供 | 2 | 李欢 | 10h |
| CP-INT-004 M4 治理 Tool 接入 M5（安全/授权/审计闭环） | 故事 | 高 | M4 平台治理 M5 兼容 | 2 | 覃焓 | 10h |

### 九、联调集成与测试（4 个）

| 标题 | 问题类型 | 优先级 | 史诗名称 | Sprint | 经办人 | 原始预估 |
|------|----------|--------|----------|--------|--------|----------|
| CP-INT-005 多智能体 P0 闭环联调（Supervisor→Agent→Tool→审批） | 任务 | 最高 | 联调集成与测试 | 2 | 全员 | 28h |
| CP-INT-006 端到端测试与 Bug 修复（安全护栏/超时/降级/回放） | 任务 | 最高 | 联调集成与测试 | 2 | 全员 | 21h |
| CP-INT-007 OpenAPI 契约校验与文档同步 | 任务 | 高 | 联调集成与测试 | 2 | 覃焓 | 7h |
| CP-INT-008 Sprint Review 与项目验收 | 任务 | 中 | 联调集成与测试 | 2 | 全员 | 14h |

---

## 统计汇总

| 指标 | 数值 |
|------|------|
| 史诗 | 7 |
| 问题 | 50 |
| Sprint 1 问题 | 35 |
| Sprint 2 问题 | 15 |
| 总预估工时 | 658h（含全员任务中的个人工时） |
| 可支配总工时 | 336h（4 人 × 7h/天 × 12 天） |

| 成员 | M5 子域 | Sprint 1 工时 | Sprint 2 工时 | 总计 | 负责模块 |
|------|---------|--------------|--------------|------|----------|
| 覃焓 | runtime / evaluation / M4 兼容 | 83h | 41h | 124h | M4 兼容 + M5 运行时/安全 + INFRA + 集成 |
| 张讯毓 | tool_gateway / M2 | 77h | 14h | 91h | M2 服务/Tool + ToolRegistry/Executor |
| 王一林 | modelops / M1 | 76h | 42h | 118h | M1 服务/Tool + ModelGateway/Dataset/Model |
| 李欢 | orchestration / M3 | 87h | 80h | 167h | M3 服务/Tool + Supervisor/Agent/Graph/API |

> **注**：李欢的工时偏高（167h 超出可用 168h 接近极限），"全员"任务（INT-005/006/008）中的个人工时约为 15h/人，实际可支配工时约 153h/人。INT-005/006 中的全员工时（28h+21h=49h）为团队累计工时而非单人工时，实际每人约 12h。

---

## 各 Issue 详细描述

### Sprint 1

#### CP-M5-001 M5 数据库迁移与 Agent Platform Schema
- **用户故事**：作为开发人员，我希望建立 M5 Agent Platform 的数据库 schema，为后续所有 M5 功能提供持久化基础。
- **功能描述**：执行 SQL 012（agent_platform_schema）创建 agent_definitions、agent_runs、agent_steps、tool_definitions、tool_calls、approval_requests、datasets、training_jobs、model_versions、evaluation_jobs 等表，保留完整约束、索引和注释。执行 SQL 013 插入 6 个 Agent、14 个 Tool 和 4 个预置模型的种子数据。
- **验收标准**：
  1. SQL 012/013 在空库上 clean install，无错误
  2. 所有表结构与详细设计 10.3 节状态机一致
  3. 种子数据可查询（6 Agent + 14 Tool + 4 Model + 权限 + 配置）
  4. Alembic 单 head、离线升降级通过

#### CP-M5-002 Agent Platform ORM 与 Repository 层
- **用户故事**：作为开发人员，我希望映射 M5 所有数据表为 SQLAlchemy ORM 模型，并实现仓储层，提供上层服务调用的数据访问接口。
- **功能描述**：映射 AgentRun、AgentStep、ToolCall、ApprovalRequest、Dataset/DatasetVersion、TrainingJob、ModelVersion、EvaluationJob 等实体，实现对应的 Repository 类（CRUD、分页筛选、状态更新、行锁读取）。不管理调用方 Session 生命周期。
- **验收标准**：
  1. 所有 ORM 模型字段/类型/约束与迁移一致
  2. 仓储方法通过 PostgreSQL 方言编译
  3. 全量 pytest 通过（新增 ≥10 条 ORM/仓储测试）
  4. 不跨 Schema 建立外键，不注册 Alembic metadata

#### CP-M5-003 ToolDefinition / ToolRegistry 契约与注册中心
- **用户故事**：作为 Agent 开发者，我希望有一个统一的 Tool 注册中心，能按 name/version/module/permission/risk 管理和发现所有 14 个 Tool。
- **功能描述**：定义 Pydantic ToolDefinition schema（name、version、module、description、input_schema、output_schema、required_permissions、risk_level、timeout_ms、idempotent、requires_approval、enabled、implementation_ref）。实现 ToolRegistry（register、resolve、list_allowed、get_active），支持按用户权限过滤可见 Tool 列表。
- **验收标准**：
  1. ToolDefinition 与 OpenAPI V0.5.0 /tool-catalog 一致
  2. 重复 name 注册安全拒绝（409 TOOL_ALREADY_REGISTERED）
  3. list_allowed 仅返回用户有权限的启用 Tool
  4. 14 个种子 Tool 均可 resolve 成功

#### CP-M5-004 ToolExecutor 执行引擎（权限/审批/幂等/超时）
- **用户故事**：作为系统，我需要一个统一的 Tool 执行引擎，确保每个 Tool 调用都经过 Schema 校验、权限检查、审批确认、幂等保护和超时控制。
- **功能描述**：实现 ToolExecutor，编排 ToolCall 完整生命周期：prepare（Schema 校验）→ authorize（调用 M4 governance.authorize_tool）→ 审批检查（R2/R3 需有效 approval_id）→ execute（调用 module Tool Adapter，设置 timeout）→ 记录审计。支持 Idempotency-Key 重放（同哈希返回原结果，不同哈希安全拒绝）。
- **验收标准**：
  1. 无效参数返回 TOOL_ARGUMENT_INVALID（422）
  2. 无权限返回 TOOL_FORBIDDEN（403）
  3. 缺失审批返回 TOOL_APPROVAL_REQUIRED（409）
  4. 审批过期/用户不匹配/参数哈希变化返回 TOOL_APPROVAL_INVALID（409）
  5. 同 Key 同哈希成功重放，同 Key 不同哈希返回 IDEMPOTENCY_CONFLICT（409）
  6. 超时返回 TOOL_TIMEOUT（504）
  7. 每次执行写入 audit（内部 governance.write_audit）

#### CP-M5-005 ApprovalService 写操作确认服务
- **用户故事**：作为学生用户，我希望 Agent 在执行写操作（报修、报名、充值申请等）前向我展示确认卡片，我确认后才真正执行，防止误操作。
- **功能描述**：实现 ApprovalService，为所有 R2/R3 写 Tool 生成确认请求（action_summary、parameter_snapshot SHA-256 哈希、user_id、agent_run_id、expires_at）。支持用户 approve/reject 决策（一次性消费）。审批过期自动失效。审批内容不保存原始敏感参数。
- **验收标准**：
  1. 创建审批返回 approval_id、参数摘要、过期时间
  2. 审批卡片展示可读的参数摘要（不泄露密码/Token/完整身份等敏感信息）
  3. approve 后 approval_id 一次性消费，重复使用拒绝
  4. 用户不匹配/过期/已消费/参数被修改均安全拒绝
  5. 审批拒绝写入 audit

#### CP-M5-006 ModelGateway 模型网关（DeepSeek 集成）
- **用户故事**：作为系统，我需要一个统一的模型网关，封装对不同模型（DeepSeek V4 Pro、本地 bge Embedding）的调用，并记录模型版本、Token 消耗和延迟指标。
- **功能描述**：实现 ModelGateway，使用 OpenAI Python SDK 调用 deepseek-v4-pro 的 Chat Completion API。封装 EmbeddingProvider（sentence-transformers + bge-small-zh-v1.5）。所有调用记录 model_version、input/output tokens、duration_ms。API Key 仅从环境变量 DEEPSEEK_API_KEY 读取，不持久化。
- **验收标准**：
  1. 可通过 DeepSeek API 完成一次 Tool Calls 请求（含多 Tool 选择）
  2. 可通过 bge-small-zh-v1.5 生成 512 维向量
  3. 模型不可用时返回 MODEL_DEPENDENCY_UNAVAILABLE（502），不崩溃
  4. API Key 不出现在日志、trace、audit 中

#### CP-M5-007 GuardrailService 安全护栏（输入/工具/输出）
- **用户故事**：作为平台运营者，我希望所有 Agent 交互都经过安全护栏检查，防止注入攻击、危险指令和非法内容输出。
- **功能描述**：实现 GuardrailService，包含三个检查点：check_input（Prompt 注入检测、危险指令过滤）、check_tool（Tool 参数合法性、禁止操作清单）、check_output（输出内容安全扫描，调用 M4 SensitiveWordService + ModerationService）。任何检查失败均终止执行并返回安全拒绝。
- **验收标准**：
  1. 输入包含已知注入模式（忽略系统提示、要求绕过限制等）返回 AGENT_INPUT_REJECTED（422）
  2. Tool 参数中出现文件路径遍历、命令执行等模式返回 TOOL_ARGUMENT_REJECTED（422）
  3. 输出触发敏感词/审核规则时标记并脱敏
  4. 护栏不泄露输入原文或检测规则

#### CP-M5-008 TraceService 执行轨迹记录
- **用户故事**：作为用户和管理员，我希望查看 Agent 完整执行轨迹，包括路由决策、Agent 步骤、Tool 调用、确认记录、耗时和错误，便于调试和审计。
- **功能描述**：实现 TraceService，记录 AgentRun → AgentStep → ToolCall 的完整调用链。每一步保存 parent/child 关系、状态、input/output 摘要、duration_ms、model_version、errors。敏感字段（Token、密码、密钥）在 trace 中脱敏。trace 按 run_id 查询，用户只能查看自己的 run。
- **验收标准**：
  1. trace 包含完整的 run_id → step_id → tool_call_id / approval_id → audit_id 链路
  2. input/output 摘要 ≤500 字符，敏感字段已脱敏
  3. 用户只能查看本人的 agent_run trace
  4. 管理员可通过 dashboard 查看聚合指标（run 总数、成功率、平均耗时）

#### CP-M5-009 AgentRegistry 与 6 个 Agent 定义
- **用户故事**：作为系统，我需要一个 Agent 注册中心，管理 6 个 Agent（Supervisor、Knowledge、Service、Community、Governance、ModelOps）的定义、版本、System Prompt 和 Tool 白名单。
- **功能描述**：实现 AgentRegistry，管理 AgentDefinition（agent_id、name、version、description、system_prompt、tool_allowlist、enabled、allowed_user_roles）。每个 Agent 白名单严格限定可调用 Tool（如 Knowledge Agent 只能调用 knowledge.search / knowledge.answer）。ModelOps Agent 仅限 admin 角色访问。
- **验收标准**：
  1. 6 个 Agent 定义与种子数据一致
  2. Agent 白名单严格执行，越权 Tool 调用拒绝
  3. 禁用的 Agent 不被 Router 选中
  4. list_for_user 按用户角色过滤可见 Agent

#### CP-M5-010 RouterService 意图路由（规则+DeepSeek 回退）
- **用户故事**：作为学生用户，我希望输入自然语言问题后，系统自动识别我的意图并路由到合适的 Agent 处理。
- **功能描述**：实现 RouterService，支持两级路由策略：1）规则引擎（关键词/模式匹配，优先匹配高优先级规则）；2）DeepSeek 回退（规则无法匹配时调用 DeepSeek 进行意图分类）。路由结果包含 target_agent、confidence、source、reason_code。低置信度返回 clarify，引导用户明确意图。
- **验收标准**：
  1. "宿舍空调坏了怎么报修" → 路由到 service agent，置信度 ≥ 0.8
  2. "最近有什么活动" → 路由到 community agent
  3. "什么是学分绩点" → 路由到 knowledge agent
  4. 模糊输入 → 返回 clarify
  5. 路由过程记录 model_version 和 latency

#### CP-M5-011 Supervisor 编排服务（路由→规划→执行→聚合）
- **用户故事**：作为系统，我需要 Supervisor Agent 接收路由结果后，规划任务、调用专业 Agent、收集结果并聚合最终回答。
- **功能描述**：实现 SupervisorService，编排完整运行流程：接收路由结果 → TaskPlanner 分解任务（跨域请求拆分为最多 3 个子任务，标记依赖关系和目标 Agent）→ 依次调用专业 Agent → 收集结果 → ResultAggregator 聚合（单域任务直接返回，跨域任务调用 DeepSeek 汇总）。最大 6 步，超步骤或循环则终止。
- **验收标准**：
  1. 单域问题仅激活一个专业 Agent
  2. 跨域问题最多分解为 3 个子任务，顺序执行
  3. 重复 Agent+Tool+参数签名检测 → 终止并报 AGENT_LOOP_DETECTED
  4. 超过 6 步 → 终止并报 AGENT_MAX_STEPS_EXCEEDED
  5. 部分子任务失败 → 返回 partial 状态和已完成结果
  6. 聚合结果通过 output guardrail 检查

#### CP-M5-012 LangGraph GraphRuntime 状态图运行时
- **用户故事**：作为开发人员，我希望使用 LangGraph StateGraph 管理 Agent 运行的状态机，支持流式 SSE 推送、暂停等待审批、恢复执行和取消。
- **功能描述**：实现 GraphRuntime，基于 LangGraph StateGraph 构建 P0 执行图：start_guardrail → route → (clarify?) → plan_tasks → invoke_specialist → execute_tool → (need_approval?) → await_approval → execute_tool → (more_tasks?) → aggregate → output_guardrail → end。支持 stream（SSE 推送每个 step/tool 状态）、cancel（中断运行）、resume_after_approval（审批通过后恢复）。
- **验收标准**：
  1. P0 图包含所有必需节点和状态转换
  2. SSE 流式推送 step 开始/完成、tool 调用/结果、审批请求事件
  3. cancel 操作正确中断运行并记录 cancelled 状态
  4. 审批通过后正确 resume 到 execute_tool 节点
  5. checkpoint 持久化到 PostgreSQL（使用 LangGraph PostgresSaver）

#### CP-M5-013 DatasetService 数据集版本管理
- **用户故事**：作为模型工程管理员，我希望管理训练数据集的版本，支持上传、校验、冻结和脱敏检查，确保训练任务引用不可变数据版本。
- **功能描述**：实现 DatasetService，支持创建数据集、上传 JSONL 文件（路由/指令微调/重排格式）、校验（格式/标签/去重/脱敏状态检查）、freeze 冻结为不可变版本。训练任务只能引用 frozen 版本。数据集来源和脱敏状态强制标记。
- **验收标准**：
  1. 支持 routing/instruction/reranker 三种数据集类型
  2. JSONL 格式校验失败返回 DATASET_FORMAT_INVALID（422）
  3. freeze 后禁止追加/修改数据
  4. 脱敏未完成的数据集禁止 frozen
  5. Agent trajectory 不可自动进入训练集

#### CP-M5-014 ModelRegistry + Evaluation 模型注册与评估
- **用户故事**：作为模型工程管理员，我希望注册模型版本、管理生命周期（候选→激活→停用→回滚）和提交评估任务。
- **功能描述**：实现 ModelRegistryService（register/activate/deactivate/rollback），同一用途最多一个 active 模型。实现 EvaluationService，支持创建评估任务（指定 frozen 数据集、待评估模型、评估指标），异步执行后生成评估报告（准确率、延迟、资源消耗等）。预置 4 个种子模型版本。
- **验收标准**：
  1. register 创建 candidate 状态模型版本
  2. activate 将原 active 置为 inactive，新模型置为 active
  3. rollback 仅改变 active 指针，不删除历史
  4. 评估任务关联 frozen 数据集版本
  5. 评估报告包含 route accuracy / Tool success rate / RAG recall@K / latency / memory
  6. 4 个种子模型可查询

---

#### CP-M1-001 M1 知识库 Schema 迁移与种子知识导入
- **用户故事**：作为开发人员，我希望建立 M1 AI 知识库的数据库 schema 并导入预置种子知识，为知识检索和问答提供数据基础。
- **功能描述**：执行 SQL 005/006（ai_knowledge schema + seed），创建 knowledge_bases、documents、document_chunks、conversations、messages 等表。种子知识覆盖常见校园问题（政策、办事、活动、生活），chunk 已预向量化或标记待索引。确保 embedding 模型就绪后可完成批量向量化。
- **验收标准**：
  1. SQL 005/006 在空库上 clean install
  2. 种子知识 ≥ 20 个文档/100 个 chunk，覆盖 M1 P0 需要的所有知识域
  3. chunk 包含 chunk_id/document_id/source/page 元数据
  4. 与 02-概要设计说明书中 RAG pipeline 的数据格式一致

#### CP-M1-002 EmbeddingProvider 与 Chroma VectorStore
- **用户故事**：作为开发人员，我希望基于 bge-small-zh-v1.5 实现文本向量化，并将向量存储到 Chroma，为 RAG 检索提供基础能力。
- **功能描述**：实现 EmbeddingProvider（sentence-transformers + bge-small-zh-v1.5，输出 512 维向量）和 Chroma VectorStore 适配器（add/delete/search，按 knowledge_base_id 过滤，支持阈值过滤和 Top-K 返回）。定义 EmbeddingPort/VectorStorePort 抽象接口，隔离具体实现。
- **验收标准**：
  1. 文本向量化返回 512 维浮点数向量
  2. Chroma search 返回 Top-K chunk 及相似度分数
  3. 按 knowledge_base_id 过滤生效
  4. 模型不可用时服务降级（返回明确错误，不崩溃）
  5. embedding 缓存到本地，不每次都加载模型

#### CP-M1-003 RetrieverService 授权检索与引用映射
- **用户故事**：作为学生用户，我向 AI 提问后，系统应在我有权访问的知识库中检索相关内容并返回引用来源。
- **功能描述**：实现 RetrieverService，提供 search_authorized 方法：查询改写 → 向量检索（Chroma Top 20） → 阈值过滤 → 引用映射（chunk → document → 来源 URL/位置）。搜索结果过滤用户无权访问的知识库。返回 chunks 带 score、citation metadata（文档名、chunk 位置、可访问链接）。
- **验收标准**：
  1. 授权过滤：学生只能检索 published 状态的公开文档
  2. 阈值 < 0.5 的 chunk 不返回
  3. 引用至少含 document_name、chunk_id、source_url
  4. 空结果返回空列表，不抛异常
  5. 检索延迟（含 embedding + Chroma 查询）< 2s

#### CP-M1-004 RAGAnswerService 带来源上下文 DeepSeek 问答
- **用户故事**：作为学生用户，我希望系统基于校园知识库给我可信、带来源引用的回答，知识库无法覆盖时明确告知。
- **功能描述**：实现 RAGAnswerService，接收用户问题 + RetrievalService 检索结果 → 构建带上下文和引用要求的 System Prompt → 调用 DeepSeek 生成回答 → 映射引用到回答中的标记 → 返回 answer、citations、confidence。低置信度或检索为空时返回兜底回答（建议联系相关部门）。
- **验收标准**：
  1. 检索到相关内容时回答含 ≥1 个引用
  2. 检索为空时返回兜底（"抱歉，当前知识库中暂无相关信息，建议联系..."）
  3. 引用包含可访问的 source_url（或 document 预览路径）
  4. 不编造不存在于检索结果中的信息
  5. System Prompt 不包含其他用户的对话历史或敏感信息

#### CP-M1-005 M1 Tool 适配器（knowledge.search / knowledge.answer）
- **用户故事**：作为 M5 Agent 系统，我需要 M1 提供两个 Tool 适配器，使知识检索和问答能力可被 Agent 调用。
- **功能描述**：实现 knowledge.search 适配器（整合授权检查 + RetrieverService.search_authorized，返回 chunks + citations）和 knowledge.answer 适配器（整合 RAGAnswerService.answer，返回 answer + citations + confidence）。适配器符合 ToolDefinition 契约（input/output JSON Schema、权限 knowledge:read、风险 R0/R1、超时 5s/60s）。
- **验收标准**：
  1. knowledge.search 可在 ToolExecutor 中注册并成功调用
  2. knowledge.answer 可在 ToolExecutor 中注册并成功调用
  3. 无 knowledge:read 权限用户调用返回 TOOL_FORBIDDEN
  4. 适配器不直接访问 Repository，通过 Application Service 调用

---

#### CP-M2-001 ServiceGuide 办事指南查询服务
- **用户故事**：作为学生用户，我希望查询学校各类办事指南，了解办理流程、所需材料、办理地点和时间。
- **功能描述**：实现 ServiceGuideService，提供 search 方法（按校区/分类/关键词过滤，分页返回）。指南按适用对象（校区/学生类型）过滤。以及 get_guide_detail（含完整步骤、材料清单、联系人）。数据来源于种子 SQL 004。
- **验收标准**：
  1. 按 campus_id + category 过滤查询
  2. 分页返回，默认 20 条/页
  3. only_active 过滤：只返回当前有效期内的指南
  4. 详情包含 steps、materials、location、work_hours、contacts
  5. 不存在的指南返回 404 SERVICE_GUIDE_NOT_FOUND

#### CP-M2-002 WorkOrder 报修工单领域服务与状态机
- **用户故事**：作为学生用户，我希望在线提交宿舍报修申请，并可查询处理进度和结果。
- **功能描述**：实现 WorkOrderService（create/get/list）和 WorkOrderStateMachine（状态机：submitted → accepted → processing → completed / cancelled / rejected）。create 校验宿舍归属、故障类型、描述等必填字段。支持 Idempotency-Key 防重复提交。状态转换执行可达性校验，非法跳转拒绝。WorkOrderAccessPolicy 限制学生只读本人、处理员可读授权范围。
- **验收标准**：
  1. create 必须确认 + 幂等 Key，重复 Key 同哈希重放
  2. submitted 只能 → accepted（处理员）/ cancelled（本人）
  3. accepted 只能 → processing / rejected
  4. processing 只能 → completed
  5. 终态（completed/cancelled/rejected）不可再转换
  6. 学生只能查看自己的工单；处理员按 campus/department 范围查看

#### CP-M2-003 WorkOrder 工单仓储与权限策略
- **用户故事**：作为开发人员，我需要工单的数据持久化和权限策略组件，支撑 WorkOrderService 的数据操作和访问控制。
- **功能描述**：实现 WorkOrderRepository（CRUD、按用户/状态/校区分页查询、行锁读取）和 WorkOrderEventRepository（工单流转事件记录）。以及 WorkOrderAccessPolicy（学生只能查本人、处理员按 campus+department 范围、管理员全局可见）。
- **验收标准**：
  1. 分页查询支持状态/校区/日期范围过滤
  2. 工单事件按时间顺序返回
  3. AccessPolicy 执行严格的资源级权限校验
  4. 不跨 Schema 建立外键

#### CP-M2-004 M2 电费路由接口完善（REST API）
- **用户故事**：作为学生用户，我希望通过 REST API 查询我的宿舍电费余额和提交模拟充值申请。
- **功能描述**：基于已完成的 ElectricityService，实现 GET /api/v1/electricity/accounts/{room_id}（查询余额，需 electricity:read_own 权限）和 POST /api/v1/electricity/topup-requests（提交模拟充值，需 electricity:topup_request:create 权限）。room_id 越权返回 TOOL_FORBIDDEN（403），防止房间枚举。
- **验收标准**：
  1. 余额查询返回 balance、update_time、source=mock
  2. 充值申请创建后返回 topup_request_id、金额、状态 simulated
  3. 本人房间可查，非本人房间返回 403
  4. 充值金额限制 1.00–500.00 CNY
  5. 所有响应标记 is_simulated=true

#### CP-M2-005 M2 Tool 适配器 — 办事指南与报修（3 个 Tool）
- **用户故事**：作为 M5 Agent 系统，我需要 M2 提供 service.get_guide、work_order.create、work_order.get 三个 Tool 适配器，使报修和指南服务可被 Agent 编排调用。
- **功能描述**：实现 3 个 M2 Tool 适配器：
  - service.get_guide：调用 ServiceGuideService.search → 返回指南列表（适用对象、步骤、材料、地点、时间、联系人）
  - work_order.create：调用 WorkOrderService.create → 创建报修工单（需 R2 确认 + 幂等 Key）
  - work_order.get：调用 WorkOrderService.get_visible → 查询用户可见工单及进度
  适配器符合 ToolDefinition 契约。
- **验收标准**：
  1. service.get_guide 在 ToolExecutor 中注册成功，按分类/校区查询
  2. work_order.create 强制 requires_approval=true，无审批拒绝
  3. work_order.get 仅返回本人或授权范围的工单
  4. 所有适配器不直接访问 Repository

#### CP-M2-006 M2 Tool 适配器 — 电费（2 个 Tool）
- **用户故事**：作为 M5 Agent 系统，我需要 M2 提供 electricity.get_balance 和 electricity.create_topup_request 两个 Tool 适配器。
- **功能描述**：基于已完成的 ElectricityService，实现 2 个 Tool 适配器：
  - electricity.get_balance：调用 ElectricityService.get_balance → 返回 Mock 余额、更新时间、数据来源
  - electricity.create_topup_request：调用 ElectricityService.create_topup_request → 创建模拟充值申请（需 R2 确认，1–500 CNY）
- **验收标准**：
  1. 适配器在 ToolExecutor 中注册并调用成功
  2. electricity.create_topup_request 强制 requires_approval=true
  3. 响应标记 source=mock、is_simulated=true
  4. 不涉及真实支付和余额变更

---

#### CP-M3-001 M3 社区 Schema 迁移与种子数据
- **用户故事**：作为开发人员，我希望建立 M3 校园社区数据库 schema 并导入种子活动/失物数据。
- **功能描述**：执行 SQL 007/008（community schema + seed），创建 topics、posts、comments、events、event_registrations、lost_found_items、claims 等表。种子数据含 ≥5 个演示活动、≥10 条演示失物招领记录。
- **验收标准**：
  1. SQL 007/008 在空库上 clean install
  2. 失物联系信息使用 SensitiveDataCipher 加密存储
  3. 活动有 capacity/max_capacity 容量字段，注册时行锁校验
  4. 种子数据可供前端演示使用

#### CP-M3-002 CampusEvent 校园活动领域服务
- **用户故事**：作为学生用户，我希望搜索可报名的校园活动，并在线完成报名。
- **功能描述**：实现 CampusEventService，提供 search_open（按时间/分类/关键词搜索可报名活动）和 register（活动报名，需容量行锁校验、幂等 Key、R2 确认）。活动截止后禁止新报名。取消报名仅限已报名用户。搜索结果按活动时间排序。
- **验收标准**：
  1. search_open 只返回状态 open 且未过截止时间的活动
  2. register 执行 FOR UPDATE 行锁防止超额报名
  3. 同一用户重复报名返回 409 DUPLICATE_RESOURCE
  4. 截止时间后报名返回 409 EVENT_REGISTRATION_CLOSED
  5. 容量满后报名返回 409 EVENT_CAPACITY_FULL

#### CP-M3-003 LostFound 失物招领领域服务与匹配算法
- **用户故事**：作为学生用户，我希望发布失物/拾物信息，系统帮我匹配候选记录，并在匹配成功后安全地交换联系方式。
- **功能描述**：实现 LostFoundService（publish/list_matches_for_owner）和 LostFoundMatcher 纯函数（加权评分算法：类别 35% + 时间 25% + 地点 20% + 描述 20%，阈值 0.55）。失物/拾物联系信息使用 AES-GCM/Fernet 加密存储（SensitiveDataCipher），匹配候选仅对记录主人可见。ClaimService 管理认领流程（create → decide → reveal_contact → confirm）。
- **验收标准**：
  1. publish 创建记录，联系信息加密存储
  2. list_matches_for_owner 返回得分 ≥ 0.55 的候选记录及匹配理由
  3. 候选记录联系人信息不暴露（仅主人可见）
  4. 认领流程四步状态转换完整
  5. 双方确认完成后方可互相查看联系方式

#### CP-M3-004 M3 Tool 适配器 — 活动（2 个 Tool）
- **用户故事**：作为 M5 Agent 系统，我需要 M3 提供 event.search 和 event.register 两个 Tool 适配器。
- **功能描述**：实现 2 个 Tool 适配器：
  - event.search：调用 CampusEventService.search_open → 返回可报名活动列表（标题、时间、地点、分类、剩余名额）
  - event.register：调用 CampusEventService.register → 活动报名（需 R2 确认 + 幂等 Key + 容量校验）
- **验收标准**：
  1. 适配器在 ToolExecutor 中注册成功
  2. event.register 强制 requires_approval=true
  3. 确认卡片展示活动名称、时间、地点

#### CP-M3-005 M3 Tool 适配器 — 失物（2 个 Tool）
- **用户故事**：作为 M5 Agent 系统，我需要 M3 提供 lost_found.publish 和 lost_found.search_matches 两个 Tool 适配器。
- **功能描述**：实现 2 个 Tool 适配器：
  - lost_found.publish：调用 LostFoundService.publish → 发布失物/拾物信息（需 R2 确认，联系信息加密）
  - lost_found.search_matches：调用 LostFoundService.list_matches_for_owner → 查询本人记录的匹配候选（R1 敏感读，owner scope）
- **验收标准**：
  1. 适配器在 ToolExecutor 中注册成功
  2. lost_found.publish 强制 requires_approval=true
  3. lost_found.search_matches 仅返回本人记录的候选，含匹配分数和原因
  4. 联系人信息不在 Tool 输出中暴露

---

#### CP-M4-M5-001 M5 兼容迁移（SQL 009：CHECK 扩展/权限/角色/配置）
- **用户故事**：作为开发人员，我需要执行 M4 的增量兼容迁移，使 M5 的新 scope/权限/角色/配置在现有的 M4 数据表上生效。
- **功能描述**：执行 SQL 009（platform_m5_compat），扩展 sensitive_words.scope CHECK（添加 tool_input/tool_output/agent_context）、moderation_cases.target_module CHECK（添加 agent_platform）、插入 15 条 M5 权限码（agent:run、tool:catalog:*、modelops:*、electricity:* 等）、创建 model_engineer 和 agent_runtime 角色、插入 7 条 agent/modelops 命名空间系统配置。Alembic 迁移链以此为基础。
- **验收标准**：
  1. 009 在已有 M4 迁移的数据库上增量执行成功
  2. 新 scope/target_module 值可成功插入
  3. 15 条新权限可查询
  4. 新角色 model_engineer/agent_runtime 含正确权限
  5. 旧 Token 缺失新权限时返回 403（需重新登录）

#### CP-M4-M5-002 M4 治理 Tool 适配器（check_content / authorize / audit）
- **用户故事**：作为 M5 系统，我需要 M4 提供 3 个内部治理 Tool（governance.check_content、governance.authorize_tool、governance.write_audit），为 Agent 运行提供安全、授权和审计基础能力。
- **功能描述**：基于已完成的 M4 服务（SensitiveWordService + ModerationService / RbacService + require_permissions / AuditService），实现 3 个内部 Tool 适配器（runtime_internal，不暴露给 LLM）。governance.check_content 扫描 tool_input/tool_output/agent_context；governance.authorize_tool 执行用户+Tool+资源+风险级别鉴权；governance.write_audit 写入已脱敏的 Agent/Tool 审计事件。
- **验收标准**：
  1. check_content 返回 risk_level、action（allow/mask/review/block）、hit 规则摘要
  2. authorize_tool 返回 allowed/reason_code（无权限/风险过高/资源越权）
  3. write_audit 写入 platform.audit_logs，敏感字段脱敏
  4. 3 个 Tool 均为 runtime_internal，不出现在 LLM Tool 列表中

#### CP-M4-M5-003 AgentConfig Adapter 与前端种子账号 Token 更新
- **用户故事**：作为开发人员，我需要 M5 能读取 agent/modelops 命名空间的系统配置，且前端演示账号的 JWT 包含 M5 新权限。
- **功能描述**：实现 AgentConfigAdapter（调用 ConfigService 读取 agent.*/modelops.* 配置：max_steps=6、max_specialists=3、approval_ttl=300s、tool_timeout_default=10s 等）。更新 seed_demo 脚本，为用户角色补充 M5 P0 权限（service:read、electricity:*、community:read、community:write、knowledge:read、agent:run 等）。旧 Token 刷新后自动获取新权限。
- **验收标准**：
  1. AgentConfigAdapter.get 可读取 agent.max_steps 等配置
  2. 密钥类配置仅从环境变量读取，不通过 ConfigService
  3. seed_demo 重新执行后，student01/student02 拥有完整 M5 P0 权限
  4. Token 刷新后 JWT claims 包含 M5 新权限

---

#### CP-INFRA-001 M5 迁移执行链验证（SQL 009–013 Alembic 迁移）
- **用户故事**：作为开发人员，我需要确保 M5 相关的所有 SQL 迁移（009–013）能通过 Alembic 正确执行，构成完整的迁移链。
- **功能描述**：将 SQL 009–013 整合到 Alembic 迁移链：执行 009_platform_m5_compat.py、012_agent_platform_schema.py、013_agent_platform_seed.py（010/011 已在 0003 中体现）。验证 Alembic 单 head、离线 upgrade/downgrade SQL 正确性。确保 seed_demo 在完整迁移后可正常执行并绑定电费演示数据。
- **验收标准**：
  1. `alembic heads` 返回唯一 head
  2. `alembic upgrade head` 在空库上 clean install，全部 13 个 SQL 对应表创建成功
  3. `alembic downgrade base` 逆序清理所有对象
  4. seed_demo 执行后 student01/02 可登录并拥有完整权限

#### CP-INFRA-002 Chroma/Redis 服务搭建与 Docker Compose 更新
- **用户故事**：作为开发人员，我需要在 Docker Compose 环境中搭建 Chroma 向量数据库和 Redis 缓存服务，支撑 M1 RAG 检索和 M5 会话缓存。
- **功能描述**：更新 docker-compose.yml，增加 Chroma 服务（chroma run --path /chroma_data，持久化卷）和 Redis 服务（redis:7-alpine，密码认证）。更新 .env.example，增加 CHROMA_HOST/CHROMA_PORT、REDIS_URL 配置项。后端健康检查支持 Chroma/Redis 探针。
- **验收标准**：
  1. `docker compose up -d` 后 Chroma:8000、Redis:6379 可访问
  2. `/health/ready` 返回 chroma/redis 状态
  3. 环境变量缺失不崩溃，标记为 not configured
  4. Docker 网络内各服务可互相通信

---

### Sprint 2

#### CP-M5-101 AgentRun REST API（创建/查询/取消/SSE 流式）
- **用户故事**：作为前端用户，我希望通过 REST API 创建 Agent 运行、查看运行列表/详情、流式接收 SSE 进度推送，以及取消或恢复运行。
- **功能描述**：实现 Agent Run API：
  - POST /api/v1/agent-runs：创建运行（输入 user_message、agent_id?）
  - GET /api/v1/agent-runs：分页查询当前用户的运行列表
  - GET /api/v1/agent-runs/{run_id}：查询运行详情（含 steps、tool_calls、final_answer）
  - GET /api/v1/agent-runs/{run_id}/stream：SSE 流式（step_start/step_end/tool_call/tool_result/approval_required/final_answer/error）
  - POST /api/v1/agent-runs/{run_id}/cancel：取消运行
  - POST /api/v1/agent-runs/{run_id}/resume：审批确认后恢复运行
- **验收标准**：
  1. 创建运行后返回 run_id、状态 created
  2. SSE 流按顺序推送事件（路由→步骤→工具→审批→结果）
  3. 取消后 GraphRuntime 中断，状态更新为 cancelled
  4. 用户只能查看/操作自己的运行
  5. 完善 OpenAPI 文档（100 个路径、136 个唯一 operationId）

#### CP-M5-102 Approval REST API（确认卡片/决策/过期）
- **用户故事**：作为前端用户，我希望通过 REST API 查看待确认的操作卡片，选择同意或拒绝。
- **功能描述**：实现 Approval API：
  - GET /api/v1/agent-runs/{run_id}/approvals：查询运行中待确认的审批列表
  - GET /api/v1/agent-runs/{run_id}/approvals/{approval_id}：查询确认卡片详情（action_summary、parameter_summary、expires_at）
  - POST /api/v1/agent-runs/{run_id}/approvals/{approval_id}/approve：同意
  - POST /api/v1/agent-runs/{run_id}/approvals/{approval_id}/reject：拒绝（含 reason）
- **验收标准**：
  1. 确认卡片包含可读的参数摘要（金额、房间号、活动名称等），不泄露敏感信息
  2. approve 后一次性消费，再次调用拒绝
  3. reject 后运行恢复但 Tool 不执行，记录拒绝审计
  4. 过期审批自动返回 409 APPROVAL_EXPIRED
  5. 非本人审批返回 403

#### CP-M5-103 Agent/Tool Catalog 查询 API
- **用户故事**：作为管理员用户，我希望查询系统中所有 Agent 和 Tool 的目录，了解各自的能力、权限和 Schema。
- **功能描述**：实现 Catalog API：
  - GET /api/v1/agents：分页查询 Agent 目录（按用户权限过滤可见 Agent）
  - GET /api/v1/agents/{agent_id}：查询 Agent 详情（含 System Prompt、Tool 白名单、版本）
  - GET /api/v1/tools：分页查询 Tool 目录（按用户权限过滤可用 Tool）
  - GET /api/v1/tools/{tool_name}：查询 Tool 详情（含 Input/Output JSON Schema、权限、风险等级）
- **验收标准**：
  1. Agent 列表仅返回用户角色允许访问的 Agent
  2. Tool 列表仅返回用户有权限的启用 Tool
  3. ModelOps Agent 仅 admin/模型工程管理员可见
  4. 内部 Tool（governance.*）不在返回列表中

#### CP-M5-104 ModelOps API（数据集/训练/模型/评估接口）
- **用户故事**：作为模型工程管理员，我希望通过 REST API 管理数据集、提交训练任务、注册模型和执行评估。
- **功能描述**：实现 ModelOps API：
  - 数据集：GET/POST /api/v1/datasets、POST /api/v1/datasets/{id}/versions、POST /api/v1/datasets/{id}/versions/{v}/freeze
  - 训练：GET/POST /api/v1/training-jobs、POST /api/v1/training-jobs/{id}/cancel
  - 模型：GET/POST /api/v1/models、POST /api/v1/models/{id}/activate、POST /api/v1/models/{id}/deactivate、POST /api/v1/models/{id}/rollback
  - 评估：GET/POST /api/v1/evaluations
- **验收标准**：
  1. 数据集版本 freeze 后不可修改
  2. 训练任务只能在 frozen 数据集版本上创建
  3. 模型 activate 将同用途旧模型置为 inactive
  4. 评估报告包含指标对比
  5. 所有写操作需 modelops:write 权限

#### CP-M5-105 5 个专业 Agent 实现（Knowledge/Service/Community/Governance/ModelOps）
- **用户故事**：作为系统，我需要 5 个专业 Agent 具备完整的领域推理能力，能根据用户请求选择合适的 Tool 完成任务。
- **功能描述**：实现 5 个专业 Agent 的执行逻辑：
  - KnowledgeAgent：接收知识类问题 → 调用 knowledge.search → 判断是否需要 knowledge.answer → 组织带引用回答
  - ServiceAgent：接收服务类请求 → 调用 service.get_guide/work_order.create/work_order.get/electricity.get_balance/electricity.create_topup_request
  - CommunityAgent：接收社区类请求 → 调用 event.search/event.register/lost_found.publish/lost_found.search_matches
  - GovernanceAgent：接收治理类请求 → 内部调用 governance.check_content/authorize_tool（runtime_internal）
  - ModelOpsAgent：接收模型管理类请求 → 查询/操作 Dataset/Model/Evaluation
  每个 Agent 严格限定 Tool 白名单，Supervisor 根据路由结果调用对应 Agent。
- **验收标准**：
  1. KnowledgeAgent：能回答"什么是学分绩点"并给出引用
  2. ServiceAgent：能根据"宿舍空调坏了怎么报修"创建工单（含确认卡片）
  3. CommunityAgent：能搜索"本周文艺活动"并完成报名
  4. GovernanceAgent：正确拦截危险请求并给出原因
  5. Agent 绝不调用白名单外的 Tool
  6. Agent 不直接写数据库，全部通过 Tool 执行

#### CP-M5-106 前端 Agent 工作台页面（对话/流式进度/确认卡片/轨迹）
- **用户故事**：作为学生用户，我希望在 Agent 工作台页面上通过自然语言提出请求，实时看到 AI 的执行进度，确认写操作，并查看最终结果和执行轨迹。
- **功能描述**：使用 Vue 3 + TypeScript + Element Plus 实现 Agent 工作台：
  - 对话界面：输入框 + 历史消息列表（用户消息/AI 回答/引用/确认卡片）
  - 流式进度：SSE 接收显示执行步骤（路由→Agent→Tool→确认等待→结果）
  - 确认卡片：R2/R3 Tool 调用时展示操作摘要、参数，同意/拒绝按钮
  - 运行轨迹：run_id 链接到轨迹详情页，展示完整 step/tool/approval 时间线
- **验收标准**：
  1. 用户输入自然语言并回车后 SSE 流式展示进度
  2. 确认卡片正确展示操作摘要和参数
  3. 用户确认后 Tool 执行，结果在对话中展示
  4. 轨迹页展示完整步骤、耗时、状态和错误信息
  5. 敏感信息不在前端控制台暴露

#### CP-M5-107 前端 ModelOps 管理页面（数据集/模型/评估）
- **用户故事**：作为模型工程管理员，我希望在管理页面查看和管理数据集版本、模型注册和评估结果。
- **功能描述**：实现 ModelOps 管理前端：
  - 数据集页面：列表、上传、版本、freeze 状态
  - 模型页面：注册列表、激活/停用/回滚操作
  - 训练任务页面：提交、状态、取消
  - 评估页面：任务列表、评估报告（指标对比）
- **验收标准**：
  1. 数据集版本状态（draft/frozen）可视化区分
  2. 模型激活/回滚操作带 confirm 二次确认
  3. 训练任务状态实时更新（queued→training→evaluating→succeeded）
  4. 评估报告含准确率、延迟等指标对比

---

#### CP-INT-001 M1 知识问答 SSE 流式接口 + 真实 Tool 接入 M5
- **用户故事**：作为系统集成者，我需要将 M1 的知识检索和问答能力以 SSE 流式接口提供给前端，并通过 ToolExecutor 接入 M5 Agent 编排。
- **功能描述**：实现 M1 对话 SSE 接口（POST /api/v1/chat，SSE 流式返回 text delta、citations、done/error 事件）。将 knowledge.search 和 knowledge.answer 适配器注册到 ToolRegistry，通过 ToolExecutor 执行完整 Tool 调用生命周期（权限→审批→执行→审计）。测试 DeepSeek + RAG 问答质量（标准问题命中率 ≥ 80%）。
- **验收标准**：
  1. SSE 流式输出符合 OpenAPI 定义的事件格式
  2. 对话中断（前端断开）时正确清理资源
  3. Agent 可通过 knowledge.search 获取知识后组织答案
  4. 引用包含可访问的文件链接或 document 预览路径
  5. 知识库无匹配时返回兜底建议

#### CP-INT-002 M2 报修/电费真实 Tool 接入 M5 + 前端工单页面
- **用户故事**：作为系统集成者，我需要将 M2 的报修和电费 Tool 通过真实 Service 接入 M5 Agent，并提供最小前端页面用于工单和电费查询。
- **功能描述**：将 M2 的 5 个 Tool 适配器注册到 ToolRegistry，端到端测试 Tool 调用链路（Supervisor→ServiceAgent→ToolExecutor→ApprovalService→ToolAdapter→Service→Repository）。实现最小前端页面：工单列表/详情、电费余额/充值申请。验证写操作确认流程（确认卡片→用户 approve→Tool 执行→审计）。
- **验收标准**：
  1. Agent 收到"我的空调坏了"后，ServiceAgent 调用 work_order.create Tool
  2. 前端收到确认卡片，用户 approve 后工单创建成功
  3. Agent 收到"查下我宿舍电费"后，ServiceAgent 调用 electricity.get_balance
  4. 越权查询他人房间返回 TOOL_FORBIDDEN
  5. 前端工单页面正确显示状态和流转时间线

#### CP-INT-003 M3 活动/失物真实 Tool 接入 M5
- **用户故事**：作为系统集成者，我需要将 M3 的活动和失物 Tool 通过真实 Service 接入 M5 Agent，验证活动和失物招领的完整流程。
- **功能描述**：将 M3 的 4 个 Tool 适配器注册到 ToolRegistry，端到端测试：
  - 活动搜索 + 报名（含容量锁、截止时间校验）
  - 失物发布 + 候选匹配查询
  验证多智能体跨域协作（如"帮我查查本周有哪些活动，顺便问一下如果捡到学生卡怎么处理" → Supervisor 分解为 CommunityAgent + KnowledgeAgent）。
- **验收标准**：
  1. Agent 收到"帮我报名明天的篮球赛"后，CommunityAgent 完成 event.register（含确认）
  2. Agent 收到"我今天丢了学生卡"后，CommunityAgent 发布失物信息并自动返回匹配候选
  3. 跨域请求正确分解为多个子任务
  4. 活动报名超额时返回 EVENT_CAPACITY_FULL

#### CP-INT-004 M4 治理 Tool 接入 M5（安全/授权/审计闭环）
- **用户故事**：作为系统集成者，我需要确保 M4 的安全、授权和审计 Tool 在 M5 运行中自动执行，形成完整的安全闭环。
- **功能描述**：将 M4 的 3 个内部 Tool 适配器注册到 ToolRegistry（runtime_internal），验证：
  - GuardrailService 自动调用 governance.check_content 扫描输入/输出
  - ToolExecutor 自动调用 governance.authorize_tool 鉴权
  - 所有 Agent/Tool 操作自动调用 governance.write_audit 写审计
  验证 request_id → run_id → step_id → tool_call_id/approval_id → audit_id 完整链路。
- **验收标准**：
  1. 每次 Tool 调用前自动执行 authorize（无权限直接拒绝）
  2. 每次输入/输出自动扫描安全检查
  3. 每次 Agent Run 完成时 audit_logs 中包含完整记录（脱敏）
  4. 审计日志可通过 /api/v1/audit-logs 查询，含 Agent 相关字段
  5. 日志链路 request_id → run_id → audit_id 可追溯

---

#### CP-INT-005 多智能体 P0 闭环联调
- **用户故事**：作为开发团队，我们需要完成 6 个 Agent + 14 个 Tool 的完整闭环联调，确保所有 P0 场景可通过。
- **功能描述**：团队协作完成 P0 联调场景：
  1. 知识问答：Supervisor→KnowledgeAgent→knowledge.search→knowledge.answer→带引用答案
  2. 服务办理：Supervisor→ServiceAgent→work_order.create→确认→执行→结果
  3. 社区互动：Supervisor→CommunityAgent→event.search→event.register→确认→执行
  4. 跨域组合：Supervisor 分解多个子任务 → 依次调用不同 Agent → 聚合结果
  5. 安全拒绝：GovernanceAgent 检测危险输入 → 安全拒绝 + 原因说明
  6. 失败降级：Tool 超时/模型不可用 → 明确降级提示
- **验收标准**：
  1. 以上 6 个场景全部通过
  2. 写操作均产生确认卡片，未经确认不执行
  3. 所有 Tool 调用有审计记录
  4. 运行轨迹可查看完整步骤时间线
  5. 最大 6 步/3 个专业 Agent 限制生效

#### CP-INT-006 端到端测试与 Bug 修复
- **用户故事**：作为开发团队，我们需要全面的端到端测试覆盖，修复发现的所有 P0 Bug，确保系统健壮性。
- **功能描述**：
  - 编写 E2E 测试用例（含正常流程、错误流程、边界条件、并发场景）
  - 测试权限边界（不同角色、资源范围、越权拒绝）
  - 测试安全护栏（注入攻击、危险指令、非法参数）
  - 测试超时/降级（模型不可用、Tool 超时、Chroma/Redis 不可用）
  - 测试幂等性（重复 Tool 调用、审批重放）
  - Bug 修复并回归测试
- **验收标准**：
  1. 所有 P0 Tool 契约测试通过
  2. 权限测试：无权限/越权统一返回 403
  3. 安全测试：已知注入模式被拒绝，敏感字段脱敏
  4. 降级测试：外部依赖不可用时明确报错不崩溃
  5. 幂等测试：重复请求安全重放/拒绝
  6. M4 原有 272 条测试全部回归通过

#### CP-INT-007 OpenAPI 契约校验与文档同步
- **用户故事**：作为开发团队，我们需要确保 OpenAPI 契约与实际实现一致，文档与代码同步更新。
- **功能描述**：
  - 校验 OpenAPI V0.5.0（100 个路径、136 个唯一 operationId）与实际 API 一致性
  - 更新 M4/M5 详细设计文档，同步已发现的差异
  - 更新 README（M5 启动说明、依赖服务、环境变量）
  - 生成 TypeScript 前端 API 客户端
- **验收标准**：
  1. `npx @redocly/cli lint` 0 errors, 0 warnings
  2. 所有 API 路径 response schema 与实现一致
  3. operationId 全部唯一
  4. 前端 TypeScript 客户端编译通过

#### CP-INT-008 Sprint Review 与项目验收
- **用户故事**：作为开发团队，我们需要在 Sprint 结束时进行 Review，演示完整功能并验收交付物。
- **功能描述**：
  - 准备 Sprint Review 演示（4 条完整 E2E 演示路径）
  - 验收交付物清单（代码、测试、文档、部署配置）
  - 补充遗留问题到 Product Backlog
  - 撰写 Sprint 总结报告
- **验收标准**：
  1. 4 条演示路径流畅无 Bug
  2. 代码全部 merge 到 main 分支
  3. 全量测试通过（≥ 350 passed）
  4. 文档完整（需求、设计、API、部署说明）
  5. Docker Compose 一键启动全部服务
