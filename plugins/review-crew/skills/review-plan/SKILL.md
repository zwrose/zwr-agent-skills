---
name: review-plan
description: Use when reviewing a draft implementation plan or design spec before any code is written.
user-invocable: true
---

# Review Plan

Run a multi-dimensional review on a draft plan or design spec **before any code is written**. The main context is an orchestrator: it locates the target plan, classifies what the design touches, dispatches the same four specialist agents `/review-crew:review-code` uses (architecture, code, security, test) in parallel against the plan doc instead of a diff, compiles their findings under the base rubric, attaches its own point of view to each finding, and revises the plan in place — auto-applying the mechanical fixes it recommends and stopping to ask only about findings it would skip/defer or fixes that involve a judgment call. This catches architecture pattern-fit issues, testing gaps, security implications of new data flows, and missing migration safety statements **before** they become rework.

This skill is a **companion to** superpowers' `writing-plans` skill — not a replacement. `writing-plans` helps you draft a plan; `/review-crew:review-plan` red-teams the draft. Read the base rubric (`${CLAUDE_PLUGIN_ROOT}/rubric/review-base.md`) for severity calibration and the verification rules every finding must pass; if anything below contradicts the base rubric, the base rubric wins.

Plan-time review is intentionally narrower than code-time review. The agents are told they are reading a draft — their job is to flag what the plan **omits** (missing test list, unspecified ownership/auth, unjustified new abstractions, no migration story, no mobile/responsive consideration when the project targets mobile) and what the plan **proposes that contradicts project patterns**, not to nitpick wording or pre-grade implementation details the plan reasonably defers.

## Invocation

| Form                              | Behavior                                                                                           |
| --------------------------------- | -------------------------------------------------------------------------------------------------- |
| `/review-crew:review-plan`        | Find the most recent file in `docs/superpowers/specs/` or `docs/superpowers/plans/` and review it. |
| `/review-crew:review-plan <path>` | Review the plan or spec at `<path>` (relative to repo root or absolute).                           |

If no spec/plan exists in either directory and no path was passed, ask the user for one via `AskUserQuestion` before continuing — there is nothing to review otherwise.

## Session Directory

All review artifacts live in a per-invocation temp directory so parallel reviews don't collide:

```bash
SESSION_DIR=$(mktemp -d /tmp/review-plan-XXXXXXXX)
```

| Path                                      | Written by   | Purpose                                                        |
| ----------------------------------------- | ------------ | -------------------------------------------------------------- |
| `$SESSION_DIR/meta.json`                  | orchestrator | Plan path, session dir, classification (what the plan touches) |
| `$SESSION_DIR/plan.md`                    | orchestrator | Stable copy of the target plan file — subagents read this      |
| `$SESSION_DIR/findings-architecture.json` | arch agent   | Architecture-reviewer findings array                           |
| `$SESSION_DIR/findings-code.json`         | code agent   | Code-reviewer findings array                                   |
| `$SESSION_DIR/findings-security.json`     | sec agent    | Security-reviewer findings array                               |
| `$SESSION_DIR/findings-test.json`         | test agent   | Test-reviewer findings array                                   |
| `$SESSION_DIR/compiled.json`              | orchestrator | Deduplicated, verified findings + summary + verdict            |

## Workflow

### 1. Setup

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

**Staleness self-check (first action).** Before the profile bootstrap and before locating the plan or dispatching anything, run the deterministic staleness/degraded self-check. It soft-fails (always exit 0) and **must never block the review** on drift — it only produces a non-blocking nudge surfaced at end of run. review-plan reads the working tree (default root), so no `--root` is passed. Run it only when a profile already resolved (`$EXISTS` is `true`) — a MISSING profile (`$LOCATION` is `none`) routes to the profile bootstrap below (which runs review-init/bootstrap), not to staleness:

```bash
if [ "$EXISTS" = "true" ]; then
  DOCTOR_JSON=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/repo_doctor.py" \
    "$PROFILE" "$PLUGIN_VERSION" "$RUBRIC_VERSION")
fi
```

