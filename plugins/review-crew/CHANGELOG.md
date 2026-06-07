# Changelog — review-crew

All notable changes to the `review-crew` plugin. Versions follow
[SemVer](https://semver.org); entries follow
[Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

## [0.1.0] — 2026-06-07

### Added

- Initial release. Multi-agent review of code, plans, and tech debt: a panel of
  four specialist reviewers (architecture / code / security / test) driven by a
  shared rubric and calibrated per-project via a generated review profile.
- Commands: `/review-crew:review-code`, `/review-crew:review-plan`,
  `/review-crew:audit-debt`, `/review-crew:review-init`.
- Eval harness (`eval/`) with frozen fixtures, a deterministic golden-eval scorer
  (`score.py`), and its unit tests.
