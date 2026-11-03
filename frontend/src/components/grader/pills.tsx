import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "bad" | "dim" | "accent";

/** Status label. Colour is never the only signal: the text always says what it means. */
export function Pill({
  tone,
  children,
  className,
  title,
}: {
  tone: Tone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <Badge
      variant={tone}
      title={title}
      className={cn("whitespace-nowrap rounded-full px-2 font-medium", className)}
    >
      {children}
    </Badge>
  );
}

export function ElementStatus({ status }: { status: "found" | "partial" | "missing" }) {
  const tone: Tone = status === "found" ? "ok" : status === "partial" ? "warn" : "bad";
  return <Pill tone={tone}>{status}</Pill>;
}

export function Severity({ severity }: { severity: string | undefined }) {
  const tone: Tone = severity === "high" ? "bad" : severity === "medium" ? "warn" : "dim";
  return <Pill tone={tone}>{severity ?? "low"}</Pill>;
}

export function CapStatus({ status }: { status: string }) {
  const tone: Tone = status === "applied" ? "bad" : status === "defect_only" ? "warn" : "dim";
  return <Pill tone={tone}>{status.replace("_", " ")}</Pill>;
}

export function Band({ band }: { band: string | null | undefined }) {
  if (!band) return <span className="text-muted-foreground">—</span>;
  const low = band.toLowerCase();
  const tone: Tone = /distinction|strong/.test(low)
    ? "ok"
    : /^pass/.test(low)
      ? "accent"
      : /revision/.test(low)
        ? "warn"
        : /insufficient|fail/.test(low)
          ? "bad"
          : "dim";
  return <Pill tone={tone}>{band}</Pill>;
}

export function Confidence({ level }: { level: string }) {
  const tone: Tone = level === "High" ? "ok" : level === "Medium" ? "warn" : "bad";
  return <Pill tone={tone}>{level} confidence</Pill>;
}

export function InjectionLabel({ label }: { label: string }) {
  const tone: Tone =
    label === "grader_manipulation" ? "bad" : label === "unclassified" ? "warn" : "dim";
  return <Pill tone={tone}>{label.replace(/_/g, " ")}</Pill>;
}

/** 0-4 rating as a compact 8-segment bar plus the number. */
export function RatingBar({
  rating,
  max = 4,
  showValue = true,
}: {
  rating: number;
  max?: number;
  showValue?: boolean;
}) {
  const segments = max * 2;
  const filled = Math.round(rating * 2);
  const tone =
    rating >= 3 ? "bg-ok" : rating >= 2 ? "bg-primary" : rating >= 1 ? "bg-warn" : "bg-bad";
  return (
    <span className="inline-flex items-center gap-1.5" aria-label={`${rating} out of ${max}`}>
      <span className="inline-flex gap-px">
        {Array.from({ length: segments }, (_, i) => (
          <span
            key={i}
            className={cn("h-2.5 w-1.5 rounded-[1px]", i < filled ? tone : "bg-muted")}
          />
        ))}
      </span>
      {showValue && <span className="font-mono text-xs tabular-nums">{rating}</span>}
    </span>
  );
}

/** Ratings from repeated runs as dots; highlighted when they disagree by a level or more. */
export function RunDots({ runs }: { runs: number[] }) {
  if (runs.length === 0) return null;
  const unstable = Math.max(...runs) - Math.min(...runs) >= 1;
  return (
    <span
      className={cn("inline-flex items-center gap-1 font-mono text-[11px]", unstable && "text-bad")}
      title={unstable ? "Runs disagree by a level or more" : "Run ratings"}
    >
      {runs.map((r, i) => (
        <span key={i} className={cn("rounded px-1", unstable ? "bg-bad-soft" : "bg-muted")}>
          {r}
        </span>
      ))}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="rounded-lg border bg-card px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
      {hint !== undefined && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-3 mt-8 flex items-end justify-between gap-4 border-b pb-2">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {children}
      </h2>
      {aside}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed bg-card px-6 py-12 text-center">
      <div className="font-medium">{title}</div>
      {children !== undefined && (
        <div className="mt-2 text-sm text-muted-foreground">{children}</div>
      )}
    </div>
  );
}
