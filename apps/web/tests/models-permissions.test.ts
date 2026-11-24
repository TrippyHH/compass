import { describe, expect, it } from "vitest";
import {
  DEFAULT_TEMPLATES,
  PERMISSION_CATALOG,
  PERMISSION_CATALOG_VERSION,
  PERMISSION_IDS,
} from "@/server/modules/access/domain/permissions";

describe("models.invoke permission", () => {
  it("is a grantable tenant and workspace action", () => {
    expect(PERMISSION_IDS).toContain("models.invoke");
    const entry = PERMISSION_CATALOG.find((item) => item.id === "models.invoke");
    expect(entry).toBeDefined();
    expect(entry?.personalGrantable).toBe(true);
    expect(entry?.scopes).toEqual(["tenant", "workspace"]);
  });

  it("is part of the analyst template but not auto-granted to viewers", () => {
    expect(DEFAULT_TEMPLATES.analyst).toContain("models.invoke");
    expect(DEFAULT_TEMPLATES.viewer).not.toContain("models.invoke");
  });

  it("uses catalog version 2 so existing pages revalidate", () => {
    expect(PERMISSION_CATALOG_VERSION).toBe("2");
    expect(PERMISSION_IDS.filter((id) => id === "models.invoke")).toHaveLength(1);
  });
});
