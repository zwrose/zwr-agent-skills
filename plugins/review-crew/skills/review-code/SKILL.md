---
name: review-code
description: Use when reviewing code changes on a local branch or an open pull request before merging — including when you want the review's findings auto-fixed locally or posted to GitHub.
user-invocable: true
---

# Review Code

Run a multi-dimensional code review on either an open pull request or a local branch (vs the default branch), then **autonomously fix what it finds**. The main context is an **orchestrator** — it fetches metadata, dispatches four specialist agents in parallel, compiles their findings, triages each into auto-fixable vs needs-your-judgment, applies fixes via a fixer subagent, and re-reviews — looping until no Critical/Important findings remain or a circuit breaker halts. It never loads the full diff or any agent's raw output into its own conversation; subagents do all heavy reading and write structured results to disk.

The skill auto-detects whether you're reviewing a PR or a local branch, always dispatches the full set of specialists (architecture, code, security, test) so coverage is uniform across reviews, enforces the severity and verification rules in the base rubric at compile time (not just by hope), and — by default — drives an auto-fix loop that commits fixes locally (never pushes). Two read-only behaviors are preserved as flags.

There are three top-level paths, chosen at invocation:

- **`--post`** → one review pass, then read-only GitHub posting (push approved findings to GitHub through `resolve_diff_lines.py` so out-of-hunk anchors never trigger 422 errors). Never touches the working tree.
- **`--review-only`** → one review pass, then a read-only interactive terminal presentation. No commits.
- **otherwise (default)** → the auto-fix loop: review → triage → fix → re-review, committing locally until clean or halted.

The four specialist agents are bundled plugin agents (`architecture-reviewer`, `code-reviewer`, `security-reviewer`, `test-reviewer`); the orchestrator dispatches each by name via the Agent tool. Each agent's review methodology lives in its own system prompt; the orchestrator's dispatch passes it the base rubric (severity/verification/format), the project profile (`.claude/review-profile.md`), `CLAUDE.md`, the diff, and the findings output path. Every finding they emit must cite a `file:line` and target a `+`/`-` line in the diff — context-line and unchanged-code findings are dropped at compile time. Each specialist runs once per round; the orchestrator does not chain a "verifier agent" or run a specialist twice within a round, because multi-turn agentic review within a single pass degrades F1 and fabricates findings as real ones get exhausted (base rubric, "In-pass verification & single-pass discipline"). The loop re-reviews from scratch each round on a fresh diff, which is different from re-running a specialist on its own output.

## Invocation

| Form                                       | Behavior                                                                                                                                                              |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/review-crew:review-code`                 | **Auto-fix loop (default).** Review → triage → fix → re-review until no Critical/Important findings remain, or a halt condition fires. Commits locally; never pushes. |
| `/review-crew:review-code --review-only`   | One review pass, interactive tiered presentation, no commits.                                                                                                         |
| `/review-crew:review-code pr <N> --post`   | One review pass, read-only, post inline findings to GitHub. Never touches the tree.                                                                                   |
| `/review-crew:review-code branch` / `pr <N>` | Force branch or PR mode; still runs the auto-fix loop unless combined with `--review-only`/`--post`.                                                                |
| `/review-crew:review-code --focus <notes>` | Pass focus notes to every specialist. Combinable with any form.                                                                                                       |

The three top-level paths: `--post` → read-only GitHub posting; `--review-only` → read-only terminal presentation; otherwise → auto-fix loop.

**Auto-detection rule.** Run `gh pr list --head "$(git rev-parse --abbrev-ref HEAD)" --json number,headRefOid,headRefName --limit 1`. If the result is non-empty, default to PR mode. Otherwise default to branch mode. If the user passed `branch` explicitly, skip the lookup. If the user passed `pr <N>` explicitly, use `<N>` and don't auto-detect.

**`--post` only applies to PR mode.** If the user passes `--post` without a PR (and auto-detection finds none), stop and tell them — branch mode has nothing to post against.

## Session Directory

All review artifacts live in a per-invocation temp directory so parallel reviews don't collide:

```bash
SESSION_DIR=$(mktemp -d /tmp/review-XXXXXXXX)
```

Files written during the review. **Per-round artifacts live under `$SESSION_DIR/round-<N>/`** in the auto-fix loop (round 1, 2, …); the read-only paths (`--review-only`, `--post`) run a single pass and write that pass's artifacts under `round-1/` as well. Only `meta.json` lives at the session-dir root.

| Path                                                | Written by     | Purpose                                                                                     |
| --------------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------- |
| `$SESSION_DIR/meta.json`                            | orchestrator   | Mode, PR number (if any), repo, branch, head SHA, base ref, verify story, focus notes       |
| `$SESSION_DIR/repo/`                                | orchestrator   | `--post`/`--review-only` PR paths only: detached `git worktree` at the PR head SHA          |
| `$SESSION_DIR/prior-comments.json`                  | orchestrator   | PR-mode only: prior review comments + threads (for author justifications)                   |
| `$SESSION_DIR/round-<N>/diff.txt`                   | orchestrator   | Round `<N>` unified diff (`git diff <baseRef>...HEAD`). **Never read by the main context.** |
| `$SESSION_DIR/round-<N>/findings-architecture.json` | arch agent     | Architecture-reviewer findings array                                                        |
| `$SESSION_DIR/round-<N>/findings-code.json`         | code agent     | Code-reviewer findings array                                                                |
| `$SESSION_DIR/round-<N>/findings-security.json`     | sec agent      | Security-reviewer findings array                                                            |
| `$SESSION_DIR/round-<N>/findings-test.json`         | test agent     | Test-reviewer findings array                                                                |
| `$SESSION_DIR/round-<N>/compiled.json`              | orchestrator   | Deduplicated, verified findings + summary + verdict (read by `circuit_breaker.py`)          |
| `$SESSION_DIR/round-<N>/triage.json`                | triage agent   | Per-finding `mechanical`/`judgment` classification + POV for every finding (loop only)      |
| `$SESSION_DIR/round-<N>/resolutions.json`           | orchestrator   | User decisions on `present-set` findings (loop only; read by `circuit_breaker.py`)          |
| `$SESSION_DIR/round-<N>/fix-batch.json`             | orchestrator   | Findings handed to the fixer this round (loop only)                                         |
| `$SESSION_DIR/round-<N>/review.json`                | orchestrator   | `--post` only: review body + approved comments (pre-resolve)                                |
| `$SESSION_DIR/round-<N>/review-resolved.json`       | resolve script | `--post` only: comments after line-anchor resolution                                        |

**CRITICAL:** The main context only ever runs `wc -l < $SESSION_DIR/round-<N>/diff.txt` to size the diff. It never `cat`s the diff, never reads the full thing, never echoes it back. Subagents read the diff from disk and write structured findings; the orchestrator reads the findings JSON, not the diff.

## Workflow

### 1. Setup

Decide mode (auto-detected or explicit, per `## Invocation`). Create the session directory.

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

