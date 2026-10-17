# Codex Helper

Codex Helper adds a small AI coding workflow to another repo.

Use `MacOS/` on macOS. Use `WSL/` inside Ubuntu WSL.

It helps Codex:

- read the right project instructions
- check files it edits
- keep long tool output short
- save useful repo knowledge in `codebase-wiki/`

## What The Parts Do

- `install.sh` is the recommended entry point. It detects macOS or Ubuntu WSL
  and runs both parts in order.
- `part1.sh` adds Codex instructions, hooks, repo memory files, helper scripts,
  and prerequisite package-manager checks.
- `part2.sh` detects what is in the repo, asks before installing matching
  quality tools, then creates Makefile commands for checks.
- `MacOS/` is for macOS.
- `WSL/` is for Ubuntu WSL.

## Install

From the repository you want to configure, preview the full setup:

```sh
bash /path/to/CodexHelper/install.sh --dry-run
```

If the preview looks right, run the installer:

```sh
bash /path/to/CodexHelper/install.sh
```

The installer selects macOS or Ubuntu WSL, runs part 1, and then runs part 2
only if part 1 succeeds. It asks before installing tools or changing existing
files. Use `--repo /path/to/project` if you are not running it from the target
repository.

Rerun the installer with `--force` to update an existing installation in place:

```sh
bash /path/to/CodexHelper/install.sh --force
```

Ordinary `--force` updates skip Context7 setup and keep the existing
code-review-graph MCP configuration unless the code-review-graph package
version changes. New and fresh installs configure the MCP integration.

Chrome DevTools MCP is configured by default with
`npx chrome-devtools-mcp@latest`. The installer checks the effective Codex MCP
registration first, preserves correct registrations and custom launch options,
and prompts before adding missing or replacing stale settings. `--yes` adds a
missing registration non-interactively; `--force` or explicit `--chrome-mcp`
replaces stale settings non-interactively. Use `--no-chrome-mcp` to skip it.
The installer requires a package-supported Node.js version (currently
`^20.19.0 || ^22.12.0 || >=23`), npm, and current stable Chrome. On WSL,
install Linux Chrome or configure an explicit browser connection.

For a non-interactive fresh setup that replaces helper-managed skill/tool
files, use:

```sh
bash /path/to/CodexHelper/install.sh --fresh-install --yes
```

After installation, restart Codex in the configured repository, open `/hooks`,
review and trust the project hooks, and start a new thread. Hook trust cannot be
automated.

The installer adds the project-local Humanizer writing skill by default. To
skip it, use `--no-humanizer`:

```sh
bash /path/to/CodexHelper/install.sh --repo-only --no-humanizer
```

Restart Codex after installation, then invoke `$humanizer` or ask Codex to
humanize prose.

