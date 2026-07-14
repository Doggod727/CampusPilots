# CampusPilot — Jira 问题规划

> **项目名称**：学生生活一站式社区 AI 助手（CampusPilot）  
> **开发团队**：4 人（张讯毓 / 王一林 / 李欢 / 覃焓）  
> **总工期**：12 天（2 个 Sprint，各 6 天）  
> **每人每天有效工时**：7h  
> **总工时池**：4 × 7 × 12 = **336h**  
> **技术栈**：Vue 3 + TypeScript + FastAPI + PostgreSQL + Redis + Chroma + DeepSeek V4 Pro + bge-small-zh-v1.5 + Celery + Docker Compose  
> **运行环境**：prac conda 环境，Python 3.10

---

## 一、史诗（Epic）概览

| Epic Key | 名称 | 负责人 | 优先级 |
|----------|------|--------|--------|
| CP-EPIC-M4 | 公共基础与平台治理 | 覃焓 | Highest |
| CP-EPIC-M2 | 校园服务中心 | 张讯毓 | High |
| CP-EPIC-M1 | AI 与知识库 | 王一林 | High |
| CP-EPIC-M3 | 校园社区与互助 | 李欢 | High |
| CP-EPIC-INFRA | 基础设施与部署 | 覃焓 | High |
| CP-EPIC-INT | 联调集成与测试 | 全员 | Medium |

### Epic 说明

**CP-EPIC-M4 — 公共基础与平台治理（Highest）**  
为整个平台提供认证授权、用户管理、RBAC 权限、敏感词过滤、内容审核队列、审计日志、系统配置和运营看板。所有其他模块依赖 M4 的认证和权限体系，必须优先完成。

**CP-EPIC-M2 — 校园服务中心（High）**  
提供办事指南查询、材料清单生成、部门联系人管理、报修工单的创建/流转/评价闭环，以及外部校园系统 Mock 适配器。

**CP-EPIC-M1 — AI 与知识库（High）**  
提供知识库管理、文档上传与异步解析、文本切分与向量索引、基于 RAG 的流式问答、对话管理、引用溯源和用户反馈闭环。

**CP-EPIC-M3 — 校园社区与互助（High）**  
提供话题/帖子/评论/互动/举报的社区内容管理、校园活动发布与报名、失物招领的发布/匹配/认领流程。

**CP-EPIC-INFRA — 基础设施与部署（High）**  
搭建前后端项目脚手架、Docker Compose 开发环境、PostgreSQL/Redis/Chroma 服务、Alembic 数据库迁移、CI 流水线。

**CP-EPIC-INT — 联调集成与测试（Medium）**  
模块间 API 联调、端到端测试、Bug 修复、文档整理、Sprint Review。

---

## 二、Sprint 规划

### Sprint 1（Day 1–6，168h）

**目标**：完成各模块 P0 功能的独立开发，Day 6 结束前各模块自测通过。

| 天数 | M4（覃焓） | M2（张讯毓） | M1（王一林） | M3（李欢） |
|------|------------|------------|------------|------------|
| Day 1 | Sprint Planning + OpenAPI 契约草案 + 项目脚手架 | Sprint Planning + OpenAPI + 脚手架 | Sprint Planning + OpenAPI + 脚手架 | Sprint Planning + OpenAPI + 脚手架 |
| Day 2 | 用户认证系统（登录/注册/JWT/Refresh Token） | 办事指南 CRUD + 分类 | 知识库 CRUD + 成员管理 | 话题 CRUD + 帖子 CRUD |
| Day 3 | RBAC 角色权限 + 用户管理 CRUD | 材料清单 + 部门联系人 | 文档上传 + 文件校验 + 异步解析（TXT/MD/DOCX/PDF） | 评论 CRUD + 点赞收藏 |
| Day 4 | 敏感词管理 + 内容审核队列（M3 对接） | 报修工单创建 + 查询 + 幂等 | 文档切分 + 向量嵌入 + Chroma 索引 | 匿名树洞 + 举报管理 |
| Day 5 | 审计日志 + 系统配置 | 工单状态机 + 时间线 | RAG 检索流水线 + DeepSeek 集成 | 内容审核对接 M4 + 敏感词扫描 |
| Day 6 | M4 自测 + 数据库迁移 + 种子数据 | M2 自测 + 修复 | M1 自测 + 修复 | M3 自测 + 修复 + 活动管理 CRUD |

### Sprint 2（Day 7–12，168h）

**目标**：完成前端页面开发、全模块联调、端到端测试、Bug 修复、项目交付。

| 天数 | M4（覃焓） | M2（张讯毓） | M1（王一林） | M3（李欢） |
|------|------------|------------|------------|------------|
| Day 7 | 管理端前端：登录页 + 用户管理 + 角色权限 | 前端：服务首页 + 指南详情 + 部门联系人 | 前端：知识库管理页 + 上传队列 | 前端：社区信息流 + 帖子详情 + 活动列表 |
| Day 8 | 管理端前端：敏感词 + 审核队列 + 审计日志 + 看板 | 前端：我的工单 + 创建工单 + 工单详情 + 处理队列 | 前端：聊天页 + SSE 流式 + 引用抽屉 | 前端：失物列表 + 认领中心 + 举报 |
| Day 9 | 与 M3 审核联调 + 全模块 Auth 联调 | 与 M4 Auth 联调 + Mock 适配器 | 与 M4 Auth 联调 + 反馈 + 断线重连 | 与 M4 审核联调 + 失物匹配联调 |
| Day 10 | 全模块联调 + 集成测试 | 全模块联调 + 集成测试 | 全模块联调 + RAG 评测（30 题） | 全模块联调 + 并发测试 |
| Day 11 | Bug 修复 + 文档整理 | Bug 修复 + 文档整理 | Bug 修复 + 文档整理 | Bug 修复 + 文档整理 |
| Day 12 | Sprint Review + 项目验收 | Sprint Review + 项目验收 | Sprint Review + 项目验收 | Sprint Review + 项目验收 |

---

## 三、问题详细描述

---

### EPIC: CP-EPIC-M4 — 公共基础与平台治理

---

#### CP-M4-001：用户认证系统（登录/注册/JWT/Refresh Token）

- **用户故事**：作为平台用户，我希望通过用户名和密码进行注册和登录，系统自动管理我的会话 Token，确保我的身份安全可信。
- **功能描述**：实现基于 Argon2id 的密码哈希、JWT Access Token（15 分钟有效期）和 Refresh Token（7 天有效期，HttpOnly Cookie）的认证体系。包括登录限流（IP+用户名 5 次/分钟）、失败锁定（5 次/15 分钟）、Token 轮换和复用检测。
- **需要完成的内容**：
  - 用户注册接口（POST /api/v1/auth/register）
  - 用户登录接口（POST /api/v1/auth/login），返回 Access Token + Set-Cookie Refresh Token
  - Token 刷新接口（POST /api/v1/auth/refresh）
  - 登出接口（POST /api/v1/auth/logout）
  - Argon2id 密码哈希工具类（PasswordHasher）
  - JWT 生成/验证工具类（TokenService）
  - Refresh Token SHA-256 存储 + 轮换 + 复用检测
  - 登录限流中间件（Redis）
  - 失败登录锁定逻辑
- **验收标准**：
  1. 用户可通过 POST /api/v1/auth/register 注册新账号，密码经 Argon2id 哈希存储
  2. 用户可通过 POST /api/v1/auth/login 登录，返回 JWT Access Token 和 HttpOnly Refresh Token Cookie
  3. Access Token 15 分钟后过期，可通过 Refresh Token 刷新
  4. Refresh Token 7 天后过期；轮换后旧 Token 标记为 rotated
  5. 同一 Refresh Token 被复用时报安全告警，所有关联 Token 全部失效
  6. 同一 IP 或用户名 1 分钟内超过 5 次失败登录，返回 429
  7. 连续 5 次密码错误后账号锁定 15 分钟
  8. 登出后 Refresh Token 被撤销
- **优先级**：Highest
- **预估工时**：14h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1
- **依赖**：CP-M4-INFRA-001（项目脚手架 + 数据库）

---

#### CP-M4-002：RBAC 角色权限系统

- **用户故事**：作为平台管理员，我希望可以定义角色和权限，将不同用户分配到不同角色，实现对系统功能的精细化访问控制。
- **功能描述**：实现基于 RBAC 的权限体系。预置 5 个系统角色（super_admin、knowledge_admin、service_staff、community_operator、student），16 个权限码，支持角色创建/编辑/删除、权限分配、用户角色绑定。
- **需要完成的内容**：
  - 权限字典定义（16 个权限码，覆盖 M1-M4 所有操作）
  - 角色 CRUD 接口（POST/GET/PUT/DELETE /api/v1/roles）
  - 角色权限分配接口（PUT /api/v1/roles/{role_id}/permissions）
  - 用户角色绑定接口（POST/GET/DELETE /api/v1/users/{user_id}/roles）
  - 当前用户权限查询接口（GET /api/v1/auth/permissions）
  - RBAC 权限校验中间件（依赖注入）
  - 资源级权限校验规则（如 M2 服务处理员按 campus_code 范围限定）
  - 种子数据脚本（预置 5 个角色 + 权限绑定）
