# zwr-agent-skills

Personal [Claude Code](https://code.claude.com) marketplace.

## Plugins

- **review-crew** — multi-agent review of code, plans, and tech debt. A panel of
  four specialist reviewers (architecture / code / security / test) driven by a
  shared rubric, calibrated to each project via a generated `.claude/review-profile.md`.
  Commands: `/review-crew:review-code`, `/review-crew:review-plan`,
  `/review-crew:audit-debt`, `/review-crew:review-init`.

## Install

```
/plugin marketplace add zwrose/zwr-agent-skills
/plugin install review-crew@zwr-agent-skills
```

Then run `/review-crew:review-init` in a project to generate its review profile.
