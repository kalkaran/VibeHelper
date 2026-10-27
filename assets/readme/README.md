# README figures

Captured on 27 October 2026. PNGs render recorded CLI text in a readable frame;
they are not generated examples or screenshots of the upstream apps. The text
files retain the original standard output. No source code or user prompts are
included.

## Graphify

- Command: `graphify benchmark`, using the existing VibeHelper graph.
- [Original output](graphify-benchmark.txt); [figure](graphify-benchmark.png).
- Installed package: 0.4.13. Its output also reports a skill/package version
  mismatch; those two warning lines are excluded from the figure excerpt.
  No tools were upgraded for this capture.
- With no `.graphify_detect.json`, this version estimates corpus words as
  `node count × 50`, then tokens as `words × 100 / 75`. Query context uses a
  character-count approximation. The 29.5× figure is a synthetic full-corpus
  comparison, not observed savings in real coding sessions. It excludes build
  cost and does not measure answer quality.
- This benchmark reads the existing graph locally without model calls. It is
  separate from code-review-graph's context estimates.

## RTK

- Command: `rtk gain --text --project`, in VibeHelper.
- [Original output](rtk-gain.txt); [figure](rtk-gain.png).
- Four existing filtered commands produced the shown totals. No commands were
  generated to inflate the sample. The display already abbreviates the project
  path; no private username is shown.
- RTK also warned on stderr that its native global hook was not installed.
  These historical counters do not establish current routing coverage.
- Input/output mean raw/filtered command text. Token estimates use bytes divided
  by four; they are not model-provider usage or subscription allowance.
  See [RTK's measurement explanation](https://github.com/rtk-ai/rtk/blob/develop/docs/guide/resources/savings-explained.md).

The README's wrapper diagram uses a fenced Mermaid block, which
[GitHub renders in Markdown](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams).
The Humanizer comparison and diagnostic excerpt are labelled illustrative.
