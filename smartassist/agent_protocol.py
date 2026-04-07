from __future__ import annotations

from pathlib import Path


SMARTASSIST_MANAGED_START = "<!-- SMARTASSIST:FEEDBACK_PROTOCOL START -->"
SMARTASSIST_MANAGED_END = "<!-- SMARTASSIST:FEEDBACK_PROTOCOL END -->"


def render_feedback_protocol() -> str:
    return (
        "LESSON CREATION PROTOCOL\n"
        "============================================================\n"
        "When the user gives feedback during this session, use SmartAssist to\n"
        "persist it for future sessions.\n\n"
        "TRIGGERS — Use SmartAssist when:\n"
        "- The user corrects your approach\n"
        "- The user states a project rule or preference\n"
        "- The user rejects generated code and explains the preferred pattern\n"
        "- A PR review or code discussion reveals a team convention\n"
        "- You discover a project-specific pattern by reading code, configs, or docs\n\n"
        "PRIMARY WORKFLOW:\n"
        "1. Call `apply_feedback_protocol` with the user's feedback or rule.\n"
        "2. Let SmartAssist decide whether to create a lesson, boost an existing one, or suggest a merge.\n"
        "3. Only call `merge_lessons` manually when SmartAssist returns `merge_suggested` and the overlap is real.\n\n"
        "HOW TO WRITE LESSONS WHEN MANUAL INPUT IS NEEDED:\n"
        "- Use imperative actions: 'Use semantic colors instead of hardcoded hex values'\n"
        "- Keep lessons project-specific, not generic programming advice\n"
        "- Categories: testing | code_edit | git | architecture | pr_review | security | debugging\n"
        "- Use intensity 4-5 for hard rules ('never', 'always'), 2-3 for softer preferences\n"
        "- Add brief context about what triggered the lesson\n\n"
        "DO NOT CREATE LESSONS FOR:\n"
        "- Generic programming knowledge\n"
        "- One-time task instructions\n"
        "- Duplicates of existing lessons\n"
        "============================================================"
    )


def render_codex_agents_md() -> str:
    body = render_feedback_protocol()
    return (
        "# SmartAssist Memory\n\n"
        "If the current workspace exposes SmartAssist MCP tools, use them proactively.\n\n"
        "## Before acting\n\n"
        "- Call `rag_search` before code edits, tests, commits, or architecture decisions that may have project-specific rules.\n"
        "- Treat SmartAssist memory as project-specific guidance, not generic advice.\n\n"
        f"## SmartAssist feedback workflow\n\n{body}\n"
    )


def render_opencode_instructions() -> str:
    body = render_feedback_protocol()
    return (
        "# SmartAssist OpenCode Instructions\n\n"
        "Use SmartAssist MCP tools when they are available in this project.\n\n"
        "- Run `rag_search` before code edits, tests, commits, or architecture decisions that may have project-specific conventions.\n"
        "- Use the feedback protocol below whenever the user corrects you or confirms a reusable project pattern.\n\n"
        f"{body}\n"
    )


def render_amp_skill() -> str:
    body = render_feedback_protocol()
    return (
        "---\n"
        "name: smartassist-memory\n"
        "description: Project memory that learns from feedback — search lessons, capture corrections, prevent repeated mistakes\n"
        "---\n\n"
        "# SmartAssist Memory Skill\n\n"
        "## Before major implementation\n\n"
        "Check for project-specific rules with `rag_search` before code edits, tests, commits, or architecture work.\n\n"
        "## On user feedback\n\n"
        f"{body}\n"
    )


def render_manual_system_instructions(agent_name: str) -> str:
    body = render_feedback_protocol()
    return (
        f"# SmartAssist Instructions for {agent_name}\n\n"
        "Add the block below to your system instructions or custom instructions for this agent.\n\n"
        "Also keep the SmartAssist tool schema connected so the agent can call `rag_search`, `apply_feedback_protocol`, `merge_lessons`, and the low-level lesson tools.\n\n"
        f"{body}\n"
    )


def upsert_managed_block(existing_text: str, block_text: str) -> str:
    managed = f"{SMARTASSIST_MANAGED_START}\n{block_text.rstrip()}\n{SMARTASSIST_MANAGED_END}\n"
    if (
        SMARTASSIST_MANAGED_START in existing_text
        and SMARTASSIST_MANAGED_END in existing_text
    ):
        start = existing_text.index(SMARTASSIST_MANAGED_START)
        end = existing_text.index(SMARTASSIST_MANAGED_END) + len(
            SMARTASSIST_MANAGED_END
        )
        prefix = existing_text[:start].rstrip()
        suffix = existing_text[end:].lstrip("\n")
        parts = [part for part in (prefix, managed.rstrip(), suffix.rstrip()) if part]
        return "\n\n".join(parts).rstrip() + "\n"

    stripped = existing_text.rstrip()
    if stripped:
        return stripped + "\n\n" + managed
    return managed


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
