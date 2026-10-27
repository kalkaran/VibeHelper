# Historical snapshot: Installer coverage and gaps

This pre-change audit is retained as historical evidence. Current behavior and
verification are maintained in [INSTALLATION-FLOW.md](INSTALLATION-FLOW.md).
The commands and findings below describe the earlier implementation.

> This audit records the installation behavior before provider isolation. The
> subsequent change separates scripts and Makefiles under `vibe_scripts/codex/`
> and `vibe_scripts/claude/`, separates workflow instructions under `agent/`,
> preserves root Makefiles and existing wiki pages, and limits cleanup to
> provider files. Forced upgrades refresh an existing marked global Codex hook
> with a backup. The change also
> refreshes Codex helpers during direct part2 `--force` and adds pytest fixtures
> for the existing quality tests. These address the collision, cleanup and
> helper-refresh portions of the findings below. Other findings remain open;
> original line references describe the audited snapshot. See the helper
> READMEs for current commands and migration steps.

Reviewed 2026-10-10 against the current working tree.

What this repo does: VibeHelper adds agent instructions, hooks, skills, MCP
integrations, and quality commands to an existing project. This review assumes
one developer installs it into projects using Codex, Claude Code, or both.

Claude has substantially less language-quality coverage than Codex. The two
Claude platforms are nearly identical; WSL is not generally missing the
Claude features available on macOS. Codex WSL is also close to macOS, but its
depth-limited secondary file search can omit checks. Installing both helpers
can reduce effective verification because their Makefile targets collide.

This is a source audit, not a clean-machine installation certification. It
covers 26 active installer, helper, and test scripts in the working tree:
10 installation scripts, eight helper scripts, and eight test scripts.
Generated local `.codex` snapshots and ignored legacy hook templates are
distinguished from the sources used by the installers.

## OmniRoute coverage added after this audit

| Behavior | Codex macOS | Codex WSL | Claude macOS | Claude WSL |
| --- | --- | --- | --- | --- |
| Automatic per-user runtime and dashboard setup | Yes | Yes | Yes | Yes |
| Reuse compatible global executable | Yes | Yes | Yes | Yes |
| Project worker and review instructions | Yes | Yes | Yes | Yes |
| User-chosen password; preserve existing | Yes | Yes | Yes | Yes |
| Exact OpenRouter free route; main-agent fallback | Yes | Yes | Yes | Yes |

The shared worker is `templates/vibe_scripts/omniroute.py`. Provider signup and
an API key remain necessary. Unattended setup without a supplied password leaves
configuration pending and continues project installation. Node.js must satisfy
OmniRoute's supported version range; an older distribution package may need an
upgrade. WSL behavior is covered by simulated installer tests, not a native WSL
service run. Other free-provider connections are not supported by this worker.

## 1. What each script handles

The normal flow is `install.sh` → platform `part1.sh` → platform `part2.sh`.
Both entry points stop when part 1 fails and reject ordinary non-WSL Linux.
Neither installs the Codex/Claude application or Chrome itself.

### Entry points and platform scripts

| Script | Responsibility |
| --- | --- |
| [Claude entry][CE] | Select OS; coexistence, cleanup; run phases. |
| [Claude macOS setup][CM1] | Claude tools, instructions, hooks and MCP. |
| [Claude WSL setup][CW1] | Same workflow; Ubuntu bootstrap and paths. |
| [Claude macOS quality][CM2] | Wire existing checks into a Make block. |
| [Claude WSL quality][CW2] | Same wiring; add user-local tool bin to PATH. |
| [Codex entry][XE] | Select OS; forward options; run phases. |
| [Codex macOS setup][XM1] | Codex tools, skills, MCP, hooks and scaffolds. |
| [Codex WSL setup][XW1] | Same workflow; Ubuntu bootstrap and paths. |
| [Codex macOS quality][XM2] | Detect languages; install and wire checks. |
| [Codex WSL quality][XW2] | Same quality workflow; apt/pipx and paths. |

