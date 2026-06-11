# review-crew agent A/B eval

A frozen measurement instrument for **Plan 5 (agent improvements)**. It proves the
improved reviewer agents are **at least as good as** the faithful-port baseline —
no seeded findings lost, no new traps flagged — per agent, on two committed
fixtures.

This directory holds the fixtures and the procedure. It does **not** run the A/B
itself — a later controller task (and Plan 6's committed golden-eval runner) drives
the dispatches described below. Nothing here modifies any agent, skill, or rubric.

## Goal

For every `(agent × fixture)`, show **improved ≥ baseline** on:

- **recall** — fraction of the fixture's seeded findings (for that agent's
  dimension) the agent emitted, and
- **precision** — the agent did NOT flag any of the fixture's traps (a flagged
  trap is a false positive), and net-new non-seeded findings are inspected as
  potential FPs,

while also recording **output tokens** per dispatch (cost signal). The gate is a
non-regression gate: improved must not lose a seed nor gain a trap relative to
baseline, for any agent.

## Fixtures

Both fixtures are stack-neutral (a generic TS/JS-ish web service). Each is a
self-contained set: a unified diff, a frozen review profile, a `CLAUDE.md`, and an
`expected.json` ground truth.

| Fixture | Diff | What it seeds |
| --- | --- | --- |
| `fixtures/web-handler/` | adds a request handler + its test | one in-scope seed per dimension (Security BOLA, Code hardcoded-error, Architecture premature-abstraction, Test claim/test-mismatch) + three traps (pre-existing context-line smell, intentional sibling import, theme-token contrast) |
| `fixtures/refactor/`    | refactors existing service modules | the new Plan-5 rules: Architecture AcyclicDependencies (import cycle), Code cognitive-complexity, Security BFLA (missing function-level authz) + BOPLA (mass-assignment), Test mock-echo + three traps (size-only growth, clear-non-duplicative shape, framework-escaped input) |
| `fixtures/failure-modes/` | adds credits/voucher/notify/cache/migration flows | **premortem-only, single-variant** — five Failure-Mode seeds, one per diff-mode class (`partial-failure`, `concurrency/race`, `dependency-failure`, `resource-exhaustion`, `migration-rollback`); multi-tenant profile; bar: `matched == total` |
| `fixtures/failure-modes-bait/` | adds guarded sync/backup/archive flows | **premortem-only, single-variant** — zero seeds, three whole-flow traps (`profile-excluded-race`, `retry-wrapped`, `framework-transaction`); single-user profile; bar: `traps_flagged == 0` |

`expected.json` schema (both fixtures):

```json
{
  "seeds": [
    {"dimension":"<Architecture|Code|Security|Test|Failure-Mode>","severity":"<tier>","taxonomy":"<term>","file":"<path>","lineHint":"<the + line text>","why":"..."}
  ],
  "traps": [
    {"file":"<path>","lineHint":"<line text>","whyNotFlagged":"<context-line | theme-token | sibling-import | size-only | framework-escaped | clear-non-duplicative | profile-excluded-race | retry-wrapped | framework-transaction>"}
  ]
}
```

Every seed's `lineHint` is the text of a `+` line in that fixture's `diff.txt`
(in diff scope). Every trap is genuinely out of scope: it sits on a context
(unchanged) line, or is an intentional/in-scope-correct `+` line that the agents'
Do-NOT-Flag lists exclude. Flagging any trap is a regression.

## Sources under test

Run each agent in two variants with **identical context except the agent-file
methodology and the base-rubric content**:

- **Baseline:** the faithful-port commit `5a05714`.
  - agent file: `git show 5a05714:plugins/review-crew/agents/<x>.md`
  - base rubric: `git show 5a05714:plugins/review-crew/rubric/review-base.md`
- **Improved:** the current working tree.
  - agent file: `plugins/review-crew/agents/<x>.md`
  - base rubric: `plugins/review-crew/rubric/review-base.md`

where `<x>` is one of `architecture-reviewer`, `code-reviewer`,
`security-reviewer`, `test-reviewer`.

Extract the four pieces of text up front, e.g.:

```bash
EVAL_DIR=plugins/review-crew/eval
OUT=$(mktemp -d /tmp/review-ab-XXXX)

for x in architecture-reviewer code-reviewer security-reviewer test-reviewer; do
  git show 5a05714:plugins/review-crew/agents/$x.md       > "$OUT/$x.baseline.md"
  cp        plugins/review-crew/agents/$x.md              "$OUT/$x.improved.md"
done
git show 5a05714:plugins/review-crew/rubric/review-base.md > "$OUT/rubric.baseline.md"
cp        plugins/review-crew/rubric/review-base.md        "$OUT/rubric.improved.md"
```

## Procedure — dual dispatch

For each **(agent × fixture × variant)** dispatch one reviewer-simulating
subagent. There are `4 agents × 2 fixtures × 2 variants = 16` dispatches. (The single-variant failure-modes fixtures are NOT part of this 16 — see §Single-variant fixtures.) Each
variant pair (baseline, improved) for a given `(agent, fixture)` gets the SAME
diff, profile, and CLAUDE.md — only the pasted agent file and pasted rubric differ.

