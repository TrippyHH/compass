# LLM Runtime 内核

`llm/` 管一次模型调用的执行边界，不导入 `models/`、SQLAlchemy、FastAPI、Repository 或 SecretStore。应用层实现 `ContinuationCodec` 并通过组合注入。

| 模块                    | 责任                                                                      |
| ----------------------- | ------------------------------------------------------------------------- |
| `contracts.py`          | 内部执行类型、ProviderAdapter Protocol、RuntimeResult 与 RuntimeCancelled |
| `policy.py`             | 单次尝试策略、180 秒预算与输出上限；纯值                                  |
| `errors.py`             | 安全错误码；无 HTTP 状态与供应商原文                                      |
| `transport.py`          | 请求私有资源、单次出站 guard、有界字节读取与 SSE 帧解析                   |
| `runtime.py`            | 唯一生命周期协调者：绑定检查、能力检查、期限、取消、清理与终态            |
| `capabilities.py`       | 协议/型号能力与参数组合校验；Chat 准入                                    |
| `catalog.py`            | 锁定 SDK 的离线目录与 System One 决策预设                                 |
| `litellm_chat.py`       | 生成适配器：请求映射、分片归一、工具聚合、结构校验、续接封装              |
| `systemone_decision.py` | 决策适配器：state/questions、Bearer 认证、答案语义校验                    |
| `continuation.py`       | 可见历史摘要与私有 assistant 字段恢复                                     |

不变量：

- 每个 call_id 只允许一次物理出站，不自动重试、不自动降级；`CallPolicy` 在构造时拒绝多尝试与 fallback。
- 取消继续向调用方传播；`RuntimeCancelled.result` 携带已记录的终态。清理最多 2 秒，不因连接关闭失败改变已有结果。
- Adapter 在等待网络前登记 client/response，先更新规范化部分输出再向公开 sink 发布增量；原始 Thinking 只留在请求内存与加密续接载荷。
- 正常 `completed` 要求正常 EOF、有效 finish_reason、完整工具参数与结构校验；断流、截断、拒答、空输出分别返回固定错误码。未知用量保持 null。
- `llm/` 只通过类型化请求与结果与 `models/` 交接；HTTP 与数据库职责留在应用层。

真实供应商调用需要有效凭据与额度，本课不包含供应商联调结果登记。
