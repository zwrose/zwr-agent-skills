# CLAUDE.md — refactor eval fixture

Conventions the review-crew agents calibrate against for this fixture.

## Module layering (acyclic)
Service modules under `src/services/` form a directed dependency graph that must
stay acyclic. `orders` may depend on `billing`; `billing` must NOT depend back on
`orders`. A two-module import cycle is an architecture defect — break it by
hoisting the shared piece into a third module both can depend on.

## Function-level authorization
Every privileged/admin action must verify `session.isAdmin` (read from the
server-verified session) and reject non-admins before doing any work. The session
check (`if (!session) return unauthorized(res)`) is NOT sufficient for admin
actions — those additionally require the privilege check.

## Updates use an explicit field allowlist
Mutations destructure a known set of updatable fields. Never spread `req.body`
(or any client-controlled object) into a `$set` / update payload — that lets a
client write ownership, privilege, or status fields it should not control.

## Ownership-scoped queries
Orders are user-scoped via `ownerId`. Reads/updates of a single order match on
both the order id AND `ownerId: session.userId`.

## Data access escapes by default
`db.query(...)` uses parameter placeholders (`?`) and the data layer escapes bound
values. Passing user input as a bound parameter is safe; do not treat it as
injection.
