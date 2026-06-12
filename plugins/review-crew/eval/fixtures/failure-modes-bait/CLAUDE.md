# CLAUDE.md — failure-modes-bait eval fixture

Conventions the review-crew agents calibrate against for this fixture.

## Deployment shape
Single-user desktop app. Exactly one local process serves exactly one user;
handlers are never invoked concurrently.

## Transactions
Multi-step writes that must land together use the data layer's atomic
transaction: `db.transaction(async (tx) => { ... })`. A mid-flow crash inside
the callback rolls the whole transaction back.

## Outbound calls
All outbound HTTP goes through `retryFetch(url, { timeoutMs, retries })` from
`src/lib/retry-fetch.ts` — it enforces a timeout and bounded retries.
