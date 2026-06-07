# A/B Results — agent improvements vs faithful-port baseline

**Method:** offline dual-dispatch (see `README.md`). For each agent × fixture, a reviewer-simulating subagent ran twice — **baseline** (agent + rubric at `git show 5a05714:…`) and **improved** (working tree) — blind to the expected-findings manifest, then scored against `fixtures/<name>/expected.json` (scope-aware matching per README §Scoring).

**Date:** 2026-06-06. **Baseline ref:** `5a05714`. **Fixtures:** `web-handler`, `refactor`.

## Gate

**GREEN — improved ≥ baseline on recall AND precision for every agent, both fixtures.**
- **No lost findings:** improved caught every seed baseline caught (same seeds).
- **No FP inflation:** neither variant flagged any of the 6 planted traps.
- **One net-new true positive** from the improved side (web-handler Test, mutation-survival lens).
- **No regressions → no agent revision required.**

## Per-agent results

### web-handler fixture (one seed/dimension + 3 traps)

| Agent | Seed | Baseline | Improved | Traps flagged (B/I) |
|---|---|---|---|---|
| Architecture | premature-abstraction (`persistNote`) | caught (Minor) | caught (Minor, `abstraction-justification`) | 0 / 0 |
| Code | hardcoded-error-string | caught (Important) | caught (Important, `error-handling`) | 0 / 0 |
| Security | BOLA (`updateNote` id-alone) | caught (Critical) | caught (Critical, `BOLA` + evidence chain) | 0 / 0 |
| Test | claim-test-mismatch | caught (Important) | caught (Important) **+ 1 net-new** weak-assertion (mutation-survival) | 0 / 0 |

### refactor fixture (new-rule seeds + 3 traps)

| Agent | Seed | Baseline | Improved | Traps flagged (B/I) |
|---|---|---|---|---|
| Architecture | AcyclicDependencies (billing↔orders cycle) | caught (Important) | caught (Important, `Acyclic Dependencies`) | 0 / 0 |
| Code | cognitive-complexity (`classifyOrder`) | caught (Important) | caught (Minor, `cognitive-complexity` + Low null-deref extra) | 0 / 0 |
| Security | BFLA + BOPLA (`cancelAllOrders`, body-spread) | caught both (Critical/Important) | caught both (`BFLA`/`BOPLA` + evidence chain) | 0 / 0 |
| Test | mock-echo (`getOrderTotal` test) | caught (Important) | caught (Important) + risk-weighted coverage finding | 0 / 0 |

Traps correctly skipped by **both** variants in every case: pre-existing context-line BOLA smell, `./responses` sibling import, theme-token color (web-handler); size-only growth, clear-non-duplicative mapper, framework-escaped bound param (refactor).

## New rules: did they fire?

- **Acyclic Dependencies (arch):** fired on its seed (improved labels it). Note: baseline also caught the cycle — the fixture's CLAUDE.md states the acyclic convention, so baseline flagged it via module-coupling. Improved adds the named taxonomy.
- **cognitive-complexity (code):** fired on its seed. Baseline also caught it (the fixture profile's code focus hint nudges toward nested-branching), framed generically; improved names it and (correctly) risk-weights the pure-function case to Minor.
- **BFLA / BOPLA (security):** fired and labeled. These existed in the baseline under other names ("Privileged routes" Critical, "Mass-assignment" Important), so recall is equal; the improvement is the OWASP taxonomy label + the required **evidence chain** (entry → unguarded sink → reachable principal).
- **mutation-survival (test):** the clearest differential — improved flagged a weak `updates the title` assertion (a dropped-`$set` mutant survives) that **baseline did not** (web-handler). Net-new true positive.
- **size-needs-2nd-symptom (arch):** acted as a precision guardrail; the size-only trap was skipped by both (it sat under the raw-size threshold, so no asymmetry materialized — a sharper future fixture could exercise the >threshold-but-single-symptom case).
- **confidence + Chain-of-Verification:** every improved-variant finding carried `confidence`; the one genuinely uncertain finding (code null-deref on `order.items`) was correctly emitted at **Low** rather than dropped or over-asserted.

## Tokens

Improved output is modestly larger per finding (the `taxonomy`, `confidence`, and evidence-chain fields). Rough per-dispatch output tokens (improved / baseline): Architecture ~110/80, Code ~280/180, Security ~150/120, Test ~400/150. The increase buys structured, labeled, confidence-gated findings; it does not change recall or precision. Acceptable per the spec (record quality **and** tokens).

## Honest read

On these two fixtures the improvements are **non-regressing** (the spec's bar) and add: named taxonomies, a confidence gate, the security evidence-chain, and one extra real catch from the mutation-survival lens. They did **not** dramatically out-recall the baseline here, because both fixtures' profiles/CLAUDE.md already steered the baseline toward the seeded issues (focus hints for cognitive-complexity, the documented acyclic convention, pre-existing BFLA/BOPLA rules). That is a fair result, not a null one: the precision guardrails (size-2nd-symptom, threat-model-gated SSRF, diff-scoped secrets) are most valuable on *noisier* inputs than these tightly-seeded fixtures, and the taxonomy/confidence/evidence structure is a quality gain independent of recall. A future Plan-6 golden-eval can add a higher-noise fixture (a sprawling diff with many near-miss traps) to stress the precision guardrails directly, and the install-time live A/B against weekly-eats will measure the agents as registered `subagent_type`s rather than inlined methodology.
