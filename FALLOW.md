# Fallow advisory lint

Standard installation includes Fallow for JavaScript and TypeScript projects.
The normal `lint-ai` run adds advisory dead-code and duplication reports after
existing linters. Existing lint coverage and failures are preserved. Fallow
findings return zero; failed analysis remains an error.

## Installation

Both providers and both platforms install Fallow during quality setup, without
a Fallow opt-in flag. The installer reuses `node_modules/.bin/fallow` when present;
otherwise it installs `fallow@3.33.1` under
`.cache/vibehelper-tools/fallow/3.33.1/`. Node 22 or newer and npm are required.
Missing prerequisites or installation failures are reported as unavailable;
other installer work continues. No analysis runs during installation.

Project dependency manifests, lockfiles, Fallow configuration and baselines stay
unchanged. Reruns reuse the binary. Clearing the cache requires another standard
install. Shared runtime files are refreshed only when unchanged and owned by an
installation receipt; custom files are preserved and reported for review.

Existing package controls still apply: `--dry-run` previews installation;
`--repo-only`, `--refresh-managed` and Codex's `--no-install` prevent package
changes. Disabling quality wiring skips this integration. Projects without
JavaScript or TypeScript sources skip installation.

## Default lint and scope

From the project root:

```bash
make -f vibe_scripts/codex/Makefile lint-ai
```

Use the Claude provider directory for Claude. Fallow uses the active provider
session's recorded edit paths. It does not select unrelated working-tree changes
or other sessions. Session edits can span several tasks; use explicit paths when
a session contains unrelated work.

Automatic scope requires an exported session identifier matching an existing
quality-hook record (`CODEX_THREAD_ID` or `CODEX_SESSION_ID` for Codex;
`CLAUDE_SESSION_ID` where provided by the Claude launcher). When that identifier
or recorded edits are unavailable, lint prints `not_checked` and suggests the
scoped command below. Claude launchers that do not export an identifier need this
explicit command. Missing Fallow also reports unavailable without blocking
ordinary lint. Neither case establishes Fallow coverage.

```bash
./vibe_scripts/codex/agent-verify.sh fallow --files src/tracking.ts src/events
```

Agents should use that command when dead-code or duplication analysis would help
the current task, especially when automatic scope is unavailable. For a requested
project-wide audit, use `fallow --all` instead. Explicit reports return nonzero
for missing tools, empty source scopes or failed analyses.

Fallow analyzes the full project using existing configuration, then VibeHelper
filters displayed findings to selected paths. Duplication findings include their
matching locations. Full reports remain in `.cache/ai-quality/`; displayed
findings do not establish project-wide coverage or authorize unrelated cleanup.
Coverage diagnostics remain visible.

`verify-ai` includes Fallow when it invokes `lint-ai`. Formatting and edit hooks
do not run Fallow. There are no automatic fixes, design reviews, health gates,
configuration changes or baseline updates.

See the [Fallow repository](https://github.com/fallow-rs/fallow) for native behavior.
The integration was tested with version 3.33.1.
