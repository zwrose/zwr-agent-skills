# Changelog — test-pilot

All notable changes to the `test-pilot` plugin. Versions follow
[SemVer](https://semver.org); entries follow
[Keep a Changelog](https://keepachangelog.com).

## [Unreleased]

## [0.1.0] — 2026-06-11

### Added

- Initial release: `test-pilot-init` / `test-pilot-plan` /
  `test-pilot-execute` skills.
- Stdlib-only Python engine (`lib/`): dual-location store with always-global
  state, injective artifact keys, diff-aware transactional apply/clean,
  protected-target gate with declared block targets, subprocess block
  contract with PEP-723/`uv` routing, PR-comment management with checkbox
  preservation and diagnostic scrubbing, CATALOG generation.
- Templates for the profile, starter blocks, and both PR comments.