The installer includes [I Have ADHD](https://github.com/ayghri/i-have-adhd) by
default and enables its response style at the start of each Codex session.
Interactive runs ask before setup; `--yes` accepts it. To skip setup, use:

```sh
bash /path/to/CodexHelper/install.sh --no-adhd
```

This installs the upstream plugin for your user and appends a marked block of
response rules to `~/.codex/AGENTS.md`, preserving existing instructions.
It respects `CODEX_HOME` and uses an existing nonempty `AGENTS.override.md`
when that file takes precedence. Restart Codex to load the defaults; no manual
skill invocation is needed. Say `stop adhd mode` to stop for the current
session.

`--fresh-install` includes this setup; `--repo-only` skips it. Reruns preserve
the existing instruction block; `--force` also refreshes the marketplace.
`--no-adhd` skips setup without removing an earlier installation. To stop the
style permanently, remove the `CodexHelper: i-have-adhd` block from the global
instructions and use `--no-adhd` on later installer runs.

The platform-specific commands below remain available for advanced use or for
rerunning one phase by itself.

## Advanced macOS Install Flow

From the repository you want to configure, preview the full setup first:

```sh
bash /path/to/CodexHelper/MacOS/part1.sh --dry-run --no-apply-codex-config
```

If the preview looks right, run it:

```sh
bash /path/to/CodexHelper/MacOS/part1.sh --no-apply-codex-config
```

The full macOS setup installs RTK with Homebrew when the default RTK hook is
enabled. Use `--no-rtk-hook` to skip RTK installation and hook creation.

If you only want repository files and no global tool installs, add
`--repo-only`.

To run the same update through part 1 only, add `--force`:

```sh
bash /path/to/CodexHelper/MacOS/part1.sh --force --no-apply-codex-config
```

Generated `AGENTS.md` files require a separate reviewer agent after substantive
coding to check for breaking changes, scope creep, and drift from the request.

To install missing tools and replace helper-managed skill/tool files, including
Impeccable, Context7, and Chrome DevTools MCP configured for Codex, use
`--fresh-install`:

```sh
bash /path/to/CodexHelper/MacOS/part1.sh --fresh-install --no-apply-codex-config
```

If your network requires an internal Python package mirror for prerequisite
Python tooling, pass it to `part1.sh`:

```sh
bash /path/to/CodexHelper/MacOS/part1.sh --fresh-install \
  --python-index-url=https://your-python-mirror.example.com/simple \
  --no-apply-codex-config
```

Outside fresh-install mode, opt into Impeccable for frontend design work in
Codex with `--impeccable`, then restart Codex and approve the Impeccable hook
in `/hooks`:

```sh
bash /path/to/CodexHelper/MacOS/part1.sh --repo-only --impeccable
--no-apply-codex-config
```

Then preview and run the quality tooling setup:

```sh
bash /path/to/CodexHelper/MacOS/part2.sh --dry-run --fresh-install
bash /path/to/CodexHelper/MacOS/part2.sh --fresh-install
```

If your network requires an internal Python package mirror for quality-tool
installs, pass it to `part2.sh`:

```sh
bash /path/to/CodexHelper/MacOS/part2.sh --fresh-install \
  --python-index-url https://your-python-mirror.example.com/simple
```

## Advanced Ubuntu WSL Install Flow

Run these commands inside Ubuntu WSL, from the repository you want to
configure.

Preview the repo-only setup first:

```sh
bash /path/to/CodexHelper/WSL/part1.sh --dry-run --repo-only
--no-apply-codex-config
```

If the preview looks right, run it:

```sh
bash /path/to/CodexHelper/WSL/part1.sh --repo-only --no-apply-codex-config
```

If Ubuntu WSL is missing base tools and you want the bootstrap to install them
with `apt-get`, use:

```sh
bash /path/to/CodexHelper/WSL/part1.sh --install-prereqs
```

To install missing Ubuntu packages and Python tools as well as refresh installed
tools and files, use `--fresh-install`:

```sh
bash /path/to/CodexHelper/WSL/part1.sh --fresh-install --no-apply-codex-config
```

If your network requires an internal Python package mirror for prerequisite
Python tooling, pass it to `part1.sh`:

```sh
bash /path/to/CodexHelper/WSL/part1.sh --repo-only --fresh-install \
  --python-index-url=https://your-python-mirror.example.com/simple \
  --no-apply-codex-config
```

To opt into Impeccable for frontend design work in Codex, run `part1.sh` with
`--impeccable`, then restart Codex and approve the Impeccable hook in `/hooks`:

```sh
bash /path/to/CodexHelper/WSL/part1.sh --repo-only --impeccable \
  --no-apply-codex-config
```

Then preview and run the quality tooling setup:

```sh
bash /path/to/CodexHelper/WSL/part2.sh --dry-run --fresh-install
bash /path/to/CodexHelper/WSL/part2.sh --fresh-install
```

If your network requires an internal Python package mirror for quality-tool
installs, pass it to `part2.sh`:

```sh
bash /path/to/CodexHelper/WSL/part2.sh --fresh-install \
  --python-index-url https://your-python-mirror.example.com/simple
```

## After Install

Restart Codex in the repo root so it reloads `AGENTS.md`. If project hooks were
created, open `/hooks` in Codex CLI, review/trust the hooks, then start a new
thread.

Run one manual check after setup to confirm the generated checker works:

```sh
make -f vibe_scripts/codex/Makefile edited-ai
```

After that, trusted hooks handle normal edited-file checks automatically.

## Using The Workflow

Once hooks are trusted, Codex checks edited files automatically after edits and
before it stops. The hooks do not literally run
`make -f vibe_scripts/codex/Makefile edited-ai` ; they call the
same underlying checker, `vibe_scripts/codex/agent-check-edited.py` , for the
files
Codex edited.

You normally do not need to run `make -f vibe_scripts/codex/Makefile edited-ai`
after every small change.

What still has to be done manually:

- Trust project hooks in `/hooks` after install or after hook files change.
- Run `make -f vibe_scripts/codex/Makefile edited-ai` once after setup, when
  hooks are not trusted, or when you
  want an explicit quick check.
- Run `make -f vibe_scripts/codex/Makefile lint-ai` when you want broader
  lint checks.
- Run `make -f vibe_scripts/codex/Makefile verify-ai` for large, risky,
  security-sensitive, or cross-project
  changes.
- Run `make -f vibe_scripts/codex/Makefile savings-ai` to see the latest Codex
  session's recorded reduction
  estimates.
- Run `make -f vibe_scripts/codex/Makefile wiki-ai` only when stable project
  knowledge should be written to
  `codebase-wiki/`.
- Review generated `codebase-wiki/` changes before relying on them.

What the hooks do:

- `pre_tool_use_policy.py` runs before Codex shell commands. It blocks
  Git-writing commands and a few destructive shell commands when configured.
- `post_tool_use.py` snapshots RTK's project counters when a Codex session
  starts and ends. It also records the `context_savings` estimate from
  code-review-graph calls and from code-review-graph output inside a shell
  command. State is stored under `.cache/session-token-savings/`, and the hook
  does not print anything into active turns.
- `rtk_pre_tool_use.py` runs before Codex shell commands. It is created by
  default and rewrites eligible literal, read-only/noisy commands through `rtk`
  when RTK is installed. Shell expansions and potentially mutating forms pass
  through unchanged. Use `--no-rtk-hook` to skip it.
- `post_edit_check.py` runs after Codex edit tools. It checks the edited paths
  and records them for the stop hook.
- `stop_edited_check.py` runs before Codex stops. It rechecks recorded edited
  files and blocks stopping if the edited-file check fails.
- Hook launchers exit successfully without running when Codex has no Git
  working tree or the configured project hook file is missing, so a stale
  session cannot be trapped by a `/.codex/hooks/...` lookup.

Use these commands when needed:

- `make -f vibe_scripts/codex/Makefile edited-ai` checks only edited files. Use
  it after setup, when hooks are
  not trusted, or when you want a quick manual check.
- `make -f vibe_scripts/codex/Makefile lint-ai` runs broader lint checks.
- `make -f vibe_scripts/codex/Makefile verify-ai` runs bigger checks. Use it
  for large, risky,
  security-sensitive, or cross-project changes.
- `make -f vibe_scripts/codex/Makefile savings-ai` shows the latest session
  report. The `SessionEnd` hook
  quietly freezes the RTK endpoint and marks the session record as finished.
- `make -f vibe_scripts/codex/Makefile wiki-ai` updates `codebase-wiki/` . Use
  it only when you learned stable
  facts that future agents should know.
- `make -f vibe_scripts/codex/Makefile skills-check` audits managed
  skills/plugins after helper upgrades or
  when behavior seems stale.
- `make -f vibe_scripts/codex/Makefile skills-update` refreshes policy-approved
  managed skills/plugins;
  manual-policy items still require explicit refresh intent.
- `./vibe_scripts/codex/agent-verify.sh` runs the edited-file check by default.
  Pass
  `lint`, `verify`, `security`, or `graph` to choose another mode.

The session report keeps two measurements separate. RTK reports the estimated
difference between raw and filtered command output. The code-review-graph
number compares its compact response with the full changed files it could have
returned. It is a hypothetical context reduction, not a count from the model
provider. Other VibeHelper features do not expose a sound counterfactual, so
the report leaves them unmeasured. RTK's project counters can include another
simultaneous session in the same repository.

Semgrep integration is retired. `--security-scan` exits with an
unsupported-option
message. Existing global binaries and custom security checks are preserved.

Review generated `codebase-wiki/` changes before treating them as durable
project memory.

## What This Sets Up

- Short output for AI checks, with full logs in `.cache/`.
- Checks for files Codex edited.
- Optional Codex hooks that run those edited-file checks automatically.
- `AGENTS.md` and `agent/` instructions for future Codex sessions.
- Optional Impeccable integration tells Codex to use `$impeccable` for frontend
  design creation, polish, audit, critique, responsive, typography, color, and
  design-system tasks when the skill is installed.
- Frontend edits automatically trigger the generated design touch gate in
  `agent/codex/coding-rules.md` ; the edited-file checker also runs
  `impeccable detect`
  on touched frontend files when Impeccable is already available locally.
- `codebase-wiki/` for stable project knowledge.

## Notes

- `part1.sh` does not auto-trust Codex hooks. Trust them manually with `/hooks`.
- Both platform installers remove the unsupported Context7 `type` setting and
  consolidate integration hooks into `config.toml`. Original files are backed
  up, and imported hooks survive `--force` updates. Moved hooks need review in
  `/hooks`. Configuration migration requires Python 3.11+ or an existing
  `tomli` installation.
- `--no-apply-codex-config` skips adding the global Git blocker. Normal global
  integration setup still repairs its configuration; `--repo-only` confines
  that cleanup to the project unless global config application is requested.
- The full macOS `part1.sh` setup installs RTK through Homebrew and creates its
  PreToolUse hook by default. `--repo-only` skips the global install; if RTK is
  absent, the hook passes commands through unchanged.
- `part2.sh` only wires checks for tools that are actually available.
- The installers detect existing tools on `PATH`, in repo-local
  `node_modules/.bin`, in `.venv/bin`, and in Composer `vendor/bin` before
  prompting for installs. Project-local tools take precedence when checks are
  wired.
- Generated managed files include a `CodexHelper-Version` marker so reruns can
  report whether an existing file is current or older. `--force` backs up and
  updates those files and refreshes installed helper-managed tools through
  their detected owner, such as Homebrew, apt, uv, pipx, npm, or Composer.
  Unknown owners are reported and left unchanged.
- `--fresh-install` includes the forced refresh and also installs missing
  prerequisites and quality tools.
- `skills-lock.json` records the desired managed skill/plugin set. The generated
  `vibe_scripts/codex/sync-skills.py --update --force` refreshes installed
  managed
  items as well as missing or drifted ones.
- When `part2.sh` needs to create `package.json`, it writes a minimal private
  package with a sanitized name instead of relying on `npm init -y`; this avoids
  invalid names from folders such as `data%20science%20cloud%20platform`.
- The WSL scripts are intended for Ubuntu WSL, not every Linux distro.
- Full tool logs are stored under `.cache/`; AI-facing output is capped.
- Review generated `codebase-wiki/` sections before relying on them as durable
  project memory.

## Generated files and upgrades

Quality commands use `vibe_scripts/codex/Makefile`. Helper scripts live beside
it, and workflow instructions live in `agent/codex/`. The project root
Makefile and existing wiki pages are preserved, including with `--force`.

For an older installation, preview with `--dry-run --refresh-managed`, then
rerun with `--refresh-managed` to refresh managed instructions and hooks without
package updates. `--force` also updates detected installed packages. Restart
the agent and review/trust updated hooks where required. Recognizable generated
Semgrep recipes are backed up and removed from legacy
Makefiles. Other legacy targets and shared `vibe_scripts/*` files remain for
manual review; provider commands use their scoped helpers. Existing user
instructions may need their command
paths updated manually.

`--cleanup` removes only unchanged files recorded as owned by this provider.
Modified files and legacy files without ownership evidence require manual
review.
It preserves the project
Makefile, other provider, shared skills, wiki, notes, caches, skill lock,
`.mcp.json`, `.gitignore` and legacy helpers. Review those shared files manually
if removing VibeHelper entirely. Skill configuration and project memory remain
shared; separating scripts does not add missing language checks.

On `--force`, an existing global `~/.codex/hooks/ai_git_blocker.py` with a
CodexHelper version marker is backed up and refreshed. This also works with
`--no-apply-codex-config`: the configuration stays unchanged. Creating a new
global hook still requires the existing opt-in. Unmarked global files are
left alone during automatic refresh. The new global policy requires
provider-specific commands in every project where it runs, so migrate those
projects too. An old global hook can reject the new `make -f` commands.

See [installation flow and evidence](../INSTALLATION-FLOW.md) for managed
refresh,
component states, migration, and repair-loop verification.

## Fallow advisory lint

Standard installation includes JavaScript/TypeScript dead-code and duplication
reports in `lint-ai`. Findings are advisory and use the active session's recorded
edits when available. Missing session scope prompts an explicit scoped report.
See [Fallow setup and scope](../FALLOW.md) for commands and coverage limits.

Impeccable design review runs from the completion hook when UI source changes,
separately from ordinary lint. See [design review scope](../DESIGN-REVIEW.md) for
upgrade behavior and detection limits.