Capture the JSON in `DOCTOR_JSON`. On `readable: false`, tell the user "profile unreadable — re-run `/review-crew:review-init`" and **continue** (do not crash, do not block). Otherwise retain `message`, `signal_hash`, and `nudge_acked` for the **end-of-run staleness nudge** (see §5's terminal summary). Do NOT act on `drift` here — it is informational only.

**Profile bootstrap (run before locating the plan or dispatching anything).** The review engine reads its per-project calibration (threat model, scope, focus hints, canonical patterns) from the resolved profile. If nothing resolved (`$LOCATION` is `none`), decide where to store it, create it, then write it:

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

Locate the target file:

```bash
if [ -n "$ARG_PATH" ]; then
  PLAN_PATH="$ARG_PATH"
else
  PLAN_PATH=$(ls -t docs/superpowers/specs/*.md docs/superpowers/plans/*.md 2>/dev/null | head -1)
fi
```

If `$PLAN_PATH` is empty or the file doesn't exist, use `AskUserQuestion` to ask the user for a path. Do not invent one.

Copy the plan to a stable artifact path and classify what it touches with simple, stack-neutral topic heuristics over the plan content:

```bash
cp "$PLAN_PATH" "$SESSION_DIR/plan.md"

TOUCHES=()
grep -Eqi 'route|endpoint|api|handler'                  "$SESSION_DIR/plan.md" && TOUCHES+=("API")
grep -Eqi 'component|view|page|screen|UI'               "$SESSION_DIR/plan.md" && TOUCHES+=("UI")
grep -Eqi 'schema|migration|database|collection|table|model' "$SESSION_DIR/plan.md" && TOUCHES+=("data")
grep -Eqi 'auth|session|permission|owner|tenant'        "$SESSION_DIR/plan.md" && TOUCHES+=("auth")
grep -Eqi 'test|spec|coverage'                          "$SESSION_DIR/plan.md" && TOUCHES+=("tests")
grep -Eqi 'architecture|layering|abstraction|module'    "$SESSION_DIR/plan.md" && TOUCHES+=("architecture")
```

Write metadata:

```bash
cat > "$SESSION_DIR/meta.json" <<EOF
{
  "planPath": "$PLAN_PATH",
  "sessionDir": "$SESSION_DIR",
  "touches": $(printf '%s\n' "${TOUCHES[@]}" | jq -R . | jq -sc .)
}
EOF
```

The classification is informational — it appears in the dispatch summary and is passed to subagents as context, but **all four specialists still run**. Coverage uniformity beats saving one agent dispatch; a "no data flow proposed" guess is exactly when a missing ownership check slips through.

### 2. Dispatch Summary

Print this dispatch summary as a plain status message, then dispatch the specialists immediately (no approval gate):

- **Plan file:** `$PLAN_PATH` and its line count (`wc -l < $SESSION_DIR/plan.md`)
- **Classification:** the `touches` array (e.g. `["API", "data", "auth"]`)
- **Specialists to dispatch (all four, in parallel):**
  - `architecture-reviewer` → `findings-architecture.json` _(does the heaviest lifting at plan time)_
  - `security-reviewer` → `findings-security.json`
  - `test-reviewer` → `findings-test.json`
  - `code-reviewer` → `findings-code.json` _(lighter at plan time)_
- **Session directory:** `$SESSION_DIR`

### 3. Dispatch Specialists in Parallel

Launch all four specialists in a **single message with four `Agent` tool calls** so they run in parallel, each dispatched by its `subagent_type` (the agent's name). Each gets the same prompt template, parameterized by `subagent_type`, dimension label, and findings filename. The agent's review methodology is its own system prompt — the prompt below is context-only (paths and rules); do **not** tell it to read an agent file. Embed the **absolute** base-rubric path (the expanded value of `RUBRIC`) so the subagent can read it. Substitute `<PROFILE_PATH>` with the resolved absolute `$PROFILE` when building each subagent prompt (subagents do not inherit shell vars):

```
You are reviewing a draft plan/spec document, NOT code.

## Your assignment
Review the plan at $SESSION_DIR/plan.md for your dimension. Read the base
rubric (absolute path below) for severity calibration, verification rules,
and the findings output format. Read the project profile and CLAUDE.md for
calibration (threat model, scope, focus hints, canonical patterns,
conventions).

## Context files
- Plan: $SESSION_DIR/plan.md
- Base rubric (severity, verification rules, findings format): <absolute RUBRIC path>
- Project profile (threat model, scope, focus hints, canonical patterns): <PROFILE_PATH>
- CLAUDE.md (project conventions): CLAUDE.md
- Project structure: feel free to Read/Grep/Glob the current repo for
  pattern verification (existing modules, conventions, neighbors).
- <if focus notes> Focus: <focus notes>

## Calibration precedence
Base rubric (binding) > CLAUDE.md (conventions) > profile (adder over CLAUDE.md)
> strict fallback when a needed field is absent in all of them.

## Plan-time framing
You are reviewing a DRAFT — it describes what WILL be built. Your job is
narrower than at code-review time:
- Architecture-reviewer: pattern fit (does this design fit existing
  patterns?), abstraction justification (is the proposed new util/hook/
  component duplicative of something that already exists?), module
  coupling implied by the design, complexity warnings derived from the
  shape of what's proposed.
- Security-reviewer: new user-data flows, auth changes, new API surface
  — are auth and ownership checks specified? Flag "we'll add validation
  later" red flags.
- Test-reviewer: what tests does this plan specify? What's missing? Are
  edge cases enumerated? Is the proposed code testable as designed?
- Code-reviewer (lighter at plan time): does the plan reference correct
  conventions? Does it propose anything that contradicts project rules
  (error constants, named exports, type-cast hygiene, etc.)? If UI work is
  involved, also flag the narrow self-usability cases per the base rubric (a
  control reachable by no available interaction, a focus bug that blocks a
  flow) — NOT general accessibility, unless the profile/CLAUDE.md scopes it in.

## Opinionated plan-content requirements (flag missing items)
- Explicit test list (not "we'll add tests")
- Explicit ownership / auth specification for new data flows
- Pattern-fit justification for proposed new abstractions
- Migration safety statement if schema changes are proposed
- Mobile / responsive (phone-width) behavior if UI work is involved AND the
  project targets mobile (per the profile / CLAUDE.md)

## Out of scope at plan time
- Naming preferences ("call it Foo not Bar")
- Implementation details the plan reasonably defers
- Style / convention checks that only matter at code time

## Verification rules
- `file:line` citation required. Cite the plan-doc heading + line
  number, OR cite related project files if the finding references
  existing code.
- Before flagging "missing X", grep the project for X under variant
  names. Don't flag a missing helper that already exists.
- Before flagging "new abstraction is unjustified", check whether the
  plan articulates why (a justification in the plan itself defuses the
  finding).

## Output
Write findings to $SESSION_DIR/findings-<agent>.json as a JSON array per
the base rubric's "Findings output format" section. The `file` field may be
either the plan path OR a related project file path. Set `dimension` to
"<dimension>" on every entry. If you have nothing to flag, write `[]` —
do not skip writing the file.
```

Per-agent substitutions:

| Agent slug / `subagent_type` | `<agent>` (findings filename) | `<dimension>` |
| ---------------------------- | ----------------------------- | ------------- |
| architecture-reviewer        | architecture                  | Architecture  |
| code-reviewer                | code                          | Code          |
| security-reviewer            | security                      | Security      |
| test-reviewer                | test                          | Test          |

After dispatch, wait for all four agents to return. Each writes its findings file to `$SESSION_DIR/`. The orchestrator does not read agent transcripts — only the JSON files.

### 4. Compile Findings (main context)

Read the four `$SESSION_DIR/findings-*.json` files. Apply, in order:

1. **Citation check.** Drop any finding with `file == null` or `line == null` — the base rubric's verification rules require a `file:line` citation.
2. **Dedupe by plan section + topic.** When two findings target the same plan section heading and same topic (e.g. both flagging "no test list"), merge them: concatenate bodies with a separator, keep the higher severity, list both dimensions (e.g. `"Test + Architecture"`).
3. **Nit cap.** If more than 5 Nits remain after dedupe, keep the first 5 and summarize the rest as a count (e.g. `"+ 8 more Nits — see $SESSION_DIR/findings-*.json"`).

Determine the verdict per the base rubric's "Verdict labels & mapping". For `/review-crew:review-plan` the labels are **PLAN READY** / **REVISE BEFORE IMPLEMENTING** / **MAJOR GAPS — RECONSIDER DESIGN**:

- 0 Critical, 0 Important → **PLAN READY**
- 0 Critical, 1+ Important → **REVISE BEFORE IMPLEMENTING**
- 1+ Critical → **MAJOR GAPS — RECONSIDER DESIGN**
- Only Minor and/or Nit → **PLAN READY** (Minor/Nit are informational)

Write to `$SESSION_DIR/compiled.json`:

```json
{
  "summary": "<1-2 sentence overall summary>",
  "verdict": "PLAN READY" | "REVISE BEFORE IMPLEMENTING" | "MAJOR GAPS — RECONSIDER DESIGN",
  "findings": [<deduplicated, verified findings array>]
}
```

Order findings: Critical → Important → Minor → Nit, then by `file` then by `line`.

### 5. Revise Loop

This skill **revises the plan in place** until it passes review. The deliverable is the improved plan document at `$PLAN_PATH`. Findings are **printed in chat each round — never written to a markdown file in the repo.** (The subagent JSON under `$SESSION_DIR` is internal plumbing and stays.)

Initialize `round = 1` and an empty `skip-set` (finding identities the user chose not to act on; identity = `plan-section::normalized-title`). If context was compacted mid-loop, re-read `$SESSION_DIR/meta.json` and the latest `$SESSION_DIR/compiled.json` to restore state, and re-derive the `skip-set` from your chat record.

Each round:

1. **Review.** (Round 1: the four specialists dispatched in §3 have already written `$SESSION_DIR/findings-*.json`.) For round > 1, re-dispatch the four specialists per §3 against the freshly-copied `$SESSION_DIR/plan.md`.
2. **Compile** per §4 into `$SESSION_DIR/compiled.json` with verdict.
3. **Effective findings** = `compiled.findings` whose identity is NOT in the `skip-set`.
4. **Form POV + classification for every effective finding.** Per the base rubric's "Orchestrator POV", from a targeted read of the cited plan section in `$SESSION_DIR/plan.md` (and any cited project file), emit for each finding a **recommendation** (`Fix` = revise the plan; `Defer` = real gap fine to nail down during implementation; `Skip` = not worth a plan change) + one-sentence rationale + High/Low confidence, and a **classification** (`mechanical` = one obvious plan edit, e.g. adding a named test to the test list; `judgment` = a real choice in wording or design among options).
5. **Print findings in chat** — grouped by plan section heading, each with its POV line (e.g. `→ POV: Defer (High confidence) — real gap, but fine to nail down the test names during implementation`). Do **not** write these to a file.
6. **Auto-revise.** For each effective finding where `recommendation == Fix` AND `classification == mechanical`, edit the plan document at `$PLAN_PATH` directly to address it (apply the finding's suggested replacement). Make these edits without asking.
7. **Interventions.** `present-set` = effective findings where `recommendation` is `Skip` or `Defer`, OR (`recommendation` is `Fix` AND `classification` is `judgment`). If non-empty, present ONE consolidated `AskUserQuestion`: lead with each finding's POV; offer **Apply as suggested** / **Apply with my guidance** (free text) / **Skip** in this neutral order. Apply the user's chosen revisions to `$PLAN_PATH`. Add every `Skip` identity to the `skip-set`.
   **Record decisions (learning loop):** append one `decisions.py` record per resolution to the resolved decisions store (`$DECISIONS`) (**Apply as suggested** → `fix`; **Apply with my guidance** → `guidance`; **Skip** → `skip`), per `## Learning Loop & Staleness Nudge`. Also append a `fix` record for each finding auto-revised in step 6. This append is non-blocking and never gates the loop.
8. **Refresh + exit check.** Re-copy the revised plan: `cp "$PLAN_PATH" "$SESSION_DIR/plan.md"`. If any edits were made this round AND one or more Critical/Important findings remain that are not in the `skip-set` AND `round < 7`, set `round += 1` and repeat from step 1 (re-review the revised plan). Otherwise **EXIT** — but if the loop is exiting because it hit the **7-round cap** with Critical/Important findings still unresolved, `log` that the cap was reached and report those remaining findings explicitly; do **not** declare PLAN READY in that case (coverage may be incomplete, mirroring audit-debt's cap at `audit-debt/SKILL.md`).

After exit, print a terminal summary in chat:

- Lead with the final verdict label in bold. If the loop exited because it hit the 7-round cap with one or more Critical/Important findings still unresolved (and not in the `skip-set`), the verdict is **REVISE** — do **not** declare PLAN READY.
- List, grouped by plan section heading, the revisions applied (auto + user-approved) and the findings the user chose to skip — each with its POV line.
- End with a count summary (e.g. `"2 auto-revised, 1 applied with guidance, 1 skipped; final verdict PLAN READY"`). If the cap was hit, note it explicitly: e.g. `"7-round cap reached; N Critical/Important findings unresolved — see above"`.

**Then, after the terminal summary**, run the three non-blocking end-of-run steps from `## Learning Loop & Staleness Nudge`, in order: (1) the **staleness nudge** (print the doctor `message` only when non-null and `nudge_acked` is false), (2) the **learning-loop proposal** (`decisions.py analyze` → at most one user-gated `AskUserQuestion`, never auto-applied), then (3) the **provisional-profile confirmation** (interactive only — offer to confirm a `status: provisional` profile; skipped when headless, already stable, or already acked). All three are placed after the review output and none blocks.

Nothing else is written to the repo — the revised `$PLAN_PATH` is the deliverable (plus the project-level `.claude/review-decisions.json` learning-loop store and, only on a dismissal, the profile's `nudge-ack` map).

## Learning Loop & Staleness Nudge

These four behaviors are **non-blocking**, run **at end of run** (after the terminal summary), and are **identical across `review-code`, `review-plan`, and `audit-debt`**. Nothing here ever auto-applies a profile or `CLAUDE.md` edit — every change is user-gated.

### Recording decisions (at resolution time)

Wherever the user resolves a finding (this skill: the §5 step 7 interventions, plus the auto-revised findings in step 6), append ONE record per decision to the **project-level** learning-loop store at the resolved `$DECISIONS` path (NOT the temp `$SESSION_DIR`). Use the bundled helper:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/lib/decisions.py" \
  append "$DECISIONS" '<record-json>'
```

`<record-json>` is `{"dimension": "<finding dimension>", "category": "<finding taxonomy/topic>", "action": "skip"|"guidance"|"fix"}`:
- `action` maps from the user's choice: **Skip** → `skip`; **Apply with my guidance** → `guidance`; **Apply as suggested** (and step-6 auto-revises) → `fix`.
- `dimension` is the finding's `dimension`; `category` is the finding's taxonomy/topic (its normalized title or topic tag). The store is append-only and atomic; it soft-fails on a bad/missing store, so this never blocks.

### Staleness nudge (end of run)

Using the `DOCTOR_JSON` captured in Setup: print the doctor's `message` as a single non-blocking line **only when** `message` is non-null AND `nudge_acked` is false:

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

## Plan-Content Requirements (Opinionated)

Agents flag missing items in this list — the plan author should be able to point to each one in the plan, or explicitly note "not applicable":

- **Explicit test list** — what tests will be written, named at the test-case level. "We'll add tests" is not acceptable.
- **Ownership / auth specification** — for every new data flow or API route, the plan names which session field scopes the data and which checks the route performs (e.g. an owner/tenant filter on all reads, an admin check for admin-only paths).
- **Pattern-fit justification** — for every proposed new abstraction (util, hook, component, module), the plan articulates why it isn't a duplicate of an existing module and where the second caller will be.
- **Migration safety statement** — if the plan changes a schema, it names the migration strategy (backfill script, defaulted field, dual-write window) and the rollback plan.
- **Mobile / responsive behavior** — if the plan introduces UI and the project targets mobile (per the profile / CLAUDE.md), it names how the UI behaves on a phone-width viewport. General accessibility is out of scope unless the profile/CLAUDE.md scopes it in; only the narrow self-usability breakage cases in the base rubric apply.

## Out of Scope at Plan Time

These are out of scope; agents are told not to flag them in plan-time framing:

- **Naming preferences** — "call it `Foo` not `Bar`" is bikeshedding at plan time. Names can be revised when the code lands.
- **Implementation details the plan reasonably defers** — a plan that says "the route does the filter; details in code" is fine. Plans are not pseudocode.
- **Style / convention checks that only matter at code time** — linters, formatters, and type-checkers all fire on the eventual code. No need to pre-grade.

## Common Mistakes

| Mistake                                                                     | Fix                                                                                                                                                             |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Flagging implementation details at plan time                                | Those are code-time concerns. The plan is allowed to defer "how" as long as "what" and "why" are clear.                                                         |
| Citing line numbers from the wrong file                                     | Plan-doc citations point at `$SESSION_DIR/plan.md`. Project-file citations point at repo paths. Don't mix them up — readers can't follow the trail.             |
| Not classifying what the plan touches                                       | Skipping the `touches` classification leads to spurious findings (e.g. UI findings on a backend-only plan). Run the regex heuristics every time.                |
| Re-running and re-raising the same findings without consulting prior rounds | Check your `skip-set` and the chat record of prior rounds before raising a finding. Authors shouldn't see the same finding twice without a new technical basis. |
| Treating "we'll add tests" as acceptable                                    | Plans must enumerate the test list. "We'll add tests" is a Critical or Important miss depending on what the plan touches.                                       |
| Skipping the all-four specialists rule based on classification              | The `touches` array is informational. All four agents always run — each returns `[]` when there's nothing in its dimension, which is cheap and uniform.         |
| Dispatching reviewers by reading an agent file                              | The four reviewers are bundled plugin agents — dispatch each by its `subagent_type` (its name). The methodology is the agent's own system prompt.               |
| Skipping the profile bootstrap                                              | If `.claude/review-profile.md` is absent, run review-init's create procedure inline first. Headless runs get a provisional strict profile.                      |
