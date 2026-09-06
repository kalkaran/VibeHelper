# VibeHelper

VibeHelper adds a small Codex or Claude Code workflow to an existing project.
It supports macOS and Ubuntu WSL.

The generated files tell the assistant what to read, how to check its edits,
and where to keep useful repo notes. Hooks can enforce shell policies, check
edited files, and trim noisy command output before it reaches the assistant.

## What it helps with

- The assistant starts with project instructions instead of guessing how the
  repo works.
- Hooks check edited files after changes and again before the assistant stops.
- Long lint and security output gets trimmed so the assistant can focus on the
  useful part.
- Stable project knowledge can live in `codebase-wiki/` instead of being
  rediscovered every session.
- Session notes can be kept in `notes/`, which makes it easier to trace what
  was asked and what changed.
- Writing help is included through Humanizer, so generated prose can sound less
  stiff.
- Existing helper-managed tools can be refreshed without replacing unrelated
  installations.

## What you gain

You spend less time repeating repo context, and future sessions have a place to
pick up durable knowledge. Each project can use only the workflow pieces that
fit.

This repository is intended for personal workflow bootstrapping across
multiple projects.

## Install it in another repo

Run the installer from the repository you want to configure. Preview first.

For Codex:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --dry-run
bash /path/to/VibeHelper/CodexHelper/install.sh
```

For Claude Code:

```sh
bash /path/to/VibeHelper/ClaudeHelper/install.sh --dry-run
bash /path/to/VibeHelper/ClaudeHelper/install.sh
```

If you run the command from somewhere else, pass the target repo:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --repo /path/to/project
bash /path/to/VibeHelper/ClaudeHelper/install.sh --repo /path/to/project
```

Both installers also support `--repo-only` when you want project files without
global tool installs.

Use the default command for a first install. On a rerun, versioned managed
files stay in place unless you request an update.

For an existing installation, `--force` is the in-place update path. It refreshes
managed files and installed helper-managed tools:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --force
bash /path/to/VibeHelper/ClaudeHelper/install.sh --force
```

Refreshes use the detected package owner, such as Homebrew, apt, uv, pipx, npm,
or Composer. Unknown or unrelated installations are reported and left
unchanged. Managed files are backed up before replacement. If ClaudeHelper
finds an existing Codex setup, it preserves shared workflow files and the
Makefile while refreshing ClaudeHelper-managed tools. Ordinary `--force`
updates skip Context7 setup. They also keep the existing code-review-graph MCP
configuration unless the code-review-graph package version changes.

Both helpers have a non-interactive fresh setup that installs missing tools and
replaces existing helper-managed skill and tool files. Codex fresh installs
explicitly target Codex, including Humanizer, Unlazy, Graphify, Context7, and
Impeccable:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --fresh-install --yes
bash /path/to/VibeHelper/ClaudeHelper/install.sh --fresh-install --yes
```

Fresh installs configure the code-review-graph MCP integration. The generated
Codex and Claude instructions require a separate reviewer agent after
substantive coding to check for breaking changes, scope creep, and drift from
the request.

When RTK is installed, the generated command-routing hook can trim eligible
read-only output before the assistant sees it. Shell expansions and potentially
mutating commands pass through unchanged. Savings depend on the command, but
these are reasonable estimates:

- `git status` or a short `ls`: about 50-150 tokens saved when the raw output
  includes untracked files or repeated metadata.
- `git diff`, `git log`, `grep`, or `rg` output across several files: often
  500-2,000 tokens saved.
- Very large diffs, logs, or search results: commonly 5,000+ tokens saved because
  RTK keeps the useful summary instead of sending the whole dump.

## What gets added

Depending on the helper and options you choose, VibeHelper can add:

- project instructions such as `AGENTS.md` or `CLAUDE.md`
- local hooks for safer assistant runs
- `agent/` workflow guidance
- `codebase-wiki/` repo memory
- `notes/` session logs
- helper scripts for edited-file checks
- Makefile commands such as `make edited-ai`, `make lint-ai`, `make verify-ai`,
  `make wiki-ai`, `make skills-check`, and `make skills-update`

## What it can install

Defaults and opt-in tools differ between the two helpers. They can install:

- Codex project files: instructions, hooks, repo notes, and local helper scripts
  for Codex.
- Claude Code project files: instructions, hooks, slash commands, repo notes,
  and local helper scripts for Claude.
- Humanizer: rewrites generated prose so docs, READMEs, and release notes sound
  less stiff.
- Context7: gives agents current library and framework docs when they need API
  details.
- Ponytail: pushes agents toward smaller changes and less overbuilt code.
- Unlazy: keeps long or multi-part tasks moving until their acceptance checks
  pass.
- Graphify: maps a repo into a graph so agents can understand large codebases faster.
- code-review-graph: tracks code relationships so agents can review blast radius
  before changing files.
- Impeccable: helps with frontend design, layout, polish, and UI checks when enabled.
- LLM Council: provides optional multi-model review for high-value decisions.
- RTK: trims noisy read-only command output before it reaches the agent.
- Ruff: formats and lints Python.
- Biome: formats and checks JavaScript, TypeScript, CSS, and JSON.
- HTMLHint: checks HTML.
- markdownlint-cli2: checks Markdown files.
- ShellCheck and shfmt: check and format shell scripts.
- PHP_CodeSniffer and PHPStan: check PHP style, syntax, and types.
- Semgrep: runs optional static security checks.

## After install

Restart Codex or Claude Code in the configured repo so it reloads the new files.

For Codex, open `/hooks`, review and trust the project hooks, then start a new
thread. Hook trust cannot be automated. For Claude Code, restart in the repo
root so it reloads `CLAUDE.md` and `.claude/settings.local.json`.

Run one check after setup:

```sh
make edited-ai
```

Trusted hooks handle normal edited-file checks after that. Use `make lint-ai`
for broader linting and `make verify-ai` for large, risky, security-sensitive,
or cross-project changes.

## More detail

- [CodexHelper README](CodexHelper/README.md)
- [ClaudeHelper README](ClaudeHelper/README.md)
