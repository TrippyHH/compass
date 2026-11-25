# 992 第 12 课 两级意图分类与模型路由

> Feature 992｜Profile standard｜状态：流程实现、真实 JEV/Flash/Pro 联调、PostgreSQL 集成测试与 1440/375px 浏览器验收完成；768px 与键盘逐项验收待补充
> [PRD](../../../agent-engineering-course-kit/chapters/chapter_04_llm_runtime/lesson_12_multi_model_routing/learning-materials/lesson-12-learning-materials/PRD.md) · [技术方案](../../../agent-engineering-course-kit/chapters/chapter_04_llm_runtime/lesson_12_multi_model_routing/learning-materials/lesson-12-learning-materials/TECH_DESIGN.md) · [实施记录](IMPLEMENTATION.md)

## 目标

在已有 `POST /v1/models/calls` 前增加薄路由：JEV 两级分类、一次 Flash 分类兜底、只读场景字典、fixed/auto/SC-39 确定性选模，然后复用现有 Runtime 执行一次主模型推理，并按 `intent/features/routing/result/metrics` 返回。

## 范围

- 分类对象严格为 `category_id` 与 `scene_id` 两个字段；特征只能来自字典。
- 业务 fixed 使用用户指定主模型，auto L2/L3 使用 Pro、其余使用 Flash；SC-39 优先使用 Flash 范围引导。
- 每轮最多一次 JEV、一次 Flash 分类、一次主模型推理；两路分类失败时推理为零次。
- 保留 Provider 管理与诊断；不新增路由管理 API、策略表、执行器、Skill/subAgent/workflow 字段或数据库迁移。
- Chat 同时接受旧 `provider_id` 与新 `main_model`，BFF 归一化为 fixed/auto 后只发一次请求。

## 验收

流程验收由受控 JEV/Flash/生成适配器覆盖：合法 JEV 直接采用、低置信/非法/不可用兜底、两级失败零推理、L1/L3、fixed/auto、SC-39、能力不足拒绝、派生调用 ID、SSE 顺序与字典确定性。真实分类效果、准确率、门槛校准与性能收益不在本课范围。
