# CampusPilot 开发任务

## 固定开发规则

- 一次只完成一个小型任务。
- 每次完成任务并验证通过后，在本文档标记完成情况。
- 开发模块前，先创建并推送对应名称的远端分支，例如 M3 使用 `m3`。
- 严格按当前任务范围实现，不做需求外扩展或过度兜底。
- 每个小型任务使用 GitHub Issue 记录问题、范围和验收标准，并在提交中关联；验证并推送后主动关闭 Issue。
- 每个模块使用同一个 Pull Request 持续交付；模块未完成时保持 Draft，完整验收后转为 Ready。

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

## 待办

- [ ] 下一项小型任务尚未选择。
- [ ] Docker/PostgreSQL 可用后，在真实空库执行 `alembic upgrade head` 与 `alembic downgrade base`。
