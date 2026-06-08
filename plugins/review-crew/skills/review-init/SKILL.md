---
name: review-init
description: Use when a project has no .claude/review-profile.md yet (e.g. before the first review-crew review in a repo), or when you want to regenerate or refresh a project's review calibration after the project has changed.
---

# review-init

Generate or refresh a project's **review profile** — `.claude/review-profile.md` —
the per-project calibration the review-crew engine reads (threat model, verify
command, scope, focus hints, canonical patterns). Two modes: **create** (no
profile yet) and **reconcile** (profile exists → re-detect and migrate).

## The profile is a CLAUDE.md-aware ADDER

The profile carries ONLY what the project's `CLAUDE.md` does not already cover.
Read `CLAUDE.md` first; never duplicate its conventions into the profile —
`## Conventions` points at `CLAUDE.md`. Skip any interview question whose answer
is already clear from detection or `CLAUDE.md`.

## Step 1 — Detect (no questions where the repo answers)

Run these and read the results; do not ask the user what you can observe:

```bash
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
# Package manager / stack
ls "$ROOT"/package.json "$ROOT"/pyproject.toml "$ROOT"/Cargo.toml "$ROOT"/go.mod 2>/dev/null
# Verify-command candidate (JS): a "check"/"test" script
[ -f "$ROOT/package.json" ] && python3 -c "import json;print(json.load(open('$ROOT/package.json')).get('scripts',{}))" 2>/dev/null
# Default branch
git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' \
  || git rev-parse --abbrev-ref HEAD
# Forge
git remote get-url origin 2>/dev/null
# Top-level source dirs
ls -d "$ROOT"/src "$ROOT"/lib "$ROOT"/app 2>/dev/null
```

Derive: **package manager / framework / test runner**; a **verify-command**
candidate (`npm run check` → `npm test` → `pnpm/yarn` equivalents → `make check`
→ none); **default-branch** (the `git symbolic-ref` result, else current branch);
**forge** (`github` if the remote host is github.com, `gitlab` if gitlab.*, else
`none`); **dep-set** (top-level dependency names, with major version where cheap);
**src-dirs**. Also **read `CLAUDE.md`** (root and any nested) to learn what
conventions/threat context it already states.

Read the engine versions for provenance:

```bash
sed -n '1p' "${CLAUDE_PLUGIN_ROOT}/rubric/review-base.md"          # -> <!-- rubric-version: N -->
python3 -c "import json;print(json.load(open('${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json'))['version'])"
```

## Step 2 — Choose mode

Resolve where the profile lives (it may be in-repo under `./.claude/` or in the
global per-repo store). `review_store.py resolve` returns the resolved path, or
`location: none` when no profile exists yet:

```bash
RES=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" resolve --kind profile) \
  || RES='{"location":"none","exists":false,"path":null}'
LOCATION=$(printf '%s' "$RES" | jq -r .location)
PROFILE=$(printf '%s' "$RES" | jq -r '.path // empty')
```

If `$LOCATION` is not `none` (a profile resolved at `$PROFILE`) → **Reconcile**
(Step 5). Otherwise (`$LOCATION` is `none`) → **Create** (Steps 3–4); decide the
storage location and mint the path before writing:

```bash
if [ "$LOCATION" = "none" ]; then
  INTERACTIVE=true   # the orchestrator sets this to false on a headless/non-interactive run (no human to answer), so decide-location returns "global" deterministically instead of "ask"
  LOC=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" decide-location --interactive "$INTERACTIVE")
  # If LOC is "ask", present the in-repo vs global AskUserQuestion, set LOC.
  PROFILE=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/review_store.py" create --kind profile --location "$LOC")
fi
```

