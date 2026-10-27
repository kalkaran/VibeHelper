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

Run the installer from the project you want to configure. If it is outside a
Git repository, the installer runs `git init`. Existing repositories and linked
worktrees are reused. Dry runs only report the initialization. No files are
staged or committed. Preview first.

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

For an existing installation, `--force` is the in-place update path. It
refreshes
managed files and installed helper-managed tools:

```sh
bash /path/to/VibeHelper/CodexHelper/install.sh --force
bash /path/to/VibeHelper/ClaudeHelper/install.sh --force
```

Refreshes use the detected package owner, such as Homebrew, apt, uv, pipx, npm,
or Composer. Unknown or unrelated installations are reported and left
unchanged. Managed files are backed up before replacement. If ClaudeHelper
finds an existing Codex setup, it refreshes its own scripts, hooks, instructions
and Makefile while preserving shared project files. Ordinary `--force`
updates skip Context7 setup. They also keep the existing code-review-graph MCP
configuration unless the code-review-graph package version changes.

Both installers configure Chrome DevTools MCP through
`npx chrome-devtools-mcp@latest`. A correct existing registration is left
unchanged on normal, force, and fresh runs because `@latest` resolves updates
when the server starts. `--force` repairs stale registrations; use
`--no-chrome-mcp` to skip setup. Chrome itself is not installed: use current
stable Chrome and a package-supported Node.js version (currently
`^20.19.0 || ^22.12.0 || >=23`). Inside WSL, install Linux Chrome or configure
an explicit connection to Chrome running outside WSL.

Both helpers have a non-interactive fresh setup that installs missing tools and
replaces existing helper-managed skill and tool files. Codex fresh installs
explicitly target Codex, including Humanizer, Unlazy, Graphify, Context7,
Chrome DevTools MCP, and Impeccable:

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
mutating commands pass through unchanged. RTK records the raw and filtered
output sizes for routed commands and estimates tokens from those sizes. The
result is useful for comparing output reduction, but it is not a model-provider
usage or billing count.

CodexHelper records RTK's project-counter change during a Codex session and
code-review-graph's own context estimate. Run
`make -f vibe_scripts/codex/Makefile savings-ai` for the latest
report. The figures stay separate because they measure different baselines;
VibeHelper does not claim a combined total for features that cannot be measured.

## Local Codex usage dashboard

The usage tracker reads recorded Codex usage across local projects. Its live
dashboard lets you select a project, compare model totals, and open the largest
sessions to inspect their turns. Each browser tab retains its own project
selection. Refreshes reuse unchanged session data; switching projects filters
the cached snapshot.

From a project with CodexHelper installed, run:

```sh
python3 vibe_scripts/codex/usage-tracker.py setup
~/.local/bin/vibe-usage
```

Setup requires Python 3.11 or later and Git. It saves the Codex log location and
installs a shared launcher. On macOS, you can also open
`~/Applications/VibeHelper Usage.command`. The dashboard starts when opened;
keep its terminal open while using it. It does not start automatically at login.
ClaudeHelper includes the same tracker under `vibe_scripts/claude/`; it currently
tracks Codex usage only.

Collection and rendering run locally without model calls or usage uploads.
Setup downloads the pinned parser unless supplied with a local checkout.
Recorded tokens, cached input and reasoning are shown separately; cached input
and reasoning are subsets, not additional usage. These counts do not measure
weekly subscription allowance or exact per-file waste.

See the [usage tracker guide](templates/vibe_scripts/usage-tracker.md) for log
locations, date filters, offline reports and installation details.

## Stage completed changes

After code changes, agents write `git-add.txt` with the reviewed files for that
task. Review the diffs, then run from the project root:

```sh
git add --pathspec-from-file=git-add.txt
```

Directly below the staging command in their final reply, agents review every
file in `git-add.txt`: its exact path, what changed, and why the task needs it.
List one file per line with its own explanation; never group multiple file paths
on one line.
These are files selected for staging. Excluded files and reasons are reported
separately.

Agents inspect each diff and list only necessary source, tests, configuration,
docs and requested assets. They exclude secrets, local tooling, logs, caches,
backups, installed dependencies, build output and temporary reports. Package
manifests belong in the list for intentional package configuration changes;
lockfiles require an intentional dependency or toolchain change.
The list uses explicit literal file paths; it is never a dump of all Git status
output. Ignored files and the manifest itself are excluded.

