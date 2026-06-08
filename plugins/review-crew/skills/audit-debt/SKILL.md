---
name: audit-debt
description: Use when periodically sweeping a whole repository for accumulated technical, security, and architectural debt (for example a monthly audit), rather than reviewing a single change.
user-invocable: true
---

# Audit Debt

Periodic full-repo sweep for accumulated technical, security, and architectural debt. The main context is an orchestrator: it gathers sweep-prep artifacts (an ecosystem-aware dependency audit, a TODO census, a file list, recent dependency churn), dispatches the same four specialist agents `/review-crew:review-code` uses in **sweep mode** (no diff scope) in parallel, computes three additional dimensions itself (dependency staleness/vulns, TODO/FIXME accumulation, documentation drift), loops the sweep until it stops surfacing new blocking debt, compiles the results into a backlog sorted by severity × inverse-effort, attaches its own point of view to each Critical/Important finding, and consolidates the findings into a proposed set of GitHub issues to file. It **never edits code** — its only output is the report and the issues.

This skill is **not a sibling of `/review-crew:review-code`**. `/review-crew:review-code` finds bugs in new code; `/review-crew:audit-debt` finds bugs in old code that has rotted. The diff-scope rule does NOT apply — every line in the project's source is in scope. The trade-off is that it is **slow and thorough by design** — meant to be run occasionally (suggest monthly), not before every PR. Running it weekly will drown you in nits you have already triaged; running it never will let real debt accumulate to "rewrite this feature" levels.

Read the base rubric (`${CLAUDE_PLUGIN_ROOT}/rubric/review-base.md`) first for the severity rubric, verification rules, findings schema, triage, POV, and verdict mapping — those are not restated here. The base rubric's tier definitions get a debt-context recalibration in §Severity Recalibration for Debt Context below; if anything in this skill contradicts the base rubric, the base rubric wins.

## Invocation

| Form                       | Behavior                                                                                                                                                                                                                                                       |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/review-crew:audit-debt`  | Sweep the whole repo. No flags. Repeats the specialist sweep until it stops surfacing new blocking debt (hard cap 7 rounds), then consolidates findings across all tiers into a proposed set of GitHub issues and offers to file them and/or save the report.  |

## Session Directory

All audit artifacts live in a per-invocation temp directory so parallel runs don't collide:

```bash
SESSION_DIR=$(mktemp -d /tmp/audit-debt-XXXXXXXX)
```

| Path                                      | Written by   | Purpose                                                                            |
| ----------------------------------------- | ------------ | ---------------------------------------------------------------------------------- |
| `$SESSION_DIR/meta.json`                  | orchestrator | Repo, branch, head SHA, session dir, file count, ecosystem                         |
| `$SESSION_DIR/sweep-prep/`                | orchestrator | Directory of prep artifacts (dependency audit, TODO census, file list, dep churn, CLAUDE.md/profile claims) |
| `$SESSION_DIR/findings-architecture.json` | arch agent   | Architecture-reviewer findings array                                               |
| `$SESSION_DIR/findings-code.json`         | code agent   | Code-reviewer findings array                                                       |
| `$SESSION_DIR/findings-security.json`     | sec agent    | Security-reviewer findings array                                                   |
| `$SESSION_DIR/findings-test.json`         | test agent   | Test-reviewer findings array                                                       |
| `$SESSION_DIR/round-<N>/findings-*.json`  | agents       | Specialist findings for discovery-loop round N (round 1 uses the flat paths above) |
| `$SESSION_DIR/all-findings.json`          | orchestrator | Accumulated specialist findings across all discovery-loop rounds                   |
| `$SESSION_DIR/orchestrator-findings.json` | orchestrator | Deps + TODO accumulation + doc-drift findings                                      |
| `$SESSION_DIR/compiled.json`              | orchestrator | Sorted, prioritized backlog + totals + summary                                     |
| `$SESSION_DIR/report.md`                  | orchestrator | Final markdown report (optionally saved by user)                                   |

## Workflow

### 1. Sweep Prep

**Resolve the base rubric path once.** The base rubric is bundled at `${CLAUDE_PLUGIN_ROOT}/rubric/review-base.md`. Capture the rubric path so it can be embedded — **expanded to an absolute path** — into subagent prompts (subagents may not inherit `${CLAUDE_PLUGIN_ROOT}`):

```bash
RUBRIC="${CLAUDE_PLUGIN_ROOT}/rubric/review-base.md"   # absolute; embed the expanded value in subagent prompts
```

**Resolve the profile and decisions paths once (resolver-driven).** The profile/decisions may live in-repo (`./.claude/`) or in the global per-repo store; `review_store.py resolve` returns the resolved path (or `location: none` when nothing exists yet). Capture `$PROFILE`, `$LOCATION`, `$EXISTS`, and `$DECISIONS` here, before the staleness self-check and profile bootstrap below use them:

```bash
RES=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" resolve --kind profile) \
  || { echo "review_store resolve failed — continuing with strict fallback"; RES='{"location":"none","exists":false,"path":null}'; }
