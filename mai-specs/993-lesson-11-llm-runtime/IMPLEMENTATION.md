# 993 实施记录

> 日期：2026-11-22｜Profile：standard｜状态：实现完成，真实联调与数据库验收待环境
> [BRIEF](BRIEF.md) · [PRD](spec/PRD.md) · [技术方案](design/TECH_DESIGN.md) · [代码设计](design/CODE_DESIGN.md)

## 交付范围

本次按 [PRD](spec/PRD.md) 与仓库技术方案实现 LLM Runtime、平台模型路由、能力测试与分析 Chat 选模调用。实现遵循用户最新提供的 [第 11 课 PRD](../../../agent-engineering-course-kit/chapters/chapter_04_llm_runtime/lesson_11_llm_runtime_boundary/learning-materials/lesson-11-learning-materials/lesson-11-llm-runtime/PRD.md) 与 [技术方案](../../../agent-engineering-course-kit/chapters/chapter_04_llm_runtime/lesson_11_llm_runtime_boundary/learning-materials/lesson-11-learning-materials/lesson-11-llm-runtime/TECH_DESIGN.md)：目录覆盖 SDK 生成模型与 System One 决策预设、手工 `provider/model`、供应商连接参数、三模型适配边界与 Chat 准入由平台开关和权限共同决定。

### Agent

| 文件                                        | 内容                                                                                                                                     |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `src/compass_agent/schemas.py`              | 公共 DTO：可信上下文、能力、目录、Provider、测试/Chat 输入、决策类型、结果、记录、SSE 事件与错误 envelope                                |
| `src/compass_agent/llm/`                    | `contracts`、`policy`、`errors`、`transport`、`runtime`、`capabilities`、`catalog`、`continuation`、`litellm_chat`、`systemone_decision` |
| `src/compass_agent/models/`                 | 三表 ORM、短事务仓储、准入/快照/占位/执行/记录服务、端点与连接参数校验、凭据与续接信封                                                   |
| `src/compass_agent/model_api.py`            | 服务身份、可信上下文、管理/目录/可用模型/测试/Chat/记录路由、SSE 与有界队列                                                              |
| `migrations/versions/0002_model_runtime.py` | `model_provider` / `model_change` / `model_call`，含唯一别名、版本、用途/作用域与决策隐藏约束                                            |
| `db/main/sql/`                              | Alembic 离线生成 bootstrap、`empty → 0001`、`0001 → 0002` 与 manifest                                                                    |
| `scripts/db-sql.mjs`                        | `pnpm db:sql -F agent` / `db:sql:check` 生成入口                                                                                         |

### Web

| 文件                                                      | 内容                                                                                                                     |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `src/contracts/models.ts`                                 | 模型写请求 zod DTO、Agent 上下文类型与生成契约类型别名                                                                   |
| `src/lib/model-contracts.ts`                              | Ajv 2020 运行时校验生成 JSON Schema                                                                                      |
| `src/server/model-agent-client.ts`                        | Agent HTTP 客户端、错误映射、SSE 帧校验与转发                                                                            |
| `src/server/modules/models/`                              | 平台/租户准入：平台管理员、租户/空间、`module.analysis` 与 `models.invoke`                                               |
| `src/server/http/model-routes.ts`                         | `/api/web/v1/platform/models/*`、`/models/available`、`/models/calls` 代理与流式中继（BFF 资源前缀，页面路由不暴露）     |
| `src/features/models/pages.tsx`                           | “Agent 与能力 → 模型路由”单入口：平台管理员看到模型列表、添加/编辑、启停与 Chat 展示、能力测试面板；其他成员看到只读摘要 |
| `src/components/analysis-composer.tsx`                    | 主模型选择、连续对话、流式输出、停止、附件内存提示                                                                       |
| `src/client/request-scope.ts`                             | `modelRequest`：DocumentSession 所有权、CSRF、revision 校验、逐事件复核与 abort                                          |
| `db/main/migrations/0003_model_invocation_permission.sql` | `models.invoke` 与权限数组上限 23/17                                                                                     |

### 契约与文档

- `scripts/export-contracts.py` 导出 OpenAPI 与 9 份模型 JSON Schema；`scripts/contracts.mjs` 同步到 `contracts/` 与 `apps/web/src/generated/`。
- 新增 `apps/agent/db/main/README.md`、`apps/agent/src/compass_agent/llm/README.md`、`apps/agent/src/compass_agent/models/README.md`。
- 更新 Agent/Web README、Web 服务端模块说明、Web 数据库 README、迁移规范、根 README、`.env.example`、`contracts/README.md` 与长期规则中的已交付边界。

## 数据库版本

| 单位         | 起点 → 目标                                                 | 源                                                                 | SQL 交付                                                                                                          |
| ------------ | ----------------------------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `agent/main` | empty / 0001 → `0002_model_runtime`                         | `apps/agent/migrations/versions/0002_model_runtime.py`             | `apps/agent/db/main/sql/bootstrap.sql`、`upgrades/0001_demo_fixture__0002_model_runtime.sql`、`manifest.json`     |
| `web/main`   | `0002_readable_routes` → `0003_model_invocation_permission` | `apps/web/db/main/migrations/0003_model_invocation_permission.sql` | 更新 `bootstrap.sql`、追加 `upgrades/0002_readable_routes__0003_model_invocation_permission.sql`、`manifest.json` |

