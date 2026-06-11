<!-- review-profile · managed by review-crew · schema 1 -->
<!-- provenance — do not hand-edit this block; everything below it is yours to edit -->
schema: 1
plugin: review-crew@0.2.0
rubric-version: 2
generated: 2026-06-10
updated: 2026-06-10
status: stable
nudge-ack: {}
signals:
  dep-set: [pytest]
  default-branch: main
  forge: github
<!-- end provenance -->

## Project
Claude Code plugin marketplace (markdown skills/agents + Python lib helpers); the product is prompt/process content under plugins/, validated by pytest and a manifest validator.

## Threat model
single-user

## Verify
command: python3 .github/scripts/validate_marketplace.py && python3 -m pytest plugins/review-crew/lib/tests/ plugins/review-crew/eval/tests/ -q

## Scope exclusions
- No deployed service, API surface, or end-user data: multi-tenant/IDOR/SSRF/rate-limiting classes do not apply. Security scope = secrets committed to the repo and unsafe shell in bundled scripts/snippets.
- No UI: accessibility and responsive/mobile checks do not apply.

## Focus hints
- security: only repo-level concerns — committed secrets, unsafe shell patterns in skill code blocks or lib scripts.
- architecture: the base rubric is the single source of truth — skills/agents must reference it, never restate schemas/severities inline; lib helpers stay stdlib-only; plugin.json is the only version source.
- test: lib helpers and eval scorer need behavior-level pytest coverage; skill/agent markdown structure is guarded by test_skill_markdown.py — table/dimension changes must update it.
- code: the markdown content IS the product — cross-references between skills, agents, and the rubric (dimension names, findings filenames, table rows, version numbers) must stay mutually consistent.

## Canonical patterns
- findings-schema: plugins/review-crew/rubric/review-base.md:53
- per-agent-dispatch-table: plugins/review-crew/skills/review-code/SKILL.md:319
- agent-file-structure: plugins/review-crew/agents/security-reviewer.md:1
- lib-test-pattern: plugins/review-crew/lib/tests/test_review_store.py:1

## Conventions
See CLAUDE.md.