Claude setup generates `CLAUDE.md`, five workflow pages, six wiki pages,
session logs, five hook scripts, settings, three slash commands, verification
helpers, and skill-management files. Codex generates `AGENTS.md`, equivalent
workflow/wiki/log scaffolds, its own hook/configuration files, verification
helpers, and session-savings reporting. Hook code is embedded in the platform
scripts; changing a similarly named local hook does not update the installer.

Sources: [Claude generation][CG], [Codex generation][XG].

### Shared and helper-specific Python and shell scripts

| Script | Used for |
| --- | --- |
| [Shared edited checker][EDIT] | Claude formatting, lint and PHP types. |
| [Shared output wrapper][WRAP] | Claude logs and capped command output. |
| [Shared wiki updater][WIKI] | Claude wiki drafts from guidance and graph. |
| [Shared verifier][VERIFY] | Reference dispatcher for approved Make commands. |
| [Shared skill sync][SYNC] | Fallback skill/plugin inventory and updates. |
| [Claude skill sync][CSYNC] | Claude copy, close to shared version. |
| [Codex skill sync][XSYNC] | Codex copy with broader force-refresh behavior. |
| [Codex config normalizer][NORM] | TOML validation and legacy hook migration. |

Claude copies the edited checker, wrapper, and wiki updater from the shared
templates. Its verifier is generated inline. Codex generates its own edited
checker, wrapper, wiki updater, and verifier inline; part 2 also embeds copies
of the edited checker and wrapper. These implementations have already drifted.
The three `skills-lock.json` templates are identical, but their sync scripts
are not. Sources: [Claude template selection][CT], [Codex sync selection][XT].

The config normalizer removes the unsupported Context7 `type` field, migrates
legacy `hooks.json` content into TOML, preserves other configuration, validates
before replacement, and backs up changed files. It is an installer dependency,
not a quality checker.

### Test script inventory

| Script | Existing coverage |
| --- | --- |
| [Claude smoke suite][CTEST] | Wiring, MCP, upgrades, WSL and coexistence. |
| [Codex smoke suite][XTEST] | Wiring, MCP, hooks, refresh and cleanup. |
| [Codex quality tests][QTEST] | Policy, adapters, empty edits, PHP failures. |
| [Codex file signals][FTEST] | Ignore virtual environments during detection. |
| [Codex session savings][STEST] | Tracker behavior across both platforms. |
| [Codex ADHD tests][ATEST] | Plugin/style setup, forwarding, repeat runs. |
| [Codex config tests][NTEST] | Hook migration, preservation, recovery rules. |
| [Session log tests][LTEST] | History preservation in all four setups. |

`tests/test_codex_config.py` invokes the Codex shell smoke suite. There is no
equivalent root pytest bridge for `ClaudeHelper/tests/run.sh`. The quality
control test file mixes pytest-style functions with a standalone `main()`;
eight functions require fixtures that pytest cannot find.

## 2. Four-installation coverage matrix

CM = Claude macOS; CW = Claude WSL; XM = Codex macOS; XW = Codex WSL.
“Default” means attempted in ordinary setup when prerequisites are available,
not guaranteed to be installed or authenticated. Repo-only and opt-out flags
can suppress work. “Opt-in” also includes fresh-install where stated below.

| Capability | CM | CW | XM | XW |
| --- | --- | --- | --- | --- |
| Humanizer | Default | Default | Default | Default |
| Unlazy | Default | Default | Default | Default |
| Ponytail | Default | Default | Register | Register |
| Graphify CLI installation | Default | Default | No | No |
| code-review-graph MCP | Default | Default | Default | Default |
| Initial CRG index build | Opt-in | Opt-in | Default | Default |
| Context7 setup | Default | Default | Opt-in | Opt-in |
| Chrome DevTools MCP | Default | Default | Default | Default |
| Impeccable | Opt-in | Opt-in | Opt-in | Opt-in |
| LLM Council clone | Opt-in | Opt-in | Opt-in | Opt-in |
| i-have-adhd plugin/style | No | No | Default | Default |
| RTK install/routing | Default | Default | Default | Default |
| Post-edit and Stop checks | Yes | Yes | Yes | Yes |
| Git/destructive command policy | Yes | Yes | Yes | Yes |
| Direct quality-command policy | No | No | Yes | Yes |
| Specific recovery command guide | No | No | Yes | Yes |
| CRG-before-file-read hook | Yes | Yes | No | No |
| Session savings and savings-ai | No | No | Yes | Yes |
| Structured quality summaries | No | No | Yes | Yes |
| Quality-tool installation phase | No | No | Yes | Yes |
| Claude slash commands | Yes | Yes | N/A | N/A |
| Codex configuration migration | N/A | N/A | Yes | Yes |