- **验收标准**：
  1. 系统预置 5 个角色，每个角色关联正确的权限码
  2. 管理员可创建自定义角色并分配权限
  3. 给指定用户绑定角色后，该用户获得对应权限
  4. 无权限用户访问受保护接口返回 403
  5. GET /api/v1/auth/permissions 返回当前用户所有权限码列表
  6. M2 服务处理员只能操作授权 campus_code 范围内的工单
- **优先级**：Highest
- **预估工时**：14h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1
- **依赖**：CP-M4-001（认证系统）

---

#### CP-M4-003：用户管理 CRUD

- **用户故事**：作为平台管理员，我希望可以查看用户列表、修改用户信息、启用/禁用用户账号，以便对平台用户进行管理。
- **功能描述**：实现用户的分页列表查询（支持按用户名/角色/状态筛选）、用户详情查看、用户信息编辑、账号启用/禁用、用户状态管理（active/disabled/locked）。
- **需要完成的内容**：
  - 用户列表接口（GET /api/v1/users），支持分页 + 筛选
  - 用户详情接口（GET /api/v1/users/{user_id}）
  - 用户编辑接口（PUT /api/v1/users/{user_id}）
  - 用户状态变更接口（PUT /api/v1/users/{user_id}/status）
  - 用户状态机（active ↔ disabled，自动 locked 解锁）
- **验收标准**：
  1. 管理员可按用户名、角色、状态筛选用户列表
  2. 列表支持分页（page + page_size）
  3. 可查看指定用户的详细信息和角色列表
  4. 可编辑用户昵称、邮箱等基本信息
  5. 可启用/禁用用户账号，禁用后无法登录
  6. locked 状态用户锁定到期后自动恢复 active
- **优先级**：High
- **预估工时**：7h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1
- **依赖**：CP-M4-002（RBAC）

---

#### CP-M4-004：敏感词管理

- **用户故事**：作为平台管理员，我希望可以管理敏感词库（添加/编辑/删除/导入），系统在内容发布时自动扫描匹配，根据策略执行允许/屏蔽/审核/拦截操作。
- **功能描述**：实现敏感词 CRUD、批量导入、文本扫描接口。支持 4 种扫描结果（allow/mask/review/block），扫描策略可配置。作为 M3 社区内容审核的前置过滤层。
- **需要完成的内容**：
  - 敏感词 CRUD 接口（POST/GET/PUT/DELETE /api/v1/sensitive-words）
  - 敏感词批量导入接口（POST /api/v1/sensitive-words/batch）
  - 文本扫描接口（POST /api/v1/moderation/scan），返回 scan_result + matched_words
  - 扫描策略配置（default_action: allow/mask/review/block）
  - 高效匹配算法（Trie/AC 自动机）
- **验收标准**：
  1. 可添加单个敏感词并配置匹配模式（精确/模糊）
  2. 支持批量导入敏感词（JSON/CSV）
  3. 输入含敏感词的文本，扫描接口返回对应的 scan_result
  4. allow → 文本原样通过
  5. mask → 敏感部分替换为 ***
  6. review → 标记需人工审核
  7. block → 拒绝发布
  8. 支持修改默认处理策略
- **优先级**：High
- **预估工时**：10h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1
- **依赖**：CP-M4-001（认证系统）

---

#### CP-M4-005：内容审核队列

- **用户故事**：作为平台运营员，我希望可以查看待审核的内容列表，对内容执行通过/驳回/升级操作，确保社区内容合规。
- **功能描述**：实现审核案件管理（pending → approved/rejected/escalated 状态机）、审核操作接口、与 M3 社区的 TargetHandler 协议对接。支持对 post/comment/event/lost_found 四种目标类型进行审核。
- **需要完成的内容**：
  - 审核案件列表接口（GET /api/v1/moderation/cases），支持按状态/类型/时间筛选
  - 审核操作接口（POST /api/v1/moderation/cases/{case_id}/approve|reject|escalate）
  - 审核案件创建接口（内部调用，由 M3 举报或敏感词扫描触发）
  - TargetHandler 协议定义（Protocol 抽象，支持 M3 注册 4 种目标类型）
  - 审核结果回调 M3（内容状态变更为 published/rejected）
  - 审核状态机（pending → approved/rejected/escalated）
- **验收标准**：
  1. 运营员可查看待审核案件列表，按类型/状态/时间筛选
  2. 点击"通过"后，对应 M3 内容状态变为 published
  3. 点击"驳回"后，对应 M3 内容状态变为 rejected，附带驳回原因
  4. 可将复杂案件升级（escalated），标记需更高权限处理
  5. M3 发布内容触发敏感词 review 时，自动创建审核案件
  6. TargetHandler 协议支持 M3 注册新目标类型
- **优先级**：High
- **预估工时**：14h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1
- **依赖**：CP-M4-004（敏感词管理）

---

#### CP-M4-006：审计日志

- **用户故事**：作为平台管理员，我希望所有关键操作（登录/权限变更/内容审核/数据修改）都被记录到不可篡改的审计日志中，以便追溯和安全审查。
- **功能描述**：实现审计日志的自动记录（通过中间件/装饰器）、分页查询、按操作类型/用户/时间筛选。对敏感字段（password/token/api_key/secret）自动脱敏。
- **需要完成的内容**：
  - 审计日志数据模型（operation, operator_id, target_type, target_id, detail, ip, user_agent）
  - 审计日志自动记录中间件（装饰器 @audit_log）
  - 审计日志查询接口（GET /api/v1/audit-logs），支持筛选 + 分页
  - 审计日志详情接口（GET /api/v1/audit-logs/{log_id}）
  - 敏感字段脱敏规则（password → ***, token → sha256:xxx, api_key → ***）
- **验收标准**：
  1. 用户登录/登出事件自动生成审计日志
  2. 角色权限变更事件自动记录
  3. 内容审核操作自动记录
  4. 可查询指定用户的全部操作记录
  5. 可筛选操作类型和时间范围
  6. 日志详情中敏感字段已被脱敏处理
  7. 日志不可修改或删除（软删除除外）
- **优先级**：Medium
- **预估工时**：10h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1
- **依赖**：CP-M4-001（认证系统）

---

#### CP-M4-007：系统配置管理

- **用户故事**：作为平台管理员，我希望可以通过 API 管理系统的全局配置项（如站点名称、注册开关、审核策略默认值等），配置变更自动记录版本。
- **功能描述**：实现系统配置的键值对管理，支持读取、更新、版本控制（乐观锁 via version 字段）。配置项包括：站点名称、注册开关、默认审核策略、上传文件大小限制等。
- **需要完成的内容**：
  - 配置列表接口（GET /api/v1/configs）
  - 配置更新接口（PUT /api/v1/configs/{key}），支持 If-Match 乐观锁
  - 单配置查询接口（GET /api/v1/configs/{key}）
- **验收标准**：
  1. 可查询所有系统配置项
  2. 更新配置时需传入 If-Match version，防止并发覆盖
  3. version 不匹配返回 409 Conflict
  4. 配置更新成功后 version 自动递增
- **优先级**：Medium
- **预估工时**：7h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1
- **依赖**：CP-M4-001（认证系统）

---

#### CP-M4-008：运营看板

- **用户故事**：作为平台管理员，我希望在仪表盘上查看关键运营指标（用户总数、日活、工单量、审核量等），帮助掌握平台运营状况。
- **功能描述**：实现运营看板 API，返回聚合统计数据。指标包括：总用户数、近7天新增用户、待处理工单数、待审核案件数、今日活跃用户数。
- **需要完成的内容**：
  - 看板数据接口（GET /api/v1/dashboard）
  - SQL 聚合查询（用户数/工单数/审核数）
  - 前端看板页面（卡片 + 简单图表展示）
- **验收标准**：
  1. 看板 API 返回正确的聚合统计数据
  2. 包含总用户数、近7天新增、待处理工单、待审核案件
  3. 前端看板页面正确展示各项指标
- **优先级**：Low
- **预估工时**：7h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 2
- **依赖**：CP-M4-003/CP-M4-005/CP-M2-005

---

#### CP-M4-009：管理端前端页面开发

- **用户故事**：作为平台管理员，我希望通过 Web 界面管理用户、角色、权限、敏感词、审核队列、审计日志和系统配置，无需直接调用 API。
- **功能描述**：使用 Vue 3 + TypeScript + Element Plus 开发管理端前端页面。包括登录页、用户管理、角色权限管理、敏感词管理、审核队列、审计日志查询、系统配置、运营看板共 8 个页面。
- **需要完成的内容**：
  - 登录页面（用户名/密码表单 + 登录状态管理）
  - 用户管理页面（列表 + 筛选 + 编辑弹窗 + 状态切换）
  - 角色权限页面（角色列表 + 权限矩阵编辑）
  - 敏感词管理页面（列表 + 添加/编辑/批量导入）
  - 审核队列页面（待审核列表 + 通过/驳回/升级操作）
  - 审计日志页面（日志列表 + 筛选 + 详情）
  - 系统配置页面（配置项列表 + 编辑）
  - 运营看板页面（统计卡片 + 图表）
  - 路由配置 + 导航守卫
  - Pinia 状态管理（Auth Store + 权限 Store）
  - Axios 请求拦截器（自动附带 Authorization + 错误处理）
