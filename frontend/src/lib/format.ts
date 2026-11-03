export const fmt = (n: number | null | undefined, digits = 2): string =>
  n === null || n === undefined || Number.isNaN(n) ? "—" : Number(n.toFixed(digits)).toString();

export const pct = (x: number | null | undefined, digits = 0): string =>
  x === null || x === undefined ? "—" : `${(x * 100).toFixed(digits)}%`;

export const signed = (x: number, digits = 2): string => `${x > 0 ? "+" : ""}${x.toFixed(digits)}`;

export const shortSha = (sha: string | null | undefined): string => (sha ? sha.slice(0, 12) : "—");

export const ratingSteps = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4];

export function citeLabel(e: {
  file: string;
  start_line: number;
  end_line: number;
  quote: string;
}): string {
  if (e.file === "fact_sheet") return `fact sheet · ${e.quote}`;
  return e.start_line === e.end_line || !e.end_line
    ? `${e.file}:${e.start_line}`
    : `${e.file}:${e.start_line}-${e.end_line}`;
}

export function humanize(id: string): string {
  return id.replace(/[_-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function encodeKey(key: string): string {
  return encodeURIComponent(key);
}