Defaults come from [Claude flags][CF] and [Codex flags][XF], with equivalent
WSL defaults. Impeccable is enabled by fresh-install for both helpers.
Codex fresh-install also enables Context7. Claude enables Context7 normally
but deliberately skips it during ordinary force refresh of an existing setup.
Codex also offers Context7 interactively; with `--yes`, it needs the explicit
flag or fresh-install. Codex registers the Ponytail marketplace but leaves
plugin installation to `/plugins`. It can refresh an existing Graphify CLI
and skill on force, but never installs a missing Graphify CLI, including on
fresh-install. This falls short of the root README's fresh-install wording.
Source: [Codex Graphify branch][XGRAPHIFY].
CRG build is `--crg-build` in Claude and default-on with `--no-crg-build` in
Codex. Registration checks and refresh behavior are not equivalent to checking
that an MCP connection works.

The specific recovery guide added in this session is present in both Codex
installers only. Claude also lacks Codex's hook-level block on direct Ruff,
PHPStan, pytest and similar quality commands. Its Bash policy checks Git and
destructive commands. Sources: [Claude policy][CP], [Codex policy][XP].

Codex requires hook review/trust after installation. Claude writes local hook
settings and instructs users to restart. Neither path proves that external
skills, plugins, MCP servers, or the browser work at the end of installation.

### Intended operating-system differences

| Area | macOS | WSL |
| --- | --- | --- |
| OS package manager | Homebrew | apt |
| Claude base setup | Requires Python | apt base packages by default |
| Claude RTK | Homebrew | RTK's shell installer |
| Codex shell/PHP tool packages | Homebrew | apt with sudo when needed |
| Codex Semgrep installation | Optional Homebrew | Optional existing pipx |
| Certificate discovery | Homebrew CA paths | System CA paths |
| Chrome detection | PATH or macOS Applications | Linux browser on PATH |
| User-local PATH | Less consistent | More explicit additions |

Claude WSL installs base curl, Git, certificates, Python, pip/venv, Node/npm,
and optional pipx unless disabled. It has independent scripts; it does not
delegate to macOS. Both helpers' Chrome checks require newer Node versions
than a generic “Node exists” check, so installing distro Node does not itself
prove Chrome MCP readiness. Sources: [Claude WSL bootstrap][CBOOT],
[Codex WSL packages][XBOOT].

### Invocation and upgrade differences

| Mode | Claude | Codex |
| --- | --- | --- |
| Ordinary entry point | Wire existing checks | Offer installs; wire checks |
| `--yes` | Already noninteractive | Accept install defaults |
| `--fresh-install` | Refresh; enable design | Also Context7/tool installs |
| `--repo-only` | Skip tools; guidance exception | Skip global/tool installs* |
| `--force` | Replace files/refresh tools | Replace files/refresh tools |
| Coexistence | Keep shared files; append Make | No symmetric protection |
| Makefile ownership | Managed block | Whole file on forced replacement |
| `--no-wire` | Skip quality Make block | Skip Makefile after tool handling |

*Codex repo-only still permits project-scoped skill setup. Direct part1
`--fresh-install --repo-only` can bootstrap global prerequisites; the unified
entry point translates that combination to avoid this path. Repo-only is not
a universal no-network flag. Sources: [Codex entry forwarding][XARGS],
[Codex prerequisite guard][XPREREQ].

## 3. Language and quality coverage

