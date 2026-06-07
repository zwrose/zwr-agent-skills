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
A TypeScript order/billing service: layered modules under src/services + Express-style admin handlers, tested with Vitest. This is a frozen review-crew eval fixture, not a real product.

## Threat model
multi-tenant

## Verify
command: npm run check

## Scope exclusions
- General accessibility (WCAG audits) — out of scope (no UI surface here).

## Focus hints
- security: function-level authorization on every admin/privileged action (BFLA); never mass-assign request bodies into updates (BOPLA).
- architecture: keep the service module graph acyclic; flag a newly-introduced import cycle between two modules.
- test: flag mock-echo — a test that asserts only the value its own mock was configured to return.
- code: flag cognitive-complexity spikes (nested branching) the diff introduces, not raw line growth alone.

## Canonical patterns
- privilege check: admin/privileged actions read `session.isAdmin` from the server-verified session and reject otherwise (e.g. `if (!session.isAdmin) return forbidden(res)`)
- ownership idiom: `{ id, ownerId: session.userId }` dual-filter on every user-scoped query/mutation
- update allowlist: updates destructure an explicit field allowlist; never spread `req.body` into `$set`
- auth wrapper: `getSession(req)` then `if (!session) return unauthorized(res)` (see src/auth/session.ts)

## Conventions
See CLAUDE.md.
