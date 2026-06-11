---
name: test-pilot-init
description: Use when a project has no test-pilot profile yet (before the first test-pilot plan/execute in a repo), or to refresh a project's testing calibration after the app changed. Sets up the profile, seeding blocks, and browser tooling.
---

# test-pilot-init

Create or reconcile a project's **test-pilot profile** plus its starter
seeding blocks. Two modes: **create** (nothing resolves) and **reconcile**
(profile exists → re-detect, diff, migrate; NEVER silently overwrite).

## Step 1 — Resolve

```bash
RES=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/store.py" resolve)
LOCATION=$(printf '%s' "$RES" | jq -r .location)
```

`location: none` → create mode (Steps 2–6). Otherwise → reconcile (Step 7).

## Step 2 — Detect (no questions the repo can answer)

Read `CLAUDE.md` first — the profile is an ADDER over it. Then detect:
stack/scripts (`package.json` scripts, `pyproject.toml`), dev command and
port, DB env vars (`.env*` files — names only, never read values into the
profile), docker-compose services, existing seed scripts, `git remote
get-url origin`. Check `uv` availability (`command -v uv`); if absent, offer
to help install it (https://docs.astral.sh/uv/) — without it, blocks are
limited to stdlib + run-command designs.

## Step 3 — Browser tooling gate

Use ToolSearch to check which browser MCPs are connected (search
"chrome-devtools", "Claude_in_Chrome", "playwright"). If NONE is available,
STOP and guide the user through installing one (chrome-devtools MCP,
Playwright plugin, or the Claude in Chrome extension) before continuing.
Record the preference order for the profile.

## Step 4 — Decide location

```bash
LOC=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/store.py" decide-location --interactive true)
# "ask" -> AskUserQuestion: in-repo (committed, team-shared) vs global
# (~/.claude/test-pilot/, zero git footprint). Headless runs get "global".
PATHS=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/store.py" create --location "$LOC")
```

## Step 5 — Interview only the gaps

Ask ONLY what detection + CLAUDE.md left open: auth strategy (test-user env
var names / bypass / real-browser-session), protected targets (which DB or
surface must the gate refuse — suggest the production/main DB you detected),
base-URL confirmation.

## Step 6 — Scaffold

1. Fill `${CLAUDE_PLUGIN_ROOT}/templates/profile.md` (prose AND the
   `json test-pilot-config` block — keep them consistent) and write it to
   the resolved profile path. Set provenance `status=stable` when the user
   answered the interview, `status=provisional` on headless defaults.
2. Write 1–2 starter blocks bespoke to this app into the resolved
   `blocks_dir`, from `templates/starter-block.py` — e.g. an HTTP seeder
   against the detected API, or a `run-command` design wrapping an existing
   seed script. Every block declares non-empty `targets` and pins PEP 723
   dependency versions.
3. Generate the catalog:
   `python3 "${CLAUDE_PLUGIN_ROOT}/lib/catalog.py" --blocks-dir <blocks_dir>`

Report what was written and where; remind the user that `test-pilot-plan`
picks it up from here.

## Step 7 — Reconcile mode

Re-run detection, then DIFF against the existing profile. Present drift to
the user (changed dev command, new env vars, vanished scripts) and apply
only what they approve. Hand-edits in the profile are preserved verbatim
unless the user approves replacing them. Never regenerate from scratch over
an existing profile.