This table describes a fresh helper-owned setup with required tools available.
Existing project scripts and preserved helper files can change the result.
Both platforms within each helper share these language branches.

| Language | Claude broad checks | Codex broad checks |
| --- | --- | --- |
| Python | None native | Ruff; mypy/Pyright; pytest |
| JS/TS/CSS/JSON | Existing npm scripts | npm scripts; configured Biome files |
| HTML | Via npm script | HTMLHint for `.html` |
| PHP | None native | Syntax, PHPCS, PHPStan |
| Shell | ShellCheck for `.sh` | ShellCheck for `.sh` |
| Markdown | PATH markdownlint-cli2 | Local or PATH markdownlint-cli2 |
| Go | None native | vet, tests, optional staticcheck |
| Rust | None native | clippy, check, tests |
| .NET | None native | build and tests |
| Security | Existing Semgrep | Working Semgrep plus JSON summary |

Sources: [Claude target selection][CQUALITY],
[Codex language/check selection][XQUALITY].

Codex can install Ruff, Biome, HTMLHint, markdownlint-cli2, ShellCheck, shfmt,
Composer/PHPCS/PHPStan, and optional Semgrep. It does not install mypy,
Pyright, pytest, Go, staticcheck, Rust, .NET, or TypeScript in part 2. PHPUnit
is recognized by the blocking/recovery policy but is not wired as a native
test target. Listed policy support is therefore broader than automatic setup.

Claude part 2 installs none of those quality tools. It only discovers root
npm scripts, PATH tools, Markdown, and existing helpers. Its npm scripts can
provide additional language coverage, but that depends on the project.
The generated Biome configuration includes only `biome.json` and
`package.json` among JSON files; it does not select every JSON file.

| Edited-file check | Claude shared helper | Codex embedded helper |
| --- | --- | --- |
| Python | No | Ruff format/lint |
| JS/TS/CSS/JSON | Local Biome | Local Biome |
| HTML/HTM | Local HTMLHint | Local HTMLHint |
| PHP | Syntax/style/types; failure bugs | Syntax/style/types |
| Shell | shfmt and ShellCheck | shfmt and ShellCheck |
| Markdown | markdownlint-cli2 | markdownlint-cli2 |
| Go | No | gofmt only |
| Rust/.NET | No | No |
| Frontend design detection | No | Existing Impeccable |
| Tests/security | No | No |
| Empty edit selection | Returns success | Returns failure |

An edited check is not full verification. Codex edited checks omit Python
typing, Go analysis/tests, JS/TS typing, Rust/.NET, tests, and security. Both
helpers can return success when selected file types or installed tools yield
no applicable checks. File lists come from edit hooks or explicit arguments,
not an automatic Git-diff fallback. Sources: [shared checker][EDIT],
[Codex edited checker][XEDIT].

Full checks also have limits: shell scans select `.sh` even though detection
recognizes `.bash`, `.zsh`, and executable extensionless scripts; HTMLHint's
full scan omits `.htm`; a manifest-free `.mjs`/`.cjs` project has no web signal.
Nested manifests trigger language flags, but commands still run at repo root.
Sources: [Codex file signals][XSIGNALS], [Codex full checks][XQUALITY].

Codex's wrapper selects RTK adapters for Ruff, mypy, pytest, Go tests, and
Cargo tests; native adapters parse PHPCS, PHPStan, Pyright, Semgrep, and
ShellCheck output. It preserves exit status and full logs. Claude's shared
wrapper only caps text and summarizes successful PHP syntax output.

## 4. Confirmed gaps and risks

The following findings describe concrete source paths. No fixes were made.

### Must fix before relying on equivalent verification

**1. Claude can replace Codex's effective verification recipe.** Claude
appends `lint-ai` and `verify-ai` definitions without detecting existing
targets. In a Python project with Codex Ruff/mypy/pytest targets, the new
Claude `verify-ai` calls its narrower dependency list; Python type/test
targets can remain in the file but stop running. Preserve existing targets
or merge their dependencies without replacing recipes. Otherwise installing
the second helper can silently reduce coverage.
[Claude generation][CQUALITY] and [append operation][CAPPEND].

