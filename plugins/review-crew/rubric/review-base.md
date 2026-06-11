<!-- rubric-version: 3 -->
# review-base

The source of truth for review **severity, verification rules, findings format,
triage, and verdicts** — shared by every review-crew skill and agent. It is
stack-neutral and universal. All **project calibration** (threat model, scope
exclusions, the verify command, focus hints, canonical patterns) lives in the
project profile at `.claude/review-profile.md`; **conventions** live in the
project's `CLAUDE.md`. If a review finding contradicts this file, this file wins.

`rubric-version` (top of file) is the staleness signal for "the rubric changed";
bump it on any semantic change here.

## Calibration comes from the profile (not baked in here)

This rubric is deliberately neutral about audience, threat model, and what is
in/out of scope — those vary per project and are read from
`.claude/review-profile.md` + `CLAUDE.md`. Reviewer strictness (how aggressively
to flag) is profile-tunable. **When the profile or a needed field is absent,
default to the STRICT posture** (assume a multi-user threat model and err toward
flagging) — it is safer to over-flag than to miss a real access-control bug.
Minor and Nit findings never change the verdict regardless of strictness.

## Severity tiers

| Tier             | Definition (stack-neutral)                                                                                   | Example (illustrative, not stack-specific)                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| **Critical**     | Corrupts data, leaks data across a trust boundary, or breaks production. NEVER for tests or style.           | A request handler returns records belonging to another principal            |
| **Important**    | Likely bug in normal use, OR a security/correctness issue warranting a fix before merge                      | A value that can be absent is dereferenced on a reachable path              |
| **Minor**        | Real issue, small impact                                                                                     | A magic number; an inconsistent error message                              |
| **Nit**          | Style/naming/cleanup; take-it-or-leave-it                                                                     | "This name could be clearer"                                                |
| **Pre-existing** | Issue only in lines the diff did not change (unchanged files, or context lines) — SKIPPED, not reported      | A pattern in code the change didn't touch                                   |

## Verification rules (binding — violations are dropped at compile time)

1. **`file:line` citation required.** No citation → drop.
2. **Diff-scope rule** (diff modes only): flag only code on `+`/`-` lines. Context
   lines and unchanged code are pre-existing → SKIP. (Audit/sweep mode reviews the
   whole repo; this rule does not apply there.)
3. **Grep-before-flag.** Before flagging "missing X", search the codebase for X
   under variant names. A thing that exists under another name is not missing.
4. **Reachability check on Important findings.** Read the caller(s); if the only
   caller already guards the case, downgrade or drop. (Critical findings are also
   checked for reachability, but under the strict posture, flag when in doubt.)
5. **Docs/spec changes:** spot-check factual claims (signatures, paths, error
   types) against source, not just prose.

## Findings output format (the single schema — agents reference this, never restate it)

Every agent emits a JSON array at the path the dispatching skill specifies. This
is the one authoritative schema; agents must not redefine the fields inline.

```json
[
  {
    "id": "<agent-name>-001",
    "severity": "Critical | Important | Minor | Nit",
    "dimension": "<one of the dimensions below>",
    "title": "<short descriptive title>",
    "file": "<path relative to repo root>",
    "line": "<number or null>",
    "body": "<explanation with code references>",
    "suggestion": "<what to do, or null>",
    "evidence": "<for Important/Critical: trigger + impact / the reachable path; omit or null for Minor/Nit>",
    "confidence": "High | Low",
    "tradeoff": "<true only if multiple valid fix approaches exist; omit otherwise>"
  }
]
```

- `confidence` is the agent's own confidence after running the in-pass Chain-of-Verification (below). **High** = the chain passed cleanly. **Low** = emitted but genuinely unsure — it flags the finding for scrutiny rather than dropping a possibly-real issue. Required on Critical/Important (a **Low** Critical/Important MUST name exactly what is uncertain in its `evidence` line); may be omitted on Minor/Nit (treated as High). Low confidence does not, on its own, change the verdict beyond what the finding's severity already implies.

