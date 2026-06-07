# CLAUDE.md — web-handler eval fixture

Conventions the review-crew agents calibrate against for this fixture.

## Error handling
All user-facing error strings are centralized in `src/errors.ts` (the `apiErrors`
object). Never hardcode an error string or build an inline error payload in a
handler — use the matching `apiErrors.*` constant (e.g. `apiErrors.noteNotFound`).

## Handlers are auth-first
Every request handler must resolve the session first and short-circuit
unauthenticated requests before any data access:
`const session = await getSession(req); if (!session) return unauthorized(res);`

## Ownership-scoped queries
Notes are a user-scoped resource owned via `ownerId`. Every read, update, and
delete of a note MUST match on BOTH the note id AND `ownerId: session.userId`
(the dual-filter idiom). Matching by id alone is a cross-tenant access bug.

## Theme / styling
Colors and badges route through the design-token theme layer (`./theme-badge`).
Contrast is the theme's responsibility; do not hardcode color values in handlers.