**2. Claude's PHP checker can hide failures.** Its syntax loop returns the
status of the last file: invalid `a.php` followed by valid `b.php` can appear
successful. The PHPCBF command includes `|| true`, suppressing genuine tool
errors. Port the existing Codex failure handling into the shared checker.
Otherwise these errors may pass the edit/Stop gate.
[Shared PHP commands][CPHP] and [Codex PHP commands][XPHP].

**3. Disabling CRG can leave Claude unable to use normal file tools.**
`--no-crg`, repo-only, or failed MCP setup still installs the graph-first hook.
It blocks Read/Grep/Glob until a CRG MCP call is observed, without checking
availability. Activate this requirement only when the integration is usable.
Otherwise an intentionally minimal installation has an unsatisfied dependency.
[Hook registration][CG] and [blocking logic][CGRAPH].

**4. Claude lacks native checks for several project types.** Its
quality stage has no Python/PHP/Go/Rust/.NET full-check branches; its edited
helper also lacks Python and Go. A Python-only edit can receive no useful
Claude check even when Ruff is installed. Reuse the existing language/check
selection and shared helper behavior before presenting the two helpers as
equivalent. [Claude checks][CQUALITY] and [shared edited selection][CSELECT].

### Platform and feature-control gaps

**5. Codex WSL misses deep matching files.** Its `has_any` stops at depth four.
A sole `src/a/b/c/test_unit.py`, `.php`, or `.html` file can set the language
flag but fail the secondary test that wires pytest, PHP syntax, or HTMLHint.
Remove the inconsistent depth cap while keeping exclusions. macOS has no
corresponding cap. [WSL helper][XDEPTH] and [macOS helper][XMDEPTH].

**6. Claude opt-outs do not fully control generated behavior.** `--no-rtk`
skips binary installation but still registers a hook that rewrites commands
when RTK is already on PATH. `--repo-only` still updates
`~/.claude/CLAUDE.md` with Graphify guidance. Make hook activation and global
writes honor their flags. Otherwise users get behavior they explicitly
excluded. [RTK registration][CG] and [global guidance call][CGLOBAL].

Codex opt-outs also need upgrade handling: a normal `--no-rtk-hook` rerun
preserves the existing config and its RTK registration. `--no-codex-hooks`
skips creation rather than uninstalling previous hooks. Reconcile installed
registrations when disabling a feature. [Codex config write][XCONFIG].

**7. Claude lacks the new quality policy and recovery instructions.** Both
Codex installers now map blocked tools to controlled commands and require a
retry; both Claude installers lack that guide and the matching quality-tool
blocker. Port the intended policy and instructions together. Copying only
the table would improve guidance without creating the same enforcement.
[Claude policy][CP], [Codex recovery][XP], and [Codex blocker][XPOLICY].

### Upgrade and maintenance gaps

**8. Existing helpers and integrations are not always repaired.** Direct
Codex part2 `--force` preserves existing wrapper/checker files even when new
recipes need `--adapter`. An old generic wrapper can then reject the new
recipes. The unified force path may refresh these through part1, so this
specific failure depends on invocation and existing files. The optional
global Codex blocker also returns early when already registered, leaving
its script stale. Refresh helper versions and installed hook content as
part of their owning upgrade paths.
[Part2 existence guards][XSTALE] and [global blocker early return][XGLOBAL].

Claude and Codex also use existing-install markers and package-version
comparisons to skip some MCP setup. A missing registration can survive an
ordinary force refresh. Check registration presence independently of whether
the package version changed. [Claude CRG/Context7 refresh][CMCP].

**9. Managed-skill coverage is incomplete and differs by helper.** The stock
locks cover Humanizer, Graphify, Impeccable, and Ponytail. Unlazy is missing
from both; Codex's ADHD integration is missing too. Claude's sync helper
rejects already-installed items before applying `--force`, while Codex's
copy has explicit update actions. Add the missing inventory and align the
intended refresh semantics. Otherwise “skills checked” covers only a subset
of what setup installs. [Claude sync][CSYNC] and [Codex sync][XSYNC].

