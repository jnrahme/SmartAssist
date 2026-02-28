#!/usr/bin/env python3
"""
PostToolUse Hook - Show RAG lessons returned to Claude.

When Claude calls rag_search via MCP, this hook reads the tool output
and prints a formatted summary so you can see what lessons were returned.
"""

import sys
import json
import re


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    # Get the tool output (the text returned by rag_search)
    tool_output = hook_input.get("tool_result", {}).get("content", "")
    if isinstance(tool_output, list):
        # MCP returns content as list of blocks
        tool_output = " ".join(
            block.get("text", "") for block in tool_output
            if isinstance(block, dict) and block.get("type") == "text"
        )

    if not tool_output or "No relevant lessons" in tool_output:
        return

    # Parse the lessons from the output
    # Format: Found N relevant lesson(s) for: "query"
    #   [category] (relevance: XX%)
    #     Lesson: text
    query_match = re.search(r'for: "(.+?)"', tool_output)
    query = query_match.group(1) if query_match else "?"

    lessons = re.findall(
        r'\[(\w+)\]\s+\(relevance:\s+(\d+)%\)\s+Lesson:\s+(.+?)(?=\n\n|\Z)',
        tool_output,
        re.DOTALL,
    )

    if not lessons:
        return

    # Print formatted output to stderr (shown to user in Claude Code)
    lines = []
    lines.append("")
    lines.append("\033[36m\033[1m  RAG Lessons Retrieved\033[0m")
    lines.append(f"\033[90m  Query: \"{query}\"\033[0m")
    lines.append("")

    for cat, relevance, text in lessons:
        rel = int(relevance)
        if rel >= 60:
            color = "\033[32m"  # green
        elif rel >= 40:
            color = "\033[33m"  # yellow
        else:
            color = "\033[31m"  # red

        cat_color = {
            "testing": "\033[32m",
            "code_edit": "\033[36m",
            "architecture": "\033[33m",
            "pr_review": "\033[35m",
            "git": "\033[33m",
            "security": "\033[31m",
        }.get(cat, "\033[37m")

        lesson_text = text.strip()[:100]
        lines.append(f"  {color}{rel}%\033[0m  {cat_color}{cat}\033[0m  {lesson_text}")

    lines.append("")

    print("\n".join(lines), file=sys.stderr)


if __name__ == "__main__":
    main()
