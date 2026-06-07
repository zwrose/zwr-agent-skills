# Releasing

Releases are **manual and per-plugin**. Each plugin versions independently; the
version that matters for Claude Code's auto-update is the one in that plugin's
`plugins/<name>/.claude-plugin/plugin.json`.

## Why bumping matters

Claude Code resolves a plugin's version from `plugin.json` first. If the resolved
version equals what a user already has, `/plugin update` and auto-update **skip**
the plugin. So a release that doesn't bump `plugin.json` ships to nobody.

Do **not** add `version` to the plugin's entry in `marketplace.json` — `plugin.json`
wins silently and the duplicate would mask it. (CI fails on this.)

## Steps (per plugin)

1. **Pick the bump** from the changes since the last release, per SemVer:
   - `fix:` → patch (x.y.**Z**)
   - `feat:` → minor (x.**Y**.0)
   - breaking (`!` / `BREAKING CHANGE:`) → major (**X**.0.0)
2. **Bump** `plugins/<name>/.claude-plugin/plugin.json` → `version`.
3. **Update** `plugins/<name>/CHANGELOG.md` (move Unreleased items under the new
   version + date).
4. **Open a PR**, let CI pass, merge to `main`.
5. **Tag and release** from `main`:
   ```bash
   git checkout main && git pull
   git tag <plugin>-vX.Y.Z          # e.g. review-crew-v0.2.0
   git push origin <plugin>-vX.Y.Z
   gh release create <plugin>-vX.Y.Z \
     --title "<plugin> vX.Y.Z" \
     --notes-from-tag   # or paste the CHANGELOG section
   ```
6. **Verify** users see it: `/plugin marketplace update` then `/plugin update`.

## Tag convention

`<plugin>-vX.Y.Z` (e.g. `review-crew-v0.2.0`). The plain `vX.Y.Z` tag from the
initial release predates this convention; new releases use the namespaced form so
multiple plugins can release independently.

## Catalog version

`marketplace.json` `metadata.version` is the catalog's own version. Bump it when
the catalog itself changes meaningfully (a plugin added/removed). It does not
trigger plugin updates.
