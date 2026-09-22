#!/usr/bin/env python3
"""Draft concise codebase-wiki pages from repo guidance and Graphify output."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WIKI_DIR = ROOT / "codebase-wiki"
GRAPH_REPORT = ROOT / "graphify-out" / "GRAPH_REPORT.md"
CLAUDE_MD = ROOT / "CLAUDE.md"


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def clean_line(line: str) -> str:
    line = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", line)
    line = re.sub(r"\[\[([^\]]+)\]\]", r"\1", line)
    tick = chr(96)
    line = re.sub(tick + r"([^" + tick + r"]+)" + tick, r"\1", line)
    return line.strip()


def markdown_section(text: str, heading: str) -> list[str]:
    match = re.search(rf"^## {re.escape(heading)}\s*$", text, re.MULTILINE)
    if not match:
        return []
    start = match.end()
    next_match = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return [clean_line(line) for line in text[start:end].splitlines() if line.strip()]


def heading_block(text: str, heading: str, limit: int = 12) -> list[str]:
    lines = markdown_section(text, heading)
    bullets: list[str] = []
    for line in lines:
        if line.startswith("- "):
            bullets.append(line)
        elif line and not line.startswith("#"):
            bullets.append(f"- {line}")
        if len(bullets) >= limit:
            break
    return bullets


def graph_communities(text: str, limit: int = 18) -> list[str]:
    communities = []
    for line in text.splitlines():
        if line.startswith("### Community "):
            communities.append("- " + clean_line(line.removeprefix("### ")))
        if len(communities) >= limit:
            break
    return communities


def keep(items: list[str], limit: int, placeholder: str) -> str:
    trimmed = [item for item in items if item and not item.startswith("Cohesion:")][
        :limit
    ]
    if not trimmed:
        return f"- {placeholder}"
    return "\n".join(trimmed)


def makefile_targets() -> list[str]:
    try:
        result = subprocess.run(
            ["make", "-qp"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except Exception:
        return []
    output = result.stdout
    found = set()
    for line in output.splitlines():
        if ":" not in line or line.startswith("\t") or line.startswith("."):
            continue
        name = line.split(":", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            found.add(name)
    preferred = [
        "edited-ai",
        "wiki-ai",
        "skills-check",
        "skills-update",
        "verify-ai",
        "lint-ai",
        "typecheck-ai",
        "security-ai",
        "verify",
        "lint",
        "typecheck",
        "test",
        "security",
    ]
    return [target for target in preferred if target in found]


def auto_block(key: str, body: str) -> str:
    return f"<!-- BEGIN AUTO-WIKI:{key} -->\n{body.rstrip()}\n<!-- END AUTO-WIKI:{key} -->\n"


def replace_section(path: Path, title: str, key: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_text(path)
    block = auto_block(key, body)
    begin = f"<!-- BEGIN AUTO-WIKI:{key} -->"
    end = f"<!-- END AUTO-WIKI:{key} -->"
    if begin in current and end in current:
        pattern = re.compile(rf"{re.escape(begin)}.*?{re.escape(end)}\n?", re.DOTALL)
        updated = pattern.sub(block, current)
    elif (
        "TODO:" in current
        or "Run make wiki-ai" in current
        or len(current.strip()) < 140
    ):
        updated = f"# {title}\n\n{block}"
    elif current.strip():
        updated = current.rstrip() + "\n\n" + block
    else:
        updated = f"# {title}\n\n{block}"
    path.write_text(updated, encoding="utf-8")


def build_index() -> str:
    return """# Codebase wiki

This is durable, human-readable memory for AI agents and developers.

Pages:
- [Architecture](architecture.md)
- [Testing](testing.md)
- [Conventions](conventions.md)
- [Known issues](known-issues.md)
- [Risky areas](risky-areas.md)

