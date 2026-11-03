import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import { getDemoData } from "@/lib/demo";
import { loadDemoData } from "@/lib/store";
import { routeTree } from "@/routeTree.gen";

// Render each page through the real router with the demo dataset loaded, and check
// that its key content appears and the error boundary does not.
const demo = getDemoData();
const cohortKey = Object.keys(demo.cohorts)[0] as string;
const reportSha = Object.keys(demo.reports)[0] as string;
const calibrationKey = Object.keys(demo.calibrations)[0] as string;

// Rendered with react-dom directly (no @testing-library/dom dependency).
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | null = null;
const text = () => document.body.textContent ?? "";

async function renderAt(path: string) {
  root?.unmount();
  document.body.innerHTML = "";
  const container = document.createElement("div");
  document.body.appendChild(container);
  const router = createRouter({
    routeTree,
    context: { queryClient: new QueryClient() },
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  root = createRoot(container);
  await act(async () => {
    root?.render(<RouterProvider router={router} />);
    await router.load();
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  return router;
}

describe("pages render with data", () => {
  beforeAll(() => {
    loadDemoData(demo);
  });
  afterEach(() => {
    root?.unmount();
    root = null;
  });

  it("cohort dashboard", async () => {
    await renderAt(`/cohorts/${encodeURIComponent(cohortKey)}`);
    expect(text()).toContain("Ranking");
    expect(text()).toContain("Criterion averages");
    expect(text()).not.toContain("This page didn't load");
  });

  it("report, instructor and student views", async () => {
    const report = demo.reports[reportSha];
    await renderAt(`/reports/${reportSha}`);
    expect(text()).toContain("Feedback by criterion");
    if (report?.status === "graded") {
      // Instructor-only details are present...
      expect(text()).toMatch(/tool calls/);
    }
    await renderAt(`/reports/${reportSha}?view=student`);
    expect(text()).toContain("Feedback by criterion");
    // ...and hidden from students.
    expect(text()).not.toMatch(/tool calls/);
    expect(text()).not.toMatch(/cost of this run/);
  });

  it("calibration", async () => {
    await renderAt(`/calibration/${encodeURIComponent(calibrationKey)}`);
    expect(text()).toContain("Agreement with human graders");
  });

  it("lists and empty states", async () => {
    await renderAt("/reports");
    expect(text()).toContain("Submission reports");
    await renderAt("/cohorts/not-a-cohort");
    expect(text()).toContain("This cohort is not loaded");
  });
});

// Real output of the Python grader (scripted-model cohort run): the frontend must render
// exactly what the backend writes, not just Lovable's demo data.
import realCohort from "./fixtures/cohort.json";
import realReport from "./fixtures/report.json";
import { addCohort, addReports } from "@/lib/store";
import type { Cohort, Report } from "@/lib/types";

describe("pages render real grader output", () => {
  beforeAll(() => {
    addCohort(realCohort as unknown as Cohort);
    addReports([realReport as unknown as Report]);
  });
  afterEach(() => {
    root?.unmount();
    root = null;
  });

  it("cohort.json from evaluate cohort", async () => {
    await renderAt(
      `/cohorts/${encodeURIComponent((realCohort as { assignment: string }).assignment)}`,
    );
    expect(text()).toContain("Ranking");
    expect(text()).toContain("Revision required");
    expect(text()).not.toContain("This page didn't load");
  });

  it("report.json from evaluate grade", async () => {
    const r = realReport as unknown as Report;
    await renderAt(`/reports/${r.submission_sha256}`);
    expect(text()).toContain("Feedback by criterion");
    expect(text()).toContain("Cap review");
    expect(text()).toContain(String(r.score.final));
    expect(text()).not.toContain("This page didn't load");
  });
});