- **验收标准**：
  1. 未登录用户自动跳转登录页
  2. 登录后根据角色权限显示对应菜单项
  3. 用户管理页面支持增删改查和状态切换
  4. 角色权限页支持创建/编辑角色和分配权限
  5. 敏感词管理页支持增删改查和批量导入
  6. 审核队列页可按状态筛选，执行通过/驳回/升级操作
  7. 审计日志页可按操作类型、用户、时间筛选
  8. 配置编辑时提交 If-Match version
  9. 看板页正确展示统计指标
- **优先级**：High
- **预估工时**：28h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 2（Day 7-8）
- **依赖**：CP-M4-001 至 CP-M4-008（后端接口就绪）

---

#### CP-M4-010：OpenAPI 契约与 API 文档

- **用户故事**：作为全栈开发人员，我希望所有 API 都有标准的 OpenAPI 规范文档，前后端以此作为唯一的联调契约。
- **功能描述**：编写和维护完整的 OpenAPI 3.0 YAML 规范（M4 部分），确保所有接口的路径、参数、请求体、响应体、状态码、OperationId 正确无误。生成 Swagger UI 供团队联调。
- **需要完成的内容**：
  - M4 OpenAPI YAML 规范编写（Auth/Users/Roles/Moderation/Audit/Config/Dashboard 7 组）
  - 统一信封格式定义（data/meta/error）
  - FastAPI 自动生成 OpenAPI JSON 并暴露 /docs
  - 全局 OperationId 命名规范校验
- **验收标准**：
  1. /docs 访问 Swagger UI 可查看所有 M4 API
  2. 所有接口的 OperationId 全局唯一且稳定
  3. 请求体和响应体 Schema 完整
  4. 错误响应格式统一
  5. 前端可通过 openapi-generator 生成 TypeScript 类型
- **优先级**：High
- **预估工时**：7h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1（Day 1）
- **依赖**：CP-M4-INFRA-001

---

### EPIC: CP-EPIC-M2 — 校园服务中心

---

#### CP-M2-001：办事指南管理

- **用户故事**：作为学生，我希望查看校园办事指南，了解各项服务的办理流程、所需材料和联系方式，帮助我高效完成校园事务。
- **功能描述**：实现办事指南的分类管理、CRUD、搜索和详情展示。指南关联分类、办理步骤、所需材料清单和适用范围（按校区/学生类型筛选）。
- **需要完成的内容**：
  - 指南分类 CRUD 接口（POST/GET/PUT/DELETE /api/v1/guide-categories）
  - 办事指南 CRUD 接口（POST/GET/PUT/DELETE /api/v1/guides）
  - 指南搜索接口（GET /api/v1/guides/search?q=&category_id=&campus_code=&student_type=）
  - 指南详情接口（GET /api/v1/guides/{guide_id}），含步骤 + 材料 + 联系人
  - 适用范围条件模型（campus_codes + student_types 白名单过滤）
  - 前端页面：服务首页 + 指南详情页
- **验收标准**：
  1. 管理员可创建/编辑/删除指南分类
  2. 管理员可创建/编辑/删除办事指南，关联分类、步骤、材料、联系人
  3. 学生可按关键词搜索指南
  4. 学生可按分类浏览指南列表
  5. 指南详情显示完整办理步骤和所需材料清单
  6. 按校区和学生类型筛选显示适用的指南
  7. 前端页面正确渲染指南内容
- **优先级**：High
- **预估工时**：14h
- **负责人**：张讯毓
- **所属 Sprint**：Sprint 1（Day 2-4）
- **依赖**：CP-M4-001（认证）

---

#### CP-M2-002：材料清单生成

- **用户故事**：作为学生，我希望输入我的校区和学生类型后，系统自动生成我需要办理某项事务的完整材料清单，避免遗漏重要文件。
- **功能描述**：根据指南 ID、校区和学生类型，自动生成对应的材料清单。支持材料项的名称、是否必需、获取方式、模板下载链接等。
- **需要完成的内容**：
  - 材料项管理接口（关联指南 CRUD 中的 materials 子资源）
  - 材料清单生成接口（GET /api/v1/guides/{guide_id}/materials?campus_code=&student_type=）
  - 条件过滤：根据 applicability 条件返回适用材料
- **验收标准**：
  1. 指定指南 + 校区 + 学生类型，返回正确材料清单
  2. 清单标明每项材料是否必需
  3. 附带获取方式说明（如"教务处领取""在线下载"）
  4. 条件不匹配的材料项不出现在清单中
- **优先级**：High
- **预估工时**：7h
- **负责人**：张讯毓
- **所属 Sprint**：Sprint 1（Day 3-4）
- **依赖**：CP-M2-001（指南管理）

---

#### CP-M2-003：部门联系人管理

- **用户故事**：作为学生，我希望查询学校各部门的联系方式和办公信息，在有疑问时能快速找到正确的对接部门。
- **功能描述**：实现部门 CRUD、部门联系人管理。支持按部门名称搜索、按有效期过滤、按 campus_code 筛选。
- **需要完成的内容**：
  - 部门 CRUD 接口（POST/GET/PUT/DELETE /api/v1/departments）
  - 部门联系人 CRUD 接口（附属 departments）
  - 联系人查询接口（GET /api/v1/departments/{dept_id}/contacts），支持有效期过滤
  - 前端：部门联系人页面
- **验收标准**：
  1. 管理员可创建/编辑/删除部门
  2. 可为部门添加多名联系人（姓名/电话/邮箱/办公地址/办公时间）
  3. 联系人支持设置有效期，过期后不显示
  4. 学生可按部门名称搜索
  5. 前端展示部门列表和联系人信息
- **优先级**：Medium
- **预估工时**：7h
- **负责人**：张讯毓
- **所属 Sprint**：Sprint 1（Day 3-4）
- **依赖**：CP-M4-001

---

#### CP-M2-004：报修工单创建与查询

- **用户故事**：作为学生，我希望在线提交宿舍报修工单，填写报修类型、位置和问题描述，并能随时查看我的工单状态和处理进度。
- **功能描述**：实现报修工单的创建（支持幂等）、分页查询、详情查看。工单编号格式 WO-YYYYMMDD-NNNN。支持按状态、校区、宿舍区域筛选。
- **需要完成的内容**：
  - 工单创建接口（POST /api/v1/work-orders），支持 Idempotency-Key
  - 工单列表接口（GET /api/v1/work-orders），支持分页 + 按状态/校区/区域筛选
  - 工单详情接口（GET /api/v1/work-orders/{wo_id}）
  - 我创建的工单接口（GET /api/v1/work-orders/mine）
  - 工单编号自动生成（WO-YYYYMMDD-NNNN，按天自增）
  - 幂等记录存储与校验（user_id + endpoint + key）
  - 前端：我的工单页 + 创建工单页 + 工单详情页
- **验收标准**：
  1. 学生可创建报修工单，填写报修类型/校区/宿舍区域/楼栋/房间号/描述
  2. 相同 Idempotency-Key 重复请求返回同一工单（不重复创建）
  3. 工单编号格式为 WO-YYYYMMDD-NNNN
  4. 学生可查看自己创建的所有工单，按状态筛选
  5. 工单详情显示完整信息和时间线
  6. 处理员可按校区/区域筛选工单
  7. 前端正确展示工单列表和详情
- **优先级**：High
- **预估工时**：14h
- **负责人**：张讯毓
- **所属 Sprint**：Sprint 1（Day 4-5）
- **依赖**：CP-M4-001/CP-M4-002

---

#### CP-M2-005：工单状态机与流转

- **用户故事**：作为服务处理员，我希望按流程处理工单（接单→处理→完成），并在每一步记录处理说明；作为学生，我希望看到工单的处理进度。
- **功能描述**：实现工单状态机（submitted → accepted → processing → completed），支持 cancel（学生取消，仅 submitted 状态）和 reject（处理员退回，仅 submitted 状态）。每次状态变更记录到工单事件时间线。使用 SELECT FOR UPDATE + 乐观锁 version 保证并发安全。
- **需要完成的内容**：
  - 工单状态流转接口（POST /api/v1/work-orders/{wo_id}/transition），传入 action + comment
  - 工单时间线接口（GET /api/v1/work-orders/{wo_id}/timeline）
  - 状态机逻辑（6 种合法迁移，含操作者/必要条件/副作用校验）
  - 并发控制（SELECT FOR UPDATE + version 乐观锁）
  - 前端：处理队列页面 + 状态流转操作
- **验收标准**：
  1. submitted 工单可被 accept（处理员接单）或 cancel（学生取消）
  2. accepted 工单可转为 processing
  3. processing 工单可转为 completed
  4. submitted 工单可被 reject（处理员退回，附原因）
  5. 非法状态迁移返回 409 + WORK_ORDER_ILLEGAL_TRANSITION
  6. 每次状态变更自动创建时间线事件
  7. 时间线按时间正序显示所有事件
  8. 并发流转时乐观锁保护，version 冲突返回 409
- **优先级**：High
- **预估工时**：14h
- **负责人**：张讯毓
- **所属 Sprint**：Sprint 1（Day 5-6）
- **依赖**：CP-M2-004（工单创建）

---

#### CP-M2-006：工单评价

