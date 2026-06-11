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
A TypeScript credits/billing API service: Express-style request handlers backed by a document store. This is a frozen review-crew eval fixture, not a real product.

## Threat model
multi-tenant

## Verify
command: npm run check

## Scope exclusions
- General accessibility — out of scope (no UI in this service).

## Focus hints
- failure-mode: money/credits flows are the crown jewels — races, partial writes, and migration safety on accounts/transfers matter most.
- security: ownership-scope on every mutation.

## Canonical patterns
- transaction idiom: `db.transaction(async (tx) => { ... })` (see src/db/client.ts)
- outbound-call wrapper: `retryFetch(url, { timeoutMs, retries })` in src/lib/retry-fetch.ts
- migration shape: every migration module exports `up()` AND `down()` (see src/migrations/README)

## Conventions
See CLAUDE.md.