**Dimensions** (the orchestrator reads this list; it is data, not hard-wired —
adding one later is a single-place change): `Architecture`, `Code`, `Security`,
`Test`, `Failure-Mode`. These five are the default crew; each dispatching skill
names the subset it runs. The dispatching skill assigns each agent its dimension
and its `id` prefix; the default crew runs one agent per dimension (e.g. the
Security reviewer emits `security-001`, …; the Failure-Mode reviewer emits
`premortem-001`, …).

## Severity caps

- **Nits:** at most 5 reported per review; summarize the rest as a count.
- **Critical / Important:** uncapped (load-bearing).
- **Minor:** uncapped, but each must pass the verification rules; if reporting
  >10, dedupe — they're usually facets of one issue.

## Triage rubric (mechanical vs judgment)

For each finding, classify the **fix** (not whether to fix):

- **judgment** when ANY of: `tradeoff: true`; the fix is a UX/design call with more
  than one reasonable option; or it changes established product behavior the user
  may have an opinion on.
- **mechanical** when the fix is determinate (one obviously-correct change).

Bias hard toward **mechanical**. Example (stack-neutral): "replace the hardcoded
not-found string with the project's error constant" = mechanical. "This empty
state needs copy and a layout decision" = judgment.

## Orchestrator POV (on every presented finding)

When a skill presents a finding for a decision, the orchestrator attaches its own
point of view — advisory; the user's decision always wins and the POV never
auto-applies.

- **Recommendation:** `Fix` (correct and worth it here) | `Skip` (good reason not
  to: correct-but-not-worth-it for this project, cost > benefit, or borderline
  false positive) | `Defer` (real but not now/here).
- **Rationale:** one sentence.
- **Confidence:** `High` | `Low` (Low flags where to scrutinize). This is the
  *orchestrator's* advisory confidence, distinct from a finding's own
  `confidence` field (the agent's self-assessment, above).

Form it from a small targeted read of the cited code — not a re-review.

## In-pass Chain-of-Verification & single-pass discipline

Each specialist runs **once** per review. Do NOT dispatch a verifier agent or
re-run a specialist — multi-turn agentic review degrades F1 and fabricates
findings as real ones are exhausted. Instead, within its single pass, the agent
runs an ordered **Chain-of-Verification** on each candidate finding before
emitting it, dropping (or downgrading) failures in order:

1. **Citation in scope** — `file:line` is present; in diff modes it lands on a
   `+`/`-` line (context/unchanged lines are pre-existing → drop).
2. **Reachable / not already guarded** — read the caller; if the only caller
   already guards the case, drop or downgrade.
3. **Claimed-missing actually missing** — grep for the symbol under variant
   names before flagging "missing X".
4. **Not tooling-caught** — drop issues a linter/formatter/type-checker already
   surfaces. (Human-judgment Nit style/naming/cleanup that tooling does not
   catch remains reportable.)
5. **Assign confidence** — if any check above is shaky, drop the finding or emit
   it at **Low** confidence.

Where a dimension defines a named taxonomy (the per-agent files do), label the
finding with its taxonomy term.

## Verdict labels & mapping

- `/review-crew:review-code`: `READY FOR PR` / `FIX BEFORE PR` / `MAJOR FIXES NEEDED`
- `/review-crew:review-plan`: `PLAN READY` / `REVISE BEFORE IMPLEMENTING` / `MAJOR GAPS — RECONSIDER DESIGN`
- `/review-crew:audit-debt`: no single verdict — a prioritized backlog

Mapping (post-dedupe, post-filter counts):
- 0 Critical, 0 Important → READY / PLAN READY
- 0 Critical, ≥1 Important → FIX BEFORE PR / REVISE BEFORE IMPLEMENTING
- ≥1 Critical → MAJOR FIXES NEEDED / MAJOR GAPS
- Only Minor/Nit → READY / PLAN READY (informational)

## Where calibration comes from (read these, in order)

1. `CLAUDE.md` — project conventions (the primary, user-maintained source).
2. `.claude/review-profile.md` — the review profile: threat model, scope
   exclusions, verify command, focus hints, canonical patterns. It is an **adder**
   over `CLAUDE.md` (it only carries what `CLAUDE.md` doesn't cover).
3. If a needed field is absent in both → the STRICT fallback (see the
   "Calibration comes from the profile" section above).