- **用户故事**：作为学生，我希望在工单完成后对处理结果进行评价（评分+文字），以反馈服务质量。
- **功能描述**：实现工单评价功能。仅创建者可评价、仅 completed 状态可评价、每个工单仅可评价一次。评分范围 1-5 星。
- **需要完成的内容**：
  - 工单评价创建接口（POST /api/v1/work-orders/{wo_id}/rating）
  - 工单评价查询接口（GET /api/v1/work-orders/{wo_id}/rating）
- **验收标准**：
  1. 仅工单创建人可提交评价
  2. 仅 completed 状态工单可评价
  3. 每个工单仅可评价一次，重复评价返回 409
  4. 评分范围为 1-5
  5. 评价内容支持文字说明（可选）
- **优先级**：Medium
- **预估工时**：4h
- **负责人**：张讯毓
- **所属 Sprint**：Sprint 2（Day 7）
- **依赖**：CP-M2-005

---

#### CP-M2-007：外部校园系统 Mock 适配器

- **用户故事**：作为开发人员，我需要一个 Mock 适配器来模拟外部校园系统（如后勤维修系统的真实进度查询），以便在演示中展示完整的工单闭环。
- **功能描述**：实现 CampusSystemPort 抽象协议 + Mock 实现。提供外部进度查询接口，返回模拟的后勤系统工单处理进度。
- **需要完成的内容**：
  - CampusSystemPort Protocol 定义
  - MockCampusSystemAdapter 实现
  - 外部进度查询接口（GET /api/v1/work-orders/{wo_id}/external-progress）
  - Mock 延迟 + 渐进式进度模拟
- **验收标准**：
  1. 调用外部进度接口返回 Mock 进度数据
  2. Mock 数据格式符合 CampusSystemPort 协议
  3. Mock 响应延迟 < 500ms
  4. 生产环境可切换为真实适配器
- **优先级**：Low
- **预估工时**：7h
- **负责人**：张讯毓
- **所属 Sprint**：Sprint 2（Day 9）
- **依赖**：CP-M2-005

---

### EPIC: CP-EPIC-M1 — AI 与知识库

---

#### CP-M1-001：知识库 CRUD 与成员管理

- **用户故事**：作为知识管理员，我希望创建知识库、上传知识文档，并管理知识库的访问成员，控制谁能查询该知识库的内容。
- **功能描述**：实现知识库的创建/查询/编辑/删除，成员添加/移除/权限设置。成员权限分为：owner（完全控制）、editor（可上传编辑文档）、viewer（仅查询）。
- **需要完成的内容**：
  - 知识库 CRUD 接口（POST/GET/PUT/DELETE /api/v1/knowledge-bases）
  - 成员管理接口（POST/GET/DELETE /api/v1/knowledge-bases/{kb_id}/members）
  - 权限校验：仅 owner 可管理成员、editor 可上传文档
  - 防越权：所有读取按当前用户 auth 作用域过滤
- **验收标准**：
  1. 用户可创建知识库，设置名称和描述
  2. 可添加/移除知识库成员并设置权限
  3. 非成员无法访问知识库
  4. editor 无法管理成员
  5. 知识库所有者可删除知识库（软删除）
- **优先级**：High
- **预估工时**：7h
- **负责人**：王一林
- **所属 Sprint**：Sprint 1（Day 2-3）
- **依赖**：CP-M4-001/CP-M4-002

---

#### CP-M1-002：文档上传与异步解析

- **用户故事**：作为知识库编辑者，我希望上传文档到知识库，系统自动解析文档内容，支持 TXT、Markdown、DOCX、PDF 格式。
- **功能描述**：实现文档上传接口（支持同时上传 ≤10 个文件、单文件 ≤20MiB）、SHA-256 去重、异步解析任务（Celery）。支持 4 种解析器：TXT（Python 文本流）、Markdown（markdown-it-py）、DOCX（python-docx）、PDF（pypdf）。文档状态机：pending → processing → ready → published。
- **需要完成的内容**：
  - 文档上传接口（POST /api/v1/knowledge-bases/{kb_id}/documents），multipart/form-data
  - 文档列表/详情接口（GET /api/v1/knowledge-bases/{kb_id}/documents）
  - 文档删除接口（DELETE /api/v1/documents/{doc_id}）
  - 4 类解析器实现（TXT/Markdown/DOCX/PDF）
  - Celery 异步任务（ai_knowledge.ingest_document）
  - 解析进度查询接口（GET /api/v1/documents/{doc_id}/ingestion-status）
  - 文件存储（Docker Volume 或本地目录）
  - 前端：知识库管理页 + 上传队列
- **验收标准**：
  1. 可上传 TXT/Markdown/DOCX/PDF 文件到指定知识库
  2. 单次最多上传 10 个文件，单文件不超过 20MiB
  3. 相同 SHA-256 的文件去重，返回已有文档
  4. 上传后自动触发 Celery 异步解析任务
  5. 解析任务分阶段更新进度（20%-100%）
  6. 解析失败后自动重试（最多 3 次）
  7. 文档状态正确流转：pending → processing → ready → published
  8. 前端可查看上传队列和解析进度
- **优先级**：High
- **预估工时**：21h
- **负责人**：王一林
- **所属 Sprint**：Sprint 1（Day 3-5）
- **依赖**：CP-M1-001

---

#### CP-M1-003：文档切分与向量索引

- **用户故事**：作为系统，我需要将解析后的文档内容切分为语义块，使用 bge-small-zh-v1.5 模型生成向量嵌入，并存入 Chroma 向量数据库，以支持高效语义检索。
- **功能描述**：实现文档切分器（按段落/句末标点切分，目标 500 字符/块，重叠 80 字符）、BGE 嵌入生成（批量 32）、Chroma Collection 管理（每个知识库一个 Collection，命名 kb_<uuid>）。数据库先落地 → 向量幂等写入 → 数据库发布。
- **需要完成的内容**：
  - 文本清洗流水线（Unicode NFKC 标准化、去除控制字符）
  - 递归文本切分器（分隔符：\n\n → \n → 。！？；）
  - BGE bge-small-zh-v1.5 模型加载与嵌入生成
  - Chroma Collection 创建与管理
  - 向量批量写入（batch_size=32）
  - 文档状态管理：ready → published
  - 模型版本变更时重建 Collection
- **验收标准**：
  1. 解析后的文本被正确切分为语义块（500 字符左右，含重叠）
  2. 每个语义块生成 512 维向量嵌入
  3. 向量正确写入 Chroma 对应 Collection
  4. 数据库 records + Chroma Collection 保持一致
  5. 向量写入失败时不将文档标为 published
  6. bge 模型版本变更时创建新 Collection 重建索引
- **优先级**：High
- **预估工时**：14h
- **负责人**：王一林
- **所属 Sprint**：Sprint 1（Day 4-6）
- **依赖**：CP-M1-002

---

#### CP-M1-004：RAG 检索流水线

- **用户故事**：作为学生，我希望用自然语言提问校园相关问题，系统从已授权的知识库中检索相关内容，结合 AI 生成准确、有来源引用的回答。
- **功能描述**：实现完整的 RAG 检索流水线。包括：用户问题向量化 → Chroma 多知识库并行检索 → PostgreSQL 回查 chunk 详情 → cosine distance 转 similarity score → 阈值过滤（≥0.62）→ 低分兜底。最多检索 10 个已授权知识库，每个 Top-K=6。
- **需要完成的内容**：
  - 问题向量化（bge-small-zh-v1.5）
  - 多知识库并行检索逻辑（已授权 kb 列表 → 各查 Top-K=6）
  - PostgreSQL chunk 回查（获取文本内容 + 元数据）
  - Cosine distance → similarity score 转换
  - 阈值判断（0.62），低分走兜底
  - 兜底消息：「抱歉，我暂时无法找到与您问题相关的信息，请尝试更具体地描述您的问题。」
  - Chron 检索日志记录（retrieval_runs + message_citations）
- **验收标准**：
  1. 仅检索当前用户已授权的知识库
  2. 每个知识库返回 Top-6 最相关 chunk
  3. chunk 超过阈值的进入生成候选
  4. 所有检索结果均低于 0.62 时返回兜底消息（不调用 LLM）
  5. 检索日志完整记录（检索时间、知识库、命中数）
  6. 检索 P95 延迟 < 500ms（不含 LLM 生成）
- **优先级**：High
- **预估工时**：14h
- **负责人**：王一林
- **所属 Sprint**：Sprint 1-2（Day 5-7）
- **依赖**：CP-M1-003

---

#### CP-M1-005：DeepSeek 流式对话与 SSE

- **用户故事**：作为学生，我希望在聊天界面中输入问题后，实时看到 AI 逐字生成的回答，并获得每条回答所引用的知识来源。
- **功能描述**：使用 DeepSeek V4 Pro 模型（OpenAI 兼容 SDK）进行流式生成。实现 SSE（Server-Sent Events）事件协议：meta → delta* → sources → done。Prompt 构造规则：系统指令 + 来源引用 `<source id="S1">...</source>` 格式。Thinking 模式关闭，max_tokens=1200。
- **需要完成的内容**：
  - DeepSeek 适配器（OpenAI SDK，base_url + model + stream=True）
  - Prompt 构造器（系统指令 + 检索结果拼接 + S 编号）
  - SSE 端点（POST /api/v1/chat，Accept: text/event-stream）
  - SSE 事件协议实现（meta/delta/sources/done/error）
  - 每 15 秒心跳 ping（: ping 注释行）
  - 引用解析（S 编号 → 文档/页码/片段回查）
  - LLM 调用记录（llm_calls 表）
  - 前端：聊天页面（会话列表 + 消息流 + 输入框 + 流式渲染）
  - 前端：引用抽屉（文档名/页码/引用片段）