**Staleness self-check (first action).** Before the profile bootstrap and before dispatching anything, run the deterministic staleness/degraded self-check. It soft-fails (always exit 0) and **must never block the review** on drift — it only produces a non-blocking nudge surfaced at end of run. The root depends on the path: `--post` reads the PR-head worktree (`--root "$SESSION_DIR/repo"`), while branch/default paths read the working tree (default root, `.`). Run it only when a profile already resolved (`$EXISTS` is `true`) — a MISSING profile (`$LOCATION` is `none`) routes to the profile bootstrap below (which runs review-init/bootstrap), not to staleness:

```bash
if [ "$EXISTS" = "true" ]; then
  # --post path: --root "$SESSION_DIR/repo" (PR-head worktree). branch/default: omit --root (working tree).
  DOCTOR_JSON=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/repo_doctor.py" \
    "$PROFILE" "$PLUGIN_VERSION" "$RUBRIC_VERSION" ${DOCTOR_ROOT_ARG})
fi
```

(`DOCTOR_ROOT_ARG` is `--root "$SESSION_DIR/repo"` on the `--post` path once the detached worktree exists — run the check after the worktree is created in PR `--post` setup — and empty otherwise.) Capture the JSON in `DOCTOR_JSON`. On `readable: false`, tell the user "profile unreadable — re-run `/review-crew:review-init`" and **continue** (do not crash, do not block). Otherwise retain `message`, `signal_hash`, and `nudge_acked` for the **end-of-run staleness nudge** (see End-of-Loop Summary / Read-Only Paths). Do NOT act on `drift` here — it is informational only.

**Profile bootstrap (run before dispatching anything).** The review engine reads its per-project calibration (threat model, verify command, scope, focus hints, canonical patterns) from the resolved profile. If nothing resolved (`$LOCATION` is `none`), decide where to store it, create it, then write it:

```bash
if [ "$LOCATION" = "none" ]; then
  # Decide location: env override > ask (interactive) > global (headless).
  INTERACTIVE=true   # the orchestrator sets this to false on a headless/non-interactive run (no human to answer), so decide-location returns "global" deterministically instead of "ask"
  LOC=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" decide-location --interactive "$INTERACTIVE")
  # If LOC is "ask", present the in-repo vs global AskUserQuestion now and set LOC.
  PROFILE=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" create --kind profile --location "$LOC")
  DECISIONS=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" create --kind decisions --location "$LOC")
  # Then run review-init's create procedure inline, writing the profile to $PROFILE.
fi
```

When `decide-location` returns `ask`, present the in-repo-vs-global `AskUserQuestion` (per the spec's *Halt-and-ask init flow*) and use the answer as `$LOC`.

When `$LOCATION` is `none`, run review-init's create procedure inline (`plugins/review-crew/skills/review-init/SKILL.md`, Steps 1–4: detect → interview → seed canonical patterns → write the profile to `$PROFILE`), then continue. Headless / non-interactive runs get a provisional, strict-threat-model profile from detected defaults. (Do not run any staleness, reconcile, or learning-loop step here — out of scope.)

**Read the verify story from the resolved profile** (the `## Verify` section of `$PROFILE`). This sets `VERIFY_CMD` for the orchestrator's verify gate and the fixer (see `## The verify command` below):

- `command: <cmd>` present → `VERIFY_CMD="<cmd>"`.
- `mode: unverified` → no verify command; the verify gate is skipped and commits proceed ungated.
- `mode: review-only` → the project opted out of auto-fix; the default path degrades to a single review pass + the `--review-only` presentation.

**PR mode:**

```bash
# Resolve PR number — either provided or auto-detected from current branch
BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ -z "$PR_NUMBER" ]; then
  PR_NUMBER=$(gh pr list --head "$BRANCH" --json number --jq '.[0].number')
fi

# Metadata: small JSON only — do NOT load the diff yet
gh pr view "$PR_NUMBER" --json number,title,author,headRefName,headRefOid,baseRefName,url > "$SESSION_DIR/pr.json"
HEAD_SHA=$(jq -r .headRefOid "$SESSION_DIR/pr.json")
PR_BRANCH=$(jq -r .headRefName "$SESSION_DIR/pr.json")
BASE_REF=$(jq -r .baseRefName "$SESSION_DIR/pr.json")   # PR base branch — used as the diff base
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)

# Prior review comments — used for author-justification handling
gh api "repos/$REPO/pulls/$PR_NUMBER/comments" \
  --jq '[.[] | {id, in_reply_to_id, path, line, position, body, user: .user.login}]' \
  > "$SESSION_DIR/prior-comments.json"

# Read-only paths ONLY (--post / --review-only): a detached worktree at the PR head
# gives subagents a clean source of truth to verify against. NOT used on the
# auto-fix path — that path edits and commits on the current branch directly.
git fetch origin "$PR_BRANCH"
git worktree add --detach "$SESSION_DIR/repo" "$HEAD_SHA"   # --post / --review-only ONLY
```

**Auto-fix branch guard (PR mode, default loop only).** Before entering the loop, the orchestrator must be standing on the PR's own branch so fix commits land where they belong:

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "$PR_BRANCH" ]; then
  echo "Auto-fix needs PR branch '$PR_BRANCH' checked out (currently on '$CURRENT_BRANCH')."
  echo "Check out the branch, or re-run with --post (read-only GitHub) or --review-only (read-only terminal)."
  exit 1
