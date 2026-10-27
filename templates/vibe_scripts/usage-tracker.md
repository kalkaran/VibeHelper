# Local Codex usage tracker

The tracker reads existing Codex rollout files and provides a central live
dashboard and offline reports. It makes no model calls and uploads no usage data.
The live dashboard serves only your local browser on `127.0.0.1`. Installation
downloads two pinned upstream files unless you supply a local checkout.

Requires Python 3.11 or later and Git for installation. From a project with
CodexHelper installed:

```sh
python3 vibe_scripts/codex/usage-tracker.py setup
~/.local/bin/vibe-usage
```

ClaudeHelper distributes the same Codex tracker under `vibe_scripts/claude/`.
It does not collect Claude usage or modify Claude configuration.

On macOS, you can instead double-click `~/Applications/VibeHelper Usage.command`.
The launcher starts the local server and opens the dashboard. Keep its terminal
open; close it or press Ctrl-C to stop. There is no login autostart. Opening the
launcher again reuses a running dashboard with matching source and filters.

Setup saves the Codex log location and port in
`~/.local/share/vibehelper/usage-dashboard/settings.json` and installs its own
copy of the app, so moving the project does not break the launcher. To select a
different log directory, run:

```sh
python3 vibe_scripts/codex/usage-tracker.py setup --codex-home /path/to/.codex
```

The live page checks for changes every five seconds while visible. It reuses
parsed data for unchanged sessions, handles active and archived logs, and keeps
expanded session rows open during updates. Failed refreshes retain the last good
view with an error message. No repeated HTML files or poll logs are generated.
The first scan may take a few seconds. Hook-output indicators update alongside
rollout changes; they are not an independently monitored live feed.

Use the **Project** selector to switch from all projects to one repository.
Totals, models, daily usage, sessions and turns follow that selection. The largest
sessions and turns appear first; open a session to inspect its individual turns.
Projects with the same name show their full paths. **Unknown project** contains
sessions whose logs have no project path.

Selection stays in the tab's URL and survives reloads. Switching projects uses
the cached snapshot without rescanning logs or calling a model. Collector notes
and quota snapshots remain global and are labelled accordingly. A project absent
from the current window shows an empty state. `serve --project` restricts the
available data; the selector cannot expand that restriction.

Run `serve --open` directly to use the live dashboard without installing a
launcher. It uses port 8766 by default; `--port 0` chooses a free port. A saved
location takes precedence over environment defaults; `--codex-home` overrides it
for that server run. Close an existing server before changing its settings.

Offline commands remain available: `dashboard` and `refresh` generate snapshots;
`report` reads the saved snapshot and returns at most 4 KiB. Open
`~/.cache/vibehelper/usage/dashboard.html` for the latest offline snapshot.
Each filter combination has its own saved JSON and HTML files. Live updates
remain in memory and do not silently overwrite these exports.

The live window rolls forward over the last 30 days unless `--since` is supplied.
`serve` accepts the same project/date filters as the offline commands. Use the
same filters for offline refresh and report:

```sh
python3 vibe_scripts/codex/usage-tracker.py dashboard --project /absolute/project/path --since 2026-10-01
python3 vibe_scripts/codex/usage-tracker.py report --project /absolute/project/path --since 2026-10-01
```

Projects match exact resolved paths. With no project filter, all available local
Codex projects are included, including those without VibeHelper. Installation
receipts identify VibeHelper projects. Records come from `CODEX_HOME/sessions`
and `CODEX_HOME/archived_sessions`, defaulting to `~/.codex`.
`TOKEN_USAGE_CODEX_HOME` takes precedence when set.

Input includes cached input. Output includes reasoning. The dashboard shows
those subsets separately; do not add them again. Sessions show their own usage,
including separate child rows under each child's recorded project. Model totals
and turn excerpts help identify the work. A turn is not necessarily a complete
task. Date filtering uses the recorded turn start, so turns spanning midnight
are assigned to their start date.

Unknown counters remain unknown. Incomplete sessions and unreadable records
produce coverage notes; totals are recorded subtotals. Deleted history cannot be
recovered. Token totals do not represent a percentage of your subscription
allowance. Large sessions can be legitimate work.

When a rollout contains a quota snapshot, the dashboard shows the recorded
payload and timestamp separately. It is historical host data; the tracker does
not infer current remaining allowance from it.

The pinned Codex adapter does not expose repeated reads or tool-output sizes.
The dashboard says these are unavailable. Existing VibeHelper hook-output byte
measurements appear separately, covering retained hook history rather than the
token date window. They are not per-file token charges or measured savings.

## Installation and maintenance

One installation is shared across projects at
`~/.local/share/vibehelper/token-usage/`. It contains the parser, MIT license and
installation receipt. Reports live under `~/.cache/vibehelper/usage/`; source
rollouts are read-only. Reports contain local project paths and short turn
excerpts. Do not publish them as project files.

The installer pins Wicked-Sick-Ltd/token-usage revision
`39cbb737084bf4f8aaa0c17b8cd1c560f6a0a47c`, verifies source and license checksums,
and applies `usage-tracker.patch`. The patch preserves optional counters and turn
IDs and adapts the existing dashboard to tokens. No upstream plugin, MCP
server, hook or login service is installed. Collection reuses the upstream parser;
there is no second transcript parser or database.

For installation without downloading:

```sh
python3 vibe_scripts/codex/usage-tracker.py install --source /path/to/token-usage-checkout
```

After an intentional helper update, rerun `setup` to refresh the launcher and app,
then restart its terminal. `install` updates only the pinned parser. Both preserve
locally modified managed files rather than overwriting them. Parser replacement
keeps the prior installation if publication fails.

Development tests require that pinned installation and run through:

```sh
./vibe_scripts/codex/agent-verify.sh test tests/test_usage_tracker.py
```

Tests use synthetic rollouts and temporary report directories. They do not run
live LLM benchmarks. To upgrade the dependency, review the new revision, update
both checksums, rebase the patch, and rerun accounting and offline tests.

From the VibeHelper source checkout, refresh only the three tracker assets in an
existing managed project:

```sh
python3 templates/vibe_scripts/managed-install.py usage-tracker --provider codex --root /absolute/project/path
```

Use `--provider claude` for a ClaudeHelper installation. This command preserves
other helper files and refuses to overwrite locally modified tracker assets.
