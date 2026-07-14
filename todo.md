# CampusPilot 开发任务

## 固定开发规则

- 一次只完成一个小型任务。
- 每次完成任务并验证通过后，在本文档标记完成情况。
- 开发模块前，先创建并推送对应名称的远端分支，例如 M3 使用 `m3`。
- 严格按当前任务范围实现，不做需求外扩展或过度兜底。
- 实现中发现的 OpenAPI、状态码、响应字段或跨模块依赖差异，先记录在本文档的“契约与设计差异”中；M4 完成时统一更新受影响文档，供后续模块开发使用。
- 每个小型任务使用 GitHub Issue 记录问题、范围和验收标准，并在提交中关联；验证并推送后主动关闭 Issue。
- 每个模块使用同一个 Pull Request 持续交付；模块未完成时保持 Draft，完整验收后转为 Ready。

## 当前模块

- M4：公共基础与平台治理
- 开发分支：`m4`

## 契约与设计差异

- 认证登录临时锁定：M4 详细设计第 4.2 节和错误码表写为 429，但 OpenAPI `/api/v1/auth/login` 定义 `423 Locked` 及 `Retry-After`。后续实现固定使用 `423 ACCOUNT_LOCKED`，429 仅用于限流；影响依赖认证的前端与 M1/M2/M3。M4 收尾时修订详细设计对应章节，OpenAPI 保持现有定义。
- 认证失败达到锁定阈值的当前请求：实现固定返回 `401 INVALID_CREDENTIALS`，从下一次请求开始返回 `423 ACCOUNT_LOCKED`；详细设计未规定该边界行为。影响依赖认证的前端与 M1/M2/M3；M4 收尾时在登录流程补充该规则。
- 认证登录禁用账号：实现返回 `403 ACCOUNT_DISABLED`，与详细设计一致，但 OpenAPI `/api/v1/auth/login` 当前未声明 403。影响依赖认证的前端与 M1/M2/M3；M4 收尾时补充 OpenAPI 响应并校验生成客户端。
- 认证 Cookie 的 Origin 校验：`POST /api/v1/auth/refresh` 与 `/api/v1/auth/logout` 对缺失或非 `FRONTEND_ORIGIN` 的 Origin 返回 `403 AUTH_FORBIDDEN`，且不执行认证服务；当前 OpenAPI 未声明该 403。影响前端刷新/登出请求；M4 收尾时补充 403 响应与 Origin 要求，CORS 策略仍由后续独立任务实现。
- 幂等登出：实现对缺失、未知、已撤销和有效 Refresh Cookie 均返回 `200`、清除 Cookie；当前 OpenAPI `/api/v1/auth/logout` 声明 401，详细设计仅笼统说明“重复登出按幂等成功处理”。影响前端登出逻辑；M4 收尾时将 OpenAPI 与详细设计的精确行为同步为该规则。

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
- [x] [#4 M4：建立平台数据库迁移基线](https://github.com/Doggod727/CampusPilot/issues/4)（2026-07-14）
  - 建立 SQLAlchemy async + Alembic 迁移基础设施。
  - 首版 Revision 覆盖 M4 平台 Schema、11 张表、约束、索引、函数、触发器和注释。
  - upgrade SQL 与原始 M4 DDL 共 51 条语句逐条一致；downgrade 保留共享扩展。
  - Alembic 离线升降级、单 head、全部自动化测试 `15 passed`，Python 编译检查通过。
- [x] [#5 M4：建立身份与权限 ORM 模型](https://github.com/Doggod727/CampusPilot/issues/5)（2026-07-14）
  - 建立共享 SQLAlchemy DeclarativeBase。
  - 映射 users、roles、permissions 及两张关联表，与首版迁移的类型、约束和索引一致。
  - 保持 Alembic target_metadata 未注册，避免未映射治理表被误删。
  - PostgreSQL 方言离线编译、迁移回归及全部自动化测试 `21 passed`。
- [x] [#7 M4：建立异步数据库会话基础设施](https://github.com/Doggod727/CampusPilot/issues/7)（2026-07-14）
  - Database 显式持有 AsyncEngine 与 async_sessionmaker，不在导入或应用启动时连接数据库。
  - Session 不自动提交，异常退出执行 rollback，所有路径均关闭 Session。
  - `/health/live` 保持不读取数据库配置或访问 PostgreSQL。
  - Alembic 离线升降级、Python 编译检查及全部自动化测试 `27 passed`。
- [x] [#9 M4：实现用户只读查询仓储](https://github.com/Doggod727/CampusPilot/issues/9)（2026-07-14）
  - UserRepository 支持按 CITEXT 用户名和 UUID 查询未软删除用户。
  - 仓储只使用调用方 Session 执行查询，不提交、回滚、flush 或关闭 Session。
  - PostgreSQL 查询编译、Alembic 离线升降级及 Python 编译检查通过。
  - 全部自动化测试 `30 passed`。
- [x] [#10 M4：实现 RBAC 只读查询仓储](https://github.com/Doggod727/CampusPilot/issues/10)（2026-07-14）
  - RbacRepository 支持查询用户角色和去重后的权限码，并按 code 稳定排序。
  - 两类查询均连接 users 并排除软删除用户，不修改或关闭调用方 Session。
  - PostgreSQL 查询编译、Alembic 离线升降级及 Python 编译检查通过。
  - 全部自动化测试 `33 passed`。
- [x] [#11 M4：实现 Argon2id 密码哈希适配器](https://github.com/Doggod727/CampusPilot/issues/11)（2026-07-14）
  - 使用 argon2-cffi 默认安全参数提供密码哈希、验证和重哈希判断。
  - 密码不匹配和非法哈希统一安全返回验证失败，不记录或返回明文密码。
  - Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `36 passed`。
- [x] [#12 M4：添加演示账号与 RBAC 种子命令](https://github.com/Doggod727/CampusPilot/issues/12)（2026-07-14）
  - `python -m app.scripts.seed_demo` 在单事务中收敛权限、系统角色、角色权限及 6 个演示账号。
  - 种子密码只从运行时 `DEMO_SEED_PASSWORD` 读取，并使用 Argon2id 哈希；命令输出不包含密码。
  - PostgreSQL 方言 SQL、Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `41 passed`；真实空库执行仍待 PostgreSQL 环境可用后完成。
- [x] [#13 M4：映射 Refresh Token ORM 模型](https://github.com/Doggod727/CampusPilot/issues/13)（2026-07-14）
  - 映射既有 refresh_tokens 的 UUID、SHA-256 哈希、时区、INET、约束和部分索引元数据。
  - 不保存原始 Refresh Token，且实体表示不泄露 token_hash。
  - PostgreSQL 方言编译、Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `43 passed`。
- [x] [#14 M4：实现 JWT 与 Refresh Token 基础服务](https://github.com/Doggod727/CampusPilot/issues/14)（2026-07-14）
  - TokenService 使用 HS256 签发并校验包含用户、角色与权限上下文的短期 Access Token。
  - 高熵 Refresh Token 仅输出运行时原值、UUID 与 SHA-256 哈希；敏感值不参与 repr 或异常信息。
  - Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `51 passed`。
- [x] [#15 M4：实现 Refresh Token 持久化仓储](https://github.com/Doggod727/CampusPilot/issues/15)（2026-07-14）
  - RefreshTokenRepository 支持新增、哈希精确行锁读取、轮换标记、单 Token 撤销及用户全部 Token 撤销。
  - 所有写操作只更新尚未撤销记录；仓储不管理调用方 Session 的事务或生命周期。
  - PostgreSQL 方言 SQL、Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `56 passed`。
- [x] [#16 M4：实现登录状态原子更新仓储](https://github.com/Doggod727/CampusPilot/issues/16)（2026-07-14）
  - UserAuthRepository 以原子更新记录登录失败、阈值锁定及成功登录后的状态重置。
  - 更新排除软删除和禁用用户，不更新 version，且不管理调用方 Session 生命周期。
  - PostgreSQL 方言 SQL、Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `60 passed`。
- [x] [#17 M4：实现认证锁定策略配置读取](https://github.com/Doggod727/CampusPilot/issues/17)（2026-07-14）
  - 映射 app_configs，并以单条只读查询严格加载认证失败阈值与锁定分钟数。
  - 缺失、重复、非整数或非正策略均安全拒绝；演示种子幂等写入 5 次失败和 15 分钟锁定配置。
  - PostgreSQL 方言 SQL、Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `68 passed`。
- [x] [#18 M4：实现审计日志写入基础](https://github.com/Doggod727/CampusPilot/issues/18)（2026-07-14）
  - 映射 audit_logs 的 UUID、INET、JSONB、约束、索引及用户逻辑外键。
  - AuditLogRepository 只向调用方 Session 追加事件，不提供修改、删除或事务生命周期操作。
  - PostgreSQL 方言 SQL、Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `70 passed`。
- [x] [#19 M4：实现审计服务与敏感字段脱敏](https://github.com/Doggod727/CampusPilot/issues/19)（2026-07-14）
  - AuditService 在调用方事务内写入成功/失败事件，并在写入前递归复制和脱敏审计快照。
  - password、token、authorization、cookie、api_key、secret 的命名变体均替换为 `***`，原始入参保持不变。
  - PostgreSQL 方言 SQL、Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `74 passed`。
- [x] [#21 M4：实现登录应用服务核心](https://github.com/Doggod727/CampusPilot/issues/21)（2026-07-14）
  - AuthService 在单一调用方事务中编排用户状态、密码校验、RBAC、Token、Refresh 哈希持久化及成功/失败审计。
  - 未知用户与密码错误统一 401；禁用为 403；锁定为 423 并携带 Retry-After；阈值当前请求的 401 规则已登记至契约台账。
  - Python 编译检查及 Alembic 单 head、离线升降级回归通过。
  - 全部自动化测试 `80 passed`。
- [x] [#22 M4：实现登录 HTTP 接口与 Refresh Cookie](https://github.com/Doggod727/CampusPilot/issues/22)（2026-07-14）
  - `POST /api/v1/auth/login` 返回统一成功信封、完整当前用户上下文，并设置 HttpOnly、SameSite=Lax 的 Refresh Cookie。
  - 新增显式 REFRESH_COOKIE_SECURE 配置；健康检查保持不读取配置或连接数据库。
  - 禁用账号 403 的 OpenAPI 漏项已登记至契约台账，留待 M4 收尾统一更新文档。
  - Python 编译检查及 Alembic 单 head、离线升降级回归通过；全部自动化测试 `84 passed`。
- [x] [#23 M4：实现 Refresh Token 轮换与复用检测服务](https://github.com/Doggod727/CampusPilot/issues/23)（2026-07-14）
  - AuthService 在单一事务中锁定有效 Refresh Token，签发 Access/替换 Refresh Token、标记旧 Token 已轮换，并只持久化新 Token 哈希。
  - 已轮换 Token 的复用会撤销该用户全部有效 Refresh Token，并返回 `401 REFRESH_TOKEN_REUSED`；缺失、过期、撤销及非 active 用户统一返回 `401 INVALID_REFRESH_TOKEN`，不泄露用户状态或原始 Token。
  - 审计事件不保存原始 Refresh Token 或哈希；Python 编译检查及 Alembic 单 head、离线升降级回归通过；全部自动化测试 `91 passed`。
- [x] [#24 M4：实现 Refresh HTTP 接口与 Origin 校验](https://github.com/Doggod727/CampusPilot/issues/24)（2026-07-14）
  - `POST /api/v1/auth/refresh` 从 Cookie 读取 Refresh Token，返回统一 TokenData 信封，并以相同安全属性覆盖轮换 Cookie。
  - 刷新路由仅接受配置的前端 Origin（兼容配置末尾斜杠）；缺失或不匹配时在数据库依赖前返回 403。全局 CORS、注销、Bearer 认证依赖和限流保持后续独立任务。
  - Python 编译检查及 Alembic 单 head、离线升降级回归通过；全部自动化测试 `99 passed`。
- [x] [#25 M4：实现幂等登出与 Cookie 清除](https://github.com/Doggod727/CampusPilot/issues/25)（2026-07-14）
  - `AuthService.logout` 在单一事务中锁定并撤销有效 Refresh Token；未知和已撤销 Token 同样幂等完成，审计不保存原始 Token 或哈希。
  - `POST /api/v1/auth/logout` 复用 Cookie Origin 校验，统一返回空数据成功信封并以原 Path/安全属性清除 Refresh Cookie。
  - Python 编译检查及 Alembic 单 head、离线升降级回归通过；全部自动化测试 `107 passed`。
- [x] [#26 M4：实现 Bearer 认证依赖与当前用户接口](https://github.com/Doggod727/CampusPilot/issues/26)（2026-07-14）
  - 增加可复用的 Bearer Access Token 认证依赖；JWT 无效时不创建数据库上下文，用户软删除、禁用、锁定或用户名 Claim 不匹配统一返回 `401 AUTH_UNAUTHORIZED`。
  - `GET /api/v1/auth/me` 返回数据库当前用户资料、角色与权限；Access Token 权限 Claim 不作为该响应的事实来源。
  - Python 编译检查及 Alembic 单 head、离线升降级回归通过；全部自动化测试 `119 passed`。

## 待办

- [ ] 下一项小型任务尚未选择。
- [ ] Docker/PostgreSQL 可用后，在真实空库执行 `alembic upgrade head` 与 `alembic downgrade base`。
