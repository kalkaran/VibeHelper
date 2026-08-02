# VibeHelper

VibeHelper helps you set up Codex or Claude Code inside a project so the assistant has a clearer way to work.

It adds a small set of project files that tell the assistant what to read, how to check its edits, and where to keep useful notes about the repo. The result is a calmer workflow: fewer repeated explanations, shorter tool output, and more consistent checks before the assistant stops.

## What it helps with

- The assistant starts with project instructions instead of guessing how the repo works.
- Edited files can be checked automatically or with one short command.
- Long lint and security output gets trimmed so the assistant can focus on the useful part.
- Stable project knowledge can live in `codebase-wiki/` instead of being rediscovered every session.
- Session notes can be kept in `notes/`, which makes it easier to trace what was asked and what changed.
- Writing help is included through Humanizer, so generated prose can sound less stiff.

## What you gain

You spend less time repeating repo context. You get fewer unfinished "I edited it but did not check it" moments. Future sessions have a place to pick up durable knowledge. The helper also keeps the setup local, so each project can use only the workflow pieces that fit.

This repo is mainly for personal workflow bootstrapping. It is useful when you work across multiple repositories and want each one to give AI assistants the same basic habits.

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

## What gets added

Depending on the helper and options you choose, VibeHelper can add:

- project instructions such as `AGENTS.md` or `CLAUDE.md`
- local hooks for safer assistant runs
- `agent/` workflow guidance
- `codebase-wiki/` repo memory
- `notes/` session logs
- helper scripts for edited-file checks
- Makefile commands such as `make edited-ai`, `make lint-ai`, `make verify-ai`, `make wiki-ai`, `make skills-check`, and `make skills-update`

## After install

Restart Codex or Claude Code in the configured repo so it reloads the new files.

For Codex, review and trust project hooks in `/hooks` if hooks were created. Hook trust has to be done by you.

Run one check after setup:

```sh
make edited-ai
```

## More detail

- [CodexHelper README](CodexHelper/README.md)
- [ClaudeHelper README](ClaudeHelper/README.md)
