# Design review

The automatic quality hook only lints and formats supported edited files.
It does not fingerprint UI source or run Impeccable at SessionStart,
PostToolUse, or Stop.

Use the existing frontend instructions and Impeccable skill when a task requires
design review. Lint and formatting retain their configured accessibility rules;
a lint pass does not establish that the design has been reviewed.

Existing design utilities and Impeccable configuration remain available.
VibeHelper's managed lint/format hook does not alter custom or global hooks.
Previously installed native Impeccable hooks are separate registrations; refreshing
the lint/format helpers does not remove them.