**10. Force and cleanup have broader effects than updating tooling.** Force
can replace curated wiki pages with starter text, retaining history only in
backups. Codex forced wiring replaces the entire Makefile. Both cleanup
entry points delete fixed paths such as `notes`, `codebase-wiki`, Makefile,
and `.mcp.json` without verifying ownership or backing them up. Preserve
user-maintained content and remove only owned entries. Otherwise upgrades
require manual restoration and cleanup can delete project content.
[Wiki writes][XWIKI], [Claude cleanup][CCLEAN], [Codex cleanup][XCLEAN].

### Test and distribution gaps

**11. The default test result is not clean and misses a Claude suite.** The
latest controlled run had 23 passes and eight missing-fixture errors, matching
the earlier baseline. Claude's shell smoke suite exists but is not called by
root pytest; OS simulations do not prove real WSL apt or browser operation.
Connect the existing tests through approved routes and repair fixture setup
without changing assertions. Otherwise parity regressions can escape checks.
[Quality tests][QTEST], [pytest bridge][NTEST], [Claude suite][CTEST].

**12. Important working-tree assets are untracked.** At review time, the
normalizer and all eight test scripts were untracked before this audit.
They exist locally, but are absent from a checkout of the tracked revision.
The Codex installer calls the normalizer as a required dependency. Review
and include these assets in the intended release before relying on local
test results. No files were staged or committed during this audit.
[Normalizer dependency][NORMCALL].

**13. Codex's Python prerequisite is weaker than its normalizer requires.**
The normalizer needs Python 3.11 or `tomli`, but prerequisite checks only
require a `python3` command. Python 3.10 without `tomli` therefore fails when
normalization runs. Check or provision the actual parser requirement before
generating the setup. This affects both platforms and is especially relevant
to WSL installations that rely on the distro Python.
[Parser requirement][NORM] and [prerequisite checks][XPYTHON].

**14. macOS can lose newly installed tools from PATH in the next shell.**
Codex exports the new tool directory before calling its `.zshrc` persistence
helper. That helper returns because the directory is now on PATH, so no
persistent entry is written. Persist before exporting, as the WSL path does.
Otherwise setup can use the newly installed tool while the next terminal
cannot find it. [PATH helpers][XPATH] and [installation order][XPATHCALL].

## 5. Verification and recommended order

Source review covered the ten installer scripts, eight helper implementations,
eight test scripts, flags, generated payloads, lock files, and Make targets.
The existing graph was used for navigation; findings were checked against
current source. Three independent reviews covered Claude setup, Codex setup,
and all quality installers. Their consequential claims were checked again
during integration. No installer implementation changed in this audit.

Evidence already produced against these installer changes in this session:

- `make edited-ai`: Ruff and ShellCheck passed; no applicable type checker.
- `make test-ai`: 23 passed, eight existing missing-fixture errors.
- The same eight errors appear in the October 4 baseline log.

The overview is checked through the controlled edited-file route.
No clean macOS/WSL installation, external package/API compatibility check,
MCP connection, browser launch, or live hook-trust flow was performed.
Source coverage should not be read as proof that a particular installed
machine has every integration active.

Recommended order:

1. Preserve effective checks when Claude and Codex share a Makefile.
2. Repair PHP failure propagation and Claude's unavailable-CRG gate.
3. Align shared language checks, retry rules, and helper refresh behavior.
4. Fix WSL depth scanning and feature opt-out boundaries.
5. Connect parity tests and include the required release assets.

