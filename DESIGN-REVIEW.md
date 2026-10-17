# Design review hook

Ordinary `edited-ai` and lint checks do not run or require Impeccable. Formatting,
language checks and their existing accessibility rules remain in place.

The shared quality hook records UI source fingerprints at SessionStart. At Stop,
it compares the session's changed files with that baseline. Changes to markup,
visible copy, UI attributes (including accessibility attributes) or stylesheet
content trigger a separate Impeccable review of those files. PostToolUse keeps
its ordinary quality checks and does not run the design detector.

Script bodies in HTML/components, event-handler callbacks, tracking metadata
(`data-track*`, `data-analytics*`, `data-gtm*`), and logic outside JSX markup are
excluded from the design fingerprint. A tracking-only change therefore does not
reopen existing palette, typography or layout findings. Pre-session dirty UI
files are part of the baseline and do not trigger review without another change.

Successful reviews are remembered until the UI fingerprint changes again.
Findings and execution failures remain pending under the existing bounded Stop
repair policy. Missing session baselines are reported as unavailable; the hook
does not fall back to scanning every dirty file. Start a new agent session after
updating the helpers.

The hook honors shared/local Impeccable enable and consent settings and the
`IMPECCABLE_HOOK_DISABLED` override. Existing detector configuration and ignores
are passed through the normal detector command. No configuration waivers are
added by VibeHelper.

## Installation and existing hooks

Both platform installers deliver the shared hook. During quality setup they
remove exact recognized native Impeccable PostToolUse/Stop registrations only
after verifying the current shared runtime and its SessionStart/Stop wiring.
Configuration files are backed up first. Custom commands, global hooks, other
events and unrelated settings remain untouched. An incomplete replacement is
reported instead of removing the native hooks.

Use the existing full-installer upgrade workflow to refresh installed helpers.
Rerunning native Impeccable hook installation later can add its broad hooks again;
rerun VibeHelper quality setup to reconcile recognized registrations.

## Detection limits

This is source inspection, not a JavaScript evaluator or browser comparison.
Changes to values defined outside markup, CSS-in-JS objects, external design
tokens and runtime rendering logic may require an explicit Impeccable review.
Inspection uses the hook time budget and Impeccable's `hook.limits.maxFileBytes`
(131072 bytes by default). If a changed file cannot be inspected within those
bounds, the hook reports review as incomplete. Unchanged large files do not
block unrelated work.
The session baseline observes changes in the shared working tree; concurrent
writers after SessionStart cannot be attributed to a particular task reliably.
Use separate worktrees when that distinction matters.

The detector reviews the selected files, so it can report existing issues within
a file whose design changed. Findings do not authorize unrelated redesigns.
