<!-- review-profile · managed by review-crew · schema 1 -->
<!-- provenance — do not hand-edit this block; everything below it is yours to edit -->
schema: 1
plugin: review-crew@0.1.0
rubric-version: 2
generated: 2026-06-06
updated: 2026-06-06
status: stable
nudge-ack: {}
signals:
  dep-set: [express@4, vitest@1]
  default-branch: main
  forge: github
<!-- end provenance -->

## Project
A TypeScript HTTP API service: Express-style request handlers backed by a document store, tested with Vitest. This is a frozen review-crew eval fixture, not a real product.

## Threat model
multi-tenant

## Verify
command: npm run check

## Scope exclusions
- General accessibility (WCAG audits) — out of scope; the design-token theme owns contrast.

## Focus hints
- security: IDOR / ownership-scope on every mutation; match resources by id AND ownerId.
- architecture: flag wrappers with a single caller (premature abstraction).
- test: claim/test alignment — a named "unauthorized" test must set the unauthenticated condition.
- code: error strings must route through the centralized error-constants module.

## Canonical patterns
- error-constants module: src/errors.ts (exports `apiErrors`, e.g. `apiErrors.noteNotFound`)
- ownership idiom: `{ id, ownerId: session.userId }` dual-filter on every user-scoped query/mutation (see `getNote` in src/handlers/notes.ts)
- auth wrapper: `getSession(req)` then auth-first short-circuit `if (!session) return unauthorized(res)` (see src/auth/session.ts)

## Conventions
See CLAUDE.md.
