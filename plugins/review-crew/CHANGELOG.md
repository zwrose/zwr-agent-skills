# Changelog — review-crew

All notable changes to the `review-crew` plugin. Versions follow
[SemVer](https://semver.org); entries follow
[Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

## [0.2.0] — 2026-06-07

### Added

- Per-project profile/decisions storage choice: **in-repo** (committed,
  team-shared) or **global** (`~/.claude/review-crew/`, zero working-tree
  footprint, shared across all git worktrees of a repo). Chosen once at first
  use via a halt-and-ask prompt; overridable with `REVIEW_CREW_STORAGE`.
- `lib/review_store.py` resolver: dual-key (origin URL + git-common-dir),
  self-healing per-key pointer store.

### Changed

- Shared Python helpers moved from `skills/review-code/` to `lib/`
  (`repo_doctor.py`, `decisions.py`, `circuit_breaker.py`,
  `resolve_diff_lines.py`); their tests now run in CI.
- The review skills resolve the profile/decisions location instead of assuming
  `.claude/review-profile.md`.

## [0.1.0] — 2026-06-07

### Added

- Initial release. Multi-agent review of code, plans, and tech debt: a panel of
  four specialist reviewers (architecture / code / security / test) driven by a
  shared rubric and calibrated per-project via a generated review profile.
- Commands: `/review-crew:review-code`, `/review-crew:review-plan`,
  `/review-crew:audit-debt`, `/review-crew:review-init`.
- Eval harness (`eval/`) with frozen fixtures, a deterministic golden-eval scorer
  (`score.py`), and its unit tests.
