"""LLM-powered lesson creation from deep seed context.

Calls an LLM (Claude CLI, Codex CLI, or API) to analyze the codebase
context and create lessons by calling the SmartAssist MCP create_lesson tool.

Zero config if the user has Claude Code or Codex installed — we spawn
their CLI as a subprocess. No API keys needed.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from smartassist.tools.deep_seed import build_deep_seed_prompt


SUPPORTED_LLMS = ["claude", "codex", "anthropic", "openai"]

DEFAULT_LLM = "claude"  # Most SmartAssist users have Claude Code


def detect_available_llm():
    """Auto-detect which LLM backend is available. Returns the best option."""
    if shutil.which("claude"):
        return "claude"
    if shutil.which("codex"):
        return "codex"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def _build_system_prompt():
    """System prompt for the LLM that creates lessons."""
    return (
        "You are an expert software architect analyzing a codebase to create lessons "
        "for SmartAssist Memory. For each pattern you identify, output a JSON object on "
        "its own line with this exact format:\n"
        '{"lesson": "...", "category": "...", "sentiment": "positive|negative", "intensity": 1-5}\n\n'
        "Categories: testing, code_edit, git, architecture, pr_review, security, debugging\n"
        "Each lesson must be >30 chars, start with an action verb, and be specific to THIS project.\n"
        "Output ONLY the JSON lines, nothing else."
    )


def create_lessons_via_claude(prompt, model=None):
    """Use Claude Code CLI (--print) to create lessons. Zero API key needed."""
    print("Using Claude Code CLI...\n")

    cmd = ["claude", "--print", "--output-format", "text"]
    if model:
        cmd.extend(["--model", model])

    full_prompt = _build_system_prompt() + "\n\n" + prompt

    try:
        result = subprocess.run(
            cmd,
            input=full_prompt,
            text=True,
            capture_output=True,
            timeout=300,  # 5 min max
        )
        if result.returncode != 0:
            print(f"Claude CLI error: {result.stderr[:200]}")
            return []
        return _parse_lesson_lines(result.stdout)
    except subprocess.TimeoutExpired:
        print("Claude CLI timed out (5 min limit)")
        return []
    except FileNotFoundError:
        print("Claude CLI not found. Install: npm install -g @anthropic-ai/claude-code")
        return []


def create_lessons_via_codex(prompt):
    """Use Codex CLI (exec) to create lessons. Zero API key needed."""
    print("Using Codex CLI...\n")

    full_prompt = _build_system_prompt() + "\n\n" + prompt

    try:
        result = subprocess.run(
            ["codex", "exec", "-"],
            input=full_prompt,
            text=True,
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0:
            print(f"Codex CLI error: {result.stderr[:200]}")
            return []
        return _parse_lesson_lines(result.stdout)
    except subprocess.TimeoutExpired:
        print("Codex CLI timed out (5 min limit)")
        return []
    except FileNotFoundError:
        print("Codex CLI not found. Install: npm install -g @openai/codex")
        return []


def create_lessons_via_anthropic(prompt, model="claude-sonnet-4-20250514"):
    """Use Anthropic SDK directly. Requires ANTHROPIC_API_KEY."""
    print(f"Using Anthropic API ({model})...\n")

    try:
        import anthropic
    except ImportError:
        print("anthropic package not installed. Run: pip install anthropic")
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY environment variable not set.")
        print("Set it: export ANTHROPIC_API_KEY=sk-ant-...")
        return []

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=8192,
            system=_build_system_prompt(),
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        return _parse_lesson_lines(text)
    except Exception as e:
        print(f"Anthropic API error: {e}")
        return []


def create_lessons_via_openai(prompt, model="gpt-4o"):
    """Use OpenAI SDK directly. Requires OPENAI_API_KEY."""
    print(f"Using OpenAI API ({model})...\n")

    try:
        import openai
    except ImportError:
        print("openai package not installed. Run: pip install openai")
        return []

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY environment variable not set.")
        print("Set it: export OPENAI_API_KEY=sk-...")
        return []

    client = openai.OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content
        return _parse_lesson_lines(text)
    except Exception as e:
        print(f"OpenAI API error: {e}")
        return []


def _parse_lesson_lines(text):
    """Parse JSON lesson lines from LLM output. Handles markdown code blocks."""
    lessons = []
    # Strip markdown code fences if present
    cleaned = text.replace("```json", "").replace("```", "")
    for line in cleaned.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            if "lesson" in data and "category" in data:
                lessons.append(data)
        except json.JSONDecodeError:
            # Try extracting JSON from a line that has extra text
            start = line.find("{")
            end = line.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(line[start:end])
                    if "lesson" in data and "category" in data:
                        lessons.append(data)
                except json.JSONDecodeError:
                    continue
    return lessons


def store_lessons(lessons, project_root):
    """Store parsed lessons into SmartAssist via the store module."""
    from smartassist.config import get_storage_path
    from smartassist.store import add_lesson, append_feedback_event
    from smartassist.thompson_sampling import ThompsonSamplingModel
    import time

    storage = get_storage_path()
    stored = 0
    failed = 0

    for lesson_data in lessons:
        text = lesson_data.get("lesson", "")
        category = lesson_data.get("category", "code_edit")
        sentiment = lesson_data.get("sentiment", "negative")
        intensity = lesson_data.get("intensity", 3)

        new_id, error = add_lesson(storage, text, category, origin="deep_seed")
        if error:
            failed += 1
            continue

        # Record feedback event
        append_feedback_event(storage, {
            "timestamp": time.time(),
            "signal": "thumbs_up" if sentiment == "positive" else "correction",
            "category": category,
            "intensity": intensity,
            "query": "",
            "response": "",
            "correction": text,
            "context": "deep-seed LLM-generated lesson",
        })

        # Update Thompson
        try:
            thompson = ThompsonSamplingModel(str(storage))
            if sentiment == "positive":
                thompson.record_success(category, intensity)
            else:
                thompson.record_failure(category, intensity)
        except Exception:
            pass

        stored += 1

    return stored, failed


def run_llm_seed(llm=None, model=None):
    """Full pipeline: gather context → call LLM → store lessons."""
    project_root = os.getcwd()

    if not Path(project_root, ".git").exists():
        print("Error: Not a git repository. Run from your project root.")
        return 1

    # Detect or use specified LLM
    if llm is None:
        llm = detect_available_llm()
        if llm is None:
            print("No LLM backend found. Options:")
            print("  1. Install Claude Code: npm install -g @anthropic-ai/claude-code")
            print("  2. Install Codex: npm install -g @openai/codex")
            print("  3. Set ANTHROPIC_API_KEY or OPENAI_API_KEY")
            print("\nOr specify: smartassist seed --deep --llm claude")
            return 1
        print(f"Auto-detected LLM: {llm}\n")
    else:
        if llm not in SUPPORTED_LLMS:
            print(f"Unknown LLM: {llm}")
            print(f"Supported: {', '.join(SUPPORTED_LLMS)}")
            return 1

    # Phase 1: Gather context
    prompt = build_deep_seed_prompt(project_root)

    # Phase 2: Call LLM
    print(f"\nCreating lessons via {llm}...\n")

    if llm == "claude":
        lessons = create_lessons_via_claude(prompt, model=model)
    elif llm == "codex":
        lessons = create_lessons_via_codex(prompt)
    elif llm == "anthropic":
        lessons = create_lessons_via_anthropic(prompt, model=model or "claude-sonnet-4-20250514")
    elif llm == "openai":
        lessons = create_lessons_via_openai(prompt, model=model or "gpt-4o")
    else:
        print(f"Unsupported LLM: {llm}")
        return 1

    if not lessons:
        print("No lessons generated. The LLM may have returned an unexpected format.")
        print("Try: smartassist seed --deep --llm claude")
        return 1

    print(f"\nLLM generated {len(lessons)} lesson candidates.\n")

    # Phase 3: Store lessons
    print("Storing lessons in smartassist.db...")
    stored, failed = store_lessons(lessons, project_root)

    print(f"\n{'='*60}")
    print(f"DEEP SEED COMPLETE")
    print(f"{'='*60}")
    print(f"  Generated: {len(lessons)}")
    print(f"  Stored:    {stored}")
    print(f"  Failed:    {failed} (quality gates rejected)")
    print(f"  LLM:       {llm}" + (f" ({model})" if model else ""))

    # Category breakdown
    cats = {}
    for l in lessons:
        c = l.get("category", "unknown")
        cats[c] = cats.get(c, 0) + 1
    if cats:
        print(f"\n  By category:")
        for c, count in sorted(cats.items(), key=lambda x: -x[1]):
            print(f"    {c}: {count}")

    print(f"{'='*60}")
    return 0
