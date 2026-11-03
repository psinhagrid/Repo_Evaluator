---
name: frontend_craft
description: One-stop frontend workflow combining four installed skills — frontend-design (visual direction), impeccable (UX craft floor and review), shadcn (component and styling rules), and vercel-react-best-practices (React performance). Use when building, redesigning, reviewing or polishing React + shadcn/ui interfaces, e.g. "/frontend_craft build the cohort dashboard" or "/frontend_craft critique the report page".
argument-hint: "[build|refine|critique|audit|polish] <screen, component or file>"
---

# Frontend craft

Build or improve the UI named in the arguments by running the four source skills as one pass:
**direction** (frontend-design) → **floor** (impeccable) → **components** (shadcn) → **performance**
(vercel-react-best-practices) → **verify**. The source skills live in `.agents/skills/`; open the
files this skill points to when a step needs their detail. Do not paste their full text into context.

Arguments: `$ARGUMENTS`. Pick the mode from the first word: `build` (new screen or component),
`refine` (improve existing UI, keep its identity), `critique` (review only, no edits), `audit`
(technical checks only), `polish` (final pass before shipping). With no mode, infer it; with no
target, ask which screen or file.

## Ground rules (resolve conflicts between the source skills)

1. **The brief and the incumbent design win.** If the project already has a design system (tokens in
   the global CSS, DESIGN.md, PRODUCT.md, or a written brief), follow it, even where frontend-design
   or impeccable would call a choice a "default". Both skills say this; it overrides their lists.
   Their one hard ban survives every brief: no eyebrow/kicker label above a heading.
2. **Mode decides ambition (impeccable).** Dashboards, tools, admin and data views are *Operate*:
   scanability, consistency and familiar patterns beat expression; brand shows in precise details.
   Marketing pages are *Persuade*: commit to a distinct point of view. Docs are *Read*.
3. **Components before markup (shadcn).** Use the project's installed shadcn components and
   semantic tokens before writing styled `div`s. Check `components.json` and the `ui` folder for
   what is installed; only use components that exist there.
4. **Refinement preserves, redesign replaces (impeccable).** In `refine` and `polish`, keep layout,
   copy and behaviour outside the target. Ask before replacing factual copy.
5. **Safety:** do not run the impeccable launcher (`scripts/impeccable`, it downloads a binary) or
   `npx shadcn` commands without the user's go-ahead. Use impeccable's documented fallback: read
   PRODUCT.md / DESIGN.md directly. Never invent data, scores or claims in UI copy.

## 1. Direction (build / redesign only)

From `.agents/skills/frontend-design/SKILL.md`:

- Name the subject, the audience and the screen's primary job in one line each.
- Write a compact plan: 4–6 named colours (or "use existing tokens"), typefaces and roles, a layout
  sketch (ASCII is fine) with alignment, and 2–3 principles specific to this screen.
- Review the plan against the brief: if a part would come out the same for any similar screen,
  change it and say why. Then build. Spend boldness in one place; keep the rest quiet.
- Copy: user language, active verbs, sentence case; buttons name their action ("Grade submission",
  not "Submit"); errors say what happened and how to fix it; empty states invite the next action.

## 2. Craft floor (before any UI edit)

Read `.agents/skills/impeccable/reference/craft-floor.md` immediately before editing UI, then build
to it without announcing it. Key checks: contrast ≥ 4.5:1 for body text; every state designed
(hover, focus, disabled, loading, error, empty); tabular numerals in data; themed focus rings and
selection; one authored motion moment at most; no gradient text, glass-as-decoration, nested cards,
thick coloured side borders, or hero-metric template unless the brief asks. For Operate screens
also read `.agents/skills/impeccable/reference/operate.md`.

## 3. Components and styling

Follow the Critical Rules in `.agents/skills/shadcn/SKILL.md`; open the matching rule file when
writing that kind of code:

- Styling → `.agents/skills/shadcn/rules/styling.md`: semantic tokens only (`bg-primary`,
  `text-muted-foreground`), no raw palette colours, no manual `dark:` overrides, `flex gap-*` not
  `space-y-*`, `size-*` for squares, `truncate`, `cn()` for conditional classes, no z-index on overlays.
- Composition → `.agents/skills/shadcn/rules/composition.md`: Items inside Groups; Dialog/Sheet/Drawer
  always have a Title (`sr-only` if hidden); full Card composition; `TabsTrigger` inside `TabsList`;
  Alert for callouts, Badge for status, Separator, Skeleton for loading, Empty for empty states.
- Forms → `.agents/skills/shadcn/rules/forms.md`; icons → `.agents/skills/shadcn/rules/icons.md`
  (`data-icon` in buttons, no size classes on icons inside components; use the project's icon library).

## 4. React performance and correctness

Apply the high-impact rules from `.agents/skills/vercel-react-best-practices/SKILL.md`; open
`rules/<rule-name>.md` for an example when unsure:

- **Waterfalls (critical):** start independent requests together (`Promise.all`); await only in the
  branch that needs the value; check cheap conditions before awaiting.
- **Bundle (critical):** import directly, not through barrel files; lazy-load heavy or rarely used
  views; defer non-critical third-party code.
- **Re-renders:** derive values during render, not in effects; never define components inside
  components; primitive effect dependencies; functional `setState`; `startTransition` /
  `useDeferredValue` for filtering large tables; put interaction logic in event handlers.
- **Rendering:** ternaries over `&&` for conditional JSX; hoist static JSX; `content-visibility` for
  long lists; avoid hydration flicker for client-only data (localStorage) by reading it after mount.
- **Client data:** version and minimise anything stored in localStorage; wrap storage access in
  try/catch.

## 5. Verify

- Run the project's typecheck, lint, tests and build; fix what fails before reporting.
- Look at the rendered result at desktop and phone widths in light and dark mode, batching fixes
  per look (impeccable). Check every state from step 2 with real data, including empty and error.
- Self-critique, then remove one accessory (frontend-design): cut the decoration that does not serve
  the screen's job.
- Report what changed, what was verified and how, and anything not verified.

## Review modes

- `critique`: no edits. Score the target against `.agents/skills/impeccable/reference/critique.md`
  heuristics plus the shadcn Critical Rules and the React rules above. Findings ranked by impact,
  each with file:line and a concrete fix.
- `audit`: technical checks from `.agents/skills/impeccable/reference/audit.md` (accessibility,
  performance, responsive), plus typecheck/lint/build results.
- `polish`: follow `.agents/skills/impeccable/reference/polish.md`, limited to the target; keep its
  identity.