[CE]: ClaudeHelper/install.sh
[CM1]: ClaudeHelper/MacOS/part1.sh
[CW1]: ClaudeHelper/WSL/part1.sh
[CM2]: ClaudeHelper/MacOS/part2.sh
[CW2]: ClaudeHelper/WSL/part2.sh
[XE]: CodexHelper/install.sh
[XM1]: CodexHelper/MacOS/part1.sh
[XW1]: CodexHelper/WSL/part1.sh
[XM2]: CodexHelper/MacOS/part2.sh
[XW2]: CodexHelper/WSL/part2.sh
[CG]: ClaudeHelper/MacOS/part1.sh#L1118
[XG]: CodexHelper/MacOS/part1.sh#L1966
[EDIT]: templates/vibe_scripts/agent-check-edited.py
[WRAP]: templates/vibe_scripts/ai-quality-wrapper.py
[WIKI]: templates/vibe_scripts/update-codebase-wiki.py
[VERIFY]: templates/vibe_scripts/agent-verify.sh
[SYNC]: templates/vibe_scripts/sync-skills.py
[CSYNC]: ClaudeHelper/templates/vibe_scripts/sync-skills.py
[XSYNC]: CodexHelper/templates/vibe_scripts/sync-skills.py
[NORM]: CodexHelper/templates/normalize-codex-config.py
[CT]: ClaudeHelper/MacOS/part1.sh#L463
[XT]: CodexHelper/MacOS/part1.sh#L2899
[CTEST]: ClaudeHelper/tests/run.sh
[XTEST]: CodexHelper/tests/install_test.sh
[QTEST]: CodexHelper/tests/quality_controls_test.py
[FTEST]: CodexHelper/tests/file_signals_test.py
[STEST]: CodexHelper/tests/session_savings_test.py
[ATEST]: tests/test_codex_adhd.py
[NTEST]: tests/test_codex_config.py
[LTEST]: tests/test_session_logs.py
[CF]: ClaudeHelper/MacOS/part1.sh#L10
[XF]: CodexHelper/MacOS/part1.sh#L19
[CP]: ClaudeHelper/MacOS/part1.sh#L1477
[XP]: CodexHelper/MacOS/part1.sh#L2009
[CBOOT]: ClaudeHelper/WSL/part1.sh#L220
[XBOOT]: CodexHelper/WSL/part2.sh#L194
[XARGS]: CodexHelper/install.sh#L282
[XPREREQ]: CodexHelper/MacOS/part1.sh#L1082
[CQUALITY]: ClaudeHelper/MacOS/part2.sh#L145
[XQUALITY]: CodexHelper/MacOS/part2.sh#L962
[XEDIT]: CodexHelper/MacOS/part2.sh#L1737
[XSIGNALS]: CodexHelper/MacOS/part2.sh#L599
[CAPPEND]: ClaudeHelper/MacOS/part2.sh#L249
[CPHP]: templates/vibe_scripts/agent-check-edited.py#L255
[XPHP]: CodexHelper/MacOS/part2.sh#L2009
[CGRAPH]: ClaudeHelper/MacOS/part1.sh#L1737
[CSELECT]: templates/vibe_scripts/agent-check-edited.py#L325
[XDEPTH]: CodexHelper/WSL/part2.sh#L325
[XMDEPTH]: CodexHelper/MacOS/part2.sh#L292
[CGLOBAL]: ClaudeHelper/MacOS/part1.sh#L2564
[XSTALE]: CodexHelper/MacOS/part2.sh#L2550
[XGLOBAL]: CodexHelper/MacOS/part1.sh#L5379
[CMCP]: ClaudeHelper/MacOS/part1.sh#L859
[XWIKI]: CodexHelper/MacOS/part1.sh#L2301
[CCLEAN]: ClaudeHelper/install.sh#L151
[XCLEAN]: CodexHelper/install.sh#L207
[NORMCALL]: CodexHelper/MacOS/part1.sh#L5302
[XGRAPHIFY]: CodexHelper/MacOS/part1.sh#L1735
[XPOLICY]: CodexHelper/MacOS/part1.sh#L4350
[XPYTHON]: CodexHelper/MacOS/part1.sh#L745
[XPATH]: CodexHelper/MacOS/part1.sh#L386
[XPATHCALL]: CodexHelper/MacOS/part1.sh#L1174
[XCONFIG]: CodexHelper/MacOS/part1.sh#L5298
