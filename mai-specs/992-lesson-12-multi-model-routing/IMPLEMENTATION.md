# 992 实施记录

> 日期：2026-11-24｜Profile：standard｜状态：实现、自动检查与真实供应商联调完成；浏览器视觉验收待环境
> [BRIEF](BRIEF.md) · [PRD](../../../agent-engineering-course-kit/chapters/chapter_04_llm_runtime/lesson_12_multi_model_routing/learning-materials/lesson-12-learning-materials/PRD.md) · [技术方案](../../../agent-engineering-course-kit/chapters/chapter_04_llm_runtime/lesson_12_multi_model_routing/learning-materials/lesson-12-learning-materials/TECH_DESIGN.md)

## 交付范围

| 区域                        | 内容                                                                                                                                                                                       |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Agent 路由                  | `routing/config.py`、`dictionary.py`、`intent.py`、`prompts.py`、`service.py`、`scenes.json` 与模块 README；40 场景、10 个一级分类、只读摘要校验                           |
| DTO 与事件                  | `MainModelSelection`、可空 `provider_id` 的 `ChatCallInput`、`Intent`、`RoutingFeatures`、`ModelRoute`、`RoutingMetrics`、`RoutedCallResponse`、分类/选模/最终事件，`RoutedModelEvent` |
| 统一入口                    | `POST /v1/models/calls` 完成分类→查表→选模→一次推理；JSON 五段响应与 SSE `start → classification_status/route_selected → text_delta → final`；诊断接口保持原协议 |
| 模型服务                    | `RoutingGrant` 内部授权、`prepare`/`run`、父/阶段 `deadline_at`、角色 Provider 解析、分类调用 ID `uuid5(call_id, stage)`、能力错误码 |
| Web                         | BFF 归一化 `provider_id`/固定与自动选择、routed JSON/SSE 运行时校验、Chat「系统优化」入口、分类/特征/实际模型/选择原因/分项消耗展示与停止 |
| 字典导出                    | `apps/agent/scripts/export_routing_dictionary.py` 支持从工作簿重建并校验运行时 JSON；不导出问题金标或期望模型 |
| 契约                        | 新增 6 份 JSON Schema 并同步 OpenAPI/TypeScript；`scripts/contracts.mjs` 与 `scripts/export-contracts.py` 纳入生成链 |

### 关键行为

1. JEV 一次两题调用，校验类型、候选、父子归属、默认概率与间隔门槛；不通过时仅一次 Flash 分类兜底，仍使用同一两字段严格 Schema。
2. 分类结果只决定 `scene_id`；`task_type/difficulty/response_mode/required_capabilities` 全部来自当前字典快照，字典版本为规范内容 SHA-256。
3. SC-39 优先使用 Flash 角色与独立范围引导提示词，返回 `reason=off_topic_flash`；业务 fixed 使用用户指定且启用、Chat 可见的模型；auto L2/L3 → Pro，其余 → Flash。
4. 字典必需能力与完整输入/输出能力在发出 `route_selected` 前预检；不满足时 `capability_not_supported`，不隐式换模型。
5. 分类 Provider 使用进程内 `RoutingGrant`（actor/scope/物理 call_id/Provider/阶段），仅要求 `enabled`；浏览器 DTO 没有 grant 字段。
6. 两路分类失败时 `intent/routing/result` 为空、主模型推理零次；JEV 未知消耗保留在 `metrics.classification_calls`，未知 token 保持 null 且 `usage_complete=false`。
7. 父时限 180 秒不因阶段重置；分类子预算 30 秒、JEV 8 秒、Flash 20 秒。每次物理调用仍为现有 Runtime 的单次尝试，不新增重试、降级或恢复器。

## 与设计/基线的差异

1. 当前仓库基线是第 11 课的单次 Runtime，未包含第 13 课的第二次物理尝试。实现保持每次物理调用一次尝试，满足第 12 课最多三次物理调用的要求；`LogicalPolicy` 未引入。
2. 学习资料未随附场景工作簿。`scenes.json` 按 PRD §2.1 的 40 场景目录与特征边界建立，并由 `export_routing_dictionary.py --check` 校验摘要、数量和引用；取得工作簿后可用 `--workbook` 重建。88 道问题金标不在仓库中，也未泄漏进运行时目录。
3. 技术方案示例中的 `ModelService.prepare/run` 与 `RoutingGrant` 已按现有 `ModelService` 结构落地；`repository.routing_provider` 校验角色类型、存在与启用，不新增策略表。
4. SC-39 与分类兜底共用 `COMPASS_ROUTING_FLASH_PROVIDER_ID` 角色，但提示词、生成参数与 `reason` 独立；范围引导要求该角色 `chat_visible`。

## 已执行验证