PROFILE=$(printf '%s' "$RES" | jq -r '.path // empty')
LOCATION=$(printf '%s' "$RES" | jq -r .location)
EXISTS=$(printf '%s' "$RES" | jq -r .exists)
DRES=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" resolve --kind decisions) \
  || { echo "review_store resolve --kind decisions failed"; DRES='{"path":null}'; }
DECISIONS=$(printf '%s' "$DRES" | jq -r '.path // empty')
```

Also resolve the engine versions the staleness self-check (next) needs — the **plugin version** from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json` (`version`) and the **rubric-version** from the first line of `$RUBRIC` (`<!-- rubric-version: N -->`):

```bash
PLUGIN_VERSION=$(python3 -c "import json;print(json.load(open('${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json'))['version'])")
RUBRIC_VERSION=$(sed -n 's/.*rubric-version: *\([0-9][0-9]*\).*/\1/p' "$RUBRIC" | head -1)
```

**Staleness self-check (first action).** Before the profile bootstrap and before generating artifacts or dispatching anything, run the deterministic staleness/degraded self-check. It soft-fails (always exit 0) and **must never block the sweep** on drift — it only produces a non-blocking nudge surfaced at end of run. audit-debt sweeps the working tree (default root), so no `--root` is passed. Run it only when a profile already resolved (`$EXISTS` is `true`) — a MISSING profile (`$LOCATION` is `none`) routes to the profile bootstrap below (which runs review-init/bootstrap), not to staleness:

```bash
if [ "$EXISTS" = "true" ]; then
  DOCTOR_JSON=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/repo_doctor.py" \
    "$PROFILE" "$PLUGIN_VERSION" "$RUBRIC_VERSION")
fi
```

