# test-pilot profile — {{project-name}}

<!-- provenance: plugin-version={{plugin-version}} profile-version=1
     status={{status}} created={{date}} updated={{date}} -->

This profile is a CLAUDE.md-aware ADDER: it carries only what the project's
CLAUDE.md does not already state. Conventions live in CLAUDE.md.

## App launch

- Dev command: `{{dev-command}}`
- Base URL: {{base-url}}
- Readiness probe: GET {{readiness-url}} → expect HTTP {{readiness-status}}
- May test-pilot start/stop the server: {{yes/no}}

## Auth strategy

{{One of: test-user credentials (env var NAMES only, never secrets) /
auth bypass mechanism / "requires the user's real browser session"
(forces Claude in Chrome). Describe exactly how execute gets a signed-in
session.}}

## Seed surfaces

Blocks may touch ONLY these surfaces:

- DB: connection from env var `{{DB_ENV_VAR}}` (name only — never the value)
- HTTP API: {{api-base}}
- Project CLI: {{seed-related npm/make scripts, if any}}

Protected targets (the engine's enforced gate REFUSES writes matching these
patterns — see the config block below): {{describe what is protected and why}}

## Browser tool order

{{e.g. chrome-devtools, claude-in-chrome — first available wins}}

## Machine-readable config

The engine parses ONLY this block; keep it in sync with the prose above.

```json test-pilot-config
{
  "schemaVersion": 1,
  "baseUrl": "{{base-url}}",
  "allowedOrigins": [],
  "dbEnvVar": "{{DB_ENV_VAR}}",
  "apiBase": "{{api-base}}",
  "protectedTargets": ["{{main-db-name}}", "{{main-db-uri-glob}}"],
  "browserTools": ["{{tool-1}}", "{{tool-2}}"],
  "devCommand": ["{{cmd}}", "{{arg}}"],
  "readinessUrl": "{{readiness-url}}",
  "mayManageServer": true
}
```