| 检查                                                       | 结果                                                                                                              |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `uv run --locked python apps/agent/scripts/export_routing_dictionary.py --check` | `version=sha256:e5bd5357...bcae scenes=40 categories=10`，字典一致                                    |
| 工作簿导出回归                                             | 测试用标准库构造含 40 行场景的 xlsx，`read_workbook` 导出后的 categories、scenes 与 version 和运行时 JSON 完全一致 |
| `pnpm lint -F agent`                                       | Ruff 检查与格式检查通过（52 files）                                                                               |
| `pnpm typecheck -F agent`                                  | Pyright 0 errors, 0 warnings                                                                                      |
| `COMPASS_TEST_DATABASE_URL=... pnpm test -F agent` | 126 passed，0 skipped；Turbo `test.env` 已纳入测试库变量，避免缓存复用跳过结果；新增 `test_routing.py` 57 项、工作簿导出回归 1 项与决策概率舍入回归 1 项 |
| PostgreSQL 集成测试 | 复用 Compose PostgreSQL 17 容器创建专用 `compass_agent_test` 库；真实 DDL、决策模型隐藏约束、调用元数据与 `usage` 写入均通过 |
| `pnpm lint -F web`                                         | ESLint 0 warning                                                                                                  |
| `pnpm typecheck -F web`                                    | `next typegen` + `tsc --noEmit` 通过                                                                           |
| `pnpm test -F web`                                         | 12 个文件、51 项通过，含 routed 契约、BFF 中继、事件顺序、fixed 选模校验与标签覆盖                                                |
| `pnpm contracts:check`                                     | 生成契约与 Python 源一致                                                                                          |
| `pnpm build -F agent`                                      | wheel/sdist 构建成功；wheel 内包含 `compass_agent/routing/scenes.json`                                          |
| `pnpm build -F web`                                        | Next.js 16.4.1 生产构建与 TypeScript 通过                                                                         |
| `git diff --check`                                         | 通过（本次变更无空白错误）                                                                                        |

### 真实供应商联调（2026-11-24）

本机 PostgreSQL 17 与三个已启用 Provider 上启动 Agent（127.0.0.1:8000），通过真实 `POST /v1/models/calls` 验证：

| 用例 | 分类结果 | 字典特征 | 实际主模型 | 分类来源 | 结果 |
| --- | --- | --- | --- | --- | --- |
| auto 简单指标对比 | JEV：business_channel / SC-02 | L1 | deepseek-flash | JEV | completed；JEV 252ms，推理 86/179 tokens |
| auto 多维诊断 | JEV：business_channel / SC-03 | L3 | deepseek-v4-pro | JEV | completed；JEV 275ms，推理 135/719 tokens |
| 固定 Pro 简单任务 | JEV：business_channel / SC-02 | L1 | deepseek-v4-pro | JEV | completed；fixed 使用用户指定 Pro，特征仍为 L1 |
| SC-39 闲聊（固定 Pro） | JEV 一级置信不足 0.30/间隔 0.03 → Flash | refuse | deepseek-flash | Flash | completed；Flash 返回 SC-39 并使用 `off_topic_flash` 独立提示词 |

数据库核对：每轮主推理记录使用整轮 `call_id`；JEV 与 Flash 分类记录分别等于 `uuid5(call_id, "intent_jev")` / `uuid5(call_id, "intent_flash")`；JEV 的 `upstream_request_id`、输入 token、耗时和错误状态已落库。真实决策 Provider 返回两位小数概率（合计 0.99），因此适配器增加舍入归一化容忍并补充回归测试，避免有效响应被误判为 `provider_protocol_error`。

本地 `.env` 已写入 JEV/Flash/Pro 角色 Provider ID；角色绑定不是密钥，Provider 凭据仍由模型管理保存。

### 浏览器端到端（2026-11-24）

- 1440px，系统优化提交简单指标对比：页面只发出 1 次 `POST /api/web/v1/models/calls`；展示“经营与渠道 / 趋势比较”、任务类型 `metric_comparison`、难度 L1、实际模型 deepseek-flash；展开“查看选择原因”可见 JEV 主路、字典/契约版本、分类输入 1359 tokens/415ms、推理 86/194 tokens/2008ms 与总耗时 2470ms。
- 375px 同一回复无横向溢出，选择、发送、停止、详情均可达；截图见 `work/chat-routing-1440.png` 与 `work/chat-routing-375.png`。
- 固定 Pro 提交“讲个笑话吧”：选择值为 Pro Provider，命中 SC-39 后实际调用 deepseek-flash，`reason=off_topic_flash`，详情显示 Flash 兜底原因 `jev_unavailable_or_uncertain`、两次分类调用与推理分项消耗；回答温和说明经营分析范围并邀请业务数据问题。截图见 `work/chat-sc39-1440.png`。
- 浏览器主流程请求均成功；控制台记录一条非阻塞 404 资源告警，未影响分类、选模与回答。

## 未验证项与限制

1. 真实联调使用已配置的 `aliyun/decision-model-preview` 决策 Provider、`deepseek-flash` 与 `deepseek-v4-pro`；TypeSafe 官方 JEV 账号未单独验证，正式留出集准确率、门槛校准与性能收益不在本课准入范围。
2. 浏览器验收覆盖 1440px 与 375px 的自动分类、详情展开和固定 Pro 的 SC-39 路径；768px 中间断点与完整键盘 Tab 顺序未逐项截图验收；控制台有一条非阻塞 404 资源告警，未影响主流程。
3. `pnpm format:check` 在全仓检查时被既有 `.claude/settings.json` 非 JSON 软链接内容阻断，并列出既有 `AGENTS.md`、`apps/agent/scripts/db-sql.mjs`、`apps/web/tests/session-suspend.test.ts` 格式告警；本次变更文件已单独 Prettier/Ruff 格式化。`pnpm test:tooling` 仍有既有 Windows 环境失败（同一 `.claude/settings.json` 与临时目录 workspace 发现），未定位到本次 `turbo.json` 测试环境变量改动引入的新失败。
4. 第 13 课可靠性策略（第二次物理尝试、恢复器等）不在本课实现范围；当前每次物理调用仍为单次尝试。
