export type Evidence = { file: string; start_line: number; end_line: number; quote: string };
export type Item = {
  text: string;
  evidence: Evidence[];
  search_ids: string[];
  severity?: "low" | "medium" | "high";
};
export type Element = {
  id: string;
  status: "found" | "partial" | "missing";
  evidence: Evidence[];
  search_ids: string[];
  note: string;
};

export type Report = {
  status: "graded" | "not_graded";
  reason?: string;
  assignment: string;
  title: string;
  policy_version: string;
  prompt_version: string;
  judge_key: string;
  submission_sha256: string;
  submission_file?: string;
  generated_at: string;
  score: {
    total: number;
    final: number;
    band: string | null;
    binding_cap: string | null;
    nearest_cutoff: { cutoff: number; distance: number } | null;
    flags: string[];
    criteria: { criterion: string; name: string; weight: number; rating: number; points: number }[];
    caps: {
      cap_id: string;
      max: number;
      description: string;
      status: "applied" | "defect_only" | "not_triggered";
      reason: string;
    }[];
  };
  sections: {
    criterion: string;
    name: string;
    weight: number;
    rating: number;
    judge_rating: number;
    points: number;
    runs: number[];
    model: string;
    tool_calls: number;
    corrections: number;
    citations_failed_first_pass: number;
    citations_failed_final: number;
    verification_notes: string[];
    level_source: "rubric" | "draft" | null;
    zero_rule: {
      description: string;
      quotes: { file: string; line: number; excerpt: string }[];
    } | null;
    assessment: {
      rating: number;
      rationale: string;
      elements: Element[];
      what_worked: Item[];
      what_to_improve: Item[];
      defects: Item[];
      unverified: string[];
      findings: { id: string; value: boolean | null }[];
    };
  }[];
  zero_rules: {
    rule: string;
    criterion: string;
    description: string;
    status: string;
    reason: string;
  }[];
  top_defects: (Item & { id: string; criterion: string })[];
  policy_defects: { source: string; severity: string; text: string; evidence: Evidence[] }[];
  verdict: string;
  next_steps: string[];
  injection_precheck: {
    regex_matches: number;
    classifier_status: string;
    classified: {
      file: string;
      line: number;
      pattern: string;
      excerpt: string;
      label: string;
      reason: string;
    }[];
    confirmed_manipulation: { file: string; line: number }[];
    hidden_unicode: { file: string; line: number }[];
    judge_flags: { file: string; line: number; excerpt: string; criterion: string }[];
  };
  integrity: {
    forbidden_files: { path: string; reason: string }[];
    secret_matches: { file: string; line: number; kind: string }[];
    placeholders: { file: string; line: number; excerpt: string }[];
    leftover_agent_text: { file: string; line: number; excerpt: string }[];
    git: {
      commit_count: number;
      chronology: string;
      authors: Record<string, number>;
      agent_only_commits: number;
    };
    similarity?: { other_name: string; kind: "code" | "writeup"; score: number }[];
  };
  limitations: { text: string; unverified: { criterion: string; item: string }[] };
  confidence: { level: "High" | "Medium" | "Low"; reasons: string[]; text: string };
  cost: { total_usd: number };
};

export type Cohort = {
  assignment: string;
  policy_version: string;
  submissions: number;
  graded: number;
  cost_usd: number;
  not_graded: Record<string, string>;
  ranking: {
    rank: number;
    submission: string;
    sha: string;
    final: number;
    total: number;
    band: string | null;
    percentile: number;
    caps: string[];
    binding_cap: string | null;
    confidence: string;
    tied_with: string[];
    ratings: Record<string, number>;
  }[];
  criteria: Record<string, { mean: number; median: number; distribution: Record<string, number> }>;
  common_gaps: [string, number][];
  caps_applied: Record<string, number>;
  hygiene: Record<string, number>;
  order_trend: { n: number; rho: number | null; p: number | null; flag: boolean };
  similarity: { a: string; b: string; kind: "code" | "writeup"; score: number }[];
};

export type Calibration = {
  passed: boolean;
  submissions: number;
  runs_per_submission: number;
  cost: { total_usd: number };
  criteria: Record<
    string,
    {
      agreement: {
        n: number;
        within_half: number;
        exact: number;
        mae: number;
        bias: number;
        bias_p: number;
      };
      stability: { n: number; mean_range: number };
      issues: string[];
    }
  >;
  haiku_routing?: Record<
    string,
    {
      route_to_haiku: boolean;
      why: string;
      haiku: { agreement: { within_half: number; mae: number } };
    }
  >;
  readme_swap?: {
    criteria: string[];
    leaks: string[];
    results?: Record<
      string,
      Record<"polished" | "weak", { n: number; mean_delta: number; mean_abs_delta: number }>
    >;
  };
  regressions: string[];
  reference_excerpt_suggestions: Record<string, Record<string, string[]>>;
};
