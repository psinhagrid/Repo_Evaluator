import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { ChevronDownIcon, ChevronUpIcon } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";

import {
  Band,
  Confidence,
  EmptyState,
  Pill,
  RatingBar,
  SectionTitle,
  Stat,
} from "@/components/grader/pills";
import { AppShell, PageHeader } from "@/components/grader/shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { fmt, humanize, pct, ratingSteps } from "@/lib/format";
import { criterionNames, useAppData } from "@/lib/store";
import type { Cohort } from "@/lib/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/cohorts/$id")({
  head: () => ({ meta: [{ title: "Cohort · Grader Console" }] }),
  component: CohortPage,
});

type Row = Cohort["ranking"][number];
type SortKey = "rank" | "final" | "percentile" | "submission";

function CohortPage() {
  const { id } = Route.useParams();
  const data = useAppData();
  const cohort = data.cohorts[id];

  if (!cohort) {
    return (
      <AppShell>
        <EmptyState title="This cohort is not loaded">
          Load its <span className="font-mono">cohort.json</span> or sync from the API on the{" "}
          <Link to="/" className="text-primary underline underline-offset-4">
            Load page
          </Link>
          .
        </EmptyState>
      </AppShell>
    );
  }
  return (
    <AppShell>
      <CohortView
        cohort={cohort}
        names={criterionNames(data, cohort.assignment)}
        reportShas={Object.keys(data.reports)}
      />
    </AppShell>
  );
}

