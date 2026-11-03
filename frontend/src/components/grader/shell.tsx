import { Link } from "@tanstack/react-router";
import { MoonIcon, SunIcon } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { hydrateFromStorage, useAppData } from "@/lib/store";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Load" },
  { to: "/cohorts", label: "Cohorts" },
  { to: "/reports", label: "Reports" },
  { to: "/calibration", label: "Calibration" },
  { to: "/grade", label: "Grade" },
] as const;

const THEME_KEY = "grader-console:theme";

function readTheme(): "light" | "dark" {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // storage unavailable
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function ThemeToggle() {
  // Client-only preference: read after mount so server and client markup match.
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);

  useEffect(() => {
    const t = readTheme();
    document.documentElement.classList.toggle("dark", t === "dark");
    setTheme(t);
  }, []);

  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // ignore
    }
    setTheme(next);
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={theme === "dark" ? "Use light theme" : "Use dark theme"}
    >
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </Button>
  );
}

function SourceBadge() {
  const data = useAppData();
  const source = data.demo
    ? "Demo data"
    : Object.keys(data.apiReportIds).length
      ? "Grader API"
      : data.files.length
        ? `${data.files.length} file${data.files.length === 1 ? "" : "s"} loaded`
        : null;
  return source === null ? null : (
    <span
      className={cn(
        "rounded-md px-2 py-0.5 text-xs",
        data.demo ? "bg-warn-soft text-warn" : "bg-muted text-muted-foreground",
      )}
    >
      {source}
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  useEffect(() => {
    hydrateFromStorage();
  }, []);

  return (
    <div className="min-h-screen">
      <header
        className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80"
        data-print-hide
      >
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
          <Link to="/" className="font-semibold tracking-tight">
            Grader Console
          </Link>
          <nav className="flex items-center gap-1 overflow-x-auto text-sm" aria-label="Main">
            {NAV.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                activeOptions={{ exact: item.to === "/" }}
                className="rounded-md px-2.5 py-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                activeProps={{ className: "bg-accent text-foreground font-medium" }}
              >
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <SourceBadge />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 pb-16 pt-6">{children}</main>
    </div>
  );
}

export function PageHeader({
  title,
  meta,
  actions,
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {meta !== undefined && <div className="mt-1 text-sm text-muted-foreground">{meta}</div>}
      </div>
      {actions !== undefined && (
        <div className="flex items-center gap-2" data-print-hide>
          {actions}
        </div>
      )}
    </div>
  );
}
