# 两级意图分类与模型路由

本包是第 12 课的薄应用服务：模型只识别两个标签，特征完全由只读字典查出，确定性契约再选择本次主模型。它不包含 Harness、Skill/subAgent/workflow 分派、任务拆分、工具或 SQL 执行。

| 文件            | 责任                                                                 |
| --------------- | -------------------------------------------------------------------- |
| `config.py`     | 代码配置的路由版本、JEV/Flash 默认门槛、分类/阶段/父时限             |
| `dictionary.py` | 只读 `scenes.json`、摘要一致性校验、父子归属校验与特征查表           |
| `intent.py`     | JEV 两题输入归一化、Flash 严格两字段 Schema、一次 Flash 兜底         |
| `prompts.py`    | 范围外与 business/clarify/refuse/handoff 提示词                      |
| `service.py`    | 入口编排：JEV → Flash 兜底 → 字典 → fixed/auto/SC-39 选模 → 一次推理 |
| `scenes.json`   | 由场景工作簿派生的运行时目录，不含问题金标、期望模型或评测备注       |

## 契约

- 分类对象严格为 `{category_id, scene_id}`；未知候选、父子不一致、低置信或额外字段都会拒绝或触发一次 Flash 兜底。
- 特征来自 `scene_id` 的字典行，模型不能生成 `difficulty`、`task_type`、`response_mode` 或能力要求。
- 选模顺序：SC-39 → Flash 专用范围引导；业务 fixed → 用户指定模型；auto L2/L3 → Pro；其余 auto → Flash。
- fixed 仍校验启用、Chat 可见性、字典必需能力与完整输入/输出契约；不满足时明确拒绝，不隐式换模型。
- 每次主模型推理只使用整轮 `call_id`；分类物理 ID 为 `uuid5(call_id, stage)`，因此同一阶段重复出站会被调用表唯一键拒绝。
- 分类阶段使用 `RoutingGrant`（仅进程内构造），允许已启用的决策/Flash 分类 Provider 在 `purpose=chat` 下接收内部输入，但浏览器 DTO 没有 grant 字段。

## 配置

```text
COMPASS_ROUTING_MODE=jev_with_flash | flash_only
COMPASS_ROUTING_JEV_PROVIDER_ID=<decision provider uuid>
COMPASS_ROUTING_FLASH_PROVIDER_ID=<llm provider uuid>
COMPASS_ROUTING_PRO_PROVIDER_ID=<llm provider uuid>
```

角色只引用已有 Provider，凭据仍由模型管理保存。角色缺失、停用或类型不匹配时返回 `route_binding_missing`；JEV 不可用可在父时限内兜底 Flash，Flash 分类失败则主模型推理为零次。

## 字典导出

```bash
uv run --locked python scripts/export_routing_dictionary.py --check
uv run --locked python scripts/export_routing_dictionary.py --workbook <场景工作簿.xlsx>
```

工作簿是编辑源，脚本只导出场景目录与默认特征；问题金标和期望模型留在测试材料中。`scenes.json` 的 `version` 是类别与场景规范内容的 SHA-256，加载时重新计算并校验；Agent 启动时预载字典，损坏或不一致会在部署阶段失败，而不是等到用户请求。

## 测试

`pnpm test -F agent` 中的 `tests/test_routing.py` 使用受控 JEV/Flash/生成适配器覆盖：JEV 直接采用、低置信兜底、非法/额外字段拒绝、双路失败零推理、L1/L3 与 fixed/auto 选模、SC-39 专用提示词、能力不足拒绝、派生调用 ID、SSE 顺序与字典确定性。真实 JEV 效果、准确率与门槛校准不在本课验收范围。
