import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type FileLines } from "@/lib/api";
import { citeLabel } from "@/lib/format";
import type { Evidence } from "@/lib/types";
import { cn } from "@/lib/utils";

/*
 * Evidence chips open one shared drawer. When the report came from the grader API the drawer shows
 * the cited lines (with context) from the anonymized snapshot; otherwise it shows the verified quote.
 */

type Ctx = { open: (e: Evidence) => void; reportId: string | null };
const EvidenceContext = createContext<Ctx>({ open: () => undefined, reportId: null });

const CONTEXT_LINES = 4;

export function EvidenceProvider({
  reportId,
  children,
}: {
  reportId: string | null;
  children: ReactNode;
}) {
  const [current, setCurrent] = useState<Evidence | null>(null);
  return (
    <EvidenceContext.Provider value={{ open: setCurrent, reportId }}>
      {children}
      <Sheet open={current !== null} onOpenChange={(o) => (o ? undefined : setCurrent(null))}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
          {current ? <EvidenceBody evidence={current} reportId={reportId} /> : null}
        </SheetContent>
      </Sheet>
    </EvidenceContext.Provider>
  );
}

function EvidenceBody({ evidence, reportId }: { evidence: Evidence; reportId: string | null }) {
  const isFact = evidence.file === "fact_sheet";
  const [lines, setLines] = useState<FileLines | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isFact || !reportId) return undefined;
    let cancelled = false;
    const start = Math.max(1, evidence.start_line - CONTEXT_LINES);
    const end = (evidence.end_line || evidence.start_line) + CONTEXT_LINES;
    api
      .file(reportId, evidence.file, start, end)
      .then((r) => {
        if (!cancelled) setLines(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load the file");
      });
    return () => {
      cancelled = true;
    };
  }, [evidence.file, evidence.start_line, evidence.end_line, isFact, reportId]);

  const last = evidence.end_line || evidence.start_line;
  return (
    <>
      <SheetHeader>
        <SheetTitle className="font-mono text-sm">
          {isFact ? "Fact sheet" : citeLabel(evidence)}
        </SheetTitle>
        <SheetDescription>
          {isFact
            ? "Computed by code from the submission; the judge cites it as ground truth."
            : "Lines from the anonymized snapshot the judge read. Every citation was checked by code."}
        </SheetDescription>
      </SheetHeader>
      <div className="mt-4 flex flex-col gap-4">
        {isFact ? (
          <code className="rounded-md bg-muted px-3 py-2 font-mono text-sm">{evidence.quote}</code>
        ) : (
          <>
            <figure className="flex flex-col gap-1">
              <figcaption className="text-xs text-muted-foreground">Quoted by the judge</figcaption>
              <blockquote className="rounded-md bg-muted px-3 py-2 font-mono text-sm">
                {evidence.quote}
              </blockquote>
            </figure>
            {reportId === null ? (
              <p className="text-sm text-muted-foreground">
                Full file view is available when results are loaded from the grader API.
              </p>
            ) : error ? (
              <p className="text-sm text-bad">{error}</p>
            ) : lines === null ? (
              <div className="flex flex-col gap-1.5">
                {Array.from({ length: 6 }, (_, i) => (
                  <Skeleton key={i} className="h-4 w-full" />
                ))}
              </div>
            ) : (
              <pre className="overflow-x-auto rounded-md border bg-card py-2 font-mono text-[13px] leading-6">
                {lines.lines.map((l) => {
                  const cited = l.n >= evidence.start_line && l.n <= last;
                  return (
                    <div key={l.n} className={cn("flex px-3", cited && "bg-accent")}>
                      <span className="w-12 shrink-0 select-none pr-3 text-right text-muted-foreground tabular-nums">
                        {l.n}
                      </span>
                      <span className="whitespace-pre">{l.text}</span>
                    </div>
                  );
                })}
              </pre>
            )}
          </>
        )}
      </div>
    </>
  );
}

export function EvidenceChip({ evidence }: { evidence: Evidence }) {
  const { open } = useContext(EvidenceContext);
  return (
    <button
      type="button"
      onClick={() => open(evidence)}
      className="inline-flex max-w-full items-center rounded border bg-card px-1.5 py-0.5 font-mono text-[11px] text-foreground transition-colors hover:border-primary/40 hover:bg-accent"
      title={evidence.quote}
    >
      <span className="truncate">{citeLabel(evidence)}</span>
    </button>
  );
}

export function EvidenceList({
  evidence,
  empty = "proven absent by search",
}: {
  evidence: Evidence[];
  empty?: string;
}) {
  if (evidence.length === 0) return <span className="text-xs text-muted-foreground">{empty}</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {evidence.map((e, i) => (
        <EvidenceChip key={`${e.file}:${e.start_line}:${i}`} evidence={e} />
      ))}
    </span>
  );
}
