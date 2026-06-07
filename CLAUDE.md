# CLAUDE.md

Guidance for working in this repo. This is a **Claude Code plugin marketplace** —
a catalog (`.claude-plugin/marketplace.json`) listing plugins under `plugins/`.

## Layout

- `.claude-plugin/marketplace.json` — the catalog. Lists each plugin + its `source`.
- `plugins/<name>/.claude-plugin/plugin.json` — per-plugin manifest (name, version).
- `plugins/<name>/` — the plugin's components (`agents/`, `skills/`, `rubric/`, `eval/`).
- `.github/workflows/ci.yml` — validation (manifest checks + pytest).
- `.github/scripts/validate_marketplace.py` — catalog/manifest validator.
- `docs/` — internal design docs and plans. **Gitignored**, kept local only.

## Versioning (per-plugin SemVer)

Each plugin owns its version in its own `plugins/<name>/.claude-plugin/plugin.json`.
This is the version Claude Code uses for update detection.

Rules (enforced by `validate_marketplace.py`):

- **Bump `plugin.json` `version` on every plugin release.** Claude Code skips the
  update if the resolved version is unchanged, so shipping without bumping is
  invisible to existing users.
- **Never put `version` in a plugin's `marketplace.json` entry.** `plugin.json`
  wins silently, so a duplicate masks the real value. plugin.json is the single
  source of truth for plugin version.
- `marketplace.json` `metadata.version` is the catalog version — independent of
  plugin versions, low-churn, does not drive plugin updates.
- Plugins version **independently**; don't lockstep-bump untouched plugins (it
  churns users' caches for no change).

## Releasing

Manual, per-plugin. See [RELEASING.md](RELEASING.md). In short: bump the plugin's
`plugin.json` version, update its `CHANGELOG.md`, merge to `main`, then tag
`<plugin>-vX.Y.Z` and cut a GitHub Release.

## Commits — Conventional Commits

Use [Conventional Commits](https://www.conventionalcommits.org/). Scope by plugin.

- `feat(review-crew): add audit-debt command`
- `fix(review-crew): correct severity gate in score.py`
- `feat(review-crew)!: ...` or a `BREAKING CHANGE:` footer for breaking changes.
- Repo-wide changes (CI, license, governance): `chore:`, `ci:`, `docs:` with no
  scope or a `repo` scope.

Commit-type → SemVer intent: `fix:` → patch, `feat:` → minor, `!`/breaking → major.

## CI

Every PR and push to `main` runs `.github/workflows/ci.yml`:

1. `validate_marketplace.py` — manifests parse, sources exist, versions are valid
   SemVer, no duplicate-version trap.
2. `pytest plugins/review-crew/eval/tests/` — the eval scorer's unit tests.

Run both locally before pushing:

```bash
python3 .github/scripts/validate_marketplace.py
python3 -m pytest plugins/review-crew/eval/tests/ -q
```

## Branch protection

`main` requires a PR with passing CI. The repo owner may bypass when needed —
prefer PRs anyway.