Maintenance rule:
- Use make wiki-ai to draft concise updates from repo guidance and Graphify output.
- Promote only stable, reusable facts into this wiki.
- Do not add temporary task notes, raw linter output, raw Graphify output, or speculation.
- Review generated sections before relying on them for future work.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    agents = read_text(CLAUDE_MD)
    graph = read_text(GRAPH_REPORT)
    targets = [f"- make {target}" for target in makefile_targets()]
    graph_summary = markdown_section(graph, "Summary")[:6]
    hubs = markdown_section(graph, "Community Hubs (Navigation)")[:16]
    god_nodes = markdown_section(
        graph, "God Nodes (most connected - your core abstractions)"
    )[:12]
    hyperedges = markdown_section(graph, "Hyperedges (group relationships)")[:12]
    surprising = markdown_section(
        graph, "Surprising Connections (you probably didn't know these)"
    )[:8]
    communities = graph_communities(graph)

    risk_terms = (
        "security",
        "csrf",
        "tracking",
        "dashboard",
        "endpoint",
        "email",
        "signup",
        "auth",
        "config",
        "rate",
    )
    risk_items = []
    for item in hubs + hyperedges + communities:
        if any(term in item.lower() for term in risk_terms):
            risk_items.append(item)

    pages = {
        "architecture.md": (
            "Architecture",
            "architecture",
            f"""## Generated Repo Map

Source: CLAUDE.md and graphify-out/GRAPH_REPORT.md.

### Project Shape
{keep(heading_block(agents, "Project Structure & Module Organization"), 10, "No project-structure guidance found in CLAUDE.md.")}

### Graphify Summary
{keep(graph_summary, 6, "Run Graphify to populate repository structure.")}

### Main Areas
{keep(hubs, 16, "No Graphify community hubs found.")}

### Core Abstractions
{keep(god_nodes, 12, "No core abstraction list found.")}

### Group Relationships
{keep(hyperedges, 12, "No grouped relationships found.")}
""",
        ),
        "testing.md": (
            "Testing",
            "testing",
            f"""## Generated Verification Memory

Source: CLAUDE.md and current Makefile targets.

### Manual Checks
{keep(heading_block(agents, "Testing Guidelines"), 10, "No testing guidance found in CLAUDE.md.")}

### Development Commands
{keep(heading_block(agents, "Build, Test, and Development Commands"), 10, "No development commands found in CLAUDE.md.")}

### Make Targets
{keep(targets, 12, "No Makefile quality targets detected.")}

### Agent Rule
- Prefer make edited-ai after edits so formatter, linter, and typechecker output stays capped for AI use.
- Use make verify-ai when the change is broad, security-sensitive, or crosses multiple workflows.
- Use make wiki-ai after Graphify or substantial discovery work to draft durable memory updates.
""",
        ),
        "conventions.md": (
            "Conventions",
            "conventions",
            f"""## Generated Conventions

Source: CLAUDE.md.

### Code Style
{keep(heading_block(agents, "Coding Style & Naming Conventions"), 12, "No coding-style guidance found in CLAUDE.md.")}

### Repo Memory
{keep(heading_block(agents, "Repo Memory Workflow"), 12, "No repo-memory workflow found in CLAUDE.md.")}

### Security Conventions
{keep(heading_block(agents, "Security & Configuration Tips"), 8, "No security conventions found in CLAUDE.md.")}
""",
        ),
        "known-issues.md": (
            "Known issues",
            "known-issues",
            f"""## Generated Known-Issue Candidates

Source: graphify-out/GRAPH_REPORT.md. These are review prompts, not confirmed bugs.

### Surprising Connections To Review
{keep(surprising, 8, "No surprising connections found.")}

### Maintenance Rule
- Move an item into a permanent known issue only after a task confirms the behavior and its impact.
- Remove stale items when the underlying code or workflow changes.
""",
        ),
        "risky-areas.md": (
            "Risky areas",
            "risky-areas",
            f"""## Generated Risk Map

Source: CLAUDE.md and graphify-out/GRAPH_REPORT.md.

### Security Baseline
{keep(heading_block(agents, "Security & Configuration Tips"), 8, "No security baseline found in CLAUDE.md.")}

### Areas Requiring Extra Care
{keep(list(dict.fromkeys(risk_items)), 18, "No Graphify risk candidates found.")}

### Agent Rule
- For these areas, read the relevant implementation files and run targeted checks before finalizing.
- Do not weaken validation, CSRF, auth, rate limiting, escaping, logging, or private-file protections.
""",
        ),
    }

    if args.dry_run:
        print(build_index().rstrip())
        for filename, (_title, key, body) in pages.items():
            print(f"\n--- codebase-wiki/{filename} ---")
            print(auto_block(key, body).rstrip())
        return 0

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    (WIKI_DIR / "index.md").write_text(build_index(), encoding="utf-8")
    for filename, (title, key, body) in pages.items():
        replace_section(WIKI_DIR / filename, title, key, body)
    print("Updated codebase-wiki generated sections.")
    if not GRAPH_REPORT.exists():
        print("Graphify report not found; run Graphify for richer repo memory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
