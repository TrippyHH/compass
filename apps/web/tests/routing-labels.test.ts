import { describe, expect, it } from "vitest";
import { CATEGORY_LABELS, SCENE_LABELS, categoryLabel, sceneLabel } from "@/lib/routing-labels";

describe("routing display labels", () => {
  it("covers every dictionary category and scene", () => {
    expect(Object.keys(CATEGORY_LABELS)).toHaveLength(10);
    expect(Object.keys(SCENE_LABELS)).toHaveLength(40);
    expect(SCENE_LABELS["SC-39"]).toBe("范围外");
    expect(CATEGORY_LABELS.input_boundary).toBe("输入与执行边界");
  });

  it("keeps an unknown identifier visible instead of hiding it", () => {
    expect(categoryLabel("new_category")).toBe("new_category");
    expect(sceneLabel("SC-99")).toBe("SC-99");
    expect(categoryLabel(null)).toBe("");
  });
});