Files containing unrelated edits are left out and reported for manual hunk review.
Every entry requires review of its full staged and unstaged diff, or its full
contents for a new file. Agents record why each file belongs and check for
secrets, private data and unintended changes. Required companion files must
also be reviewed or already committed; incomplete dependent changes stay out.
Uncertainty means exclusion, with the reason reported to you.

After formatting, agents check exact paths and preview the nonempty list with
`git add --dry-run --pathspec-from-file=git-add.txt`. The final reviewer checks
the list and evidence. Any later file edits require a fresh review. Existing
staged changes are reported separately; this list does not remove them.

Staging still includes every edit in each listed file. Running
`git add git-add.txt` only stages the manifest.
Agents leave staging and committing to you.

## Free model delegation with OmniRoute

Installers ask whether to install OmniRoute and its dashboard, with no as the
default. Only an explicit yes starts setup; empty input or EOF skips it.
`--yes` does not approve this optional installation. Both helpers share
`~/.local/share/vibehelper/omniroute/`. An existing
compatible global executable is reused; otherwise the installer installs the
verified version privately. Project scripts and instructions are installed for
Codex and Claude on both macOS and WSL. `--force` updates existing projects.

Setup asks you to choose a dashboard password. It preserves an existing
password. Unattended setup needs `VIBEHELPER_OMNIROUTE_PASSWORD` supplied by you;
without it, the runtime installs and configuration stays pending. It never
invents a dashboard password. The dashboard is at <http://127.0.0.1:20130>.
Use `--no-omniroute` to skip runtime setup, or `--repo-only` for project files.
Node.js must satisfy OmniRoute's requirement: `>=22.22.2 <23 || >=24 <27`.