The prompt template **mirrors how `review-code` dispatches its specialists** (see
`skills/review-code/SKILL.md` → "Dispatch Specialists in Parallel"): a context-only
block naming the diff path, the base rubric, the profile, the CLAUDE.md, and the
findings output path, plus the binding diff-scope and verification rules. The one
difference from production is that `review-code` dispatches a bundled agent **by
`subagent_type`** (the methodology is the agent's own system prompt); here we
A/B two *versions* of that methodology, so we paste the agent file and the rubric
inline into a generic subagent instead.

### Prompt template (per dispatch)

```
You are the `<Dimension>` reviewer. Apply the methodology in the agent file
below to the diff at <EVAL_DIR>/fixtures/<fixture>/diff.txt.

## Context files
- Diff: <EVAL_DIR>/fixtures/<fixture>/diff.txt
- Base rubric (severity, verification rules, findings format): inline below
- Project profile (threat model, scope, focus hints, canonical patterns): <EVAL_DIR>/fixtures/<fixture>/profile.md
- CLAUDE.md (project conventions): <EVAL_DIR>/fixtures/<fixture>/CLAUDE.md

## Calibration precedence
Base rubric (binding) > CLAUDE.md (conventions) > profile (adder over CLAUDE.md)
> strict fallback when a needed field is absent in all of them.

## Diff-scope rule — CRITICAL
You are reviewing CHANGES in this diff. Only flag code on `+` or `-` lines.
Context lines (no prefix) and unchanged code are pre-existing — SKIP them,
even if they violate conventions.

## Verification rules
- `file:line` citation required. No citation → drop the finding before writing.
- Before flagging "missing X", grep-equivalent reasoning: a thing under another
  name is not missing.
- For Important findings, check reachability; if already guarded, downgrade/drop.

## Output
Emit findings JSON per the base rubric's "Findings output format" section to
<output path>. Set `dimension` to "<Dimension>" on every entry. If you have
nothing to flag, write an empty array (`[]`).

----- AGENT FILE (methodology) -----
[paste the variant's agent file: $OUT/<x>.{baseline|improved}.md]

----- BASE RUBRIC -----
[paste the variant's rubric: $OUT/rubric.{baseline|improved}.md]
```

Substitutions per dispatch: `<Dimension>` ∈ {Architecture, Code, Security, Test};
`<x>` the matching agent slug; `<fixture>` ∈ {web-handler, refactor}; output path
e.g. `$OUT/<x>.<fixture>.<variant>.json`.

Because the fixtures are frozen on disk (not a live git tree), subagents read the
diff/profile/CLAUDE.md directly from `<EVAL_DIR>/fixtures/<fixture>/`. There is no
worktree, no PR, no prior-comments file — those `review-code` context lines are
intentionally omitted.

## Scoring

For each emitted findings file, match against that fixture's `expected.json`:

1. **Recall (seeds).** For each seed whose `dimension` matches the agent, check
   whether any emitted finding lands on the seed AND carries the right dimension.
   Matching rule depends on the seed's scope:
   - **Function-scoped taxonomies** (`cognitive-complexity`, `mock-echo`,
     `AcyclicDependencies`, `premature-abstraction`, `BFLA`, plus the Failure-Mode
     whole-flow classes `concurrency/race`, `partial-failure`, `dependency-failure`,
     `resource-exhaustion`, `migration-rollback`) — a finding matches if its cited
     line falls **anywhere within the seeded function/symbol's line span** in
     `diff.txt`, since reviewers legitimately cite the declaration, the offending
     branch, or the assertion. Do NOT use the ±2 line window for these.
   - **Line-scoped taxonomies** (`BOLA`, `BOPLA`, `hardcoded-error-string`,
     `claim-test-mismatch`, and any single-statement seed) — match `lineHint`
     text against the cited `file:line` allowing **±2 lines of slack**.
   Recall = matched seeds / seeds for that dimension.
   Note the seed's `taxonomy` and whether the finding labeled it (the new Plan-5
   rules — AcyclicDependencies, cognitive-complexity, BFLA, BOPLA, mock-echo —
   should fire on their target seeds in the **improved** variant).
2. **Precision / traps (FP).** For each trap, check whether any emitted finding
   cites the trap's location — use the same scope-aware matching as step 1
   (function-span for a function-scoped trap such as `size-only` or
   `clear-non-duplicative`; ±2 lines otherwise). Any hit is a false positive —
   record it. Precision penalty = flagged traps.
3. **Net-new non-seeded findings.** Any emitted finding that matches neither a
   seed nor a trap is a candidate FP — list it for human inspection (it may be a
   legitimate extra catch or noise; do not auto-count it against the gate, but
   surface it).
4. **Output tokens.** Record each dispatch's output-token count.

Tabulate **baseline vs improved**, per agent, per fixture:

| agent | fixture | variant | seeds hit / total | traps flagged | net-new | output tokens |
| --- | --- | --- | --- | --- | --- | --- |