Capture the JSON in `DOCTOR_JSON`. On `readable: false`, tell the user "profile unreadable — re-run `/review-crew:review-init`" and **continue** (do not crash, do not block). Otherwise retain `message`, `signal_hash`, and `nudge_acked` for the **end-of-run staleness nudge** (see §5's end). Do NOT act on `drift` here — it is informational only.

**Profile bootstrap (run before generating artifacts or dispatching anything).** The review engine reads its per-project calibration (threat model, verify command, scope, focus hints, canonical patterns) from the resolved profile. If nothing resolved (`$LOCATION` is `none`), decide where to store it, create it, then write it:

```bash
if [ "$LOCATION" = "none" ]; then
  INTERACTIVE=true   # the orchestrator sets this to false on a headless/non-interactive run (no human to answer), so decide-location returns "global" deterministically instead of "ask"
  LOC=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" decide-location --interactive "$INTERACTIVE")
  # If LOC is "ask", STOP — present the in-repo-vs-global AskUserQuestion, set LOC, then run the create calls below.
  PROFILE=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" create --kind profile --location "$LOC")
  DECISIONS=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" create --kind decisions --location "$LOC")
fi
```

When `decide-location` returns `ask`, present the in-repo-vs-global `AskUserQuestion` (per the spec's *Halt-and-ask init flow*) and use the answer as `$LOC`.

When `$LOCATION` is `none`, run review-init's create procedure inline (`plugins/review-crew/skills/review-init/SKILL.md`, Steps 1–4: detect → interview → seed canonical patterns → write the profile to `$PROFILE`), then continue. Headless / non-interactive runs get a provisional, strict-threat-model profile from detected defaults. (Do not run any staleness, reconcile, or learning-loop step here — out of scope.)

**Detect the ecosystem.** Mirror review-init's detection: read the profile's `signals` (`dep-set`, `default-branch`, `forge`) and `## Verify` block if present, and detect the manifest/lockfile the same way review-init Step 1 does. The ecosystem drives the dependency audit, the source-dir census, and the dependency-churn pass below.

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
# Recognized manifests (first match wins for the deps audit tool)
ls "$ROOT"/package.json "$ROOT"/pyproject.toml "$ROOT"/requirements.txt "$ROOT"/Cargo.toml "$ROOT"/go.mod 2>/dev/null
# Source dirs: prefer the profile's src-dirs; else detect top-level source dirs; else repo root
ls -d "$ROOT"/src "$ROOT"/lib "$ROOT"/app 2>/dev/null
```

Set `SRC_DIRS` to the profile's recorded source dirs if present, else the detected top-level source dirs (e.g. `src`), else fall back to the repo root (`.`).

Generate the artifacts every specialist (and the orchestrator) will read:

```bash
mkdir -p "$SESSION_DIR/sweep-prep"
```

**Ecosystem-aware dependency audit (CVEs, advisories).** Detect the ecosystem and run the matching audit tool *only if it is available* on the PATH. If no recognized manifest exists, or the audit tool is absent, **skip the deps dimension, write no audit artifact, and `log` that it was skipped** — the §4 deps pass no-ops gracefully when the artifact is absent:

```bash
if [ -f "$ROOT/package.json" ]; then
  ECOSYSTEM=node
  npm audit --json > "$SESSION_DIR/sweep-prep/deps-audit.json" 2>&1 || true
elif [ -f "$ROOT/pyproject.toml" ] || [ -f "$ROOT/requirements.txt" ]; then
  ECOSYSTEM=python
  command -v pip-audit >/dev/null && pip-audit -f json > "$SESSION_DIR/sweep-prep/deps-audit.json" 2>&1 || true
elif [ -f "$ROOT/Cargo.toml" ]; then
  ECOSYSTEM=rust
  command -v cargo-audit >/dev/null && cargo audit --json > "$SESSION_DIR/sweep-prep/deps-audit.json" 2>&1 || true
elif [ -f "$ROOT/go.mod" ]; then
  ECOSYSTEM=go
  command -v govulncheck >/dev/null && govulncheck ./... > "$SESSION_DIR/sweep-prep/deps-audit.txt" 2>&1 || true
fi
# ECOSYSTEM is unset when no recognized manifest exists; the deps dimension is then
# skipped (no artifact written) — `log` it.
```

**TODO/FIXME/XXX/HACK census** — language-agnostic, over the profile's source dirs (fallback: repo root):

```bash
rg "TODO|FIXME|XXX|HACK" -n $SRC_DIRS > "$SESSION_DIR/sweep-prep/todos.txt" 2>&1 || true
```

**Full file list for sweep dispatch** — stack-neutral census via `git ls-files`:

```bash
git ls-files -- $SRC_DIRS > "$SESSION_DIR/sweep-prep/files.txt" 2>/dev/null || git ls-files > "$SESSION_DIR/sweep-prep/files.txt"
```

**Recently churned dependencies** (last 90 days) — used to flag low-trust recent adds. **JS-ecosystem-specific**; for non-JS ecosystems run the analogous manifest churn if cheap, else skip and `log`:

```bash
if [ -f "$ROOT/package.json" ]; then
  git log --since="90 days ago" --pretty=format: --name-only -- package.json | sort -u > "$SESSION_DIR/sweep-prep/dep-changes.txt"
fi
```

Then read `CLAUDE.md` and the resolved profile (`$PROFILE`) and extract specific factual claims the orchestrator will later verify in §4 (e.g., named module paths under the project's source dirs, a claimed canonical accessor or error-constants location, an `auth` strategy claim). Save the extracted claims to `$SESSION_DIR/sweep-prep/claude-md-claims.txt` so the doc-drift checks in §4 don't re-read the whole file.

Write metadata:

```bash
FILE_COUNT=$(wc -l < "$SESSION_DIR/sweep-prep/files.txt")
HEAD_SHA=$(git rev-parse HEAD)
BRANCH=$(git rev-parse --abbrev-ref HEAD)
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo "local")

cat > "$SESSION_DIR/meta.json" <<EOF
{
  "repo": "$REPO",
  "branch": "$BRANCH",
  "headSha": "$HEAD_SHA",
  "sessionDir": "$SESSION_DIR",
  "fileCount": $FILE_COUNT,
  "ecosystem": "${ECOSYSTEM:-unknown}"
}
EOF
```

### 2. Dispatch Summary

Print this dispatch summary as a plain status message, then dispatch the specialists immediately (no approval gate):

- **Skill:** `audit-debt`
- **Scope:** the whole repo — `$FILE_COUNT` files under the project's source dirs
- **Specialists to dispatch (all four, in parallel):**
  - `architecture-reviewer` → `findings-architecture.json`
  - `code-reviewer` → `findings-code.json`
  - `security-reviewer` → `findings-security.json`
  - `test-reviewer` → `findings-test.json`
- **Orchestrator-driven dimensions (run in parallel with specialists):** dependency staleness + vulnerabilities, TODO/FIXME accumulation, documentation drift
- **Session directory:** `$SESSION_DIR`
- **Note:** this is a slow run by design — expect ~minutes, not seconds.

### 3. Dispatch Specialists in Parallel

Launch all four specialists in a **single message with four `Agent` tool calls** so they run in parallel, each dispatched by its `subagent_type` (the agent's name). Each gets the same sweep-mode prompt template, parameterized by `subagent_type`, dimension label, and findings filename. The agent's review methodology is its own system prompt — the prompt below is context-only (paths and rules); do **not** tell it to read an agent file. Embed the **absolute** base-rubric path (the expanded value of `RUBRIC`) so the subagent can read it. Substitute `<PROFILE_PATH>` with the resolved absolute `$PROFILE` when building each subagent prompt (subagents do not inherit shell vars):

```
You are sweeping the codebase for accumulated debt, NOT reviewing a diff.

## Your assignment
Sweep the entire codebase under the project's source dirs (and other paths as
relevant) for accumulated issues in your dimension. Read the base rubric
(absolute path below) for severity calibration, verification rules, and the
findings output format. Read the project profile and CLAUDE.md for calibration
(threat model, scope, focus hints, canonical patterns, conventions). The
diff-scope rule does NOT apply — you are auditing existing code.