fi
```

If the guard fails (detached HEAD, or you're reviewing someone else's PR), STOP — do not create the detached worktree and do not enter the loop. Tell the user to use `--post` or `--review-only`. The detached `git worktree add --detach` step above is for the `--post`/`--review-only` PR paths ONLY, never for the auto-fix path.

**Branch mode:**

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD)
HEAD_SHA=$(git rev-parse HEAD)
BASE_REF=$(git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo main)   # branch mode diffs against the default branch
REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo "local")

# No worktree, no prior comments — subagents verify against the current working tree
```

**Per-round diff is ALWAYS local.** Do NOT use `gh pr diff` to fetch the diff. Each round computes the diff locally from `<baseRef>` (PR mode: the PR's `baseRefName`; branch mode: the default branch), because rounds 2+ have local fix commits that are not on the remote — `gh pr diff` would miss them. The per-round command (run inside the loop, see `## Auto-Fix Loop`) is:

```bash
git diff "$BASE_REF"...HEAD > "$SESSION_DIR/round-<round>/diff.txt"
```

The read-only paths run a single pass and compute the same local diff into `round-1/diff.txt`.

Then write `meta.json` in both modes:

```bash
cat > "$SESSION_DIR/meta.json" <<EOF
{
  "mode": "${MODE}",
  "path": "${REVIEW_PATH}",
  "pr": ${PR_NUMBER:-null},
  "repo": "${REPO}",
  "branch": "${BRANCH}",
  "headSha": "${HEAD_SHA}",
  "baseRef": "${BASE_REF}",
  "sessionDir": "${SESSION_DIR}",
  "verify": "${VERIFY_CMD:-unverified}",
  "focusNotes": ${FOCUS_JSON:-null}
}
EOF
```

`REVIEW_PATH` is `loop` (default), `review-only`, or `post`, decided from the flags at invocation. It is written to `meta.json` so a cold-resumed orchestrator (after compaction) knows which top-level flow to continue. The `verify` field records the verify command string, or `"unverified"` / `"review-only"`, so a cold-resumed orchestrator recovers the verify story.

Size the round-1 diff for the dispatch summary (after writing it to `round-1/diff.txt` per the command above):

```bash
DIFF_LINES=$(wc -l < "$SESSION_DIR/round-1/diff.txt")
```

**CRITICAL:** Do not `cat`, `head`, `tail`, or otherwise read any `diff.txt` from the main context. The line count is the only thing the orchestrator needs to know about its contents.

### 2. Dispatch Summary

Print this dispatch summary as a plain status message, then dispatch the specialists immediately (no approval gate):

- **Skill:** `review-code`
- **Mode:** PR or branch
- **Target:** `PR #<N> "<title>"` (PR mode) or `<branch> vs <baseRef>` (branch mode)
- **Repo:** `<owner>/<repo>`
- **Head SHA:** short hash
- **Diff size:** `<DIFF_LINES>` lines
- **Verify:** `VERIFY_CMD` (the command string), or `unverified` (no gate), or `review-only` (auto-fix disabled — this run degrades to a single pass + presentation)
- **Specialists to dispatch (all four, in parallel):**
  - `architecture-reviewer` → `findings-architecture.json`
  - `code-reviewer` → `findings-code.json`
  - `security-reviewer` → `findings-security.json`
  - `test-reviewer` → `findings-test.json`
- **Session directory:** `$SESSION_DIR` (round 1 artifacts under `round-1/`)
- **Focus notes:** the `--focus` argument, if any
- **Path:** default → auto-fix loop (compile + dedupe → triage → fix → re-review, committing locally); `--review-only` → one pass + interactive presentation; `--post` → one pass + post to GitHub
- **What happens after dispatch (default loop):** compile + dedupe → triage → user interventions on judgment calls → fixer subagent commits → verify gate (`VERIFY_CMD`, unless `unverified`) → circuit-breaker → re-review or exit

Do **not** tier or skip specialists based on which files changed. Coverage uniformity matters more than saving an agent dispatch — a "no security-relevant files changed" guess is exactly when an IDOR slips through. All four always run. The agents themselves return an empty findings array when there's nothing in their dimension, which is cheap.

### 3. Dispatch Specialists in Parallel

Launch all four specialists in a **single message with four `Agent` tool calls** so they run in parallel, each dispatched by its `subagent_type` (the agent's name). Each gets the same prompt template, parameterized by `subagent_type`, dimension label, and findings filename. The agent's review methodology is its own system prompt — the prompt below is context-only (paths and rules); do **not** tell it to read an agent file. Embed the **absolute** base-rubric path (the expanded value of `RUBRIC`) so the subagent can read it. Substitute `<PROFILE_PATH>` with the resolved absolute `$PROFILE` when building each subagent prompt (subagents do not inherit shell vars):

```
You are reviewing <mode> for repo <repo>, target <pr-or-branch>.

## Your assignment
Review the diff at $SESSION_DIR/round-<round>/diff.txt for your dimension.
Read the base rubric (absolute path below) for severity calibration,
verification rules, and the findings output format. Read the project profile
and CLAUDE.md for calibration (threat model, scope, focus hints, canonical
patterns, conventions). Apply the diff-scope rule: only flag code in `+` or
`-` lines.

## Context files
- Diff: $SESSION_DIR/round-<round>/diff.txt
- Base rubric (severity, verification rules, findings format): <absolute RUBRIC path>
- Project profile (threat model, scope, focus hints, canonical patterns): <PROFILE_PATH>
- CLAUDE.md (project conventions): CLAUDE.md
- <PR read-only paths only> PR branch checkout: $SESSION_DIR/repo/
- <PR mode only> Prior comments + author justifications: $SESSION_DIR/prior-comments.json
- <if focus notes> Focus: <focus notes>

## Calibration precedence
Base rubric (binding) > CLAUDE.md (conventions) > profile (adder over CLAUDE.md)
> strict fallback when a needed field is absent in all of them.

## PR branch checkout (--post / --review-only PR paths only)
On the read-only PR paths the PR branch is checked out at $SESSION_DIR/repo/.
This is the ONLY source of truth for verifying code. Use Read, Grep, and Glob
against this directory, NOT the main repo working directory — it may be on a
different branch with stale or missing code. (On the auto-fix loop there is no
detached checkout: the PR branch IS the current working tree, so verify against
the working tree directly.)

## Diff-scope rule — CRITICAL
You are reviewing CHANGES MADE BY THIS PR/BRANCH. Do NOT flag pre-existing
issues. Only flag code in `+` or `-` lines of the diff. Context lines
(no prefix) and unchanged code in modified files are pre-existing — SKIP
them, even if they violate conventions. That's the #1 source of false
findings.

## Verification rules
- `file:line` citation required. No citation → drop your own finding
  before writing it out.
- Before flagging "missing X", grep the codebase (PR checkout, in PR mode)
  for X under different names. Don't flag a missing helper that exists
  under a slightly different name.
- For Important findings, check callers / reachability before asserting.
  If the only caller already guards the edge case, downgrade or drop.
- For docs/spec changes, spot-check factual claims (function signatures,
  error types, file paths) against actual source.

## Author-justification rule (PR mode only)
$SESSION_DIR/prior-comments.json contains prior review comments and their
threads. If a previous review flagged a finding and the author replied
with substantive explanatory text (not just "ok" or an emoji) explaining
why it's intentional, do NOT re-raise the same finding unless the
justification contains a technical error. Outdated comments (where
`position == null`) still count — the explanation may apply even if the
code anchor moved.

## Output
Write findings to $SESSION_DIR/round-<round>/findings-<agent>.json as a JSON
array per the base rubric's "Findings output format" section. Set `tradeoff:
true` only when a finding has multiple valid fix approaches (a judgment call);
omit it otherwise (see the base rubric's "Triage rubric"). Set `dimension` to
"<dimension>" on every entry. Severity caps from the base rubric apply (Nits at
most 5 reported per agent). If you have nothing to flag, write an empty array
(`[]`) — do not skip writing the file.
```

Per-agent substitutions:

| Agent slug / `subagent_type` | `<agent>` (findings filename) | `<dimension>` |
| ---------------------------- | ----------------------------- | ------------- |
| architecture-reviewer        | architecture                  | Architecture  |
| code-reviewer                | code                          | Code          |
| security-reviewer            | security                      | Security      |
| test-reviewer                | test                          | Test          |

After dispatch, wait for all four agents to return. Each writes its findings file to `$SESSION_DIR/round-<round>/`. The orchestrator does not read agent transcripts — only the JSON files.

### 4. Compile + Dedupe (main context)

Read the four `$SESSION_DIR/round-<round>/findings-*.json` files. Apply, in order:

1. **Citation check.** Drop any finding with `file == null` or `line == null` — the base rubric's verification rules require a `file:line` citation.
2. **Diff-scope verification.** Parse `$SESSION_DIR/round-<round>/diff.txt` to identify, for each file, the set of line numbers on `+` or `-` lines (the same hunk-walking logic `resolve_diff_lines.py` uses). Drop findings whose `(file, line)` pair isn't in that set. This is the same rule the subagents are supposed to enforce — duplicating it at compile time catches the cases they slip up on, especially context-line flags.
3. **Reachability pre-check on Important findings.** For each remaining `severity == "Important"` finding, open the cited file (in `$SESSION_DIR/repo/` for the read-only PR paths, working tree otherwise), find the call sites of the affected symbol, and confirm the edge case is reachable. **When in doubt, downgrade to Minor rather than drop** — the user can still see and approve it, but it isn't blocking the verdict.
4. **Dedupe by `(file, line)`.** When two findings target the same `(file, line)`, merge them: concatenate bodies with a separator, keep the higher severity, and list both dimensions (e.g. `"Security + Code"`). The merged finding **keeps the higher-severity input's `title`** (ties → the earlier one in dimension order Architecture, Code, Security, Test), so the finding identity (`file::normalized-title`) is deterministic round-to-round — the circuit breaker's recurrence check depends on a stable title. The merged finding is **`tradeoff: true` if either input is** (a judgment call in one facet makes the whole finding a judgment call). This also prevents the visual clutter of two GitHub comments on the same line.
5. **Author-justification filter (PR mode).** Cross-reference `prior-comments.json`. If a prior comment thread on the same `(file, line)` (or with the same finding topic on an outdated anchor) shows a substantive author justification, drop the new finding unless its body identifies a technical error in the justification.
6. **Nit cap.** After dedupe, if more than 5 Nits remain, keep the first 5 and replace the rest with a single summary entry like `"+ 12 more Nits — see $SESSION_DIR/round-<round>/findings-*.json for details"` (the base rubric's severity caps).

Determine the verdict per the base rubric's "Verdict labels & mapping" (count post-dedupe, post-filter findings). For `/review-crew:review-code` the labels are **READY FOR PR** / **FIX BEFORE PR** / **MAJOR FIXES NEEDED**:

- 0 Critical, 0 Important → **READY FOR PR**
- 0 Critical, 1+ Important → **FIX BEFORE PR**
- 1+ Critical → **MAJOR FIXES NEEDED**
- Only Minor and/or Nit → **READY FOR PR** (Minor/Nit are informational)

Write the result to `$SESSION_DIR/round-<round>/compiled.json` (preserve each finding's `tradeoff` field through dedupe so triage can read it):

```json
{
  "summary": "<1-2 sentence overall summary>",
  "verdict": "READY FOR PR" | "FIX BEFORE PR" | "MAJOR FIXES NEEDED",
  "findings": [<deduplicated, verified findings array>]
}
```

Order findings: Critical → Important → Minor → Nit, then by file path, then by line.

## Auto-Fix Loop (default path)

Runs when neither `--post` nor `--review-only` is set, and the profile's verify story is not `mode: review-only` (a `review-only` profile degrades the default path to a single review pass + the `--review-only` presentation — see `## The verify command`). The orchestrator keeps a **skip-set** of finding identities the user chose to skip (identity = `file::normalized-title`, matching `circuit_breaker.py`). Initialize `round = 1`, `skip-set = {}`.

**If context was compacted mid-loop**, re-read `$SESSION_DIR/meta.json` (its `path` field says whether the loop, `--review-only`, or `--post` is active; its `verify` field restores the verify story), the highest-numbered `round-N/` files, and every `round-*/resolutions.json` (to rebuild the skip-set, and the **skipped-blocking** set from each entry's `severity`). Then resume mid-round by inspecting which `round-<highest>/` artifacts already exist:

| Present in `round-<N>/`                                       | Resume at                            |
| ------------------------------------------------------------- | ------------------------------------ |
| no `compiled.json`                                            | step 1 (restart the round)           |
| `compiled.json`, no `triage.json`                             | step 6                               |
| `triage.json`, no `fix-batch.json`                            | step 7                               |
| `fix-batch.json`, no `Auto-fix round <N>` commit in `git log` | step 11 (re-dispatch the fixer)      |
| `Auto-fix round <N>` commit present                           | step 12 (re-run verify, then breaker) |

Each round:

1. `mkdir -p $SESSION_DIR/round-<round>`. Regenerate the diff locally: `git diff <baseRef>...HEAD > $SESSION_DIR/round-<round>/diff.txt`. Size it with `wc -l` only — never `cat` it.
2. **Review.** Dispatch the four specialists in parallel by `subagent_type` (same prompt template as `## Dispatch Specialists in Parallel`), writing `round-<round>/findings-<agent>.json`. Point them at `round-<round>/diff.txt`.
3. **Compile + dedupe** into `round-<round>/compiled.json` with verdict (same pipeline as `## Compile + Dedupe`).
4. **Effective findings** = `compiled.findings` whose identity is NOT in the skip-set.
5. If `effective` is empty → **EXIT SUCCESS** (jump to End-of-Loop Summary).
6. **Triage.** Dispatch the triage subagent (template below) over `effective`, writing `round-<round>/triage.json`.
7. **Interventions.** `present-set` = effective findings where `recommendation` is `Skip` or `Defer`, OR (`recommendation` is `Fix` AND `classification` is `judgment`). These are the only findings with a genuine decision left for the user; everything the agent recommends fixing mechanically is handled in step 8 without asking.
   - If non-empty: present ONE consolidated `AskUserQuestion`. For each finding, **lead with the orchestrator POV** from `triage.json` (per the base rubric's "Orchestrator POV") — show the recommendation, rationale, and confidence right under the finding, e.g. `→ POV: Skip (Low confidence) — correct in theory but this path is never hit concurrently under the profile's threat model`. Then offer **Fix as suggested** / **Fix with my guidance** (free text) / **Skip** — keep the options in this neutral order regardless of the POV; the POV informs, it does not pre-select. List the auto-fix findings (`recommendation` Fix AND `classification` mechanical) in the same prompt as an FYI (no per-item action; they are fixed automatically). Write `round-<round>/resolutions.json`:
     ```json
     { "round": <N>, "resolutions": [
       { "id": "<finding id>", "file": "<file>", "title": "<title>", "severity": "<severity>", "action": "fix" | "fix-with-guidance" | "skip", "guidance": "<text or omitted>" }
     ] }
     ```
     Add every `skip` identity to the skip-set; if a skipped finding is Critical or Important, also remember it as a **skipped-blocking** finding (its `severity` is recorded in `resolutions.json` so this survives compaction). `approved` = `present-set` entries with action `fix`/`fix-with-guidance` (carry `guidance`).
     **Record decisions (learning loop):** after writing `resolutions.json`, append one `decisions.py` record per resolution to the resolved decisions store (`$DECISIONS`) (`action`: `skip` → `skip`, `fix-with-guidance` → `guidance`, `fix` → `fix`), per `## Learning Loop & Staleness Nudge` → "Recording decisions". Also append a `fix` record for each `auto-fix-set` finding fixed silently this round. This append is non-blocking and never gates the loop.
   - If empty: `approved = []`, write no resolutions file.
8. **Fix batch.** `auto-fix-set` = effective findings where `recommendation` is `Fix` AND `classification` is `mechanical`. `fix-batch` = `auto-fix-set ∪ approved`. Write `round-<round>/fix-batch.json` (full finding objects; attach `userGuidance` to any with guidance).
9. **Blocking-to-fix** = count of `fix-batch` findings with severity Critical or Important.
10. If `fix-batch` is empty (everything this round was skipped) → **EXIT** with a "remaining findings deliberately skipped" note (list them).
11. **Fix.** Dispatch the fixer subagent (template below) with `fix-batch.json`.
    - Status `CHECK_FAILED` → **HALT**; surface the failing `VERIFY_CMD` output. (When the profile is `mode: unverified`, the fixer runs no checks and cannot return `CHECK_FAILED`.)
    - Status `ESCALATED` → for each escalated finding, present it as a `present-set` intervention now (same prompt shape as step 7), then re-dispatch the fixer with the user's decisions folded in. The follow-up dispatch uses this same `CHECK_FAILED`/`ESCALATED` contract; a finding the user has already decided on is no longer eligible to escalate, so it cannot ping-pong. Do NOT add an escalated finding to the skip-set unless the user skips it. After escalation handling resolves the final `fix-batch`, recompute **blocking-to-fix** (step 9) before evaluating step 14.
12. **Verify.** If a `VERIFY_CMD` is set, the orchestrator independently runs it from the user's own working tree (never the PR head), non-interactively, with a timeout. Fail (non-zero exit) → **HALT** with `CHECK_FAILED`; surface output. (Do not re-review on a broken tree.) If the profile's verify story is `mode: unverified`, **SKIP this gate** — there is no command to run; the round's commit stands ungated.
13. **Circuit breaker.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/lib/circuit_breaker.py" "$SESSION_DIR" 7`. Parse its JSON. If `halt: true` → **HALT**; surface `reason` + `detail` + still-open findings + the commit range (`git log <baseRef>..HEAD --oneline`). Do NOT read or `cat` the diff into the orchestrator context.
14. If `blocking-to-fix > 0` → `round += 1` and repeat from step 1. If `blocking-to-fix == 0`:
    - and there is **no** skipped-blocking finding → **EXIT SUCCESS** (no blocking findings remain; any Minor/Nit are now fixed).
    - and one or more blocking findings were deliberately skipped → **EXIT — CLEAN EXCEPT FOR SKIPPED**: the tree is clean except for the skipped blocking finding(s). List them; do not report a plain SUCCESS verdict.

### Triage subagent prompt

```
You are triaging code-review findings for one round of an auto-fix loop.

## Input
- Findings to classify: $SESSION_DIR/round-<N>/compiled.json (use only the
  findings whose ids are in this list: <ids of effective findings>)
- Triage rubric: the base rubric's "Triage rubric (mechanical vs judgment)"
  section (absolute path: <absolute RUBRIC path>)
- Project profile: <PROFILE_PATH> (threat model, scope, focus hints)
- Project conventions: CLAUDE.md
- Code to inspect: the current working tree (read the cited files to judge
  whether a fix is mechanical or a judgment call)

## Your job
For EACH listed finding, emit TWO things — a fix-complexity classification AND an
orchestrator POV. Read the cited file before deciding; use what you read for both.

### 1. classification: "mechanical" or "judgment"
Apply the base rubric's "Triage rubric" — this is about the FIX, not whether to
fix. Mark "judgment" ONLY when applying the fix involves a real choice
(`finding.tradeoff === true`; a UX/design call with more than one reasonable
option; or a change to established product behavior the user may have an opinion
on). Everything else (one determinate, obviously-correct fix) → mechanical. Bias
hard toward mechanical.

### 2. recommendation (orchestrator POV) — EVERY finding
Per the base rubric's "Orchestrator POV", emit for every finding (this drives
whether the loop fixes it silently or stops to ask the user):
- recommendation: "Fix" | "Skip" | "Defer"
  - Fix = correct and worth the change here.
  - Skip = good reason not to (correct-but-not-worth-it for this project per the
    profile's threat model/scope, cost > benefit, or borderline/likely-false-
    positive on a closer read).
  - Defer = real but not now/not here (big-job, out of scope for this change).
- rationale: one sentence saying why.
- confidence: "High" | "Low" (Low = genuinely unsure; flags it for scrutiny).

## Output
Write $SESSION_DIR/round-<N>/triage.json — every listed finding id exactly once:
[ { "id": "<id>", "classification": "mechanical" | "judgment", "reason": "<one sentence>",
    "recommendation": "Fix" | "Skip" | "Defer", "rationale": "<one sentence>", "confidence": "High" | "Low" } ]
(All four POV-related fields are present on EVERY entry.)
```

### Fixer subagent prompt

```
You are the fixer for one round of an auto-fix code-review loop.

## Input
- Findings to fix: $SESSION_DIR/round-<N>/fix-batch.json (array; each has
  id, severity, dimension, file, line, body, suggestion, and optional
  userGuidance)
- Conventions: CLAUDE.md and the project profile (<PROFILE_PATH>);
  severity/format from the base rubric (<absolute RUBRIC path>)
- Work in the current branch's working tree at <cwd>
- Verify command: <VERIFY_CMD, or the literal "none" when the profile is mode: unverified>

## Your job
1. Apply a fix for EACH finding. Follow CLAUDE.md conventions and the profile's
   canonical patterns. When a finding has userGuidance, follow it over the
   original suggestion.
2. Fix ONLY what the findings call for. No unrelated refactors (YAGNI).
3. If a verify command was provided, run it. If it fails, fix the failure and
   retry ONCE. If it still fails, STOP and report CHECK_FAILED with the failing
   output — never commit broken code. If the verify command is "none"
   (unverified profile), skip this check entirely.
4. Commit ALL changes in ONE commit (after the check passes, or immediately when
   unverified): `git commit -m "Auto-fix round <N>: <count> findings (<dimensions>)"`
5. Report back.

## Escalation
If a finding you were told to auto-fix actually requires a judgment call you
cannot make (multiple valid approaches, ambiguous intent), do NOT guess.
Report it under "escalated" with the id and why.

## Report format
- Status: DONE | CHECK_FAILED | ESCALATED
- fixed: [ids]
- escalated: [ { id, why } ]
- newIssuesNoticed: [brief notes on anything seen but not fixed]
- commit: <sha or "none">
- checkOutput: <tail of the verify command, only if CHECK_FAILED>
```

### End-of-Loop Summary

Print: final verdict, rounds run, commits created (one per round), findings fixed by severity, any findings deliberately skipped, and any new findings the fixer noticed/introduced along the way (informational). If the verify story was `unverified`, state that fixes were committed **without a verify gate**. Because fixes are local-only, offer to push the branch (or, if this was a PR you don't own, point to `--post`). Do not push without explicit confirmation.

**Then, after the summary**, run the three non-blocking end-of-run steps from `## Learning Loop & Staleness Nudge`, in order: (1) the **staleness nudge** (print the doctor `message` only when non-null and `nudge_acked` is false), (2) the **learning-loop proposal** (`decisions.py analyze` → at most one user-gated `AskUserQuestion`, never auto-applied), then (3) the **provisional-profile confirmation** (interactive only — offer to confirm a `status: provisional` profile; skipped when headless, already stable, or already acked). All three are placed after the review output and none blocks.

## Read-Only Paths

These two paths run a **single review pass** (loop steps 1-3, writing artifacts under `round-1/`) and then diverge. Neither triages, fixes, commits, or loops.

### `--review-only`

After the single pass, run the interactive tiered presentation and a terminal report. No commits. (A profile with `mode: review-only` makes the default path degrade into exactly this presentation.)

**If context was compacted between dispatch and presentation**, re-read `$SESSION_DIR/round-1/compiled.json` and `$SESSION_DIR/meta.json` to restore state. The skill is resumable from disk.

**Form the orchestrator POV before presenting.** Per the base rubric's "Orchestrator POV", for each Critical/Important finding open the cited file at the cited line (in `$SESSION_DIR/repo/` for the PR path, working tree otherwise) and form a **Fix / Skip / Defer + one-sentence rationale + High/Low confidence** take. This is the coordinator's own judgment from a small targeted read — not a re-review. For batched Minor/Nit, derive the POV from the finding text (read the file only if the text is insufficient).

**Apply the review gate.** Partition findings by POV: `auto-include` = `recommendation == Fix` (these enter the report without asking); `ask-set` = `recommendation` is `Skip` or `Defer` (these need your call). Only the `ask-set` is presented below; the `auto-include` set is added to the approved findings silently.

Open with the verdict banner and the one-line summary. If the `ask-set` is empty, skip straight to the report. Otherwise run the tiered presentation over the `ask-set` only:

- **Critical and Important findings (ask-set) — individually.** For each, use `AskUserQuestion`. Header includes severity tag, dimension(s), and `file:line`. Body shows the finding text, the suggested fix, and — on its own line — the **POV**: e.g. `→ POV: Skip (Low confidence) — correct in theory but this path is never hit concurrently under the profile's threat model`. Options (keep this neutral order; the POV informs but does not pre-select):
  - **Approve** — include at current severity.
  - **Modify** — open a free-text edit for the comment body before approval.
  - **Downgrade** — drop one severity tier (Critical → Important, Important → Minor). A downgraded Important → Minor is **auto-approved at Minor** and not re-presented in the Minor batch.
  - **Skip** — exclude entirely.
  - The user may use "Other" to push back, ask a clarifying question, or request a targeted re-verification. Engage. If they question a specific finding, read the relevant file from `$SESSION_DIR/repo/` (or working tree) to re-check that one location — this is a small, targeted read, not loading the full diff.

- **Minor and Nit findings (ask-set) — batched, multi-select.** Present in batches of 4 via `AskUserQuestion` with multi-select. For each finding, show severity, `file:line`, a 2-3 sentence summary, and a compact POV tag (e.g. `POV: Skip (Low)`). Always offer **Include all** and **Skip all** as alternatives at the bottom of the batch.

The approved set = `auto-include` ∪ the findings approved from the `ask-set`. After the last batch, summarize how many of each severity were approved, then print a terminal report grouped by severity. Lead with the verdict label in bold. For each approved finding: severity tag, `file:line`, title, body, and the orchestrator POV line. End with the count summary (e.g. `"3 Critical, 5 Important, 2 Minor approved"`). Save nothing else to disk — `compiled.json` already has the full record.

**Record decisions (learning loop):** as you resolve the `ask-set` findings, append one `decisions.py` record per decision to the resolved decisions store (`$DECISIONS`) (**Approve**/**Modify**/**Downgrade** → `fix`; **Skip** → `skip`), per `## Learning Loop & Staleness Nudge`. Then, after the terminal report, run the three non-blocking end-of-run steps (staleness nudge, then learning-loop proposal, then provisional-profile confirmation) from that section, in order.

### `--post`

After the single pass (PR mode only), post approved findings to GitHub. No triage, no fix, no loop, no commits to the tree. Run the interactive tiered presentation above (including its **review gate**) to select which findings to post: `recommendation == Fix` findings are auto-selected for posting, and only `Skip`/`Defer` findings are presented for your call. The orchestrator POV is shown to **you** during selection, but is **not** included in the posted comment body (the public comment stays the finding + suggestion). Then ask the user the review event type via `AskUserQuestion`:

- **COMMENT** — findings without approval/rejection
- **REQUEST_CHANGES** — blocks merge until resolved
- **APPROVE** — approve with comments

Then build the review JSON from approved findings:

```bash
cat > "$SESSION_DIR/round-1/review.json" <<EOF
{
  "commit_id": "<HEAD_SHA from meta.json>",
  "body": "<summary from compiled.json + verdict label>",
  "event": "<user's choice>",
  "comments": [
    {"path": "<file>", "line": <N>, "side": "RIGHT", "body": "<severity tag + finding body + suggestion>"}
  ]
}
EOF
```

Run `resolve_diff_lines.py` to validate every comment anchor against the diff. This is non-optional — GitHub returns 422 "Line could not be resolved" for any inline comment whose `(file, line)` doesn't land on a `+` or context line inside a hunk, and the script moves out-of-hunk comments to the nearest valid line (prefixing the body with `(Re: line N)`) and drops comments for files not in the diff:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/lib/resolve_diff_lines.py" \
  "$SESSION_DIR/round-1/diff.txt" \
  "$SESSION_DIR/round-1/review.json" \
  --output "$SESSION_DIR/round-1/review-resolved.json"
```

Surface the script's stderr to the user — any `MOVED:` or `DROPPED:` lines mean a finding got relocated or excluded, and the user should know before the review goes out.

Post the review:

```bash
gh api "repos/$REPO/pulls/$PR_NUMBER/reviews" \
  --input "$SESSION_DIR/round-1/review-resolved.json"
```

**Post-submit verification — non-optional.** Fetch the last review to confirm it actually landed (silent failures and accidental duplicates have burned us before):

```bash
gh api "repos/$REPO/pulls/$PR_NUMBER/reviews" \
  --jq '.[-1] | {id, state, submitted_at, html_url}'
```

If the post returns 422 "Line could not be resolved" despite running `resolve_diff_lines.py`, the script's stderr will have logged which comments were moved or dropped — re-check those, fix manually in `review-resolved.json`, and retry the `gh api ... reviews` call. Do **not** test line validity by posting real reviews iteratively; submitted reviews cannot be deleted via API.

Report the review URL (`html_url` from the verification call) to the user.

**Record decisions + end-of-run steps (learning loop):** as you resolve the `ask-set` during selection, append one `decisions.py` record per decision to the resolved decisions store (`$DECISIONS`) (a finding selected for posting → `fix`; a **Skip**/**Drop** → `skip`), per `## Learning Loop & Staleness Nudge`. Then, after reporting the review URL, run the three non-blocking end-of-run steps (staleness nudge, then learning-loop proposal, then provisional-profile confirmation) from that section, in order. (On the `--post` path the staleness check ran with `--root "$SESSION_DIR/repo"`.)

## The verify command

The orchestrator's verify gate (loop step 12) and the fixer (prompt step 3) both run the project's own verify command, read from the resolved profile (`$PROFILE`)'s `## Verify` section during Setup. There are three branches:

- **`command: <cmd>` →** `VERIFY_CMD="<cmd>"`. Both the orchestrator's gate and the fixer run `VERIFY_CMD` from the user's own working tree (never the PR head), non-interactively, with a timeout. A non-zero exit is a **HALT / `CHECK_FAILED`** — the orchestrator surfaces the failing output and does not re-review on a broken tree.
- **`mode: unverified` →** there is no verify command. SKIP the verify gate (step 12); tell the fixer not to run checks (verify command `"none"`); commits proceed ungated. State "unverified" in the dispatch summary and the End-of-Loop summary.
- **`mode: review-only` →** the project opted out of auto-fix. The default path degrades to a single review pass + the `--review-only` presentation (no triage, no fixer, no commits, no loop). Note this in the dispatch summary.

`meta.json` records the verify story (`verify`: the command string, or `"unverified"` / `"review-only"`) so a cold-resumed orchestrator recovers it without re-reading the profile.

## Learning Loop & Staleness Nudge

These four behaviors are **non-blocking**, run **at end of run** (after the review output / end-of-loop summary), and are **identical across `review-code`, `review-plan`, and `audit-debt`**. Nothing here ever auto-applies a profile or `CLAUDE.md` edit — every change is user-gated.

### Recording decisions (at resolution time)

Wherever the user resolves a finding (this skill: the §7 interventions and any escalation re-prompt — i.e. when an `AskUserQuestion` resolution is recorded), append ONE record per decision to the **project-level** learning-loop store at the resolved `$DECISIONS` path (NOT the temp `$SESSION_DIR`). Use the bundled helper:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/lib/decisions.py" \
  append "$DECISIONS" '<record-json>'
```

`<record-json>` is `{"dimension": "<finding dimension>", "category": "<finding taxonomy/topic>", "action": "skip"|"guidance"|"fix"}`:
- `action` maps from the user's choice: **Skip** → `skip`; **Fix with my guidance** → `guidance`; **Fix as suggested** (and any auto-fix the user implicitly accepted by not skipping) → `fix`.
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

## Verification Rules (for subagents)

These are the base rubric's binding verification rules; they are restated in every subagent prompt and enforced again at compile time. See the base rubric's "Verification rules" and "In-pass verification & single-pass discipline" sections for the authoritative statement. Subagents that violate them produce findings that get dropped before the user ever sees them.

1. **`file:line` citation required.** No citation → finding is dropped at compile time, before presentation.
2. **Diff-scope rule.** Only `+` and `-` lines of `$SESSION_DIR/round-<N>/diff.txt` are in scope. Context lines (no prefix) and unchanged code in modified files are pre-existing — flagging them is the #1 source of false findings.
3. **Grep-before-flag.** Before flagging "missing X", search for X under variant names. In PR mode, grep `$SESSION_DIR/repo/`, not the main working tree.
4. **Reachability check on Important findings.** Read the caller(s) of the affected symbol. If the only caller already guards the edge case, downgrade or drop.
5. **Worktree-as-source-of-truth (PR mode).** All code verification reads go through `$SESSION_DIR/repo/`. The main working tree may be on a different branch with stale or missing code; using it for verification produces false findings against code that doesn't exist on the PR.
6. **Trust nothing from project docs without spot-checking.** Project docs (`CLAUDE.md`, the profile, `docs/*`) can be outdated. If a finding's rationale depends on a doc claim, verify against source code or flag uncertainty.
7. **Single-pass discipline.** Each specialist runs once per review. The orchestrator does not chain a verifier agent or re-run a specialist — published research on multi-turn agentic review shows F1 degrades and agents fabricate findings as real ones get exhausted.

## Common Mistakes

| Mistake                                                 | Fix                                                                                                                                                                |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Flagging pre-existing code as a PR issue**            | **The #1 mistake.** Diff-scope rule: only flag `+`/`-` lines. Context lines and unchanged code are out of scope even if they violate conventions.                  |
| Loading the full diff into main context                 | The orchestrator only ever runs `wc -l < $SESSION_DIR/round-<N>/diff.txt`. Subagents read the diff from disk; the orchestrator reads JSON findings.                |
| Finding based on assumed code state                     | Subagents must verify against `$SESSION_DIR/repo/` (PR mode) or the working tree (branch mode). No "I think this calls X" — open the file and confirm.             |
| Marking test issues as Critical                         | Critical is reserved for production bugs, data loss, security vulns. Test anti-patterns are Important at most — see the base rubric's "Severity tiers".            |
| Severity miscalibrated to deployment context            | Calibrate to the profile's threat model (strict / multi-user when the profile is absent). Don't raise threats the profile declares out of scope.                  |
| Posting without interactive approval                    | Every finding goes through `AskUserQuestion` (individually for Critical/Important, batched for Minor/Nit). Never auto-post anything from raw subagent output.      |
| Not using `resolve_diff_lines.py` before posting        | Always run the script before `gh api ... reviews`. It moves out-of-hunk comments to valid lines and drops comments for files not in the diff. Skipping it → 422.   |
| Not verifying the review was actually posted            | After `gh api ... reviews` returns success, fetch the last review and confirm `state` and `submitted_at`. Silent failures and duplicate posts have happened.       |
| Re-flagging issues the author already justified         | PR mode: check `prior-comments.json` for substantive author replies. If the explanation is sound, don't re-raise. Outdated comments still count.                   |
| Using diff.txt line numbers as file line numbers        | Diff line numbers and file line numbers are different. `resolve_diff_lines.py` parses `@@` hunk headers to map between them; trust the script.                     |
| Dropping resolved Important findings silently           | If the reachability check or author-justification filter drops an Important, mention it to the user — they may want to see what was filtered.                      |
| Skipping `--post` verification when GH returns success  | `gh api` can return 200 on a malformed body that GitHub silently treats as a no-op. Always run the post-submit verify call.                                        |
| Trying to delete a bad review via API                   | Submitted reviews cannot be deleted via the GitHub API. Never iterate by re-posting — fix `review-resolved.json` and retry only after the resolve script is clean. |
| Tiering or skipping specialists based on "what changed" | All four specialists always run. Coverage uniformity beats saving one agent dispatch — the agent returns `[]` if there's nothing to flag.                          |
| Using `gh pr diff` inside the loop                      | Rounds 2+ have local fix commits not on the remote. Always recompute `git diff <baseRef>...HEAD` locally each round.                                               |
| Auto-fixing a PR you don't have checked out             | Auto-fix needs the PR's branch as the current branch. If it isn't, stop and direct the user to `--post` or `--review-only`.                                        |
| Re-reviewing on a broken tree                           | If `VERIFY_CMD` fails after a fix, HALT. Never run the next review round on code that doesn't pass verification. (No gate when the profile is `mode: unverified`.) |
| Re-raising a finding the user skipped                   | Skipped identities go in the skip-set and are excluded from every later round's effective findings AND the circuit breaker.                                        |
| Eyeballing "are we stuck?" by hand                      | Always call `circuit_breaker.py`. Finding-identity comparison across rounds is deterministic; manual judgment drifts after compaction.                             |
| Pushing automatically at loop end                       | The loop commits locally only. Pushing is always a separate, user-confirmed step.                                                                                 |
| Dispatching reviewers by reading an agent file          | The four reviewers are bundled plugin agents — dispatch each by its `subagent_type` (its name). The methodology is the agent's own system prompt.                  |
| Skipping the profile bootstrap                           | If `.claude/review-profile.md` is absent, run review-init's create procedure inline first. Headless runs get a provisional strict profile.                         |
