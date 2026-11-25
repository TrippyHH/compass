# 网络契约

唯一源为 `apps/agent/src/compass_agent/schemas.py` 与 FastAPI 路由。`openapi.json` 与各 JSON Schema 均由根 `pnpm contracts` 生成，禁止手改。Web 消费副本同时生成到 `apps/web/src/generated`，通过 `pnpm contracts:check` 验证无漂移。

当前契约覆盖健康检查、合成数据概览，以及模型目录、Provider 管理、可用模型、能力测试、Chat 调用、调用记录与公开流事件：

- `model-provider-page.schema.json`、`model-provider-view.schema.json`、`model-saved-provider.schema.json`
- `model-catalog-page.schema.json`、`model-available.schema.json`、`model-change-page.schema.json`
- `model-call-response.schema.json`、`model-call-record.schema.json`
- `model-intent.schema.json`、`model-routing-features.schema.json`、`model-route.schema.json`、`model-routing-metrics.schema.json`、`model-routed-call-response.schema.json`：统一 Chat 的两级分类、字典特征、选模与分项消耗
- `model-event.schema.json`：诊断 SSE `start / text_delta / tool_delta / reasoning_meta / final` 判别联合
- `model-routed-event.schema.json`：统一 Chat SSE `start / classification_status / route_selected / text_delta / tool_delta / reasoning_meta / final` 判别联合

Web BFF 使用生成的 JSON Schema 通过 Ajv 2020 做运行时校验，TypeScript 类型由 openapi-typescript 生成；`apps/web/src/generated/openapi.json` 是随类型一同发布的只读参考副本。生成过程串行运行 Python 导出与 openapi-typescript，无数据库与网络服务依赖；模型调用的真实凭据不参与契约生成。
