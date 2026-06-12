# zwr-agent-skills

**Agent tools for [Claude Code](https://code.claude.com) — built for my workflow, open for yours.**

A small, opinionated marketplace of Claude Code plugins. Everything here earns its
keep in my own day-to-day; it's public so you can use it too. Add the marketplace
once, then install the plugins you want:

```
/plugin marketplace add zwrose/zwr-agent-skills
```

---

## review-crew

**A standing review panel for your code, plans, and tech debt.**

Most AI review is one model skimming a diff for "anything wrong?" review-crew is
built differently: a panel of **five specialist reviewers** — architecture, code,
security, test, and failure-mode (premortem) — each with its own methodology, running in parallel under a
shared severity rubric. An orchestrator compiles their findings, triages each one,
and (for code) drives an **auto-fix loop** that applies the safe fixes and
re-reviews until nothing Critical or Important remains.

Two things make it more than a clever prompt:

- **Calibrated to your project.** `review-init` generates a
  `.claude/review-profile.md` — your threat model, verify command, scope, and
  canonical patterns — so reviews match *your* codebase instead of generic best
  practices. Severity rules, diff-scope discipline, and "cite `file:line` or drop
  the finding" are enforced when findings are compiled, not left to hope.
- **Measured, not vibes.** The reviewer agents ship with a frozen eval harness
  (planted findings + decoy traps, a deterministic scorer) and a non-regression
  gate: a change has to prove it catches real issues without inflating false
  positives before it lands. See [`plugins/review-crew/eval/`](plugins/review-crew/eval/).

It's also context-frugal — the orchestrator never loads the full diff or raw agent
output into its own conversation; subagents do the heavy reading and write
structured results to disk.

### Commands

| Command | Use it to… |
| --- | --- |
| `/review-crew:review-init` | Generate or refresh a project's review profile (**run this first**). |
| `/review-crew:review-code` | Review an open PR or local branch and auto-fix what it finds — commits locally, never pushes. |
| `/review-crew:review-plan` | Red-team a draft plan or design spec **before** any code is written. |
| `/review-crew:audit-debt` | Periodically sweep a whole repo for accumulated debt → a prioritized set of GitHub issues. |

### Install & first run

```
/plugin marketplace add zwrose/zwr-agent-skills
/plugin install review-crew@zwr-agent-skills
```

Then, in any project:

```
/review-crew:review-init      # calibrate to this repo
/review-crew:review-code      # review the current branch / PR
```

---

## Contributing

Issues and pull requests are welcome. Fork the repo, open a PR, and I'll review it
and help get it merged. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Zach Rose
