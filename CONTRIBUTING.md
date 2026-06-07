# Contributing

Thanks for your interest in **zwr-agent-skills**. This is a personal marketplace I
maintain, but it's public and contributions are genuinely welcome — bug reports,
fixes, docs, eval fixtures, and new ideas all help.

How it works: **anyone can fork the repo and open a pull request; I review and
merge.** Only the maintainer has merge rights, and `main` is protected so every
change lands through a PR with passing CI. That keeps the published plugins stable
for everyone installing them.

## Ways to contribute

- **Report a bug or request a feature** — open an issue. For review-crew, include
  the command you ran, what you expected, and what actually happened.
- **Fix or improve something** — open a PR (see below).
- **Improve a reviewer** — if you change agent methodology or the rubric, the eval
  gate has to stay green. Start with [`plugins/review-crew/eval/README.md`](plugins/review-crew/eval/README.md).

## Development setup

```bash
git clone https://github.com/<your-fork>/zwr-agent-skills
cd zwr-agent-skills
python3 -m pip install --upgrade pytest   # the only test dependency
```

To try a plugin from your working copy, add the local checkout as a marketplace:

```
/plugin marketplace add /absolute/path/to/zwr-agent-skills
/plugin install review-crew@zwr-agent-skills
```

Restart Claude Code after installing, and re-install after changes.

## Before you open a PR

Run the same checks CI runs — both must pass:

```bash
python3 .github/scripts/validate_marketplace.py
python3 -m pytest plugins/review-crew/eval/tests/ -q
# and, if you touched review-code's scripts:
python3 -m pytest plugins/review-crew/skills/review-code/tests/ -q
```

Then:

- **Use [Conventional Commits](https://www.conventionalcommits.org/)**, scoped by
  plugin — e.g. `fix(review-crew): …`, `feat(review-crew): …`, `docs: …`. See
  [CLAUDE.md](CLAUDE.md) for the convention.
- **Don't bump plugin versions.** Versioning, tags, and releases are
  maintainer-owned (see [RELEASING.md](RELEASING.md)). If your change is
  user-facing, add a bullet under `## [Unreleased]` in the relevant plugin's
  `CHANGELOG.md` and I'll cut the release.
- **Keep PRs focused.** One logical change per PR is easiest to review and merge.

## PR process

1. Fork → branch → commit → push to your fork.
2. Open a PR against `main`. CI runs automatically.
   - First-time contributors: a maintainer approves the workflow run before CI
     executes (a GitHub default for public repos).
3. I review, CI must be green, and I merge (squash; the branch is deleted on merge).

## Plugin structure rules

If you're adding or restructuring a plugin, a few hard rules (CI enforces the
manifest ones):

- `.claude-plugin/` contains **only** manifests — `plugin.json`, and
  `marketplace.json` at the repo root. Components live at the plugin root:
  `agents/`, `skills/`, `rubric/`, `eval/`.
- Each plugin's `plugin.json` carries its own SemVer `version`. Do **not** also set
  `version` in the marketplace entry — `plugin.json` wins silently and the
  duplicate masks it.
- A new plugin must be listed in `.claude-plugin/marketplace.json` with a `source`
  path that exists.

## Be decent

Be respectful and assume good faith. That's the whole code of conduct.
