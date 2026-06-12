<!-- review-profile · managed by review-crew · schema 1 -->
<!-- provenance — do not hand-edit this block; everything below it is yours to edit -->
schema: 1
plugin: review-crew@0.3.0
rubric-version: 3
generated: 2026-06-11
updated: 2026-06-11
status: stable
nudge-ack: {}
signals:
  dep-set: [express@4, vitest@1]
  default-branch: main
  forge: github
<!-- end provenance -->

## Project
A single-user local desktop notes app (one process, one user). This is a frozen review-crew eval fixture, not a real product.

## Threat model
single-user

## Verify
command: npm run check

## Scope exclusions
- General accessibility — out of scope (no UI in this fixture's diff).

## Focus hints
- failure-mode: single-user local app — no concurrent invokers; the transaction idiom and retryFetch wrapper are the canonical guards.

## Canonical patterns
- transaction idiom: `db.transaction(async (tx) => { ... })` (see src/db/client.ts)
- outbound-call wrapper: `retryFetch(url, { timeoutMs, retries })` in src/lib/retry-fetch.ts

## Conventions
See CLAUDE.md.
