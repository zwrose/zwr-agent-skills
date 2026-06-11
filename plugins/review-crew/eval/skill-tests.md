# review-crew skill-test state matrix

A documented, re-runnable **state matrix** of orchestrator behavior across the
dimensions the review skills branch on: **profile presence × profile status ×
verify-mode × review-init branches × strict fallback**. For each cell it records
the **trigger**, the **expected behavior**, and the **skill:section that
implements it** — then a **verification pass** confirms the cited prose actually
produces that behavior. (Live-run cells — section 7 — record a procedure and an outcome target instead of an implementing section.)

This is the **lightweight skill-testing** the design spec calls for
(`docs/superpowers/specs/2026-06-06-code-review-marketplace-design.md`, "Skill
testing": "cover the verify-mode × provisional × missing-profile state matrix").
It pairs with the finding-quality side of the eval — `eval/score.py` + the
`eval/fixtures/` golden diffs (`eval/README.md`) — which covers agent
recall/precision rather than orchestrator branching.

## Live-execution gate

The cells below are **verified against skill prose**, not by running the plugin.
Live execution is **gated on plugin install (Plan 6 Part B)**: the plugin is not
yet installed in a consumer repo, so the orchestrator's branches cannot be driven
end-to-end here. Until then **this matrix is the manual / re-runnable checklist**:
re-read each cited `file:section` after any skill edit and confirm the row still
holds. When the plugin is installed, these same cells become the script for live
smoke runs (drive each trigger in a consumer repo; confirm the expected behavior).

## How to re-run this verification (prose level)

For each cell: open the cited `SKILL.md` at the named section and confirm the prose
states the expected behavior. The "Verified" column records the result of the last
such pass; the "Fix" column flags cells whose prose had to be amended to match the
intended behavior (see **Prose fixes applied**, end of file).

Shared abbreviations: **R-CODE** = `skills/review-code/SKILL.md`, **R-PLAN** =
`skills/review-plan/SKILL.md`, **R-DEBT** = `skills/audit-debt/SKILL.md`,
**R-INIT** = `skills/review-init/SKILL.md`, **BASE** = `rubric/review-base.md`,
**DOCTOR** = `lib/repo_doctor.py`.

---

## 1. Profile presence

| # | Trigger | Expected behavior | Implements (file:section) | Verified | Fix |
|---|---------|-------------------|----------------------------|----------|-----|
| P1 | A review skill runs and the resolver returns **LOCATION == none** (profile absent at the resolved path) | Setup runs review-init's **create procedure inline** (review-init Steps 1–4: detect → interview → seed patterns → write the profile), then proceeds with the review. Does NOT invoke another skill mid-run; does NOT run staleness/reconcile/learning-loop. | R-CODE §1 Setup "Profile bootstrap"; R-PLAN §1 Setup "Profile bootstrap"; R-DEBT §1 Sweep Prep "Profile bootstrap" | yes | already correct |
| P2 | A review skill runs and the profile is **present** | The deterministic **staleness self-check** (`repo_doctor.py`) runs as the first action, captured into `DOCTOR_JSON`; the bootstrap is skipped. The check is guarded on the resolver's **EXISTS == true** so it runs **only** when a profile exists at the resolved location. | R-CODE §1 Setup "Staleness self-check (first action)"; R-PLAN §1; R-DEBT §1 | yes | already correct |
| P3 | Profile present but the **staleness check itself can't read it** (`readable: false`) | Tell the user "profile unreadable — re-run `/review-crew:review-init`" and **continue** (do not crash, do not block). `repo_doctor.py` fails soft (always exit 0). | R-CODE §1 Setup; R-PLAN §1; R-DEBT §1; DOCTOR module docstring ("FAIL SOFT … NEVER crash") | yes | already correct |
| P4 | No profile **and** the run is fully headless / non-interactive | The inline bootstrap writes a `status: provisional` profile from detected defaults with the **STRICT** threat model, then proceeds. | R-CODE §1 Setup "Profile bootstrap" (headless branch); R-PLAN §1; R-DEBT §1; R-INIT §3 "Headless / non-interactive" | yes | already correct |

## 2. Profile status

| # | Trigger | Expected behavior | Implements (file:section) | Verified | Fix |
|---|---------|-------------------|----------------------------|----------|-----|
| S1 | Profile `status: stable` | Normal operation. Calibration is read from the profile fields; no status-driven posture change. | BASE "Where calibration comes from"; R-CODE §1 Setup "Read the verify story" | yes | already correct |
| S2 | Profile `status: provisional` (e.g. a headless-written profile) | The review **proceeds normally** off the profile's recorded fields. Strictness is **not** keyed off the `status` flag — it follows the profile's **threat-model field**, which a headless/provisional profile sets to the STRICT value (multi-user, err toward flagging). So a provisional profile reviews with a strict posture **because of its strict threat-model field**, not because of the status flag. | R-INIT §3 (headless writes provisional + STRICT threat model); BASE "Calibration comes from the profile" (strictness is threat-model-driven) | yes | already correct |
| S6 | Existing profile is `status: provisional` **AND the run is interactive** | After the review output, the skill offers **ONE non-blocking** `AskUserQuestion` to confirm the provisional profile: **Confirm (mark stable)** flips `status: provisional → stable` + bumps `updated:` (only on the user's choice — never auto-flips), **Refresh via review-init** points at `/review-crew:review-init`, **Keep provisional** records a dismissal under the constant `provisional-confirm` signal (suppresses re-asking until the profile changes). **Skipped** when headless/non-interactive, when `status:` is already `stable`, or when `provisional-confirm` is already in `nudge-ack`. Closes spec gap C1. | R-CODE / R-PLAN / R-DEBT §"Learning Loop & Staleness Nudge" → "Provisional-profile confirmation (interactive only, end of run)" (byte-identical across the three) + "Recording a dismissal (shared)" (constant `provisional-confirm` signal); R-CODE end-of-loop / `--review-only` / `--post` end-of-run "(3) provisional-profile confirmation"; R-PLAN §5 / R-DEBT §5 end-of-run "(3) …" | yes | C1 closed — now implemented |
| S3 | Profile is **stale** — material drift detected (`rubric-version` advanced, `schema` outdated, ≥ DEP_THRESHOLD added deps, new top-level src dir, or verify-command / default-branch no longer resolves) | The review **runs to completion normally** (drift is informational only at Setup — "Do NOT act on `drift` here"). **After** the review output, a **single non-blocking nudge** line is printed — only when `message` is non-null. | R-CODE §1 Setup (capture `DOCTOR_JSON`, don't act on drift) + §"Learning Loop & Staleness Nudge" → "Staleness nudge (end of run)"; R-PLAN, R-DEBT same; DOCTOR "Material drift is ANY of" | yes | already correct |
| S4 | Stale profile, but the same drift signal was already dismissed (`nudge-ack` contains its `signal_hash`) | The nudge is **suppressed** — printed only when `nudge_acked` is false. Re-fires only once the signal changes (a new `signal_hash`). | R-CODE §"Staleness nudge (end of run)" ("only when … `nudge_acked` is false"); DOCTOR (`nudge_acked = signal_hash in nudge-ack`); R-PLAN, R-DEBT same | yes | already correct |
| S5 | User dismisses / ignores the staleness nudge | Record the dismissal by writing the doctor's `signal_hash` into the profile's `nudge-ack` map (the only profile write either nudge makes, and only on dismissal). | R-CODE §"Recording a dismissal (shared)"; R-PLAN, R-DEBT same | yes | already correct |

## 3. Verify-mode (review-code)

| # | Trigger | Expected behavior | Implements (file:section) | Verified | Fix |
|---|---------|-------------------|----------------------------|----------|-----|
| V1 | Profile `## Verify` has `command: <cmd>` | `VERIFY_CMD="<cmd>"`. The orchestrator's **verify gate (loop step 12)** AND the **fixer** both run `VERIFY_CMD` from the user's working tree, non-interactively, with a timeout. **Non-zero exit = HALT / `CHECK_FAILED`** — surface failing output, do not re-review on a broken tree. | R-CODE §1 Setup "Read the verify story" + §Auto-Fix Loop step 11–12 + §"The verify command" (`command:` branch) | yes | already correct |
| V2 | Profile `## Verify` has `mode: unverified` | No verify command. The **verify gate (step 12) is SKIPPED**; the fixer is told the verify command is `"none"` and runs no checks (cannot return `CHECK_FAILED`); commits proceed **ungated**. The dispatch summary AND the End-of-Loop summary both **warn** that fixes were committed without a verify gate. | R-CODE §1 Setup + §Auto-Fix Loop steps 11–12 + §Dispatch Summary ("Verify: … unverified") + §End-of-Loop Summary ("state that fixes were committed without a verify gate") + §"The verify command" (`unverified` branch) | yes | already correct |
| V3 | Profile `## Verify` has `mode: review-only`, default invocation (no `--post`/`--review-only`) | The default auto-fix path **degrades up front** to a **single review pass + the `--review-only` presentation** (no triage, no fixer, no commits, no loop). The degrade is **stated in the dispatch summary**, not buried. | R-CODE §Dispatch Summary ("Verify: … review-only — auto-fix disabled — this run degrades to a single pass + presentation") + §Auto-Fix Loop (gating clause: "verify story is not `mode: review-only`") + §Read-Only Paths `--review-only` ("A profile with `mode: review-only` makes the default path degrade into exactly this presentation") + §"The verify command" (`review-only` branch) | yes | already correct |
| V4 | `mode: unverified` AND a fixer escalation/verify path | Because there is no command, the fixer's step-3 check is skipped and `CHECK_FAILED` cannot arise from a verify failure on this path. | R-CODE §Auto-Fix Loop step 11 ("When the profile is `mode: unverified`, the fixer runs no checks and cannot return `CHECK_FAILED`") + Fixer subagent prompt step 3 | yes | already correct |

Note: `review-plan` and `audit-debt` do not have an auto-fix verify gate (plan
revises a doc; audit files issues), so the verify-mode cells V1–V4 are
review-code-specific. `audit-debt` does read `## Verify` once — as a
**doc-drift check** (does the command's binary resolve), cell D4 below.

## 4. review-init branches

| # | Trigger | Expected behavior | Implements (file:section) | Verified | Fix |
|---|---------|-------------------|----------------------------|----------|-----|
| I1 | `review-init`, no profile, **interactive** | **Create** mode: detect (no questions the repo answers), CLAUDE.md-aware tight inline interview (threat model / verify command / scope, skipping anything already answered), seed canonical patterns, write the profile, offer to commit. | R-INIT §2 "Choose mode" → §3 Create interview → §4 seed + write | yes | already correct |
| I2 | `review-init` (or inline bootstrap), **headless / non-interactive** | Skip the interview; write a `status: provisional` profile from detected defaults with the **STRICT** threat model; note in the body it was auto-generated. | R-INIT §3 "Headless / non-interactive" | yes | already correct |
| I3 | Create mode, **no `CLAUDE.md`** present | Offer the **three options**: (a) generate a starter `CLAUDE.md`, (b) inline a minimal conventions block into the profile, or (c) run code-reviewer correctness-only. Record the choice in `## Conventions`. | R-INIT §3 (no-CLAUDE.md branch) | yes | already correct |
| I4 | Create mode, **no verify command detected** | Offer the **three options**: (a) *set one up* (propose a `check` command for the user to add; do NOT edit their build config), (b) `mode: unverified`, (c) `mode: review-only`; record the choice. | R-INIT §3 question 2 (no-verify branch) | yes | already correct |
| I5 | `review-init`, **profile exists** | **Reconcile** mode: re-read CLAUDE.md + re-detect → apply migration steps → diff → preserve hand-edits & calibration → ask → bump provenance (may flip provisional → stable). | R-INIT §2 "Choose mode" → §5 Reconcile | yes | already correct |
| I6 | Reconcile, profile's `rubric-version` < engine's | Flag **rubric-version drift** in the proposed-changes diff ("rubric-version M→N: the review rubric has advanced; recalibrating") and update it on write. This is the same signal `repo_doctor.py` surfaces as a staleness nudge; reconcile is where it gets cleared. | R-INIT §5 step 4 ("Detect `rubric-version` drift") + step 7 (refresh rubric-version, clearing drift) | yes | already correct |
| I7 | Reconcile, profile has a `nudge-ack` map | **Preserve `nudge-ack` verbatim** across the reconcile (don't reset acks the user already dismissed); add the field as empty `{}` only if an older profile predates it. | R-INIT §5 step 5 + step 7 ("Preserve the `nudge-ack` map") | yes | already correct |
| I8 | Reconcile, profile `schema` **higher** than the plugin supports | **Read-side guard:** STOP with a loud message ("profile schema N is newer than this review-crew; upgrade the plugin"); do not rewrite it; degrade conservatively (strict posture) rather than misread newer fields. | R-INIT §5 step 2 "Read-side guard" | yes | already correct |
| I9 | Reconcile, unknown `## ` sections/keys present | **Preserve verbatim** (forward-compat); missing fields filled per create defaults only when safe; never silently delete user calibration. | R-INIT §5 step 5 "Migration rules" | yes | already correct |
| I10 | Reconcile, schema migration needed (`N→N+1`) | Apply each ordered migration step between the profile's `schema` and the plugin's. (Schema 1 is current; no migration steps yet — the contract is documented so the first one isn't a cross-file edit.) | R-INIT §5 step 3 | yes | already correct |

## 5. Strict fallback (missing field)

| # | Trigger | Expected behavior | Implements (file:section) | Verified | Fix |
|---|---------|-------------------|----------------------------|----------|-----|
| F1 | A needed field is absent in **both** the profile and `CLAUDE.md` (e.g. no threat model anywhere; a "present but empty" section counts as absent) | **STRICT posture**: assume a multi-user threat model and err toward flagging (safer to over-flag than miss an access-control bug). Minor/Nit never change the verdict regardless of strictness. | BASE "Calibration comes from the profile" + "Where calibration comes from" step 3 (strict fallback) | yes | already correct |
| F2 | The threat-model field specifically is absent everywhere | Strict threat-model fallback (multi-user). review-init **always writes** the threat model, so absence is rare — but the base rubric guarantees the fallback when it does happen. | BASE "Calibration comes from the profile"; design spec "A missing or empty `## ` section/field falls back to the base rubric; the threat-model fallback is strict" | yes | already correct |
| F3 | Reviewer strictness with the **whole profile** absent (the bootstrap is the primary handler, but if a subagent reads before/without one) | Subagent prompts state the precedence "Base rubric (binding) > CLAUDE.md > profile (adder) > **strict fallback when a needed field is absent in all of them**", so a subagent with no profile field still defaults strict. | R-CODE / R-PLAN / R-DEBT §"Dispatch Specialists" prompt "Calibration precedence"; BASE strict fallback | yes | already correct |

## 6. audit-debt-specific verify-mode read

| # | Trigger | Expected behavior | Implements (file:section) | Verified | Fix |
|---|---------|-------------------|----------------------------|----------|-----|
| D4 | `audit-debt` reads `## Verify` for the doc-drift dimension | If `command:` is set, confirm its binary resolves on PATH (missing → Minor "verify command does not resolve"). If `mode: unverified` or `mode: review-only`, there is no command — **skip this check**. | R-DEBT §4 "Documentation drift" ("The profile's verify command resolves") | yes | already correct |

## 7. Manual plan-time premortem scenario (live run)

Unlike cells P1–F3 above (verified against prose), this scenario is a
**live-run procedure** — an extension of, not a violation of, this file's
prose-verification framing. It requires the **updated plugin installed
locally** (a dev install / marketplace refresh carrying `premortem-reviewer`,
review-crew ≥ 0.3.0 — not the cached older release), otherwise the run says
nothing.

| # | Trigger | Expected behavior | Verified |
|---|---------|-------------------|----------|
| M1 | Run `/review-crew:review-plan plugins/review-crew/eval/samples/gappy-plan.md` in a consumer repo with the updated plugin | The premortem-reviewer's findings include (a) ≥1 `assumption-violation` finding citing the sample plan's heading + line for the unstated single-writer assumption ("Dedupe is handled by reading the dirty flag"), and (b) a missing **Failure-handling statement** finding for the push-then-clear multi-step flow (step 2 succeeds, step 3 fails → notes re-push forever or are lost, nothing in the plan says which) | record date + outcome in `eval/RESULTS.md` |

Procedure: run the command, read the round-1 chat findings (do NOT let the
revise loop edit the sample — answer Skip for every revision so the sample
stays gappy), confirm (a) and (b) appeared with plan-doc citations, then
record the outcome in `eval/RESULTS.md`.

---

## Coverage summary

- **Cells:** **28 prose-verified + 1 live-run (M1)** — Profile presence (4: P1–P4) + Profile status (6:
  S1–S6) + Verify-mode/review-code (4: V1–V4) + review-init branches (10: I1–I10)
  + Strict fallback (3: F1–F3) + audit-debt verify read (1: D4) = 4 + 6 + 4 + 10 +
  3 + 1 = **28**.
- **Verified against prose:** all 28.
- **Live-run (not prose-verified):** 1 — M1 (section 7); gated on plugin install, outcome recorded in `eval/RESULTS.md`.
- **Already correct (no prose change needed):** 27.
- **Implemented to close a flagged gap (C1):** 1 (S6 — provisional-profile
  confirmation on interactive review).

Every required dimension from the spec is covered: profile-presence (P1–P4) ×
status (S1–S6) × verify-mode (V1–V4, D4) × review-init-branches (I1–I10) ×
strict-fallback (F1–F3). Each cell cites the implementing `file:section`.

## Prose fixes applied

**None.** Every cited cell's prose already produced the intended behavior on a
close read. The three review skills (`review-code`, `review-plan`, `audit-debt`)
are uniform on the shared mechanisms (profile bootstrap, staleness self-check,
end-of-run nudge/learning-loop, calibration precedence), and `review-init` covers
all of its branches (create/headless/no-CLAUDE/no-verify/reconcile + migration +
read-side guard + nudge-ack preservation). No additive clarification was needed to
make any row hold.

## Concerns / open behavior questions

**C1 — "next interactive review offers to confirm a provisional profile" (spec
line ~195). — CLOSED (implemented).** The design spec says a *headless*-written
`status: provisional` profile should have "the next interactive review offer to
confirm it." This is now implemented as a fourth shared end-of-run behavior:
**Provisional-profile confirmation (interactive only, end of run)** in the
`## Learning Loop & Staleness Nudge` section, byte-identical across `review-code`,
`review-plan`, and `audit-debt` (cell **S6**). When an existing profile is
`status: provisional` and the run is interactive (and `provisional-confirm` is not
already in `nudge-ack`), the skill offers ONE non-blocking `AskUserQuestion` after
the review output: **Confirm (mark stable)** flips `status: provisional → stable`
+ bumps `updated:` (only on the user's explicit choice — it never auto-flips),
**Refresh via review-init** defers to `/review-crew:review-init`, and **Keep
provisional** records a dismissal under the constant `provisional-confirm` signal
so it doesn't re-ask until the profile changes. It is **skipped** on
headless/non-interactive runs (never blocks an automated run), when `status:` is
already `stable`, or when the signal is already acked. The confirm-and-upgrade
path still also exists in `review-init` reconcile (I5); the new behavior is the
proactive, non-blocking nudge the spec called for, placed at end of run alongside
the staleness nudge and learning-loop proposal — Setup still scopes reconcile out.
