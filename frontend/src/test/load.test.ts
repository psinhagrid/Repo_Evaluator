import { describe, expect, it } from "vitest";

import { getDemoData } from "@/lib/demo";
import { classify } from "@/lib/load";

describe("classify", () => {
  const demo = getDemoData();
  const cohort = Object.values(demo.cohorts)[0];
  const report = Object.values(demo.reports)[0];
  const calibration = Object.values(demo.calibrations)[0];

  it("recognises each result file by its shape", () => {
    expect(classify(cohort)).toBe("cohort");
    expect(classify(report)).toBe("report");
    expect(classify(calibration)).toBe("calibration");
    expect(classify({ status: "not_graded", reason: "empty archive" })).toBe("report");
  });

  it("rejects anything else", () => {
    expect(classify(null)).toBeNull();
    expect(classify([1, 2])).toBeNull();
    expect(classify({ hello: "world" })).toBeNull();
  });
});