- **验收标准**：
  1. 发送问题后 SSE 流式返回 AI 逐字生成的回答
  2. 首包到达时间（TTFB）< 2 秒
  3. 回答末尾返回 sources 事件，含引用来源列表
  4. 来源引用可点击查看完整上下文
  5. 兜底场景（检索无结果）不调用 DeepSeek，直接返回预设消息
  6. DeepSeek 生成超时 120 秒自动断开
  7. 前端正确展示流式文字和引用标记
  8. llm_calls 表记录每次调用（tokens/prompt/model/latency）
- **优先级**：High
- **预估工时**：21h
- **负责人**：王一林
- **所属 Sprint**：Sprint 2（Day 7-9）
- **依赖**：CP-M1-004

---

#### CP-M1-006：对话管理与反馈

- **用户故事**：作为学生，我希望查看和继续我的历史对话，并对 AI 回答的质量进行评价（点赞/点踩），帮助改进回答质量。
- **功能描述**：实现对话的创建/列表/详情，消息的存储和查询，回答的点赞/点踩反馈（含可选的纠正建议文本）。支持断线后通过 Idempotency-Key 重试。
- **需要完成的内容**：
  - 会话 CRUD 接口（POST/GET/DELETE /api/v1/conversations）
  - 消息列表接口（GET /api/v1/conversations/{conv_id}/messages）
  - 消息反馈接口（POST /api/v1/messages/{msg_id}/feedback），like/dislike + 建议
  - 断线重连：GET 查询最后消息接口
  - Idempotency-Key 支持（同一 key 返回同一 message）
- **验收标准**：
  1. 每次发起新对话自动创建会话
  2. 会话列表按更新时间倒序排列
  3. 选中历史会话可查看完整消息记录
  4. 可对 AI 回答点赞或点踩
  5. 点踩时可填写纠正建议
  6. 相同 Idempotency-Key 重复请求返回已有消息
  7. 页面刷新/断线后可恢复最后一条消息
- **优先级**：High
- **预估工时**：7h
- **负责人**：王一林
- **所属 Sprint**：Sprint 2（Day 9）
- **依赖**：CP-M1-005

---

#### CP-M1-007：前端知识库与聊天页面

- **用户故事**：作为学生和管理员，我希望通过友好的 Web 界面上传文档、管理知识库、进行 AI 对话和查看引用来源。
- **功能描述**：使用 Vue 3 + TypeScript + Element Plus 开发前端页面。包括：知识库管理页（列表 + 创建/编辑 + 上传队列 + 成员管理）、聊天页（会话列表 + 消息流 + SSE 流式渲染 + 输入框 + 引用抽屉）。
- **需要完成的内容**：
  - 知识库管理页（KnowledgeBasePage）
  - 文档上传队列组件（拖拽上传 + 进度显示 + 解析状态）
  - 聊天页（ChatPage：会话列表 + 消息气泡 + Markdown 渲染 + 流式打字效果）
  - 引用抽屉组件（CitationDrawer）
  - 反馈按钮（点赞/点踩 + 建议输入）
  - SSE EventSource 封装（断线重连 + 心跳处理）
- **验收标准**：
  1. 前端正确展示知识库列表，可创建/编辑/删除知识库
  2. 拖拽或点击上传文档，显示上传和解析进度
  3. 聊天页展示会话列表，可切换/新建会话
  4. 发送消息后 SSE 流式渲染 AI 回答
  5. 回答中引用标记可点击展开引用抽屉
  6. 点赞/点踩按钮交互正常
  7. 页面刷新后可恢复当前会话状态
- **优先级**：High
- **预估工时**：14h
- **负责人**：王一林
- **所属 Sprint**：Sprint 2（Day 7-8）
- **依赖**：CP-M1-001 至 CP-M1-006

---

### EPIC: CP-EPIC-M3 — 校园社区与互助

---

#### CP-M3-001：话题管理

- **用户故事**：作为社区运营员，我希望创建和管理讨论话题（如"校园活动""学习交流""生活服务""树洞"），为社区内容提供分类和归属。
- **功能描述**：实现话题的 CRUD 管理。话题包含名称、描述、图标、是否允许匿名发帖、是否置顶等属性。
- **需要完成的内容**：
  - 话题 CRUD 接口（POST/GET/PUT/DELETE /api/v1/topics）
  - 话题列表查询（支持分页 + 排序 + 筛选）
  - 前端：社区信息流页面（话题标签切换）
- **验收标准**：
  1. 管理员可创建/编辑/删除话题
  2. 话题列表支持分页和排序
  3. 可设置话题是否允许匿名发帖
  4. 前端按话题筛选显示帖子列表
- **优先级**：High
- **预估工时**：4h
- **负责人**：李欢
- **所属 Sprint**：Sprint 1（Day 2）
- **依赖**：CP-M4-001

---

#### CP-M3-002：帖子 CRUD 与内容发布

- **用户故事**：作为学生，我希望在话题下发帖、编辑自己的帖子、删除帖子，并能查看帖子的详情和评论。
- **功能描述**：实现帖子的创建/查询/编辑/删除。帖子支持 Markdown 内容，发布时经过 M4 敏感词扫描和审核流程（pending_review → published/rejected）。支持分页列表、按话题筛选、按热度/时间排序。
- **需要完成的内容**：
  - 帖子 CRUD 接口（POST/GET/PUT/DELETE /api/v1/topics/{topic_id}/posts）
  - 帖子详情接口（GET /api/v1/posts/{post_id}）
  - 帖子列表接口（GET /api/v1/posts），支持按话题/排序/分页
  - 内容发布审核流程（调用 M4 敏感词扫描 → 按结果决定状态）
  - Markdown 安全处理（html:false + DOMPurify）
  - 前端：帖子详情页 + 发帖/编辑组件
- **验收标准**：
  1. 学生可在指定话题下发帖
  2. 帖子内容支持 Markdown 格式
  3. 发布时自动进行敏感词扫描
  4. allow → 直接发布 / mask → 敏感部分替换后发布 / review → 进入审核队列 / block → 拒绝发布
  5. 帖子支持编辑和删除（仅作者可操作）
  6. 帖子列表支持按话题筛选和按时间/热度排序
  7. 前端 Markdown 渲染安全（无 XSS）
  8. 前端正确展示帖子列表和详情
- **优先级**：High
- **预估工时**：14h
- **负责人**：李欢
- **所属 Sprint**：Sprint 1（Day 2-4）
- **依赖**：CP-M4-001/CP-M4-004/CP-M3-001

---

#### CP-M3-003：评论管理

- **用户故事**：作为学生，我希望在帖子下发表评论、回复他人评论，参与社区讨论。
- **功能描述**：实现两层评论系统（父评论 + 子评论）。只能评论 published 状态的帖子。删除父评论时子评论保留（显示"该评论已被删除"）。支持分页加载。
- **需要完成的内容**：
  - 评论创建接口（POST /api/v1/posts/{post_id}/comments）
  - 评论列表接口（GET /api/v1/posts/{post_id}/comments），两层树形结构
  - 评论删除接口（DELETE /api/v1/comments/{comment_id}）
  - 评论审核（同帖子审核流程）
  - 前端：评论列表 + 评论输入框 + 回复功能
- **验收标准**：
  1. 可对 published 帖子发表评论
  2. 可回复其他评论（两层嵌套）
  3. 评论发布经过敏感词扫描
  4. 删除父评论后子评论保留（显示占位提示）
  5. 前端正确展示树形评论和回复交互
- **优先级**：High
- **预估工时**：10h
- **负责人**：李欢
- **所属 Sprint**：Sprint 1（Day 3-4）
- **依赖**：CP-M3-002

---

#### CP-M3-004：点赞收藏与互动

- **用户故事**：作为学生，我希望给喜欢的帖子点赞或收藏，表达我的态度并方便日后查找。
- **功能描述**：实现帖子点赞和收藏的 PUT/DELETE 幂等操作。仅限 published 帖子。使用数据库事务保证计数器一致性（INSERT ON CONFLICT + DELETE RETURNING + GREATEST(count-1,0)）。
- **需要完成的内容**：
  - 点赞接口（PUT/DELETE /api/v1/posts/{post_id}/like）
  - 收藏接口（PUT/DELETE /api/v1/posts/{post_id}/favorite）
  - 我的点赞/收藏列表接口（GET /api/v1/posts/mine?filter=liked|favorited）
  - 计数器并发安全更新
  - 前端：点赞/收藏按钮 + 数量显示
- **验收标准**：
  1. 可对 published 帖子点赞/取消点赞（幂等）
  2. 可收藏/取消收藏帖子（幂等）
  3. 点赞数和收藏数实时更新且准确
  4. 可查看我的点赞和收藏列表
  5. 前端按钮交互正确，高并发下计数不丢失
