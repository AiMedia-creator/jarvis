import { describe, expect, it } from "vitest";
import { pageSizeForLayout, visibleCameras } from "./components/CCTVGrid";
import type { Camera } from "./types";

const cameras: Camera[] = Array.from({ length: 8 }, (_, index) => ({
  id: `cam-${index}`,
  name: `Camera ${index}`,
  city_id: "demo",
  kind: "mock",
}));

describe("CCTV grid pagination", () => {
  it("shows exactly four feeds in 2x2 pages", () => {
    expect(pageSizeForLayout("2x2")).toBe(4);
    expect(visibleCameras(cameras, "2x2", 0)).toHaveLength(4);
    expect(
      visibleCameras(cameras, "2x2", 1).map((camera) => camera.id),
    ).toEqual(["cam-4", "cam-5", "cam-6", "cam-7"]);
  });
  it("does not add duplicate placeholders to a 3x3 page with eight cameras", () => {
    expect(pageSizeForLayout("3x3")).toBe(9);
    expect(visibleCameras(cameras, "3x3", 0)).toHaveLength(8);
    expect(
      new Set(visibleCameras(cameras, "3x3", 0).map((camera) => camera.id))
        .size,
    ).toBe(8);
  });
});