## Context files
- File list: $SESSION_DIR/sweep-prep/files.txt
- Base rubric (severity, verification rules, findings format): <absolute RUBRIC path>
- Project profile (threat model, scope, focus hints, canonical patterns): <PROFILE_PATH>
- CLAUDE.md (project conventions): CLAUDE.md
- Findings output path: $SESSION_DIR/findings-<agent>.json
- <if focus notes> Focus: <focus notes>

## Calibration precedence
Base rubric (binding) > CLAUDE.md (conventions) > profile (adder over CLAUDE.md)
> strict fallback when a needed field is absent in all of them.

## Sweep-mode framing
The diff-scope rule does NOT apply — you are auditing existing code. But the
"Do NOT Flag" list in the base rubric and your dimension's methodology STILL
applies. Pattern violations that are *consistent across the codebase* are
pre-existing convention, not debt — consistency > novelty.

## Per-agent focus at audit time
- architecture-reviewer: layering violations (e.g., data access invoked
  directly from a presentation-layer file), file-size monsters (>500 lines),
  abstraction creep (3+ duplicate patterns without a shared util, OR utils that
  are used in only 1 place), cross-feature import smells.
- code-reviewer: convention drift (docs/profile say X, code does Y), pattern
  inconsistency (some routes use the project's error constants, others hardcode),
  naming / convention drift, missing import aliases, dead exports. Also the
  narrow self-usability cases per the base rubric (a control reachable by no
  available interaction, a focus bug that blocks a flow, text too low-contrast
  to read) — NOT general accessibility, unless the profile/CLAUDE.md scopes it in.
- security-reviewer: missing ownership filters in EXISTING routes (high-priority
  sweep — apply the ownership/IDOR methodology to every route that touches an
  owner-scoped resource), missing input/id validation checks, share/invite paths
  without the canonical auth verification.
- test-reviewer: untested files (especially API routes and shared utility
  modules — flag what does NOT have a matching test), low-coverage code paths,
  weak assertions (tests that pass without exercising behavior).

## Effort estimate (REQUIRED on every finding)
For each finding, estimate effort:
- "Quick" (<30 min): rename, hardcoded → constant, dependency-audit auto-fix
- "Medium" (30 min – 4 hours): refactor a unit, add tests for a route, fix an IDOR
- "Big-job" (multi-session): restructure a feature, migrate a data store, replace a dependency

## Output
Write findings to $SESSION_DIR/findings-<agent>.json as a JSON array per the
base rubric's "Findings output format" section, with an additional "effort"
field: "Quick" | "Medium" | "Big-job". Set `dimension` to "<dimension>" on
every entry. If you have nothing to flag, write `[]` — do not skip writing the
file.
```

Per-agent substitutions:

| Agent slug / `subagent_type` | `<agent>` (findings filename) | `<dimension>` |
| ---------------------------- | ----------------------------- | ------------- |
| architecture-reviewer        | architecture                  | Architecture  |
| code-reviewer                | code                          | Code          |
| security-reviewer            | security                      | Security      |
| test-reviewer                | test                          | Test          |

After dispatch, wait for all four agents to return. Each writes its findings file to `$SESSION_DIR/`. The orchestrator does not read agent transcripts — only the JSON files.

### 4. Orchestrator-Driven Dimensions (main context, in parallel with §3)

While the specialists run, the main context computes three additional dimensions itself. These don't need a subagent — they're rule-based passes over the sweep-prep artifacts.

**Dependency staleness + vulnerabilities.** If no dependency-audit artifact was written in §1 (no recognized manifest, or the audit tool was absent), **skip this dimension entirely** — emit no deps findings and rely on the §1 `log` that recorded the skip.

- Parse the dependency-audit artifact from `$SESSION_DIR/sweep-prep/` (`deps-audit.json` or `deps-audit.txt`, whichever the ecosystem produced). For each advisory, emit a finding with severity mapped from advisory severity: `critical` / `high` → Critical / Important; `moderate` → Minor; `low` → Nit. Include the dep name, advisory URL, and recommended fix (the audit tool's auto-fix command, or the version range). Effort: `Quick` for auto-fixable, `Medium` if a major bump is required, `Big-job` for breaking changes that require code edits.
- **(JS ecosystem only.)** For deps listed in `$SESSION_DIR/sweep-prep/dep-changes.txt` (recently added in the last 90 days): note any with low weekly download counts (`npm view <name> --json` and inspect `dist-tags`/`time`; if available, use `npm view <name> downloads`). Flag as Minor "recently added, low-trust dep" with effort `Quick`. For non-JS ecosystems, run the analogous low-trust-dep check if cheap, else skip and `log`.
- **(JS ecosystem only.)** Scan `package.json` for `dependencies` or `devDependencies` entries whose source declares a `postinstall` or `preinstall` script (`npm view <name> scripts`). Flag as Minor "supply-chain risk vector — review what this script does" with effort `Quick`. For non-JS ecosystems, skip this sub-bullet and `log`.

**TODO/FIXME accumulation.**

- Read `$SESSION_DIR/sweep-prep/todos.txt`. Count total. For the 5 oldest, find the introducing commit with `git log --reverse --pretty=format:"%ai %H %s" -S "TODO" -- <file>` (heuristic — the `-S` flag picks the commit that added the token). Emit a **single** finding: `"Accumulated <N> TODO/FIXME markers across the codebase; oldest dated <YYYY-MM-DD> in <file>."` Severity: Minor if N < 20, Important if N >= 20. Effort: `Medium` (triage + close).

**Documentation drift.**

- Read `$SESSION_DIR/sweep-prep/claude-md-claims.txt`. For each path/file reference in `CLAUDE.md` (and the profile): verify it exists on disk. Missing references → Minor "CLAUDE.md drift: references nonexistent `<path>`" with effort `Quick`.
- Repeat for any `docs/*.md` files the project keeps (e.g. `docs/architecture.md`, `docs/api-patterns.md`, `docs/setup.md`, `docs/testing.md`, `docs/product.md`) if they exist. Same check — every file path mentioned in prose should resolve.
- **The profile's verify command resolves.** Read the `## Verify` block from the resolved profile (`$PROFILE`): if `command:` is set, confirm its binary/command is runnable on the PATH (missing → Minor "verify command `<cmd>` does not resolve" with effort `Quick`). If `mode: unverified` or `mode: review-only`, there is no command to check — skip this check.

Write all orchestrator-derived findings to `$SESSION_DIR/orchestrator-findings.json` using the same schema as the agent findings (including the `effort` field). Use ids like `orchestrator-deps-001`, `orchestrator-todo-001`, `orchestrator-docs-001` so they don't collide with subagent ids.

### 5. Compile + Prioritize + Present

**Discovery loop (specialists only — no code is ever edited).** A single stochastic sweep misses findings, so repeat the specialist sweep until it stops surfacing new blocking debt. Sweep-prep (§1) and the orchestrator-derived dimensions (§4) are deterministic — compute them once, not per round.

Initialize `round = 1` and an empty `seen` set (finding identities = `file::normalized-title`). Each round:

**Loop step 1:** (Round 1: the specialists dispatched in §3 have already written `$SESSION_DIR/findings-*.json`.) For round > 1, re-dispatch the four specialists per §3 into `$SESSION_DIR/round-<round>/findings-*.json`.

**Loop step 2:** Read this round's specialist findings. Compute `new-blocking` = findings with severity Critical or Important whose identity is not already in `seen`. Add every finding's identity to `seen` and accumulate every finding into the running pool `$SESSION_DIR/all-findings.json`.

**Loop step 3:** If `new-blocking` is empty → **stop looping**. Else if `round == 7` → **stop looping** and `log` that the 7-round cap was reached (coverage may be incomplete — note it in the report). Else `round += 1` and repeat.

Then merge the accumulated specialist pool with `orchestrator-findings.json` and continue with the consolidation below.

After the discovery loop exits, read the accumulated specialist pool (`$SESSION_DIR/all-findings.json`) together with `orchestrator-findings.json`. Apply:

1. **Citation check.** Drop any finding with `file == null` or `line == null` (per the base rubric's verification rules — citations are required even in sweep mode). Exception: the single aggregate "Accumulated TODO/FIXME markers" finding may cite the oldest file/line.
2. **Existence check.** For every cited file, confirm it exists. Subagents occasionally hallucinate file paths under a sweep — drop findings whose `file` does not resolve on disk.
3. **Dedupe by `(file, line)`.** Merge bodies with a separator, keep the higher severity, list both dimensions.
4. **Nit cap.** Keep at most 5 Nits in the presentation; replace the rest with a single summary count (per the base rubric's severity caps).
5. **Orchestrator POV (Critical/Important only).** Per the base rubric's "Orchestrator POV", for each Critical/Important finding that survives the above, open the cited file at the cited line and form a **Fix / Skip / Defer + one-sentence rationale + High/Low confidence** take. In a debt sweep, "Defer" is common and legitimate — a Big-job finding is usually real-but-schedule-it, not fix-now. "Skip" means the debt isn't worth paying down (consistent-by-convention, or cost > benefit per the profile's threat model/scope). Attach `recommendation`, `rationale`, and `confidence` to each such finding in `compiled.json`. Skip the POV for Minor/Nit — the backlog stays lean.

Then **sort by severity × inverse-effort** (high-impact + low-effort first) into these presentation buckets:

1. Critical (any effort)
2. Important + Quick
3. Important + Medium
4. Important + Big-job
5. Minor + Quick
6. Minor + Medium or Big-job
7. Nit (cap at 5 + count of rest)

Write `$SESSION_DIR/compiled.json`:

```json
{
  "summary": "<1 paragraph: N findings across <C> categories; top concerns are X, Y, Z>",
  "totals": { "Critical": N, "Important": N, "Minor": N, "Nit": N },
  "findings": [<sorted array, in bucket order above; Critical/Important entries carry recommendation, rationale, confidence>]
}
```

Render `$SESSION_DIR/report.md`: a markdown report grouped by category (Architecture / Code / Security / Test / Dependencies / TODOs / Docs), with the priority order above applied within each category. Under each Critical/Important finding, render its **POV** on its own line (e.g. `→ POV: Defer (High confidence) — real debt, but a multi-session restructure; schedule it, don't fix it now`). Print the report to the terminal.

**Consolidate into proposed GitHub issues.** `audit-debt` never edits code — its output is a backlog. (The forge is GitHub; the profile records the forge.) Roll the surviving findings **across all tiers, including Minor and Nit** into a proposed set of issues:

- **Critical / Important:** apply the review gate — auto-include findings with `recommendation` of `Fix` or `Defer` (filing an issue _is_ deferring real debt to a backlog), and ask the user only about `Skip`/borderline ones via `AskUserQuestion` (lead with the POV; **File** / **Drop**).
- **Minor / Nit:** these carry no POV; include them by default.

**Record decisions (learning loop).** This issue-gate is audit-debt's resolution point: append one `decisions.py` record per finding decided here to the resolved decisions store (`$DECISIONS`), per `## Learning Loop & Staleness Nudge`. Map the action: a finding **filed** as an issue (auto-included `Fix`/`Defer`, or **File** on a gated one) → `fix`; a **Drop** / deselected finding → `skip`. (`guidance` does not arise in audit-debt — it files or drops, it never edits code.) This append is non-blocking and never gates the sweep.
- **Do not mix tiers within a single issue.** A Critical/Important finding gets its own issue (or is grouped only with closely-related same-tier findings). Minor/Nit findings are consolidated into their own separate lower-tier issue(s) — never folded into a higher-tier issue.

Present the proposed issue set in chat (title + tier + the findings each issue covers). Then `AskUserQuestion`: _"File these as GitHub issues?"_ Options:

- **Yes, file all** — run `gh issue create` for each proposed issue.
- **Let me deselect some** — present the proposed issues multi-select, then file the kept ones.
- **No** — skip.

Issue title format: `"<severity>: <finding title>"` (for a multi-finding lower-tier issue, a summary title like `"Nit: 5 convention nits across src/"`). Body: each finding's text + `file:line` + suggestion + effort estimate, then `_Surfaced by /review-crew:audit-debt on <date>_`. The POV guides filing decisions; it is not written into the issue body.

**Optionally save the report.** `AskUserQuestion`: _"Save this report to a file?"_ Options:

- **Yes, default location** — `cp "$SESSION_DIR/report.md" "docs/debt-audit-$(date +%Y-%m-%d).md"`
- **Yes, custom path** — prompt for the path, then `cp`.
- **No** — skip.

**Then, after the report-save offer**, run the three non-blocking end-of-run steps from `## Learning Loop & Staleness Nudge`, in order: (1) the **staleness nudge** (print the doctor `message` only when non-null and `nudge_acked` is false), (2) the **learning-loop proposal** (`decisions.py analyze` → at most one user-gated `AskUserQuestion`, never auto-applied), then (3) the **provisional-profile confirmation** (interactive only — offer to confirm a `status: provisional` profile; skipped when headless, already stable, or already acked). All three are placed after the audit output and none blocks.

End of skill — no code edits, no commits, no posting to PRs, no further checks (beyond the non-blocking end-of-run learning-loop/staleness steps above, which write only the project-level `.claude/review-decisions.json` store and — only on a dismissal — the profile's `nudge-ack` map).

## Learning Loop & Staleness Nudge

These four behaviors are **non-blocking**, run **at end of run** (after the report-save offer), and are **identical across `review-code`, `review-plan`, and `audit-debt`**. Nothing here ever auto-applies a profile or `CLAUDE.md` edit — every change is user-gated.

### Recording decisions (at resolution time)

audit-debt's resolution point is the §5 issue-gate (File / Drop, and the auto-included Fix/Defer). Append ONE record per decision to the **project-level** learning-loop store at the resolved `$DECISIONS` path (NOT the temp `$SESSION_DIR`). Use the bundled helper:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/lib/decisions.py" \
  append "$DECISIONS" '<record-json>'
```

`<record-json>` is `{"dimension": "<finding dimension>", "category": "<finding taxonomy/topic>", "action": "skip"|"guidance"|"fix"}`:
- `action` maps from the issue-gate decision: **filed** (auto-included `Fix`/`Defer`, or **File**) → `fix`; **Drop**/deselected → `skip`. `guidance` does not arise here (audit-debt files or drops; it never edits code).
- `dimension` is the finding's `dimension`; `category` is the finding's taxonomy/topic (its normalized title or topic tag). The store is append-only and atomic; it soft-fails on a bad/missing store, so this never blocks.

### Staleness nudge (end of run)

Using the `DOCTOR_JSON` captured in §1: print the doctor's `message` as a single non-blocking line **only when** `message` is non-null AND `nudge_acked` is false:

> ℹ️ Profile may be stale: `<message>`. Run `/review-crew:review-init` to refresh (this nudge won't repeat once acknowledged).

If the user declines or ignores it, record the dismissal (see "Recording a dismissal" below) using the doctor's `signal_hash`. Suppress the line entirely when `nudge_acked` is true or `message` is null.

### Learning-loop proposal (end of run)

After the staleness nudge, analyze the decision store for a repeated signal:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/lib/decisions.py" \
  analyze "$DECISIONS" --nudge-ack <comma-separated profile nudge-ack hashes>
```

Pass the profile's current `nudge-ack` map keys (read from the resolved profile (`$PROFILE`)'s provenance block) as the comma-separated `--nudge-ack` list so an already-dismissed proposal does not re-fire. If the result's `proposal` is non-null, present it via **ONE** `AskUserQuestion` (lead with `proposal.text`; the proposal names a `target` of `profile` or `CLAUDE.md`):
- **Apply to `<target>`** — apply the proposed calibration/convention edit to the named target.
- **Edit then apply** — open a free-text edit, then apply the edited version.
- **Dismiss** — do not apply; record the dismissal using `proposal.signal_hash` (see below).

**NEVER auto-apply.** A proposal is applied ONLY on the user's explicit **Apply** / **Edit then apply** choice. If `proposal` is null, do nothing.

### Provisional-profile confirmation (interactive only, end of run)

If the loaded profile's `status:` is `provisional` AND this run is interactive (a human is present to answer) AND the provisional-confirm signal is not already in the profile's `nudge-ack`, offer ONE non-blocking `AskUserQuestion` after the review output:

> This project's review profile was auto-generated (provisional) and hasn't been confirmed. Confirm it now?

- **Confirm (mark stable)** — flip the profile's provenance `status: provisional` → `status: stable` in the resolved profile (`$PROFILE`) (a small, user-approved provenance write; bump `updated:`). Nothing else changes.
- **Refresh via review-init** — point the user at `/review-crew:review-init` (its reconcile re-detects + can flip status) and do not change the profile now.
- **Keep provisional** — record a dismissal (see "Recording a dismissal") using the constant provisional-confirm signal hash so this does not re-ask until the profile changes.

Skip this entirely when the run is **headless/non-interactive** (no human to answer — never block an automated run), when `status:` is already `stable`, or when the provisional-confirm signal is already acknowledged. This is the spec's "next interactive review offers to confirm a provisional profile" behavior; it never auto-flips without the user's choice.

### Recording a dismissal (shared)

The staleness nudge (above), the learning-loop proposal, and the provisional-profile confirmation share one dismissal mechanism: **write the relevant `signal_hash` into the profile's `nudge-ack` map** in the resolved profile (`$PROFILE`)'s provenance block, so the same signal does not re-fire until it changes. The map is `nudge-ack: {<hash>: true, ...}` on the provenance line; add the hash as a new key (the staleness nudge uses `DOCTOR_JSON.signal_hash`; the proposal uses `proposal.signal_hash`; the provisional-profile confirmation's **Keep provisional** uses a **constant signal** — the literal `provisional-confirm` — so that one suppresses re-asking until the profile itself changes, since a reconcile/regenerate that flips or refreshes `status` clears or supersedes it). This is the ONLY write any of these nudges makes to the profile, and only on dismissal — it is not a calibration edit.

## Severity Recalibration for Debt Context

Restated for this skill (the base rubric's table is calibrated for diff review; debt review needs slightly different anchors):

| Tier          | Definition (debt context)                                                                                                     | Examples                                                                                                                                    |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Critical**  | Active security risk in shipped code — exploitable today, not "theoretical if X."                                             | An API route returning owner-scoped data without an ownership filter; a mutation without an ownership check; a missing admin gate on an admin-only route |
| **Important** | Bug waiting to happen (will trigger under normal use) OR significant architecture violation that is making future work harder | A handler that throws on malformed input and surfaces as a 500; a 900-line unit with 8 responsibilities; an untested route handler          |
| **Minor**     | Real issue, small impact — consistency, missing test on a low-risk path, small refactor, minor abstraction creep              | A magic number; one route hardcodes error strings while others use the project's error constants; a util used by only one caller            |
| **Nit**       | Cleanup / naming / dead code that doesn't change behavior or risk                                                             | An unused export; inconsistent comment style; an outdated TODO that's no longer relevant                                                    |

Apply this rubric in §5 compile + prioritization. The diff-scope tier (`Pre-existing`) does **not** apply in debt mode — pre-existing IS the point.

## Effort Labels

Required on every finding. Subagents are told to emit this in §3; orchestrator-derived findings include it in §4.

| Label       | Range                | Examples                                                                                                                                             |
| ----------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Quick**   | <30 minutes          | Rename a misleading variable; replace a hardcoded string with an error constant; swap a hardcoded value for a shared token; a dependency-audit auto-fix |
| **Medium**  | 30 minutes – 4 hours | Refactor a unit to remove a duplicate pattern; write tests for an existing API route; fix an IDOR with a dual-filter; close a TODO with a small impl |
| **Big-job** | Multi-session        | Restructure a feature directory; migrate a data-store schema; replace a transitive dependency; rewrite a 900-line unit                              |

The `severity × inverse-effort` sort means an `Important + Quick` finding ranks above an `Important + Big-job`. Big-jobs aren't deprioritized because they don't matter — they're presented later because they need scheduling, not a same-day fix.

## Common Mistakes

| Mistake                                                            | Fix                                                                                                                                                                        |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Flagging pre-existing code as "debt" when it's working as designed | Debt = rotted, not just unfamiliar. If the pattern is intentional and consistent across the codebase, it's convention, not debt.                                           |
| Over-counting Nits and burying the real findings                   | Nit cap (5) applies the same as in `/review-crew:review-code`. A Nit avalanche in a debt audit is signal that the auditor is reaching — dedupe or drop.                    |
| Missing the difference between "could improve" and "is broken"     | `could improve` is Minor at best; only flag Important if you can name what will actually break. Critical is reserved for active security risk in shipped code.             |
| Citing files that don't exist                                      | `§5 step 2` (existence check) drops these at compile time. Subagents under sweep dispatch sometimes hallucinate paths — the orchestrator catches it.                       |
| Treating consistent patterns as drift                              | Consistency > novelty. If 12 of 13 routes use the same pattern, the 13th matching is **consistency**, not debt. If 6 use pattern A and 7 use pattern B, THAT is drift.     |
| Mapping every dependency-audit advisory to Critical                | Advisory severity is a hint, not a verdict — `moderate` maps to Minor in this skill. If the vulnerable code path isn't reachable in our usage, the advisory is even lower. |
| Running this before every PR                                       | This skill is slow and broad by design. Run it monthly. For PR review, use `/review-crew:review-code`.                                                                     |
| Treating the GitHub-issue offer as automatic                       | Every issue created is a chore for the author. Review the proposed issue set before filing — use "Let me deselect some" or "No" to trim it down.                           |
| Running a deps pass when no audit tool ran                         | The deps audit is ecosystem-aware and skips gracefully (no manifest, or tool absent). If §1 wrote no audit artifact, emit no deps findings — don't invent advisories.     |
| Dispatching reviewers by reading an agent file                     | The four reviewers are bundled plugin agents — dispatch each by its `subagent_type` (its name). The methodology is the agent's own system prompt.                          |
| Skipping the profile bootstrap                                     | If `.claude/review-profile.md` is absent, run review-init's create procedure inline first. Headless runs get a provisional strict profile.                                 |