- **优先级**：Medium
- **预估工时**：7h
- **负责人**：李欢
- **所属 Sprint**：Sprint 1（Day 4-5）
- **依赖**：CP-M3-002

---

#### CP-M3-005：匿名树洞

- **用户故事**：作为学生，我希望在支持匿名的话题中以匿名身份发帖和评论，保护我的隐私；作为社区运营员，在必要时（如违规举报）可反查匿名身份，但需要记录审计日志。
- **功能描述**：实现匿名发帖/评论功能。仅 allow_anonymous=true 的话题支持匿名。匿名发布时 user_id 存储真实用户，但 API 响应中 user_id 返回 null，display_name 返回"匿名同学"。匿名反查需专用权限 + 2-500 字事由 + 审计记录。
- **需要完成的内容**：
  - 匿名发帖/评论逻辑（响应脱敏）
  - 匿名身份反查接口（GET /api/v1/community/anonymous-identity/{content_id}），需 community:anonymous_identity:read 权限
  - 反查审计日志记录
  - 前端：匿名开关 + 匿名标识 + 运营员反查入口
- **验收标准**：
  1. 匿名话题下发帖/评论可选择以匿名身份发布
  2. 匿名内容的 API 响应中不暴露真实用户身份
  3. 前端匿名内容显示"匿名同学"
  4. 仅拥有 community:anonymous_identity:read 权限的用户可反查
  5. 反查必须提供 2-500 字事由
  6. 反查操作自动记录审计日志
- **优先级**：High
- **预估工时**：7h
- **负责人**：李欢
- **所属 Sprint**：Sprint 1（Day 4-5）
- **依赖**：CP-M4-002/CP-M4-006/CP-M3-002

---

#### CP-M3-006：举报管理

- **用户故事**：作为学生，我希望举报违规内容（帖子/评论/活动/失物信息）；作为运营员，我希望查看举报列表并关联到审核流程进行处理。
- **功能描述**：实现举报创建、列表查询。目标限定 4 种类型（post/comment/event/lost_found）。同一用户对同一目标仅可创建一次举报，创建时自动关联 M4 审核案件。举报列表支持按状态/类型筛选。
- **需要完成的内容**：
  - 举报创建接口（POST /api/v1/community/reports）
  - 举报列表接口（GET /api/v1/community/reports），支持筛选
  - 首次创建唯一举报 + 关联 M4 审核案件
  - 举报限流（5 次/分钟/用户）
  - 前端：举报弹窗 + 举报列表页（运营员）
- **验收标准**：
  1. 可对帖子/评论/活动/失物信息进行举报
  2. 举报需选择原因类型（违规/ spam / 人身攻击 / 其他）
  3. 同一用户对同一目标不可重复举报
  4. 创建举报时自动在 M4 创建审核案件
  5. 运营员可查看举报列表并按状态筛选
  6. 举报限流：同一用户每分钟最多 5 次举报
- **优先级**：High
- **预估工时**：10h
- **负责人**：李欢
- **所属 Sprint**：Sprint 1-2（Day 5-7）
- **依赖**：CP-M4-005/CP-M3-002

---

#### CP-M3-007：校园活动管理

- **用户故事**：作为学生干部/运营员，我希望发布校园活动（讲座/比赛/社团活动），设置报名人数上限和截止日期；作为学生，我希望浏览活动列表并报名参加。
- **功能描述**：实现活动 CRUD、活动列表查询（支持分页/时间范围筛选）、活动报名与取消报名。使用 SELECT FOR UPDATE + Idempotency-Key 保证报名并发安全。Celery Beat 每分钟自动将过期活动标记为结束。
- **需要完成的内容**：
  - 活动 CRUD 接口（POST/GET/PUT/DELETE /api/v1/events）
  - 活动列表接口（GET /api/v1/events），支持筛选 + 分页
  - 活动报名接口（POST /api/v1/events/{event_id}/register），Idempotency-Key
  - 取消报名接口（DELETE /api/v1/events/{event_id}/register）
  - 报名人数并发控制（SELECT FOR UPDATE + registered_count 校验）
  - Celery Beat 定时任务（每分钟检查并结束过期活动）
  - 活动时间校验（starts_at < ends_at, registration_deadline <= starts_at）
  - 前端：活动列表页 + 活动详情页 + 报名按钮
- **验收标准**：
  1. 可创建活动，设置标题/时间/地点/人数上限/报名截止时间
  2. 活动列表支持时间范围筛选
  3. 活动开始时间必须早于结束时间
  4. 报名截止时间必须 ≤ 活动开始时间
  5. 报名未达上限时学生可报名，达上限后返回 EVENT_REGISTRATION_FULL
  6. 并发报名不超卖（使用行锁保证）
  7. 相同 Idempotency-Key 不会重复报名
  8. 报名截止后不可报名
  9. 活动结束后自动更新状态
  10. 前端正确展示活动列表、详情和报名状态
- **优先级**：Medium
- **预估工时**：14h
- **负责人**：李欢
- **所属 Sprint**：Sprint 1-2（Day 6-8）
- **依赖**：CP-M4-001/CP-M4-002

---

#### CP-M3-008：失物发布与匹配

- **用户故事**：作为学生，我希望发布捡到或丢失的物品信息；系统自动匹配可能的失物和招领信息，帮助我快速找回失物。
- **功能描述**：实现失物招领信息的发布（物品类别/地点/时间/描述/联系方式）、列表查询、候选匹配算法。匹配算法：score = 类别（35%）+ 地点（25%）+ 时间（20%）+ 关键词（20%），阈值 0.55。联系方式使用 AES-GCM/Fernet 认证加密存储。
- **需要完成的内容**：
  - 失物发布接口（POST /api/v1/lost-found），含联系方式加密
  - 失物列表接口（GET /api/v1/lost-found），支持按类别/地点/时间/类型筛选
  - 失物详情接口（GET /api/v1/lost-found/{item_id}）
  - 失物编辑/删除接口
  - 候选匹配算法实现（加权评分 + 阈值过滤）
  - 匹配接口（GET /api/v1/lost-found/{item_id}/matches），发布时自动计算
  - 联系方式加密/解密服务（Fernet）
  - 前端：失物列表页 + 发布页 + 匹配推荐
- **验收标准**：
  1. 可发布失物或招领信息（类别/地点/时间/描述/联系方式）
  2. 联系方式在数据库中密文存储
  3. 发布后自动计算匹配，返回候选匹配列表
  4. 匹配分数 ≥ 0.55 的出现在候选列表中
  5. 权重配置：类别 35% + 地点 25% + 时间 20% + 关键词 20% 接近合理
  6. 按类别/地点/状态筛选失物列表
  7. 前端正确展示失物列表、发布表单和匹配推荐
- **优先级**：Medium
- **预估工时**：14h
- **负责人**：李欢
- **所属 Sprint**：Sprint 2（Day 8-9）
- **依赖**：CP-M4-001

---

#### CP-M3-009：认领管理

- **用户故事**：作为失物发布者，我希望收到认领请求后进行审核验证；作为认领者，我希望提交认领申请并上传证据；双方确认后完成认领。
- **功能描述**：实现认领状态机（pending → verified → completed/rejected/cancelled）。双方确认时加锁（UUID 排序防止数据库死锁）。认领需上传证据说明。
- **需要完成的内容**：
  - 认领创建接口（POST /api/v1/lost-found/{item_id}/claims）
  - 认领状态流转接口（POST /api/v1/lost-found/{item_id}/claims/{claim_id}/verify|complete|reject|cancel）
  - 认领列表/详情接口
  - 双方确认事务锁（lock claim + target item + claimant item，UUID 排序）
  - 前端：认领中心页面
- **验收标准**：
  1. 可对失物信息发起认领，上传证据说明
  2. 发布者可审核认领（验证通过/驳回）
  3. 认领者确认完成
  4. 双方确认后认领状态变为 completed
  5. 发布者和认领者双方对应的失物/招领信息均更新状态
  6. 并发操作下状态一致（事务锁保护）
  7. 前端正确展示认领流程和状态变更
- **优先级**：Medium
- **预估工时**：7h
- **负责人**：李欢
- **所属 Sprint**：Sprint 2（Day 9）
- **依赖**：CP-M3-008

---

#### CP-M3-010：前端社区页面开发

- **用户故事**：作为学生，我希望通过友好的 Web 界面浏览社区话题、查看帖子详情、参与评论互动、查看活动和失物信息。
- **功能描述**：使用 Vue 3 + TypeScript + Element Plus 开发社区前端页面。包括：社区信息流（话题标签切换）、帖子详情（Markdown 渲染 + 评论树）、活动列表/详情、失物列表/详情/发布、认领中心、举报弹窗。
- **需要完成的内容**：
  - 社区信息流页面（CommunityFeed：话题筛选 + 帖子卡片列表 + 排序）
  - 帖子详情页（PostDetail：内容 + 评论树 + 点赞/收藏/举报按钮）
  - 活动列表页（EventList）+ 活动详情页（EventDetail + 报名）
  - 失物列表页（LostFoundList）+ 发布页 + 详情页 + 匹配推荐
  - 认领中心页（ClaimCenter）
  - 发帖/评论组件（Markdown 编辑器 + 匿名开关）
  - 举报弹窗组件