and a per-agent roll-up noting **which new Plan-5 rules fired on their target
seeds** (only meaningful for the improved variant on `fixtures/refactor/`).

## Gate

Improved passes iff, **for every agent**:

- improved recall ≥ baseline recall (no seeded finding the baseline caught is
  lost), AND
- improved traps-flagged ≤ baseline traps-flagged AND improved flags **zero**
  traps (no newly-flagged trap; precision does not regress).

Any regression (a lost seed or a newly-flagged trap) means the agent revision is
wrong: revise that agent file and re-run its four dispatches (2 fixtures × 2
variants). Do not weaken a fixture to make an agent pass — the fixtures are the
frozen ground truth.

## Single-variant fixtures (failure-modes, failure-modes-bait)

The two `failure-modes*` fixtures were added with the `premortem-reviewer`
agent (review-crew 0.3.0). No five-agent baseline exists at `5a05714`, so the
A/B gate above does not apply to them — they run **single-variant**
(`gate: n/a`) with mechanical acceptance bars, scored on a **premortem-only
dispatch** (trap matching is dimension-agnostic, so a five-crew union would
conflate other agents' findings into the bars):

- `failure-modes` passes iff `matched == total` (zero missed seeds).
- `failure-modes-bait` passes iff `traps_flagged == 0`.

Everything in §Sources-under-test, §Procedure, and §Gate above stays anchored
to the historical four-agent baseline as-is; this section is additive. The
Failure-Mode whole-flow taxonomy classes (`concurrency/race`,
`partial-failure`, `dependency-failure`, `resource-exhaustion`,
`migration-rollback`) are function-scoped (±15) in `score.py`; only
`detectability` and `assumption-violation` stay line-scoped (±2). Bait trap
`whyNotFlagged` reasons must carry their scope token (substring detection) and
trap `lineHints` must be unique line texts (first-occurrence-wins resolution).
Liveness smokes in `eval/tests/test_score.py` assert every seed and trap in
both fixtures resolves and can fire.

## Running the scorer

`score.py` makes the §Scoring rules above re-runnable: it loads a fixture's
`expected.json` + `diff.txt`, matches a set of emitted findings against the seeds
and traps (scope-aware, exactly as defined in §Scoring), and prints the result as
JSON.

```bash
EVAL_DIR=plugins/review-crew/eval

# Score one variant's findings against a fixture. The findings input is a dir
# (loads every *.json), a glob, or a single JSON file; each file is a JSON array
# of emitted findings (each has at least dimension, file, line).
python3 "$EVAL_DIR/score.py" "$EVAL_DIR/fixtures/refactor" "$OUT/code-reviewer.refactor.improved.json"

# Compare improved vs a baseline to get the non-regression gate:
python3 "$EVAL_DIR/score.py" "$EVAL_DIR/fixtures/refactor" \
    "$OUT/code-reviewer.refactor.improved.json" \
    --baseline "$OUT/code-reviewer.refactor.baseline.json"
```

Output shape:

```json
{ "recall":    {"matched": N, "total": M, "by_dimension": {"<dim>": {"matched": x, "total": y}}, "missed": ["<seed taxonomy/title>"]},
  "precision": {"traps_flagged": K, "trap_hits": ["<file>:<line>"]},
  "net_new":   [<emitted findings matching neither a seed nor a trap — candidate FPs for human inspection>],
  "gate":      "PASS | FAIL | n/a" }
```

`gate` is `PASS` iff (improved recall ≥ baseline recall) AND (improved
traps_flagged ≤ baseline traps_flagged) AND (improved flags zero traps); `FAIL`
on any regression; `n/a` when no `--baseline` is given.

**Seed-line resolution & the function-span window.** A seed's `lineHint` is the
text of a `+` line in `diff.txt`, so the scorer parses the diff once to map each
added (and context) line's text to its new-file line number, then resolves each
seed/trap to a concrete line. Line-scoped taxonomies match a finding within an
**exact ±2** lines. For the **function-scoped** taxonomies (`cognitive-complexity`,
`mock-echo`, `AcyclicDependencies`, `premature-abstraction`, `BFLA`, plus the
Failure-Mode whole-flow classes `concurrency/race`, `partial-failure`,
`dependency-failure`, `resource-exhaustion`, `migration-rollback`) — where exact
span extraction from a diff is fuzzy — the scorer uses the README's documented
generous rule: a finding matches if it is within **±15 lines (K=15)**
of the seed's resolved line **OR** carries the same `taxonomy` (anywhere in the
file). Function-scoped *traps* (`size-only`, `clear-non-duplicative`, and the
Failure-Mode bait reasons `profile-excluded-race`/`retry-wrapped`/`framework-transaction`)
use the same ±15 window; all other traps are line-scoped (±2).

Unit tests live in `eval/tests/`:

```bash
python3 -m pytest plugins/review-crew/eval/tests/ -q
```

## Provenance / reuse

These fixtures + this README are also the seed for **Plan 6's committed
golden-eval runner**: the same `expected.json` ground truth and the same
dual-dispatch shape become an automated, checked-in eval. Keep the fixtures frozen
so historical runs stay comparable; add new fixtures rather than mutating these.