Create an [OpenRouter account and API key](https://openrouter.ai/settings/keys),
then run the matching project helper and enter the key at its hidden prompt:

```sh
python3 vibe_scripts/codex/omniroute.py connect openrouter
# For Claude projects, use vibe_scripts/claude/omniroute.py instead.
```

For a suitable simple task, agents ask whether you want to try OmniRoute to
save tokens, unless you already authorized it. They wait for your reply before
sending task context. If you decline, the current agent continues. After approval,
they try one bounded request. The installer ships no fixed model list: the helper
discovers models for eligible accounts, checks context size, and ranks candidates
for `code`, `edit`, `summary` or `extract` using capability and name hints. Use
`ask --task edit` with the task on stdin; `route --task edit` previews selection
without inference. Ranking is heuristic and every proposal needs main-agent
review and the normal project checks. Token savings are not yet measured.

The generated `agent/codex/usage.md` (or Claude equivalent) describes a fixed
12-task comparison across Python, JavaScript and PHP. Startup instructions load
detailed quality and staging procedures when needed. Hook reports keep full
diagnostics on disk; compact messages do not skip checks or unblock failures.
`python3 vibe_scripts/codex/project-memory.py usage` reports local text sizes
separately from unknown token and weekly-allowance savings. After reviewing an
OmniRoute proposal, use `review --attempt-id ID --outcome accepted` (or `revised`
or `rejected`) to record its quality outcome without changing route ranking.

Add accounts through the dashboard. Use `report` to find connection IDs, then
`confirm-free CONNECTION_ID --billing-disabled` only after verifying free-only
API usage, disabled paid billing and no spendable paid credits. `all` confirms
current accounts only. Revoke with `revoke-free CONNECTION_ID` before changing
credentials or billing; confirmations do not track credential replacements.
OpenRouter uses independently verified zero-price models without confirmation.
Kilo Gateway also requires zero-price catalog entries. Other audited API-key
providers are Gemini, Groq, NVIDIA, Cloudflare AI and SambaNova.

Every `ask` checks cooldowns, model locks and recent OpenRouter free-request
estimates. Other quota observations are advisory because their scope varies.
Discovery uses account caches, fetching model metadata on cache misses. It sends
no inference probes. The single attempt uses a temporary key restricted to its
account and model, deleted afterward and backed by expiry. Managed setup disables
cloud sync. Unavailable routes or rejected output return work to the main agent.

Use `start` after reboot; no login service is installed. `status` checks gateway
policy; successful inference needs a real task. Project cleanup leaves the shared
runtime and accounts intact. Provider privacy terms apply to submitted snippets.

For a manual inventory, run `python3 vibe_scripts/codex/omniroute.py report`
(or the Claude path). Add `--json` for structured output, or `--refresh-models`
to fetch current model metadata for the seven audited API-key providers listed
in the installed OmniRoute guide. Default reporting reads local caches only.
The report includes connections, model IDs, recorded gateway tokens, quota
snapshot times and local rate-limit status. Unreported remaining tokens and
provider limits are unknown. OpenRouter free-request counters are local estimates;
model catalogs do not prove account access or free pricing.

## What gets added

Depending on the helper and options you choose, VibeHelper can add:

- project instructions such as `AGENTS.md` or `CLAUDE.md`
- local hooks for safer assistant runs
- `agent/` workflow guidance
- `codebase-wiki/` repo memory
- `notes/` session logs
- helper scripts for edited-file checks
- Separate Makefiles in `vibe_scripts/codex/` and `vibe_scripts/claude/`, with
  edited-file, lint, verification, wiki and skills commands; Codex also has
  `savings-ai`

## Project memory and planning

To preview cleanup of legacy automatic graph hooks and repeated quality checks:

```bash
python3 fix-hooks.py /path/to/project --include-global
```

The same command accepts a parent folder, such as `/Users/n/Code`, to find
installed repositories. Add `--clean-caches` to apply the same retention as the
background Stop hook: remove diagnostic logs and repair reports older than 72
hours, keeping the newest file in each group. Session locks, state, graph indexes
and recent evidence remain. Media backup retention keeps the newest completed
archive of each type under `.cache/between-backups`; database and curation
snapshots also keep anything from the past three days. Unknown files and distinct
media encodings remain. To delete a specific cache directory completely, name it
with `--remove-cache NAME`; this overrides retention for that path and can remove
recovery backups.

Every installed provider gets `vibe_scripts/<provider>/cleanup-cache.py` and one
Stop registration. The launcher detaches the worker and returns immediately; a
shared lock prevents overlapping cleanup. The worker writes its last result to
`.cache/cleanup-cache-last.json`. Backup completion checks use producer markers;
legacy media exports without a marker require a successful gzip integrity read
before older copies are removed. That read happens only while duplicates exist.

Add `--apply` to install the reviewed changes with backups. Omit `--include-global`
to leave user configuration alone. The cleanup preserves custom hooks and refuses
unrecognized quality helpers or Git blockers. Start a new agent session afterward.
Graph refresh remains available through the project-memory commands below.
Automatic hooks format and lint supported, existing edited files. Unsupported
files, such as swap files and media, are skipped. Detailed logs stay on disk;
failure summaries show relevant diagnostics and an evidence path. Successful
checks emit `PASS`. Moves are checked at the
new path; removed paths remain recorded in session history. Deletion-only changes
skip edited-file checks silently and record that no checks ran. Hooks
do not launch test suites, require test receipts, or scan installed dependencies.
Real edited-file findings and missing session baselines still block completion.
At Stop, changed UI markup or styles also require scoped design review. Logic-only
changes do not trigger it; see [design review](DESIGN-REVIEW.md).

After deleting, moving or refactoring files, run the project's tests explicitly:

```bash
make -f vibe_scripts/codex/Makefile test-ai
```

Use `vibe_scripts/claude/Makefile` for Claude, or `verify-ai` for the full controlled
verification. Test failures retain their nonzero exit status. Both providers' macOS
and WSL installers deliver this separation; existing installations receive it on
managed refresh. No session reset or cache deletion is required.

Both helpers install `project-memory.py` in their provider directory. Before
substantial planning, request a brief for the task and the files being inspected:

```bash
python3 vibe_scripts/codex/project-memory.py context \
  --task "Fix tracking attribution" --files src/tracking.py
```

Use `vibe_scripts/claude/` for Claude. Add `--architecture` when broader Graphify
context helps. The brief links relevant wiki pages, queries code relationships
and tests, and reports freshness. Confirm the source before relying on a graph
finding; inspection does not authorize unrelated edits or design work.

Automatic memory hooks are disabled by default. Installers remove known legacy
registrations and retain explicit `context`, `status` and `refresh` commands.
Missing tools and failed updates remain visible without blocking completion.
Custom graph watchers remain untouched. Graphify's code refresh can rebuild all
code; semantic document updates require separate extraction and source review.
The `status` command lists pending work, while `refresh --architecture` requests
both code indexes. Neither command installs or upgrades tools.

Generated reports, caches and session logs are excluded from Graphify inputs.
Use `wiki-ai` only for durable knowledge, review its generated diff, and preserve
handwritten sections. See `agent/codex/context-strategy.md` or its Claude
equivalent for recording a completed semantic review.

Hook repair changes on 2026-10-19:

- Removed automatic graph updates and the graph-first file-read blockade.
- Limited each edit check to its edited paths. The 2026-10-20 update removes the
  test-dependent Stop cache and checks surviving edited files on each Stop.
- Replaced full-file hashing in quality snapshots with file metadata.
- Separated the global Git mutation blocker from project-specific quality rules.
  Quoted command names are accepted; compound shell execution keeps conservative
  checks. The mutation strings in regression tests are parsed, never executed.
- Added repeatable repair previews, backups, per-repository results and background
  Stop cleanup. Both providers' macOS and WSL installers receive the shared fixes.

The review also found unsupported-file Stop retries and mandatory agent/delegation
prompts. Those policies remain unchanged. Deleted-file testing was separated from
automatic hooks on 2026-10-20 as described above. Configured
graph timeouts showed a potential long wait; the review did not establish actual
historical latency. New sessions load the repaired registrations.

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
- Chrome DevTools MCP: lets Codex or Claude Code inspect and control Chrome for
  browser debugging.
- Ponytail: pushes agents toward smaller changes and less overbuilt code.
- Unlazy: keeps long or multi-part tasks moving until their acceptance checks
  pass.
- Graphify: maps a repo into a graph so agents can understand large codebases
  faster.
- code-review-graph: tracks code relationships so agents can review blast radius
  before changing files.
- Impeccable: helps with frontend design, layout, polish, and UI checks when
  enabled.
- LLM Council: provides optional multi-model review for high-value decisions.
- OmniRoute: routes approved small tasks to eligible free models through a local
  gateway, with the main agent reviewing the result.
- Codex usage tracker: shows recorded usage by project, model, session and turn
  in a live local dashboard.
- RTK: trims noisy read-only command output before it reaches the agent.
- Ruff: formats and lints Python.
- Biome: formats and checks JavaScript, TypeScript, CSS, and JSON.
- HTMLHint: checks HTML.
- markdownlint-cli2: checks Markdown files.
- ShellCheck and shfmt: check and format shell scripts.
- PHP_CodeSniffer and PHPStan: check PHP style, syntax, and types.

## How the tools help

### Codex project files

New sessions need the project's conventions and commands. `AGENTS.md`, repo
notes, hooks and helper scripts give Codex that starting point and connect edits
to scoped quality checks.

**Example:** after editing a Python file, the hook formats and lints that file
and returns a short result.

### Claude Code project files

`CLAUDE.md`, hooks, slash commands and helper scripts bring the same workflow
into Claude Code's native configuration.

**Example:** a new Claude session reads the repo instructions and uses its
installed verification script to check edited files.

### Humanizer

The agent uses editing rules to remove filler, inflated claims and repetitive
phrasing while preserving meaning. This is an LLM-assisted rewrite.

Example edit:

| Before: 27 words | After: 7 words |
| --- | --- |
| It is important to note that you will need to restart the application after making changes to the configuration in order for those changes to take effect. | Restart the application to apply configuration changes. |

### Context7

Remembered API details can be outdated. The agent identifies the library and
fetches documentation for the specific question through Context7's service.

**Example:** before changing authentication middleware, retrieve the relevant
framework's current middleware API and example.

### Chrome DevTools MCP

Browser tools let the agent inspect the DOM, console and network, operate
controls and capture screenshots. This exposes problems visible only when the
application runs.

**Example:** reproduce a failed form submission, inspect its network response
and confirm the repaired form works in the browser.

### Ponytail

Skill instructions keep small requests from growing into unnecessary
architecture. The agent favours existing code and the smallest complete change,
then checks its risks and verifies the result.

**Example:** reuse the project's date formatter for a new column instead of
adding another dependency and formatting layer.

### Unlazy

Long tasks can end with a convincing summary and unfinished work. Unlazy records
the required outcomes before implementation and checks the evidence before the
agent reports completion.

#### Acceptance gates

A gate is an observable condition that must hold for the task to be complete.
For example, a project-selector task might use these gates:

| Gate | Required evidence |
| --- | --- |
| Selecting a project shows only that project's usage. | A test with two projects passes. |
| Refresh preserves the selected project. | A refresh test passes. |
| The selector remains usable on a narrow screen. | A browser review records the viewport and result. |

Runnable gates store `CHECK` (the command), `EXPECT` (the success marker) and
`EVIDENCE` in `GATES.md`. Passing requires a zero exit status, the expected output
and evidence tied to the current check definition. Manual gates record review
evidence where a command cannot decide the outcome.

#### Other parts of the workflow

- **Task tree:** split larger work into bounded deliverables with dependencies,
  file ownership and their own gates. Small tasks use one ledger.
- **Review passes:** implement, review the approach, find defects and polish;
  repeat until the review is clean.
- **Independent verification:** in delegated work, the parent reruns checks and
  verifies that the pieces work together.
- **Honest handoff:** unmet or abandoned gates remain visible. The final report
  reconciles the original request with the evidence instead of declaring them done.
- **Optional stop check:** a separately enabled Claude Code hook checks gate and
  dispatch status. It does not execute tests; a progress guard releases repeated
  no-progress blocks after six attempts.

Gate commands require explicit approval before execution. Planning and review
use the agent; the gate checker itself runs locally without model calls.

### Graphify

Repeatedly reading a large repo uses context quickly. Graphify builds a
persistent graph of entities and relationships so focused queries can return
connected context with source references. Graph building or enrichment can use
models; its local benchmark does not.

**Example:** ask which modules handle authentication, then inspect those modules
and their connections. Graphify's context benchmark displays the comparison:

![Graphify benchmark excerpt: 926 nodes, 1,529 edges, about 2,093 tokens per sample query, and a 29.5× reduction against its estimated full-corpus baseline.](assets/readme/graphify-benchmark.png)

### code-review-graph

A small edit can break a distant caller. This code graph identifies callers,
dependents and affected flows so the agent can focus its source review and tests.

**Example:** before changing a shared validation function, find its callers and
check the affected API routes and tests.

### Impeccable

Design guidance helps the agent choose a clear hierarchy, readable typography
and consistent spacing. Browser inspection and design checks expose overflow,
weak focus states and awkward layouts. A deterministic detector supplements
the visual review.

**Example:** improve a dashboard's project selector, then inspect its keyboard
focus, spacing and narrow-screen layout. The Stop hook requires the detector
after design-related source changes, with existing enable/consent settings
preserved; logic-only edits do not trigger it.

### LLM Council

For decisions worth several model calls, [LLM Council](https://github.com/karpathy/llm-council)
collects answers through OpenRouter, has models review them and uses a chair
model to synthesize the result. VibeHelper can clone it; running it needs
separate setup and uses provider tokens.

**Example:** compare two migration strategies and review their tradeoffs before
committing to either implementation.

### OmniRoute

OmniRoute gives approved small tasks a route to eligible free models through a
local gateway. The helper checks account/model eligibility, makes one bounded
attempt and returns a proposal for the main agent to review. Attempt history
records outcomes and reported usage.

**Example:** delegate a small text extraction after approval, then have the main
agent check the returned fields. Tasks sent through the gateway reach the chosen
provider. See [OmniRoute setup](#free-model-delegation-with-omniroute).

### Codex usage tracker

The live local dashboard makes expensive sessions easier to find. It reads
recorded Codex logs and groups usage by project, model, session and turn.
Collection and rendering use local code, with no LLM calls or usage uploads.

**Example:** select a project, open its largest session and inspect the turns
with the highest recorded input. The counts show recorded tokens, not the
remaining weekly allowance. See [dashboard setup](#local-codex-usage-dashboard).

### RTK

Eligible read-only commands pass through filters that remove repetition and
routine noise before the agent reads the result. Filtering runs locally without
a model call.

Example `git status` output, shortened from **14 lines to 4**:

Before:

```text
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
        modified:   src/app.py
        modified:   tests/test_app.py

Untracked files:
  (use "git add <file>..." to include in what will be committed)
        notes.txt

no changes added to commit (use "git add" and/or "git commit -a")
```

After, using `rtk git status`:

```text
main | 2M 1?
M src/app.py
M tests/test_app.py
? notes.txt
```

RTK also records a savings table:

![RTK project savings table: four commands, 933 estimated input tokens, 524 estimated output tokens and 409 estimated tokens removed, or 43.8%.](assets/readme/rtk-gain.png)

### Linters and formatters: the quality wrapper

**Problem:** raw linter logs can flood context, while repo-wide scans pull
unrelated work into the task. **Approach:** select supported session-edited files,
run the applicable checks, and parse their output before returning it to the
agent. Unsupported files, including swap files and media, are skipped.

| Tool inside the wrapper | What it checks or fixes | Example |
| --- | --- | --- |
| **Ruff** | Formats and lints Python. | Normalize spacing; flag an unused import. |
| **Biome** | Formats and checks supported JavaScript, TypeScript, CSS and JSON. | Format a component; flag an unused variable. |
| **HTMLHint** | Reports HTML markup problems. | Flag duplicate element IDs. |
| **markdownlint-cli2** | Checks Markdown structure and style, with line-length checks disabled. | Flag a skipped heading level. |
| **ShellCheck + shfmt** | Check shell scripts and normalize formatting. | Flag an unquoted variable; fix indentation. |
| **PHP_CodeSniffer + PHPStan** | Check PHP style and static types; the companion fixer formats when configured. | Fix spacing; flag an incompatible argument type. |

```mermaid
flowchart TD
    A[Agent edits files] --> B[Select supported session-edited paths]
    B --> C[Run applicable formatter and linter]
    C --> D[Preserve detailed logs and exit status]
    D --> E[Python parses diagnostics and bounds the summary]
    E --> F{Result}
    F -->|Pass| G[PASS]
    F -->|Findings or tool failure| H[Relevant diagnostics and evidence path]
    H --> I[Agent checks relevance to the original task]
    I -->|In scope| J[Review fix and recheck]
    I -->|Outside scope| K[Report blocker and ask before expanding work]
    J --> B
```

The parser is **Python, not an LLM**. PHPCS, PHPStan, Pyright and ShellCheck have
structured JSON adapters; other outputs use text extraction. The wrapper retains
locations, rule IDs, relevant warnings and tool failures, not just lines
containing “error”. A parse failure is reported rather than treated as a pass.
Full evidence stays on disk; the hook's failure excerpt is capped at **12 lines
and 2 KiB**.

Illustrative agent-facing result:

```text
lint-shellcheck: findings
scripts/build.sh:8:6: warning [SC2086] Double quote to prevent globbing and word splitting.
Full log: .cache/ai-quality/<run>.log
```

Formatting and configured automatic fixes handle mechanical changes. The agent
reviews that diff and repairs remaining in-scope findings manually, then rechecks.
Tests, broader analysis and whole-project lint are separate explicit checks.
An unresolved finding stays blocked; an unchanged blocked Stop reuses its evidence
instead of rerunning the same checks.
Hook-input errors retain their exception details and locations in
`.cache/hook-errors.log`; the separate summary omits raw input and error text.

The wrapper limits what it checks and returns. The agent's scope instructions
require approval for unrelated work; a linter finding does not authorize it.
This reduces scope drift without claiming the parser can enforce every agent
decision.

## After install

Restart Codex or Claude Code in the configured repo so it reloads the new files.

For Codex, open `/hooks`, review and trust the project hooks, then start a new
thread. Hook trust cannot be automated. For Claude Code, restart in the repo
root so it reloads `CLAUDE.md` and `.claude/settings.local.json`.

Run one check after setup:

```sh
# Codex
./vibe_scripts/codex/agent-verify.sh edited
# Claude
./vibe_scripts/claude/agent-verify.sh edited
```

Trusted hooks handle normal edited-file checks after that. Pass `lint` to the
provider verifier to check edits without formatting, or `lint --all` to explicitly
lint the whole project. Use `verify` for large, risky,
security-sensitive or cross-project changes. Each verifier selects its own
Makefile. The project root Makefile is preserved.

Existing installs can use `--refresh-managed` to refresh managed instructions
and hooks without package updates. `--force` also updates installed packages.
Legacy shared scripts and root targets remain available for manual review; new
commands use provider-specific paths. Cleanup preserves shared project data
and the other provider. See each helper README for the retained files.

## More detail

- [CodexHelper README](CodexHelper/README.md)
- [ClaudeHelper README](ClaudeHelper/README.md)

## Installation and check outcomes

See [the maintained flow and evidence](INSTALLATION-FLOW.md) for installer phases,
component states, migration, repair hooks, and verification limits.

Use `--refresh-managed` to refresh helper files without updating packages.
`--force` still refreshes installed tools through their detected package manager.
Semgrep integration has been retired; `--security-scan` is unsupported. Existing
global binaries are left installed. Custom security checks remain project-owned.