- **验收标准**：
  1. 前端正确展示社区信息流，按话题筛选和排序
  2. 帖子详情展示 Markdown 内容和树形评论
  3. 点赞/收藏按钮交互正确
  4. 活动列表展示即将开始的活动，可报名
  5. 失物列表按类别筛选，发布新失物信息
  6. 匹配推荐在详情页展示
  7. 认领流程前端完整
  8. 举报弹窗交互正常
- **优先级**：High
- **预估工时**：21h
- **负责人**：李欢
- **所属 Sprint**：Sprint 2（Day 7-9）
- **依赖**：CP-M3-001 至 CP-M3-009

---

### EPIC: CP-EPIC-INFRA — 基础设施与部署

---

#### CP-INFRA-001：项目脚手架与开发环境搭建

- **用户故事**：作为开发团队，我们需要统一的开发环境，包括前后端项目脚手架、数据库、缓存、向量数据库和服务编排。
- **功能描述**：初始化 Vue 3 前端项目（Vite + TypeScript + Element Plus + Pinia + Vue Router）、FastAPI 后端项目、配置 Docker Compose（6 个容器：web/api/worker/postgres/redis/chroma）、统一的环境变量管理。
- **需要完成的内容**：
  - 前端项目初始化（pnpm create vite + 依赖安装 + 目录结构）
  - 后端项目初始化（FastAPI app + 目录结构 + 依赖清单 requirements.txt）
  - Docker Compose 编排（postgres 16 + redis 7 + chroma + api + worker + nginx）
  - 环境变量模板（.env.example）
  - PostgreSQL 初始化 SQL 脚本
  - 各容器健康检查配置
  - 后端共享目录搭建（core/shared/infrastructure）
- **验收标准**：
  1. docker compose up 启动全部 6 个服务
  2. 前端 localhost:5173 可访问
  3. 后端 localhost:8000/docs 可见 Swagger UI
  4. PostgreSQL localhost:5432 可连接
  5. Redis localhost:6379 可连接
  6. Chroma 服务正常运行
  7. 各服务健康检查通过
  8. .env.example 包含所有必需环境变量
- **优先级**：Highest
- **预估工时**：14h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1（Day 1-2）
- **依赖**：无

---

#### CP-INFRA-002：数据库迁移与种子数据

- **用户故事**：作为开发团队，我们需要通过 Alembic 管理数据库版本迁移，并准备好各模块的种子数据用于开发和测试。
- **功能描述**：配置 Alembic、编写所有模块的 DDL 迁移脚本（按 M4 → M2 → M1 → M3 顺序）、编写种子数据脚本（管理员账号、测试用户、预置角色权限、示例指南/话题/知识库）。
- **需要完成的内容**：
  - Alembic 配置（alembic.ini + env.py，asyncpg 驱动）
  - M4 DDL 迁移（users/roles/permissions 等 12 张表）
  - M2 DDL 迁移（campuses/departments/guides/work_orders 等 12 张表）
  - M1 DDL 迁移（knowledge_bases/documents/conversations 等 11 张表）
  - M3 DDL 迁移（topics/posts/comments/events/lost_found 等 10 张表）
  - 种子数据脚本（管理员 + 测试学生 + 示例数据）
- **验收标准**：
  1. alembic upgrade head 一次性创建所有表
  2. 表结构符合设计文档定义
  3. 外键、索引、约束正确
  4. 种子数据可正常插入
  5. 脚本可按模块顺序执行
- **优先级**：High
- **预估工时**：14h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1（Day 2-3, Day 5-6）
- **依赖**：CP-INFRA-001

---

#### CP-INFRA-003：Celery 异步任务配置

- **用户故事**：作为开发团队，我们需要配置 Celery 异步任务队列，以支持文档解析、向量索引、活动定时结束等后台任务。
- **功能描述**：配置 Celery app（broker=Redis, backend=Redis）、定义任务队列、配置自动重试、定义 Celery Beat 定时任务调度（活动自动结束每分钟执行）。
- **需要完成的内容**：
  - Celery app 初始化配置
  - 任务模块注册（M1: ingest_document，M3: auto_end_events）
  - Celery Beat 配置
  - Worker 启动命令
- **验收标准**：
  1. Celery worker 可正常启动
  2. 任务可发送到队列并被 Worker 消费
  3. 任务失败自动重试（最多 3 次）
  4. Celery Beat 每分钟执行活动结束检查
  5. Docker Compose 中 Worker 服务正常运行
- **优先级**：High
- **预估工时**：7h
- **负责人**：覃焓
- **所属 Sprint**：Sprint 1（Day 3）
- **依赖**：CP-INFRA-001

---

### EPIC: CP-EPIC-INT — 联调集成与测试

---

#### CP-INT-001：全模块 API 联调

- **用户故事**：作为开发团队，我们需要确保 M1/M2/M3/M4 四个模块的 API 可以正确交互，前端能正常调用后端接口，模块间数据流正确。
- **功能描述**：进行全模块联调。重点验证：M4 Auth 被 M1/M2/M3 正确调用、M3 内容审核与 M4 审核队列对接、M1 RAG 检索端到端流程、M2 工单状态机端到端流程、M3 失物匹配端到端流程。
- **需要完成的内容**：
  - Auth 联调（M1/M2/M3 调用 M4 认证鉴权）
  - 审核联调（M3 发布内容 → M4 敏感词扫描 → M4 审核队列）
  - M1 RAG 联调（文档上传 → 解析 → 索引 → 检索 → DeepSeek 回答）
  - M2 工单联调（创建 → 流转 → 评价）
  - M3 失物联调（发布 → 匹配 → 认领）
  - 前端-后端全链路联调
- **验收标准**：
  1. M1/M2/M3 所有需鉴权的接口正确通过 M4 Auth
  2. M3 发帖/评论经 M4 敏感词扫描后正确入库
  3. M4 审核通过后 M3 内容状态正确更新
  4. M1 完整 RAG 流程可端到端跑通
  5. M2 工单状态机各流转路径可正常执行
  6. M3 失物匹配算法返回合理结果
  7. 前端所有页面可正常调用后端接口
- **优先级**：High
- **预估工时**：28h（4人 × 7h）
- **负责人**：全员
- **所属 Sprint**：Sprint 2（Day 9-10）
- **依赖**：所有模块 P0 功能完成

---

#### CP-INT-002：端到端测试与 Bug 修复

- **用户故事**：作为开发团队，我们需要对核心业务场景进行端到端测试，确保系统在真实使用场景下运行正常，并修复发现的 Bug。
- **功能描述**：编写和执行核心场景的端到端测试用例。使用 pytest 进行后端 API 测试和集成测试，手动执行前端 E2E 流程验证。修复测试中发现的 Bug。测试覆盖率目标：核心代码 ≥ 70%。
- **需要完成的内容**：
  - M4 测试：认证流程/权限校验/审核流程/审计日志
  - M2 测试：工单状态机/并发创建/Mock 适配器
  - M1 测试：文档解析/切分/检索/RAG 评测（30题，命中率≥80%，引用可用率100%）
  - M3 测试：并发报名/失物匹配/认领状态机/举报审核
  - Bug 修复
- **验收标准**：
  1. 所有 P0 API 接口有对应的测试用例
  2. 核心代码测试覆盖率 ≥ 70%
  3. M1 RAG 30 题评测命中率 ≥ 80%
  4. M1 RAG 引用可用率 = 100%
  5. 工单状态机所有合法/非法迁移均通过测试
  6. 报名并发测试 10 并发不超卖
  7. 所有发现的 P0 Bug 已修复
- **优先级**：High
- **预估工时**：28h（4人 × 7h）
- **负责人**：全员
- **所属 Sprint**：Sprint 2（Day 10-11）
- **依赖**：CP-INT-001

---

#### CP-INT-003：Sprint Review 与项目验收

- **用户故事**：作为开发团队，我们需要整理项目交付物、准备演示环境、进行 Sprint Review 和项目验收。
- **功能描述**：整理项目文档（API 文档/部署手册/用户手册）、准备演示数据和环境、进行 Sprint Review 演示、提交最终交付物。
- **需要完成的内容**：
  - Sprint Review PPT/演示准备
  - API 文档确认（Swagger UI /docs 完整）
  - 部署手册编写（Docker Compose 启动步骤）
  - 用户手册编写（核心功能使用说明）
  - 演示环境就绪（种子数据 + Mock 数据）
  - 代码仓库整理（Tag + Release）
- **验收标准**：
  1. 完成 Sprint Review 演示
  2. 所有交付物文档齐全
  3. 演示环境正常运行
  4. 代码已提交到 Git 仓库
  5. 所有验收标准项通过检查
- **优先级**：Medium
- **预估工时**：14h（4人 × 3.5h）
- **负责人**：全员
- **所属 Sprint**：Sprint 2（Day 11-12）
- **依赖**：CP-INT-002

---

## 四、工时汇总

### 4.1 按成员汇总

| 成员 | 模块 | Sprint 1 (h) | Sprint 2 (h) | 合计 (h) |
|------|------|-------------|-------------|----------|
| 王一林 | M1 AI与知识库 | 42 | 42 | 84 |
| 张讯毓 | M2 校园服务中心 | 42 | 42 | 84 |
| 李欢 | M3 校园社区与互助 | 42 | 42 | 84 |
| 覃焓 | M4 平台治理 + 基础设施 | 42 | 42 | 84 |
| **总计** | | **168** | **168** | **336** |

