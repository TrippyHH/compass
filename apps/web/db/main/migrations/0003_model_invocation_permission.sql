-- Up Migration
-- Replace both permission_keys CHECK constraints on tenant_member and workspace_member.
-- New grantable action: models.invoke. Previous upper bounds were 22 (tenant) and 16 (workspace).
DO $$
DECLARE
  target text;
  constraint_name text;
BEGIN
  FOREACH target IN ARRAY ARRAY['web.tenant_member', 'web.workspace_member'] LOOP
    FOR constraint_name IN
      SELECT conname FROM pg_constraint
      WHERE conrelid = target::regclass AND contype = 'c'
        AND (
          pg_get_constraintdef(oid) LIKE '%permission_keys <@%'
          OR pg_get_constraintdef(oid) LIKE '%cardinality(permission_keys) <=%'
        )
    LOOP
      EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', target, constraint_name);
    END LOOP;
  END LOOP;
END $$;

ALTER TABLE web.tenant_member
  ADD CONSTRAINT tenant_member_permission_count CHECK (
    cardinality(permission_keys) <= 23 AND (
      cardinality(permission_keys) = 0 OR
      (array_ndims(permission_keys) = 1 AND array_position(permission_keys, NULL) IS NULL)
    )
  ),
  ADD CONSTRAINT tenant_member_permission_allowed CHECK (permission_keys <@ ARRAY[
    'module.analysis','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','module.admin.organization',
    'workspaces.read','workspaces.write','members.read','settings.read','settings.write','roles.read',
    'overview.read','audit.read','audit.export','models.invoke'
  ]::text[]);

ALTER TABLE web.workspace_member
  ADD CONSTRAINT workspace_member_permission_count CHECK (
    cardinality(permission_keys) <= 17 AND (
      cardinality(permission_keys) = 0 OR
      (array_ndims(permission_keys) = 1 AND array_position(permission_keys, NULL) IS NULL)
    )
  ),
  ADD CONSTRAINT workspace_member_permission_allowed CHECK (permission_keys <@ ARRAY[
    'module.analysis','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','workspaces.read',
    'overview.read','audit.read','audit.export','models.invoke'
  ]::text[]);

-- Down Migration
-- Downgrading requires removing models.invoke grants first; the old constraints cannot store them.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM web.tenant_member WHERE 'models.invoke' = ANY(permission_keys))
     OR EXISTS (SELECT 1 FROM web.workspace_member WHERE 'models.invoke' = ANY(permission_keys)) THEN
    RAISE EXCEPTION 'Remove models.invoke grants before downgrading';
  END IF;
END $$;

DO $$
DECLARE
  target text;
  constraint_name text;
BEGIN
  FOREACH target IN ARRAY ARRAY['web.tenant_member', 'web.workspace_member'] LOOP
    FOR constraint_name IN
      SELECT conname FROM pg_constraint
      WHERE conrelid = target::regclass AND contype = 'c'
        AND (
          pg_get_constraintdef(oid) LIKE '%permission_keys <@%'
          OR pg_get_constraintdef(oid) LIKE '%cardinality(permission_keys) <=%'
        )
    LOOP
      EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', target, constraint_name);
    END LOOP;
  END LOOP;
END $$;

ALTER TABLE web.tenant_member
  ADD CONSTRAINT tenant_member_permission_count CHECK (
    cardinality(permission_keys) <= 22 AND (
      cardinality(permission_keys) = 0 OR
      (array_ndims(permission_keys) = 1 AND array_position(permission_keys, NULL) IS NULL)
    )
  ),
  ADD CONSTRAINT tenant_member_permission_allowed CHECK (permission_keys <@ ARRAY[
    'module.analysis','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','module.admin.organization',
    'workspaces.read','workspaces.write','members.read','settings.read','settings.write','roles.read',
    'overview.read','audit.read','audit.export'
  ]::text[]);

ALTER TABLE web.workspace_member
  ADD CONSTRAINT workspace_member_permission_count CHECK (
    cardinality(permission_keys) <= 16 AND (
      cardinality(permission_keys) = 0 OR
      (array_ndims(permission_keys) = 1 AND array_position(permission_keys, NULL) IS NULL)
    )
  ),
  ADD CONSTRAINT workspace_member_permission_allowed CHECK (permission_keys <@ ARRAY[
    'module.analysis','module.reports','module.plugins','module.automations','module.boards',
    'module.admin.overview','module.admin.data','module.admin.capabilities','module.admin.runs',
    'module.admin.quality','module.admin.security','module.admin.memory','workspaces.read',
    'overview.read','audit.read','audit.export'
  ]::text[]);