function CohortView({
  cohort,
  names,
  reportShas,
}: {
  cohort: Cohort;
  names: Record<string, string>;
  reportShas: string[];
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [band, setBand] = useState("all");
  const [confidence, setConfidence] = useState("all");
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "rank", desc: false });
  const deferredQuery = useDeferredValue(query);

  const criteria = Object.keys(cohort.criteria);
  const bands = Object.keys(countBy(cohort.ranking, (r) => r.band ?? "No band"));
  const finals = cohort.ranking.map((r) => r.final).sort((a, b) => a - b);
  const median = finals.length ? finals[Math.floor((finals.length - 1) / 2)] : undefined;
  const capped = cohort.ranking.filter((r) => r.caps.length > 0).length;
  const similarSubs = new Set(cohort.similarity.flatMap((m) => [m.a, m.b])).size;

  const rows = useMemo(() => {
    const q = deferredQuery.trim().toLowerCase();
    const filtered = cohort.ranking.filter(
      (r) =>
        (band === "all" || (r.band ?? "No band") === band) &&
        (confidence === "all" || r.confidence === confidence) &&
        (q === "" || r.submission.toLowerCase().includes(q) || r.sha.startsWith(q)),
    );
    const dir = sort.desc ? -1 : 1;
    return filtered.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      return (
        (typeof av === "string" && typeof bv === "string"
          ? av.localeCompare(bv)
          : Number(av) - Number(bv)) * dir
      );
    });
  }, [cohort.ranking, deferredQuery, band, confidence, sort]);

  const reportFor = (row: Row) => reportShas.find((s) => s.startsWith(row.sha));

  const toggleSort = (key: SortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, desc: !s.desc }
        : { key, desc: key === "final" || key === "percentile" },
    );

  return (
    <>
      <PageHeader
        title={cohort.assignment}
        meta={
          <>
            Policy {cohort.policy_version} · {cohort.graded} of {cohort.submissions} graded · cost $
            {fmt(cohort.cost_usd)}
          </>
        }
      />

      {cohort.order_trend.flag ? (
        <Alert variant="destructive" className="mt-4">
          <AlertTitle>Scores trend with grading order</AlertTitle>
          <AlertDescription>
            Spearman rho {fmt(cohort.order_trend.rho, 3)}, p = {fmt(cohort.order_trend.p, 3)}.
            Investigate before releasing grades: a drift like this usually means the grading
            conditions changed during the run.
          </AlertDescription>
        </Alert>
      ) : null}

      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat label="Median final" value={fmt(median)} hint="out of 100" />
        <div className="rounded-lg border bg-card px-4 py-3 lg:col-span-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Bands
          </div>
          <BandBar ranking={cohort.ranking} />
        </div>
        <Stat
          label="Capped"
          value={capped}
          hint={`${pct(cohort.graded ? capped / cohort.graded : 0)} of graded`}
        />
        <Stat
          label="Similarity flags"
          value={similarSubs}
          hint={
            cohort.order_trend.rho === null
              ? "order check: too few"
              : `order check p = ${fmt(cohort.order_trend.p, 3)}`
          }
        />
      </div>

      <SectionTitle
        aside={
          <span className="text-xs text-muted-foreground">
            {rows.length} of {cohort.ranking.length}
          </span>
        }
      >
        Ranking
      </SectionTitle>
      <div className="mb-3 flex flex-wrap gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search submission or hash"
          className="w-64"
          aria-label="Search submissions"
        />
        <FilterSelect label="Band" value={band} onChange={setBand} options={bands} />
        <FilterSelect
          label="Confidence"
          value={confidence}
          onChange={setConfidence}
          options={["High", "Medium", "Low"]}
        />
      </div>
      <div className="max-h-[70vh] overflow-auto rounded-lg border bg-card">
        <table className="dense-table">
          <thead>
            <tr>
              <SortHeader label="Rank" k="rank" sort={sort} onSort={toggleSort} numeric />
              <SortHeader label="Submission" k="submission" sort={sort} onSort={toggleSort} />
              <SortHeader label="Final" k="final" sort={sort} onSort={toggleSort} numeric />
              <th>Band</th>
              <SortHeader label="Pctl" k="percentile" sort={sort} onSort={toggleSort} numeric />
              <th>Caps</th>
              <th>Confidence</th>
              {criteria.map((c) => (
                <th key={c} title={names[c] ?? humanize(c)}>
                  {shortName(names[c] ?? humanize(c))}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td
                  colSpan={7 + criteria.length}
                  className="py-8 text-center text-muted-foreground"
                >
                  No submissions match these filters.
                </td>
              </tr>
            ) : (
              rows.map((r) => {
                const sha = reportFor(r);
                return (
                  <tr
                    key={r.submission}
                    className={cn(sha && "cursor-pointer")}
                    onClick={
                      sha
                        ? () => void navigate({ to: "/reports/$id", params: { id: sha } })
                        : undefined
                    }
                  >
                    <td className="num text-muted-foreground">{r.rank}</td>
                    <td className="max-w-56">
                      <div className="truncate font-medium">{r.submission}</div>
                      {r.tied_with.length > 0 ? (
                        <div className="text-xs text-warn">tied with {r.tied_with.join(", ")}</div>
                      ) : null}
                    </td>
                    <td className="num font-semibold">{fmt(r.final)}</td>
                    <td>
                      <Band band={r.band} />
                    </td>
                    <td className="num">{fmt(r.percentile, 1)}</td>
                    <td>
                      <span className="flex flex-wrap gap-1">
                        {r.caps.map((c) => (
                          <Pill
                            key={c}
                            tone={c === r.binding_cap ? "bad" : "warn"}
                            title={c === r.binding_cap ? "Binding cap" : "Applied, not binding"}
                          >
                            {c === r.binding_cap ? `${c} (binding)` : c}
                          </Pill>
                        ))}
                      </span>
                    </td>
                    <td>
                      <Confidence level={r.confidence} />
                    </td>
                    {criteria.map((c) => {
                      const v = r.ratings[c];
                      return <td key={c}>{v === undefined ? "—" : <RatingBar rating={v} />}</td>;
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <SectionTitle>Criterion averages</SectionTitle>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {criteria.map((c) => {
          const s = cohort.criteria[c];
          return s ? <Histogram key={c} name={names[c] ?? humanize(c)} stats={s} /> : null;
        })}
      </div>

      <div className="mt-2 grid gap-6 lg:grid-cols-2">
        <div>
          <SectionTitle>Most common gaps</SectionTitle>
          {cohort.common_gaps.length === 0 ? (
            <p className="text-sm text-muted-foreground">No missing or partial elements.</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {cohort.common_gaps.map(([label, n]) => (
                <li key={label} className="grid grid-cols-[1fr_auto] items-center gap-3 text-sm">
                  <div className="relative overflow-hidden rounded-sm">
                    <div
                      className="absolute inset-y-0 left-0 bg-accent"
                      style={{ width: `${(n / cohort.graded) * 100}%` }}
                    />
                    <span className="relative block truncate px-2 py-0.5">{label}</span>
                  </div>
                  <span className="tabular-nums text-muted-foreground">{n}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <SectionTitle>Hygiene issues</SectionTitle>
          <KeyCounts counts={cohort.hygiene} empty="No hygiene issues." />
          <SectionTitle>Caps applied</SectionTitle>
          <KeyCounts counts={cohort.caps_applied} empty="No caps applied." />
        </div>
      </div>

      <SectionTitle
        aside={
          <span className="text-xs text-muted-foreground">consequence set by course policy</span>
        }
      >
        Near-identical submissions
      </SectionTitle>
      {cohort.similarity.length === 0 ? (
        <p className="text-sm text-muted-foreground">No pairs above the similarity threshold.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="dense-table">
            <thead>
              <tr>
                <th>Submission</th>
                <th>Similar to</th>
                <th>Kind</th>
                <th className="num">Overlap</th>
              </tr>
            </thead>
            <tbody>
              {cohort.similarity.map((m) => (
                <tr key={`${m.a}-${m.b}-${m.kind}`}>
                  <td>{m.a}</td>
                  <td>{m.b}</td>
                  <td>{m.kind === "code" ? "Code" : "Write-up"}</td>
                  <td className="num font-medium">{pct(m.score)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {Object.keys(cohort.not_graded).length > 0 ? (
        <>
          <SectionTitle>Not graded</SectionTitle>
          <ul className="flex flex-col gap-1 text-sm">
            {Object.entries(cohort.not_graded).map(([name, why]) => (
              <li key={name}>
                <span className="font-medium">{name}</span>{" "}
                <span className="text-muted-foreground">— {why}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

function countBy<T>(items: T[], key: (t: T) => string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const it of items) {
    const k = key(it);
    out[k] = (out[k] ?? 0) + 1;
  }
  return out;
}

function shortName(name: string): string {
  return name.length > 14 ? `${name.slice(0, 13)}…` : name;
}

const BAND_TONES = ["bg-ok", "bg-primary", "bg-warn", "bg-bad", "bg-dim"];

function BandBar({ ranking }: { ranking: Row[] }) {
  const counts = countBy(ranking, (r) => r.band ?? "No band");
  const total = ranking.length || 1;
  const entries = Object.entries(counts);
  return (
    <div className="mt-2 flex flex-col gap-2">
      <div className="flex h-2.5 overflow-hidden rounded-full bg-muted">
        {entries.map(([b, n], i) => (
          <div
            key={b}
            className={BAND_TONES[i % BAND_TONES.length]}
            style={{ width: `${(n / total) * 100}%` }}
            title={`${b}: ${n}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {entries.map(([b, n], i) => (
          <span key={b} className="inline-flex items-center gap-1">
            <span className={cn("size-2 rounded-full", BAND_TONES[i % BAND_TONES.length])} />
            {b} <span className="tabular-nums text-foreground">{n}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Histogram({
  name,
  stats,
}: {
  name: string;
  stats: { mean: number; median: number; distribution: Record<string, number> };
}) {
  const counts = ratingSteps.map(
    (s) => stats.distribution[String(s)] ?? stats.distribution[s.toFixed(1)] ?? 0,
  );
  const max = Math.max(1, ...counts);
  return (
    <div className="rounded-lg border bg-card px-4 py-3">
      <div className="truncate text-sm font-medium" title={name}>
        {name}
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground">
        mean {fmt(stats.mean)} · median {fmt(stats.median)}
      </div>
      <div
        className="mt-3 flex h-16 items-end gap-1"
        role="img"
        aria-label={`Rating distribution for ${name}`}
      >
        {counts.map((n, i) => (
          <div key={i} className="flex flex-1 flex-col items-center gap-1">
            <div
              className="w-full rounded-sm bg-primary/70"
              style={{ height: `${(n / max) * 100}%`, minHeight: n ? 2 : 0 }}
              title={`${ratingSteps[i]}: ${n}`}
            />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-muted-foreground">
        <span>0</span>
        <span>2</span>
        <span>4</span>
      </div>
    </div>
  );
}

function KeyCounts({ counts, empty }: { counts: Record<string, number>; empty: string }) {
  const entries = Object.entries(counts);
  if (entries.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {entries.map(([k, n]) => (
        <li key={k} className="flex justify-between gap-4">
          <span>{humanize(k)}</span>
          <span className="tabular-nums text-muted-foreground">{n}</span>
        </li>
      ))}
    </ul>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-44" aria-label={label}>
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectItem value="all">All {label.toLowerCase()}s</SelectItem>
          {options.map((o) => (
            <SelectItem key={o} value={o}>
              {o}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

function SortHeader({
  label,
  k,
  sort,
  onSort,
  numeric = false,
}: {
  label: string;
  k: SortKey;
  sort: { key: SortKey; desc: boolean };
  onSort: (k: SortKey) => void;
  numeric?: boolean;
}) {
  const active = sort.key === k;
  return (
    <th
      className={cn(numeric && "num")}
      aria-sort={active ? (sort.desc ? "descending" : "ascending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(k)}
        className="inline-flex items-center gap-1 uppercase hover:text-foreground"
      >
        {label}
        {sort.desc ? (
          <ChevronDownIcon aria-hidden className={cn("size-3", !active && "opacity-0")} />
        ) : (
          <ChevronUpIcon aria-hidden className={cn("size-3", !active && "opacity-0")} />
        )}
      </button>
    </th>
  );
}
