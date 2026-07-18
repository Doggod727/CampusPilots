# 四川大学公开数据快照

采集日期：2026-07-17。来源：四川大学官网（https://www.scu.edu.cn/）公开页面。

## 文件说明

- `seed_data.json` — M2 种子数据：4 个真实校区、8 个真实服务部门与联系窗口、3 个指南分类、6 项服务指南（含适用校区、材料清单与办理步骤）。
- `organizations.json` — 官网组织机构页全量快照（机关部处 27、院系 42、业务单位 12）。
- `knowledge_docs.json` + `docs/*.md` — M1 知识库真实文档（学校简介、校区地址、校庆公告、川大要闻、基层动态共 12 篇全文）。

## 采集方法与边界

- scu.edu.cn 部署瑞数 WAF，纯 HTTP 请求只返回 202 JS 挑战页；本快照经真实浏览器会话（Playwright）通过挑战后提取，仅访问公开页面，未绕过任何访问控制。
- 部门名称、校区名称与地址、新闻公告全文为官网真实内容。
- 联系窗口的办公地点/时间为按部门职能编制的演示值（官网“云上川大·服务大厅”域名不可解析，办事指南详情不可得）；电话统一为明显占位号 `028-00000000`（数据库约束要求电话或邮箱至少一项非空），邮箱置空，不虚构真实联系方式。
- 指南的 `source_url` 指向已验证可达的官网组织机构页面。
- 电费与充值按设计为 Mock 业务（`is_simulated=true`），不对应真实电费系统。

## 隐私审核（2026-07-18）

- 已对全部文件执行手机号（`1[3-9]\d{9}`）、座机、邮箱、身份证（`\d{17}[\dX]`）模式扫描：零命中（`028-00000000` 为明确占位号，非真实联系方式）。
- 新闻公告中出现的公开职务人名属官网公开报道内容，保留；不含个人手机号与个人邮箱。
- 仓库级守护测试 `backend/tests/test_scu_snapshot.py` 持续阻止个人敏感模式与缺失 `source_url` 的文档入库。

## 内容哈希（SHA-256，2026-07-18 审核）

- `organizations.json`：`ad2ea38926ee6dd48f581dec4a16c7eb3c2fc4832bbf3818bc542bec67852e53`
- `seed_data.json`：`3d14ceb07a7ba69494d89909aefe9ace1ffff4288261ac5b24ee8f39d2c25ffd`
- `knowledge_docs.json`：`d7438e10bb181f13deb8785fc47e2d062d0f4f678425cdc7500bf4dd7d535079`
- `docs/school-intro.md`：`742b80f920cf3209f4150b8faa3107d4c41028970fee115dd787723994da9849`
- `docs/campuses.md`：`e9b5ce5b44b03a85ca40ef38b2fc528827684d44b6149942a6e95a9b19a3bca8`
- `docs/anniversary-130.md`：`8372ef5a3752f57278bc4e369d4f8aef5114db784f1dedf3bd0f41a7945bfc12`
- `docs/news-national-science-awards.md`：`d4d9b8fef35c950daa7996cfc50a22272f2065f3944a9d15340ebb86e7bfbd88`
- `docs/news-party-study-session.md`：`d4c60a3261120e83b6808d642b845138d80b800aeddd16dfbbe992670451a154`
- `docs/news-july-first-commendation.md`：`0585424482982dd58d01fcf79ffeb01c3fec6623c15730e49bc4dd24bd90e4e3`
- `docs/news-spring-semester-summary.md`：`ba0837de9e5abd13970c1c9b9bac3e6eaca8076737ef537652773b33d9dc33cb`
- `docs/news-graduate-party-role-model.md`：`632df37ebf415e55e51b2b6fa0f852b8dbf3b294c84d3cacf67e20a897e8fe8b`
- `docs/news-ai-school-research.md`：`a9054fc3408d3503f2b8a228e94e4fcd4563d9cc2f169075a24e2778035f836b`
- `docs/news-summer-practice-departure.md`：`1004684a19cfb04d3627bdf4dcb2b0449beb65457c747ff2ca7e47a2b595fb3f`
- `docs/news-women-children-conference.md`：`8692e6c0e7a398b3655467c90b9f8fadd8b48120f8dbd40a23c7c4339b53326e`
- `docs/news-computer-101-plan.md`：`63301e66ee50a6e4b2d2781d1d87cdcd587ebcf128b751e2f223ffcb9a59bf68`
