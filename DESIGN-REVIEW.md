# Design review

Ordinary lint/format commands and PostToolUse checks do not run Impeccable.
At Stop, UI markup, visible copy, accessibility attributes or stylesheet changes
require a separate design check of the affected files.

SessionStart records a bounded source fingerprint alongside the existing file
baseline. Resuming that session preserves it. This is local Python inspection,
not a model call or a design audit of the repository.

At Stop, the hook compares only changed frontend files against that baseline.
Script bodies, event handlers, tracking attributes and logic outside JSX markup
do not trigger the detector when the design fingerprint is unchanged. Deleted
files and unchanged pre-session UI files do not trigger review.

Impeccable's local detector runs only for changed design fingerprints. A pass is
reused until the design or review settings change. Findings and unavailable
required checks block completion; repeated unchanged failures reuse saved
evidence. Full logs remain on disk and hook excerpts stay within 12 lines/2 KiB.
An explicit controlled check invalidates a cached failure after tool repairs.

Design review still applies when a file has no applicable language linter.
Existing Impeccable enable/consent settings and `IMPECCABLE_HOOK_DISABLED` are
preserved. Without an available detector, an actual design change is reported
as unverified; unrelated logic work is unaffected. New sessions are needed after
upgrading a session that has no design baseline.

The detector can report existing issues in a file whose design changed. Those
findings do not authorize unrelated redesign. Apply the project's scope rules
and retain unresolved verification as blocked.

Source inspection cannot evaluate runtime styles, external design tokens or
every visual effect of application logic. Explicit browser/design review remains
necessary when those change. Oversized files and exhausted inspection budgets
are reported as unverified for the selected files, never expanded into a repo scan.

Quality-only refresh preserves separate native Impeccable registrations. Full
installer setup can migrate recognized duplicate registrations after checking
the replacement runtime. Custom commands and global hooks remain untouched.
