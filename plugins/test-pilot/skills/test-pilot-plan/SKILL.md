---
name: test-pilot-plan
description: Use when a PR/branch needs a manual test plan with seeded data — "write a test plan", "seed test data for this PR", "set up manual testing". Seeds scenarios via the test-pilot engine and posts a checkbox plan to the PR. Does NOT execute the plan (that is test-pilot-execute).
---

# test-pilot-plan

Produce seeded test data + a machine-readable plan record + a PR comment for
the current branch. **Stop at plan ready** — execution belongs to
test-pilot-execute.

## Hard boundaries

1. **Every data mutation goes through a block via the engine CLI.** No
   ad-hoc DB writes, no direct seed scripts outside a block. No exceptions.
2. **`--allow-protected` MUST NOT be passed unless the user explicitly
   instructed it in the current session.** A refusal from the gate is a
   STOP-and-ask, not a retry-with-flag.
3. **Read CATALOG.md in full before selecting blocks** — every invocation,
   no exceptions, even if you read it earlier this session.
4. Manifests are edited via this flow; hand-edits must update `updatedAt`
   and be validated with `--dry-run`.

## Flow

1. **Resolve.** `python3 "${CLAUDE_PLUGIN_ROOT}/lib/store.py" resolve` →
   `location: none` means run the test-pilot-init skill first, then return.
2. **Read the CATALOG** at `<blocks_dir>/CATALOG.md` IN FULL (blocking).
3. **Analyze the diff.** `gh pr diff` (else
   `git diff <default-branch>...HEAD`). Identify what needs human-style
   verification: visual rendering, interaction flows, auth paths, edge cases
   automated tests miss.
4. **Pick blocks; create missing ones.** If no block fits, write a new
   module in `<blocks_dir>` from `templates/starter-block.py` (non-empty
   `targets`, pinned PEP 723 deps — check `uv` first; without it use
   stdlib/run-command designs), then regenerate:
   `python3 "${CLAUDE_PLUGIN_ROOT}/lib/catalog.py" --blocks-dir <blocks_dir>`
5. **Write/merge the manifest** at `<manifests_dir>/<key>.json` where
   `key=$(python3 "${CLAUDE_PLUGIN_ROOT}/lib/store.py" key --branch B [--slot S])`.
   `branch`/`slot` go INSIDE the JSON (schemaVersion 1). On re-invocation
   MERGE: preserve unchanged scenarios, update `updatedAt`. Use slots for
   independent flows on one PR. Validate:
   `python3 "${CLAUDE_PLUGIN_ROOT}/lib/engine.py" apply --branch B [--slot S] --dry-run --json`
6. **Apply.** `python3 "${CLAUDE_PLUGIN_ROOT}/lib/engine.py" apply --branch B [--slot S] --json`
   — on `ok: false`, report the failing block + scenarioId and fix the
   manifest/block; never work around the engine.
7. **Write the plan record** (source of truth) at
   `<manifests_dir>/<key>.plan.json`: schemaVersion 1, branch, slot, steps
   with `id`, `instruction`, `expected`, `scenarioIds` (ids from the
   manifest).
8. **Render + post the comment.** Fill `templates/plan-comment.md` from the
   plan record; marker comes from the key. Post:
   `python3 "${CLAUDE_PLUGIN_ROOT}/lib/pr_comment.py" upsert --pr N --family plan --key K --body-file F --plans-dir <plans_dir>`
   (edits in place; preserves the human's checked boxes). No PR or gh
   failure → write the rendered plan to `<plans_dir>/<key>.md` and tell the
   user the path.
9. **STOP.** Plan ready. Do not drive a browser; hand off to
   test-pilot-execute.

## Rationalization table

| Excuse | Reality |
|---|---|
| "Quick insert, skip the block" | Untracked seed = leak. Block + engine, always. |
| "Gate refused; I'll pass --allow-protected" | Only the USER authorizes that flag. Stop and ask. |
| "I read CATALOG.md last time" | Blocks drift. Read it again. |
| "Plan is ready, may as well execute" | Execution is test-pilot-execute's job. STOP. |
| "I'll edit the PR comment by hand" | pr_comment.py owns markers/merge. Use upsert. |