When `decide-location` returns `ask`, present the in-repo-vs-global
`AskUserQuestion` (per the spec's *Halt-and-ask init flow*) and use the answer as
`$LOC`. The minted `$PROFILE` is the path Step 4 writes to.

## Step 3 — Create: CLAUDE.md-aware interview (full, inline, tight)

Ask only what detection + `CLAUDE.md` did not answer. Use `AskUserQuestion`.
Typical questions (skip any already answered):

1. **Threat model** — `single-user` / `multi-tenant` / `public`. (If `CLAUDE.md`
   already states the deployment/threat context, infer it and skip.)
2. **Verify command** — confirm the detected candidate. If **none was detected**,
   offer three options and record the choice:
   - *Set one up* — propose a `check` command for the user to add to their build
     config (e.g. `tsc --noEmit && eslint . && vitest run`); do NOT edit their
     config yourself. Record the proposed command once they add it.
   - *Unverified* — write `mode: unverified` (auto-fix will commit without gating).
   - *Review-only* — write `mode: review-only` (no auto-fix).
3. **Scope exclusions** — anything explicitly out of scope (e.g. accessibility for
   a non-UI or internal tool). Default to none.

If **no `CLAUDE.md` exists**, offer: (a) generate a starter `CLAUDE.md`, (b) inline
a minimal conventions block into the profile, or (c) run code-reviewer
correctness-only. Record the choice in `## Conventions`.

**Headless / non-interactive** (no human to answer, e.g. an automated run): skip
the interview, write a `status: provisional` profile from detected defaults with
the **strict** threat model, and note in the body it was auto-generated.

## Step 4 — Create: seed canonical patterns, then write

Seed `## Canonical patterns` by detection — grep for the project's own idioms so
generalized agents stay sharp (each is `pattern: file:line`):

```bash
grep -rnE "getServerSession|requireAuth|withAuth|authorize\(" "$ROOT"/src 2>/dev/null | head -1   # auth wrapper
grep -rlE "export const [A-Z_]+_ERRORS|errors?\.(ts|js|py)$" "$ROOT"/src 2>/dev/null | head -1     # error constants
grep -rnE "userId|ownerId|tenantId" "$ROOT"/src 2>/dev/null | head -1                              # ownership idiom
```

Record only patterns you actually found (omit the section if none). Then write
the profile to the resolved `$PROFILE` (from Step 2) using the **template below**.
Only when `$PROFILE` is in-repo (under `./.claude/`) `AskUserQuestion` whether to
commit it (`git add "$PROFILE" && git commit`); a global-store profile lives
outside the working tree and is not committed.

### Profile template

```markdown
<!-- review-profile · managed by review-crew · schema 1 -->
<!-- provenance — do not hand-edit this block; everything below it is yours to edit -->
schema: 1
plugin: review-crew@<plugin-version>
rubric-version: <N from review-base.md>
generated: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
status: <provisional|stable>
nudge-ack: {}
signals:
  dep-set: [<names@major>]
  default-branch: <branch>
  forge: <github|gitlab|none>
<!-- end provenance -->

## Project
<one line: stack + what it is>

## Threat model
<single-user|multi-tenant|public>

## Verify
command: <verify command>
# or: mode: unverified | mode: review-only

## Scope exclusions
- <only real exclusions; omit the bullets if none>

## Focus hints
- security: <one line>
- architecture: <one line>
- test: <one line>
- code: <one line>

## Canonical patterns
- <pattern-name>: <file:line>

## Conventions
See CLAUDE.md.
```

`status` is `stable` when the interview was completed interactively with a real
verify story; `provisional` for headless/greenfield/defaulted profiles.

## Step 5 — Reconcile (profile already exists)

1. Read the existing profile and its `schema:`.
2. **Read-side guard:** if the profile's `schema` is **higher** than this plugin
   supports (the template's `schema` above), STOP with a loud message
   ("profile schema N is newer than this review-crew; upgrade the plugin") and do
   not rewrite it — degrade conservatively (strict posture) rather than misread
   newer fields.
3. **Apply migrations** for each step between the profile's `schema` and the
   plugin's. *(Schema 1 is current; there are no migration steps yet. When a
   future schema adds/renames fields, each `N→N+1` step is listed here and applied
   in order.)*
4. Re-read `CLAUDE.md` and re-detect (Step 1). Compute proposed changes: new
   `dep-set`, changed `default-branch`/`forge`, newly-covered-by-CLAUDE.md items
   to drop from the profile, etc.
   **Detect `rubric-version` drift.** Read the engine's current rubric-version
   (Step 1: the `<!-- rubric-version: N -->` line of `review-base.md` — currently
   **2**). If the profile's `rubric-version` is **lower** than the engine's, flag
   it as drift in the proposed-changes diff ("rubric-version M→N: the review rubric
   has advanced; recalibrating") and update it on write (step 7). This is the same
   signal `repo_doctor.py` surfaces as a staleness nudge during a review — reconcile
   is where it actually gets cleared.
5. **Migration rules:** unknown `## ` sections/keys are **preserved verbatim**
   (forward-compat); missing fields are filled per the create defaults only when
   safe; never silently delete user calibration. The `nudge-ack` map is user
   state — **preserve it verbatim** across the reconcile (do not reset acks the
   user has already dismissed). Add the `nudge-ack:` field (empty `{}`) only if an
   older profile predates it.
6. Show the user a **diff** of proposed changes and `AskUserQuestion` to apply,
   edit, or skip. Preserve all hand-edits below the provenance block unless the
   user approves a change.
7. Write the profile; bump `updated:` and refresh `signals` + `rubric-version` +
   `plugin` (set `rubric-version` to the engine's current value, clearing any
   drift detected in step 4). **Preserve the `nudge-ack` map** (carry the existing
   acks forward unchanged). Flip `status` `provisional → stable` if the profile is
   now complete.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Duplicating CLAUDE.md conventions into the profile | The profile is an adder — point `## Conventions` at CLAUDE.md; only add gaps. |
| Asking the user something detection/CLAUDE.md already answers | Detect and read first; the interview covers only the remainder. |
| Rewriting a profile from a newer schema | Honor the read-side guard: stop and tell the user to upgrade. |
| Silently dropping hand-edits on reconcile | Preserve everything below provenance; show a diff; only change on approval. |
| Editing the user's build config to "set up" a verify command | Propose the command; let the user add it. |
