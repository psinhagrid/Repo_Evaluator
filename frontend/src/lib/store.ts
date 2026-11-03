import { useSyncExternalStore } from "react";
import type { Calibration, Cohort, Report } from "./types";

export type AppData = {
  cohorts: Record<string, Cohort>;
  calibrations: Record<string, Calibration>;
  reports: Record<string, Report>;
  files: string[];
  demo: boolean;
  /** Set when data came from the grader API: maps submission sha256 -> API report id (for file views). */
  apiReportIds: Record<string, string>;
};

export const EMPTY_DATA: AppData = {
  cohorts: {},
  calibrations: {},
  reports: {},
  files: [],
  demo: false,
  apiReportIds: {},
};

const STORAGE_KEY = "grader-console:v1";

let state: AppData = EMPTY_DATA;
let hydrated = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAppData(): AppData {
  return state;
}

// Server snapshot is always empty: data lives in the browser only.
export function getServerAppData(): AppData {
  return EMPTY_DATA;
}

export function useAppData(): AppData {
  return useSyncExternalStore(subscribe, getAppData, getServerAppData);
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Quota exceeded or storage unavailable; in-memory data still works.
  }
}

export function setAppData(next: AppData) {
  state = next;
  persist();
  emit();
}

export function addCohort(cohort: Cohort) {
  setAppData({ ...state, cohorts: { ...state.cohorts, [cohort.assignment]: cohort } });
}

export function addCalibration(calibration: Calibration, assignment: string) {
  setAppData({ ...state, calibrations: { ...state.calibrations, [assignment]: calibration } });
}

export function addReports(reports: Report[]) {
  // Reports loaded from files have no API id; drop stale ids for the same submissions.
  const next = { ...state.reports };
  const ids = { ...state.apiReportIds };
  for (const r of reports) {
    next[r.submission_sha256] = r;
    delete ids[r.submission_sha256];
  }
  setAppData({ ...state, reports: next, apiReportIds: ids });
}

export function addFiles(names: string[]) {
  setAppData({ ...state, files: [...state.files, ...names] });
}

export function loadDemoData(data: AppData) {
  setAppData({ ...data, demo: true, apiReportIds: {} });
}

/** Replace everything with data pulled from the grader API. */
export function loadApiData(next: Omit<AppData, "demo" | "files">) {
  setAppData({ ...next, files: [], demo: false });
}

export function addApiReport(report: Report, reportId: string) {
  setAppData({
    ...state,
    demo: false,
    reports: { ...state.reports, [report.submission_sha256]: report },
    apiReportIds: { ...state.apiReportIds, [report.submission_sha256]: reportId },
  });
}

export function clearAll() {
  state = EMPTY_DATA;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
  emit();
}

export function hydrateFromStorage() {
  if (hydrated) return;
  hydrated = true;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<AppData>;
      state = { ...EMPTY_DATA, ...parsed, apiReportIds: parsed.apiReportIds ?? {} };
    }
  } catch {
    // Corrupt or unavailable storage; start empty.
  }
  emit();
}

export function lookupReport(data: AppData, id: string): Report | undefined {
  return (
    data.reports[id] ??
    Object.values(data.reports).find(
      (r) =>
        r.title === id || r.submission_sha256.startsWith(id) || decodeURIComponent(id) === r.title,
    )
  );
}

export function criterionNames(data: AppData, assignment: string): Record<string, string> {
  const names: Record<string, string> = {};
  for (const r of Object.values(data.reports)) {
    if (r.assignment !== assignment) continue;
    for (const c of r.score.criteria) names[c.criterion] = c.name;
    break;
  }
  return names;
}
