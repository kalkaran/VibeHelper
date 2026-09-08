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
Impeccable and Context7 configured for Codex, use `--fresh-install`:

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
bash /path/to/CodexHelper/MacOS/part1.sh --repo-only --impeccable --no-apply-codex-config
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
bash /path/to/CodexHelper/WSL/part1.sh --dry-run --repo-only --no-apply-codex-config
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
make edited-ai
```

After that, trusted hooks handle normal edited-file checks automatically.

## Using The Workflow

Once hooks are trusted, Codex checks edited files automatically after edits and
before it stops. The hooks do not literally run `make edited-ai`; they call the
same underlying checker, `vibe_scripts/agent-check-edited.py`, for the files
Codex edited.

You normally do not need to run `make edited-ai` after every small change.

What still has to be done manually:

- Trust project hooks in `/hooks` after install or after hook files change.
- Run `make edited-ai` once after setup, when hooks are not trusted, or when you
  want an explicit quick check.
- Run `make lint-ai` when you want broader non-Semgrep lint checks.
- Run `make verify-ai` for large, risky, security-sensitive, or cross-project
  changes.
- Run `make savings-ai` to see the latest Codex session's recorded reduction
  estimates.
- Run `make wiki-ai` only when stable project knowledge should be written to
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

- `make edited-ai` checks only edited files. Use it after setup, when hooks are
  not trusted, or when you want a quick manual check.
- `make lint-ai` runs broader lint checks without Semgrep.
- `make verify-ai` runs bigger checks. Use it for large, risky,
  security-sensitive, or cross-project changes. It may run Semgrep.
- `make savings-ai` shows the latest session report. The `SessionEnd` hook
  quietly freezes the RTK endpoint and marks the session record as finished.
- `make wiki-ai` updates `codebase-wiki/`. Use it only when you learned stable
  facts that future agents should know.
- `make skills-check` audits managed skills/plugins after helper upgrades or
  when behavior seems stale.
- `make skills-update` refreshes policy-approved managed skills/plugins;
  manual-policy items still require explicit refresh intent.
- `./vibe_scripts/agent-verify.sh` runs the edited-file check by default. Pass
  `lint`, `verify`, `security`, or `graph` to choose another mode.

The session report keeps two measurements separate. RTK reports the estimated
difference between raw and filtered command output. The code-review-graph
number compares its compact response with the full changed files it could have
returned. It is a hypothetical context reduction, not a count from the model
provider. Other VibeHelper features do not expose a sound counterfactual, so
the report leaves them unmeasured. RTK's project counters can include another
simultaneous session in the same repository.

Semgrep can be verbose. It is not automatic. It runs only when you ask for
security or verify checks, or when you start `part1.sh` with `--security-scan`.

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
  `agent/coding-rules.md`; the edited-file checker also runs `impeccable detect`
  on touched frontend files when Impeccable is already available locally.
- `codebase-wiki/` for stable project knowledge.

## Notes

- `part1.sh` does not auto-trust Codex hooks. Trust them manually with `/hooks`.
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
  `vibe_scripts/sync-skills.py --update --force` refreshes installed managed
  items as well as missing or drifted ones.
- When `part2.sh` needs to create `package.json`, it writes a minimal private
  package with a sanitized name instead of relying on `npm init -y`; this avoids
  invalid names from folders such as `data%20science%20cloud%20platform`.
- Semgrep is wired as an explicit security check, not as an automatic hook.
- The WSL scripts are intended for Ubuntu WSL, not every Linux distro.
- Full tool logs are stored under `.cache/`; AI-facing output is capped.
- Review generated `codebase-wiki/` sections before relying on them as durable
  project memory.