两个库分别迁移、分别恢复，不假定跨库事务。Agent 0001 与 Web 0001/0002 历史文件未修改。

## 与设计文档的差异

技术方案中的独立平台页面 `platform/models` 未保留：模型接入只有一个页面入口「管理与治理 → Agent 与能力 → 模型路由」（`admin/capabilities/models`）。平台管理员在该页看到完整管理面板，其他获权成员只读摘要，因此不需要再在「平台管理」分组里重复一个入口，也不会出现两个页面各自维护同一份配置。BFF 资源前缀 `/api/web/v1/platform/models/*` 与 Agent 路由保持不变，仅页面路由合并。

## 已执行验证

| 检查                                              | 结果                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ruff check` / `ruff format --check`（Agent）     | 通过                                                                                                                                                                                                                                                                                                                      |
| `pyright`（`apps/agent/src`、`apps/agent/tests`） | 0 errors                                                                                                                                                                                                                                                                                                                  |
| `pytest apps/agent/tests`                         | 64 passed，1 skipped（缺少 `COMPASS_TEST_DATABASE_URL` 的 PostgreSQL 集成测试）                                                                                                                                                                                                                                           |
| TypeScript `tsc --noEmit`（Web）                  | 通过                                                                                                                                                                                                                                                                                                                      |
| ESLint（Web，含导入边界与 a11y 规则）             | 通过，0 warning                                                                                                                                                                                                                                                                                                           |
| 生成契约                                          | 用同一 Python 导出器与 openapi-typescript 重新生成，逐文件比对无差异；9 份 JSON Schema 用 Ajv 2020 编译，Provider、start、text_delta 与 final 样例校验通过                                                                                                                                                                |
| SQL 交付                                          | Web 由生成器重写并校验 SH 摘要；Agent 由 Alembic 离线 SQL 生成 manifest；`test_database_tools_integration.py` 校验摘要、版本链与生成入口                                                                                                                                                                                  |
| Web 单元测试（Vitest）                            | 未执行：Windows 沙箱禁止 Vite 内部子进程，`spawn EPERM`；测试文件已随代码提交                                                                                                                                                                                                                                             |
| PostgreSQL 迁移与应用                             | 在本机 `compass-postgres-1`（pgvector/pgvector:pg17，127.0.0.1:55432）执行：`web/main` 由 `0002_readable_routes` 升至 `0003_model_invocation_permission`，`agent/main` 由 `0001` 升至 `0002`；`alembic_version=0002`、`model_provider` 表与 `tenant_member_permission_count/_allowed` 约束存在；两侧 `/readyz` 均返回 200 |
| Agent 模型接口冒烟                                | 携带服务身份与可信上下文：`/v1/models/catalog` 返回 LiteLLM 目录（`deepseek/deepseek-chat` 等）、`/v1/models/providers` 返回空列表；缺少服务身份返回 401；非法 `provider/model` 返回 422 与中文提示                                                                                                                       |
| BFF 路由冒烟                                      | `/api/web/v1/platform/models`、`/platform/models/catalog`、`/models/available`（带租户/空间）均 200；无会话返回 401；响应带 `private, no-store`                                                                                                                                                                           |
| 浏览器页面冒烟                                    | 本机 Chromium 打开 `/t/yuanshan/w/huadong/admin/capabilities/models`：页签、模型列表、添加 Provider 弹窗（模型清单、别称、Base URL、API Key、连接参数、接入与 Chat 开关）渲染正常，网络无失败请求                                                                                                                         |
| 真实模型调用                                      | 未执行：无供应商凭据与额度；Fake Adapter 与受控 SSE 测试通过不替代真实联调                                                                                                                                                                                                                                                |

## 未完成项与限制

1. `httpx`、`cryptography`、`jsonschema` 与 `litellm-core 1.106.6` 已在开发环境安装，但离线环境无法更新 `uv.lock`，因此未写入 `pyproject.toml`。运行时使用按需导入：缺少加密或校验依赖时保存/调用安全失败，管理接口、可用模型与契约导出仍可用。在有网络的环境执行 `uv add --package compass-agent httpx cryptography jsonschema litellm-core==1.106.6` 并提交锁文件，然后重新运行 Agent 检查。当前证据中的 64 项测试在已安装依赖的本机执行；CI 若缺少这些依赖，相关模块通过 `pytest.importorskip` 跳过，跳过不计作通过。
2. Agent 的受管角色供应与 `db:apply-sql` 入口尚未交付；当前提供 `db:sql`、`db:sql:check`、原生 `db:migrate` 与 SQL manifest。正式环境仍需分离迁移/运行账号。
3. Web `0003` 与 Agent `0002` 已在本机 PostgreSQL 17 完成旧库升级，但空库 bootstrap、SQL 交付文件重放、备份恢复与重复执行仍待专用库验收；本地演示数据（租户「远山零售」、空间「华东区域」）只存在于本机开发库。
4. 三模型真实联调、429/5xx/断流供应商回归与并发压测待配置凭据后执行。单次出站、取消、终态、能力拒绝等已在 Fake Adapter 与受控流中覆盖。
5. 分析 Chat 本期仅保留页面内对话；附件只在本页内存存在且不发送给模型；服务端会话历史、工具执行、自动路由与重试降级不在本期范围。
