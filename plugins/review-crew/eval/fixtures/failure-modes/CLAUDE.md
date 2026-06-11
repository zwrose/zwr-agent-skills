# CLAUDE.md — failure-modes eval fixture

Conventions the review-crew agents calibrate against for this fixture.

## Transactions
Multi-step writes that must land together use the data layer's transaction:
`db.transaction(async (tx) => { ... })`. A sequence of dependent `db.*.update`
calls outside a transaction is a partial-failure bug.

## Outbound calls
All outbound HTTP goes through `retryFetch(url, { timeoutMs, retries })` from
`src/lib/retry-fetch.ts` — it enforces a timeout and bounded retries. Raw
`fetch` in service code has no timeout and no failure story.

## Migrations
Every migration module exports BOTH `up()` and `down()`. Destructive steps
(dropping or unsetting the old field) belong in a LATER migration, after a
verification window — never in the same pass that writes the new field.

## Concurrency
This is a multi-tenant service; any handler can run concurrently with itself.
Check-then-act flows on shared rows need an atomic guard (compare-and-set
filter, unique constraint, or a transaction with the read inside).
