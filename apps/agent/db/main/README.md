# Agent 主数据库

Owner 为 `apps/agent`，key 为 `agent/main`；PostgreSQL 17，应用 schema 为默认 `public`，版本表为 `alembic_version`。迁移唯一源为 [migrations/versions](../../migrations/versions/)；0001 保留演示样本，0002 新增模型接入配置、配置变更与调用元记录三张表。

运行连接使用 `DATABASE_URL`。迁移由独立受控任务执行，应用启动、模块导入与契约导出不会隐式迁移。

从 Compass 根执行：

```bash
pnpm db:migrate -F agent
pnpm db:sql -F agent
pnpm db:sql:check -F agent
```

`db:sql` 以 Alembic 离线模式重放迁移链，生成完整初始化脚本与 `0001 → 0002` 增量脚本；[交付清单](sql/manifest.json)登记 owner、Feature、源文件 SHA-256、目标版本、前提、事务边界、数据影响与恢复方式。生成物禁止手改，`--check` 用于发现漂移。

- 完整脚本：[bootstrap.sql](sql/bootstrap.sql)，目标 `0002_model_runtime`。
- 增量脚本：[0001_demo_fixture__0002_model_runtime.sql](sql/upgrades/0001_demo_fixture__0002_model_runtime.sql)。
- 迁移事务：PostgreSQL DDL 事务，整份脚本一次提交；失败不保留部分表。
- 恢复：使用 `pg_dump` 备份到受控位置，向新供应的同所有权数据库恢复，核对 `alembic_version`、业务数据与运行权限后切换连接并重启 Agent；保留旧库供复核。不提供 down 或 DROP 作为数据恢复方式。

模型调用表只保存状态、模型、版本、耗时、用量与安全错误，不保存提示词、回复正文、决策正文或原始 Thinking。运行账号只获得业务读写权限，迁移账号独立。
