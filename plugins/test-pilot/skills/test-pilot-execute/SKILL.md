---
name: test-pilot-execute
description: Use when a test-pilot plan should be exercised before human spot-check — "run the test plan", "pilot this PR", "verify the branch in the browser". Drives the app via a browser MCP, fixes bugs it finds, and posts a results comment.
---

# test-pilot-execute

Exercise the branch's test-pilot plan in a real browser, fix what breaks,
post a results comment, and leave the PR ready for human spot-check.

## Hard boundaries

1. **`--allow-protected` MUST NOT be passed unless the user explicitly
   instructed it in the current session.**
2. **Commit guardrails** (app-bug fixes): stage ONLY the files the fix
   touched — never `git add -A`; REFUSE to commit when HEAD is the default
   branch; push plain (never force) and only to the branch's existing
   tracking ref — no tracking ref, no push.
3. **Navigation is constrained** to origins matching the profile's
   `baseUrl` (plus `allowedOrigins`). Anywhere else is off-limits.
4. **Every quoted diagnostic is scrubbed** before it reaches a comment:
   `python3 "${CLAUDE_PLUGIN_ROOT}/lib/pr_comment.py" scrub` (stdin→stdout).
   Never quote raw request headers.
5. The plan comment's checkboxes belong to the human — never check them.

## Flow

1. **Resolve.** `store.py resolve`; read the profile and its config block.
   Find plan records `<manifests_dir>/<key>.plan.json` for the current
   branch — default: every slot in sequence; an explicit slot argument
   narrows to one. None → run the test-pilot-plan skill first, then return.
   The PR comment is NEVER parsed as the plan source.
   Validate each before executing: `python3 "${CLAUDE_PLUGIN_ROOT}/lib/engine.py" validate-plan --branch B [--slot S] --json` — a validation error means regenerate the plan, never an app bug.
2. **Seed check.** `engine.py status --json`; apply the manifest if drift or
   nothing applied (`engine.py apply --branch B [--slot S] --json`).
3. **App up.** Per the profile: if `mayManageServer`, start `devCommand` in
   the background and poll `readinessUrl` until it answers; else verify it
   answers and ask the user to start it if not.
4. **Browser tool.** Profile `browserTools` order ∩ currently connected
   (ToolSearch). Empty intersection → ABORT with remediation: "run
   test-pilot-init to install/record a browser tool". Never continue
   without one.
5. **Execute each step** from the plan record: perform the interactions,
   verify `expected` via DOM/snapshot reads, watch console/network for
   silent errors. Record pass/fail + scrubbed notes per step in a run log
   under `<state_dir>/runs/<key>/`.
6. **On failure, classify, then act:**
   - *Plan or seed mistake* → fix the plan record/manifest quietly, re-apply
     if needed, re-run the step. In in-repo mode, commit these corrections
     under `.claude/test-pilot/` as their own scoped commit; in global mode
     there is nothing to commit.
   - *App bug* → diagnose (console, network, code), fix the code, re-run the
     failed step PLUS any steps the fix could plausibly affect, commit per
     the guardrails above, record the fix (description + SHA).
   - *Unfixable after ~3 attempts* → mark the step failed with a scrubbed
     diagnosis, continue remaining steps, surface loudly at the end.
7. **Post results.** Fill `templates/results-comment.md` (verdict: PASSED /
   FAILED / PARTIAL; per-step table; fixes with SHAs; run metadata). Post:
   `pr_comment.py upsert --pr N --family results --key K --body-file F --plans-dir <plans_dir>`.
   No PR → write to `<plans_dir>/<key>.results.md`. If the run was
   interrupted (browser died, server unreachable), post whatever completed
   marked **partial** — state stays intact for resumption.
8. **Hand off.** Report what is seeded, what passed/failed, what was fixed
   (SHAs), and that the PR is ready for spot-checking.

## Rationalization table

| Excuse | Reality |
|---|---|
| "Gate refused the re-seed; --allow-protected will unblock" | Only the USER authorizes that flag. Stop and ask. |
| "Faster to git add -A after the fix" | Scoped staging only. Unrelated diffs are not yours to commit. |
| "It's basically done, I'll check the plan boxes" | Boxes are the human's spot-check. Leave them. |
| "The console dump is harmless, paste it raw" | Scrub EVERY diagnostic. No raw headers, ever. |
| "No browser tool — I'll verify via curl instead" | Abort with remediation. curl is not the plan. |
| "HEAD is main but the fix is tiny" | Never commit on the default branch. |
