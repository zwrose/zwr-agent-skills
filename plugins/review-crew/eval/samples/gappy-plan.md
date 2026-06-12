# Sync Pipeline Implementation Plan (SAMPLE — deliberately gappy)

> This is a frozen review-crew eval sample, not a real plan. It exists for the
> manual plan-time premortem scenario in `eval/skill-tests.md`: it contains a
> multi-step write with NO failure-handling statement and an unstated
> load-bearing assumption. Do not "fix" it.

**Goal:** Sync local notes to the remote search index.

## Design

A `syncNotes()` job runs every 5 minutes:

1. Query all notes where `dirty == true`.
2. Push each note's content to the remote index over HTTP.
3. Set `dirty = false` on every pushed note.

The job is dispatched by the existing scheduler. Dedupe is handled by reading
the `dirty` flag — a note already pushed in a running job won't be picked up
again because the flag is cleared at the end.

## Tests

- `syncNotes pushes dirty notes` — happy path.
- `syncNotes skips clean notes`.
