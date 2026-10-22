# Weekly allowance and workflow measurements

The goal is accepted work per Codex weekly allowance. Text sizes, gateway
tokens and API prices do not establish subscription allowance savings.
Keep model settings, tool permissions and required checks unchanged until a
comparison shows a benefit without a quality regression.

## Choose the smallest sufficient workflow

Use maintained scripts for deterministic work. Batch independent reads and
return relevant excerpts. Read each applicable instruction once per task;
re-read it when it changes. Use focused symbols, callers, tests and source
locations before broad graph exploration. Graph freshness is evidence, not
permission for unrelated maintenance. Read full referenced evidence when the
summary is insufficient. Never treat omitted results as absence.

Keep the required independent reviewer. Reuse the same reviewer with a bounded
brief, the original objective, relevant source and the final diff. Do not send
whole conversation histories when a focused brief provides the needed context.

## Model choices to evaluate

For Codex, compare Sol at default reasoning for ordinary coding, Astra/high for
difficult architecture, security or ambiguous debugging, and Luna for focused,
repeatable work. Use available account models; do not silently substitute one.
Prefer Standard speed when preserving allowance matters more than latency.
These are trial candidates, not automatic defaults or a promise of equal quality.
Do not change global model settings or downgrade the independent reviewer.

Current references: [models](https://learn.chatgpt.com/docs/models),
[usage limits](https://learn.chatgpt.com/docs/pricing#what-are-the-usage-limits-for-my-plan).
Verify these when selecting models: availability and usage rates can change.

## Local measurements

Run from the project root:

```sh
python3 vibe_scripts/@PROVIDER@/project-memory.py usage
```

The JSON separates instruction characters, workflow characters, hook output
bytes and retrieval context-payload characters (excluding the CLI envelope and
pretty-printing). It does not estimate tokens or weekly savings.
No hook records means no observed hook workload. Missing provider measurements
remain unknown. Existing savings-ai reports keep their RTK and graph estimates
separate; never add them to these figures.

Compare identical tasks in isolated worktrees, with fresh session records and
the same initial source, tools and checks. Save reports outside the worktree
being measured. Compare hook bytes per event by status, not totals from sessions
with different work. Record the Codex dashboard before and after, model,
reasoning, speed, timestamps and reset window. Other concurrent Codex use or
coarse dashboard resolution makes allowance attribution unknown.

## Delegation outcomes

Read omniroute.md and obtain its required consent before sending context.
Delegate before solving the task yourself. Use one bounded attempt, then fall
back to the main agent when unavailable. Keep architecture, security, migrations
and unfamiliar behavior with the main agent. Preserve all gateway protections.

After reviewing a completed proposal, record the result:

```sh
python3 vibe_scripts/@PROVIDER@/omniroute.py review --attempt-id ID --outcome accepted
```

Outcomes are accepted, revised or rejected. Optional --review-seconds and
--main-tokens record actual measurements; omit unknown values. Main tokens
must cover preparation, review, corrections and fallback work, not just the
worker call. Corrections append a new review entry; history stays intact.
Reviews do not reset failed routes or change ranking. Use history --json to
inspect them. A gateway success is not a quality acceptance.

## Fixed comparison pack

evaluation.json contains 12 fixed tasks: mechanical edit, behavior change,
debugging and review for Python, JavaScript and PHP. Each has initial source,
acceptance criteria and a reference result for the evaluator. Do not give the
reference to the candidate. Copy each task to a fresh project with its normal
controlled checks; never mutate the original pack.

For each task, compare the current workflow and one proposed change, then the
combined workflow. Record task ID, source hash, model/reasoning/speed, check
commands/results, independent reviewer outcome, correction rounds, wall time,
worker usage, supervising-agent usage and dashboard observations. Unknown
measurements stay null. Record rejected and interrupted trials too.

Mechanical tasks must preserve behavior. Behavior/debugging tasks must fail
the stated case before the fix and pass it afterward. Review tasks must identify
the defect and its impact without unrelated edits. Reference results are
evaluation aids, not substitutes for checks or independent review. Fewer bytes
with worse repair quality fails the comparison. No default routing change is
justified by transport success or unreviewed model answers.

Start with generated project fixtures and VibeHelper. Run through the same
approved verification routes. Preserve missing-tool and sandbox diagnostics;
use normal approval for required environment access. Publish measured deltas
and limitations before expanding defaults to other projects.
