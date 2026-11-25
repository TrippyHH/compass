# Agent

Agent Runtime 与推理业务应用。FastAPI + SQLAlchemy + psycopg + Alembic，Python 包名 `compass-agent`，导入入口 `compass_agent`。`package.json` 仅将 Python 命令暴露给 Turbo；Python 依赖以 `pyproject.toml` / 根 `uv.lock` 为准。

从工程根运行 `pnpm dev -F agent`，统一加载根 `.env`。本目录 `turbo.json` 为构建和静态检查声明 Python 工具链缓存指纹；开发、迁移及 seed 不缓存。

## 接口

- `/healthz`：进程存活，无数据库依赖。
- `/readyz`：数据库、演示表与模型表可用性；未迁移时返回 503。
- `/v1/demo/overview`：固定合成数据聚合；默认关闭，启用后仍只读。
- `/v1/models/*`：平台模型目录、Provider 管理、能力测试与统一分析 Chat 调用；契约见 [schemas.py](src/compass_agent/schemas.py) 与 [model_api.py](src/compass_agent/model_api.py)。
- `POST /v1/models/calls`：每轮一次请求，服务端依次完成两级分类、字典特征、fixed/auto/SC-39 选模和一次主模型推理；响应分层返回 `intent/features/routing/result/metrics`。路由实现与边界见 [routing/README.md](src/compass_agent/routing/README.md)。

模型接口要求 BFF 服务身份与可信上下文：`Authorization: Bearer $COMPASS_AGENT_SERVICE_TOKEN` 加 `X-Compass-Context`（base64url 的 TrustedContext JSON）。Agent 不直连 Web 库，也不接受浏览器声明的角色作为权限。平台管理与测试路由只接平台作用域；Chat 只接租户作用域，并在出站前校验启用、Chat 可见性与“生成模型输入”。统一 Chat 流为 `start → classification_status/route_selected → text_delta/tool_delta/reasoning_meta → final`，同一 call_id 最多一个 final，final 使用整轮五段响应且不重复正文。Provider 诊断仍使用原有 `start → ... → final` 单次调用协议。

调用路径固定为：ModelService 准入与占位 → Runtime 能力检查与期限 → ProviderAdapter（LiteLLM 生成 / System One 决策）→ 安全终态与调用元记录。每次调用一次上游尝试、无自动重试与降级；取消向连接与任务传播，资源清理由 Runtime 统一收尾。

## 配置

| 配置                                | 用途                                        | 缺失行为                     |
| ----------------------------------- | ------------------------------------------- | ---------------------------- |
| `DATABASE_URL`                      | Agent 运行库                                | 依赖数据库的操作拒绝         |
| `COMPASS_AGENT_SERVICE_TOKEN`       | BFF → Agent 服务身份                        | 模型接口返回 503             |
| `COMPASS_MODEL_ENCRYPTION_KEY`      | 凭据与续接密文保护（Fernet 信封）           | 不能保存或调用模型           |
| `COMPASS_MODEL_ALLOWED_ORIGINS`     | 精确 HTTPS origin 白名单，逗号分隔          | 默认 DeepSeek 与 JEV 官方    |
| `COMPASS_MODEL_ALLOW_LOCAL_HTTP`    | 本地回环 HTTP 诊断                          | 默认允许 loopback，仅本地    |
| `COMPASS_ROUTING_MODE`              | `jev_with_flash` / `flash_only`             | 默认 `jev_with_flash`        |
| `COMPASS_ROUTING_JEV_PROVIDER_ID`   | JEV 两题分类角色                            | 不可用时 Flash 兜底          |
| `COMPASS_ROUTING_FLASH_PROVIDER_ID` | Flash 分类兜底、auto L1/待定与 SC-39 主模型 | 明确 `route_binding_missing` |
| `COMPASS_ROUTING_PRO_PROVIDER_ID`   | auto L2/L3 主模型角色                       | 明确 `route_binding_missing` |
| `COMPASS_DEMO_ENABLED`              | 演示接口开关                                | 返回 404                     |
| `LITELLM_LOCAL_MODEL_COST_MAP`      | SDK 离线目录                                | 由部署环境在导入 SDK 前设置  |

模型执行需要 `httpx`、`cryptography`、`jsonschema` 与 `litellm-core 1.106.6`。当前开发环境已安装这些包，但离线环境无法刷新 `uv.lock`，因此 `pyproject.toml` 暂未声明。在有网络的环境执行：

```bash
uv add --package compass-agent httpx cryptography jsonschema litellm-core==1.106.6
```

并提交锁文件后作为正式运行依赖。运行时导入为按需加载：缺少 `cryptography` 时保存/调用返回安全失败，缺少 `jsonschema` 时结构或工具校验返回安全失败，缺少 `litellm-core` 时生成调用返回安全失败；管理接口、可用模型与契约导出仍可用。相关测试在缺少依赖时通过 `pytest.importorskip` 明确跳过，跳过不计作通过。

## 数据库

迁移唯一源为 [migrations/versions](migrations/versions/)：0001 演示样本不可改写，0002 新增 `model_provider`、`model_change`、`model_call`。运行、迁移和完整/增量 SQL 的说明见 [agent/main](db/main/README.md)。

```bash
pnpm db:migrate -F agent
pnpm db:sql -F agent
pnpm db:sql:check -F agent
pnpm db:seed -F agent
```

模型表只保存配置、版本、调用状态、耗时、用量与安全错误；不保存 Prompt、回复正文、决策正文、工具结果或原始 Thinking。凭据与连接参数保存在 Fernet 信封中，读取接口只返回“是否已配置”。应用启动、模块导入与契约导出不会隐式迁移。

## 测试

```bash
pnpm lint -F agent
pnpm typecheck -F agent
pnpm test -F agent
```

单元测试使用内存 SQLite 与注入的 Fake Adapter，覆盖 Runtime 生命周期、取消、单次出站、能力拒绝、配置幂等与版本冲突、密钥往返、SSE 事件与调用元记录。路由测试另覆盖 JEV/Flash 分类、字典特征、fixed/auto/SC-39 选模、分类兜底与派生调用 ID。

PostgreSQL 集成测试会先删除并重建目标库的 ORM 表，必须指向专用测试库，不能使用 `compass` 运行库。Compose 的 PostgreSQL 已暴露在 `127.0.0.1:55432`，可复用同一容器创建测试库：

```bash
docker exec compass-postgres-1 createdb -U compass compass_agent_test
COMPASS_TEST_DATABASE_URL=postgresql+psycopg://compass:compass_local_only@127.0.0.1:55432/compass_agent_test \
  uv run --locked pytest tests
```

未设置 `COMPASS_TEST_DATABASE_URL` 时该项明确跳过；跳过不能计作通过。

真实模型调用需要有效账号、API Key、端点与额度，按供应商独立验证；stub 或单元测试成功不代表真实联调完成。账户、租户、权限与通用配置由 Web / BFF 拥有；Agent 只校验委托上下文与自身资源。

完整零售业务数据由独立的 [tools/data-seed](../../tools/data-seed/README.md) 生成、校验和发布。普通分析 Runner、Dispatcher、Sandbox 控制与 Memory Tools 按后续切片接入；当前提供健康检查、演示查询、样本初始化与 LLM Runtime。