### 4.2 按 Epic 汇总

| Epic | 问题数 | 总工时 (h) | Sprint 1 (h) | Sprint 2 (h) |
|------|--------|-----------|-------------|-------------|
| CP-EPIC-M4 | 10 | 118 | 76 | 42 |
| CP-EPIC-M2 | 7 | 67 | 49 | 18 |
| CP-EPIC-M1 | 7 | 98 | 56 | 42 |
| CP-EPIC-M3 | 10 | 108 | 52 | 56 |
| CP-EPIC-INFRA | 3 | 35 | 35 | 0 |
| CP-EPIC-INT | 3 | 70 | 0 | 70 |
| **总计** | **40** | **496** | — | — |

> **注**：Epic 级合计工时（496h）超过总工时池（336h），因为部分问题存在人员并行（联调/测试阶段全员参与同一问题）。实际每人分配工时严格控制在 84h。

### 4.3 按优先级汇总

| 优先级 | 问题数 |
|--------|--------|
| Highest | 3 |
| High | 22 |
| Medium | 9 |
| Low | 2 |

---

## 五、人员分配详情

### 王一林（M1 — AI与知识库）

| Sprint | Issue | 任务 | 工时 |
|--------|-------|------|------|
| S1 | CP-M1-001 | 知识库 CRUD 与成员管理 | 7h |
| S1 | CP-M1-002 | 文档上传与异步解析（部分） | 14h |
| S1 | CP-M1-002 | 文档上传与异步解析（继续） | 7h |
| S1 | CP-M1-003 | 文档切分与向量索引 | 14h |
| S2 | CP-M1-004 | RAG 检索流水线 | 14h |
| S2 | CP-M1-005 | DeepSeek 流式对话与 SSE（部分） | 14h |
| S2 | CP-M1-005 | DeepSeek 流式对话与 SSE（继续） | 7h |
| S2 | CP-M1-006 | 对话管理与反馈 | 7h |
| **合计** | | | **84h** |

### 张讯毓（M2 — 校园服务中心）

| Sprint | Issue | 任务 | 工时 |
|--------|-------|------|------|
| S1 | CP-M2-001 | 办事指南管理 | 14h |
| S1 | CP-M2-002 | 材料清单生成 | 7h |
| S1 | CP-M2-003 | 部门联系人管理 | 7h |
| S1 | CP-M2-004 | 报修工单创建与查询 | 14h |
| S1-2 | CP-M2-005 | 工单状态机与流转 | 14h |
| S2 | CP-M2-006 | 工单评价 | 4h |
| S2 | CP-M2-007 | Mock 适配器 | 7h |
| S2 | (前端页面) | M2 前端页面（含在问题工时中） | — |
| 联调 | CP-INT-001/002 | 联调与测试 | 14h |
| 验收 | CP-INT-003 | 验收 | 3h |
| **合计** | | | **84h** |

### 李欢（M3 — 校园社区与互助）

| Sprint | Issue | 任务 | 工时 |
|--------|-------|------|------|
| S1 | CP-M3-001 | 话题管理 | 4h |
| S1 | CP-M3-002 | 帖子 CRUD 与内容发布 | 14h |
| S1 | CP-M3-003 | 评论管理 | 10h |
| S1 | CP-M3-004 | 点赞收藏与互动 | 7h |
| S1 | CP-M3-005 | 匿名树洞 | 7h |
| S2 | CP-M3-006 | 举报管理 | 10h |
| S2 | CP-M3-007 | 校园活动管理 | 14h |
| S2 | CP-M3-008 | 失物发布与匹配 | 14h |
| S2 | CP-M3-009 | 认领管理 | 7h |
| 联调 | CP-INT-001/002 | 联调与测试 | 14h |
| 验收 | CP-INT-003 | 验收 | 3h |
| **合计** | | | **104h** |

> **注**：李欢 的 Epic 预估超 84h，实际执行时需压缩或挪入 P1（如 CP-M3-007 活动管理可部分延后）。前端页面工时已包含在各问题中。

### 覃焓（M4 — 平台治理 + 基础设施）

| Sprint | Issue | 任务 | 工时 |
|--------|-------|------|------|
| S1 | CP-INFRA-001 | 项目脚手架与环境搭建 | 14h |
| S1 | CP-INFRA-002 | 数据库迁移与种子数据 | 14h |
| S1 | CP-INFRA-003 | Celery 配置 | 7h |
| S1 | CP-M4-010 | OpenAPI 契约 | 7h |
| S1 | CP-M4-001 | 用户认证系统 | 14h |
| S1 | CP-M4-002 | RBAC 角色权限 | 14h |
| S1 | CP-M4-003 | 用户管理 CRUD | 7h |
| S1 | CP-M4-004 | 敏感词管理 | 10h |
| S2 | CP-M4-005 | 内容审核队列 | 14h |
| S2 | CP-M4-006 | 审计日志 | 10h |
| S2 | CP-M4-007 | 系统配置 | 7h |
| S2 | CP-M4-008 | 运营看板 | 7h |
| S2 | CP-M4-009 | 管理端前端 | 28h |
| 联调 | CP-INT-001/002 | 联调与测试 | 14h |
| 验收 | CP-INT-003 | 验收 | 3h |
| **合计** | | | **170h** |

> **注**：覃焓 的 Epic 预估超 84h，实际执行时：CP-M4-009 前端页面需压缩或分担；CP-M4-008 运营看板可降为 P2 延后。前端页面与后端开发之间的 "脚手架日" 部分时间可与其他成员共享。

---

## 六、风险提示与建议

1. **工时超配风险**：M4（覃焓）和 M3（李欢）的预估工时超过每人 84h 上限。建议将低优先级功能（CP-M4-008 运营看板、CP-M4-009 部分前端页面、CP-M3-007 活动管理中的 Celery Beat 部分）标记为 P1，视进度决定是否纳入 Sprint 交付。

2. **M4 前置依赖**：M4 的认证授权是 M1/M2/M3 的硬依赖。M4 必须在 Sprint 1 前 3 天完成 CP-M4-001（认证）和 CP-M4-002（RBAC），否则所有模块的联调都会阻塞。

3. **M1 RAG 评测**：30 题评测集的准确率 ≥ 80% 是硬性验收标准，需在 Sprint 2 尽早开始评测并迭代优化 Prompt 和检索参数。

4. **DeepSeek API 依赖**：DeepSeek V4 Pro 为外部依赖，需提前确保 API Key 可用、网络通畅。建议准备 Mock LLM 适配器防止 API 不可用时开发阻塞。

5. **交叉联调窗口**：Sprint 2 Day 9-10 的联调窗口仅 2 天，时间紧张。建议各模块在 Sprint 1 结束前完成模块内自测，减少联调时的 Bug 数量。

---

## 七、在 Jira 中如何填写

### 7.1 创建史诗（Epic）

在 Jira 中创建以下 Epic：

| Epic Name | Epic Key |
|-----------|----------|
| 公共基础与平台治理 | CP-EPIC-M4 |
| 校园服务中心 | CP-EPIC-M2 |
| AI 与知识库 | CP-EPIC-M1 |
| 校园社区与互助 | CP-EPIC-M3 |
| 基础设施与部署 | CP-EPIC-INFRA |
| 联调集成与测试 | CP-EPIC-INT |

### 7.2 创建 Sprint

- **Sprint 1**：6 天，命名为 "CampusPilot Sprint 1 - P0 模块开发"
- **Sprint 2**：6 天，命名为 "CampusPilot Sprint 2 - 前端/联调/测试/验收"

### 7.3 创建问题（Issue）

每个问题按上述模板填写：
- **Issue Type**：Story（用户故事）或 Task（技术任务）
- **Summary**：使用上面定义的 Issue Key + 标题
- **Description**：填入"用户故事 + 功能描述 + 需要完成的内容"
- **Acceptance Criteria**：填入"验收标准"
- **Priority**：Highest / High / Medium / Low
- **Story Points / Original Estimate**：填入预估工时
- **Assignee**：指定负责人
- **Sprint**：指定所属 Sprint
- **Epic Link**：关联对应 Epic
- **Components**：按模块设置（如 "M1-AI"、"M2-Service"）

### 7.4 示例：一个问题在 Jira 中的展示效果

```
Summary: CP-M4-001 用户认证系统（登录/注册/JWT/Refresh Token）

Description:
用户故事：作为平台用户，我希望通过用户名和密码进行注册和登录，
系统自动管理我的会话 Token，确保我的身份安全可信。

功能描述：实现基于 Argon2id 的密码哈希、JWT Access Token...
（详细内容如上述模板）

Acceptance Criteria:
1. 用户可通过 POST /api/v1/auth/register 注册新账号
2. 用户可通过 POST /api/v1/auth/login 登录
...（共 8 条）

Priority: Highest
Original Estimate: 14h
Assignee: 覃焓
Sprint: CampusPilot Sprint 1
Epic Link: CP-EPIC-M4
```

---

*文档版本：V1.0*  
*最后更新：2026-07-14*
