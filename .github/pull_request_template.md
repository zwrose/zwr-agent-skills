## What & why

<!-- What does this change, and why? Link any related issue (e.g. "Closes #12"). -->

## Checklist

- [ ] Commits follow [Conventional Commits](https://www.conventionalcommits.org/), scoped by plugin
- [ ] `python3 .github/scripts/validate_marketplace.py` passes
- [ ] `python3 -m pytest plugins/review-crew/eval/tests/ -q` passes (and lib tests if touched)
- [ ] Added an `## [Unreleased]` CHANGELOG entry if the change is user-facing
- [ ] Did **not** bump plugin versions (maintainer-owned — see RELEASING.md)
