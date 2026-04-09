#!/usr/bin/env python3
"""
SmartAssist CLI - Portable RAG learning system for Claude Code.

Usage:
    smartassist setup         Configure Claude Code (MCP server + hooks + init)
    smartassist telemetry     Manage opt-in telemetry and aggregate KPIs
    smartassist doctor        Audit install readiness and runtime wiring
    smartassist uninstall     Remove SmartAssist from Claude Code config
    smartassist init          Initialize SmartAssist in current project
    smartassist serve         Start MCP server (stdio)
    smartassist health        Run health checks
    smartassist migrate PATH  Migrate data from old rag-setup location
    smartassist vectorize     Re-vectorize curated lessons
    smartassist maintenance   Run staleness + compaction checks
    smartassist analyze       Show usage analytics
    smartassist dashboard     Generate HTML dashboard
    smartassist qa            Run QA scenarios and generate demo artifacts
    smartassist seed          Seed database from CLAUDE.md conventions
    smartassist version       Show version information
"""

import os
import sys
import json
import hashlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from smartassist.agent_protocol import (
    render_amp_skill,
    render_codex_agents_md,
    render_manual_system_instructions,
    render_opencode_instructions,
    upsert_managed_block,
)
from smartassist.claude_config import (
    ensure_project_mcp_server,
    remove_legacy_mcp_servers,
    remove_project_mcp_servers,
    remove_project_state_mcp_servers,
    remove_user_mcp_servers,
)


def _mcp_env() -> dict[str, str]:
    return {
        "HOME": str(Path.home()),
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:"
        + str(Path.home() / ".local" / "bin"),
        "TMPDIR": "/tmp",
    }


def _resolve_mcp_server_command() -> tuple[str, list[str]]:
    smartassist_path = shutil.which("smartassist")
    if smartassist_path:
        real_path = Path(smartassist_path).resolve()
        venv_python = str(real_path.parent / "python")
        if Path(venv_python).exists():
            return venv_python, ["-m", "smartassist.mcp_server"]
        return smartassist_path, ["serve"]
    return "smartassist", ["serve"]


def _record_setup_note(message: str, log=None):
    if log is None:
        print(f"  {message}")
    else:
        log(message)


def _add_summary_entry(summary, message: str) -> None:
    if summary is None:
        return
    if callable(summary):
        summary(message)
        return
    summary.append(message)


def _write_text_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n")
    return path


def _record_telemetry_event(
    event_name: str,
    *,
    agent_type: str = "",
    metadata: dict | None = None,
) -> None:
    try:
        from smartassist.telemetry import record_lifecycle_event

        record_lifecycle_event(
            event_name,
            agent_type=agent_type,
            metadata=metadata or {},
        )
    except Exception:
        pass


def _register_telemetry_project(storage_path: Path) -> None:
    try:
        from smartassist.telemetry import register_project

        register_project(storage_path)
    except Exception:
        pass


def _project_backup_key(project_root: Path) -> str:
    resolved = project_root.resolve()
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{resolved.name}-{digest}"


def _resolve_git_dir(project_root: Path) -> Path | None:
    dot_git = project_root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            text = dot_git.read_text().strip()
        except OSError:
            return None
        prefix = "gitdir:"
        if text.lower().startswith(prefix):
            git_dir = text[len(prefix) :].strip()
            path = Path(git_dir)
            if not path.is_absolute():
                path = (project_root / git_dir).resolve()
            return path
    return None


def _ensure_local_git_excludes(
    project_root: Path,
    patterns: list[str],
    summary=None,
) -> Path | None:
    git_dir = _resolve_git_dir(project_root)
    if git_dir is None:
        return None

    exclude_path = git_dir / "info" / "exclude"
    existing_lines = (
        exclude_path.read_text().splitlines() if exclude_path.exists() else []
    )
    normalized = {line.strip() for line in existing_lines}
    missing = [pattern for pattern in patterns if pattern not in normalized]
    if not missing:
        _add_summary_entry(
            summary,
            f"Git exclude: SmartAssist entries already present in {exclude_path}",
        )
        return exclude_path

    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    new_lines = list(existing_lines)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    if "# SmartAssist local metadata" not in normalized:
        new_lines.append("# SmartAssist local metadata")
    new_lines.extend(missing)
    exclude_path.write_text("\n".join(new_lines).rstrip() + "\n")
    _add_summary_entry(
        summary,
        f"Git exclude: added {', '.join(missing)} to {exclude_path}",
    )
    return exclude_path


def _cleanup_smartassist_gitignore(
    project_root: Path,
    summary=None,
) -> bool:
    gitignore = project_root / ".gitignore"
    if not gitignore.exists():
        return False

    try:
        lines = gitignore.read_text().splitlines()
    except OSError:
        return False

    new_lines: list[str] = []
    removed = False
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        if stripped == "# SmartAssist data":
            next_line = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
            if next_line == ".claude/smartassist/":
                removed = True
                idx += 2
                continue
        if stripped == ".claude/smartassist/":
            removed = True
            idx += 1
            continue
        new_lines.append(lines[idx])
        idx += 1

    if not removed:
        return False

    while new_lines and not new_lines[-1].strip():
        new_lines.pop()
    gitignore.write_text("\n".join(new_lines).rstrip() + ("\n" if new_lines else ""))
    _add_summary_entry(
        summary, "Git ignore: removed SmartAssist-managed .gitignore entries"
    )
    return True


def _project_backup_dir(project_root: Path) -> Path:
    return (
        Path.home()
        / ".smartassist"
        / "backups"
        / "projects"
        / _project_backup_key(project_root)
    )


def _relocate_project_backup_noise(
    project_root: Path,
    summary=None,
) -> list[Path]:
    destination = _project_backup_dir(project_root)
    moved: list[Path] = []
    for backup in sorted(project_root.glob(".mcp.json.bak.*")):
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / backup.name
        shutil.move(str(backup), str(target))
        moved.append(target)
    if moved:
        _add_summary_entry(
            summary,
            f"Backup cleanup: moved {len(moved)} project MCP backup(s) to {destination}",
        )
    return moved


def _upsert_global_toml_setting(
    existing_text: str, key: str, value_literal: str
) -> str:
    lines = [
        line
        for line in existing_text.splitlines()
        if not line.strip().startswith(f"{key} =")
    ]
    insert_at = 0
    while insert_at < len(lines) and (
        not lines[insert_at].strip() or lines[insert_at].lstrip().startswith("#")
    ):
        insert_at += 1
    lines.insert(insert_at, f"{key} = {value_literal}")
    return "\n".join(lines).rstrip() + "\n"


def _ensure_codex_instruction_setup() -> tuple[Path, Path]:
    codex_dir = Path.home() / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)

    agents_path = codex_dir / "AGENTS.md"
    existing_agents = agents_path.read_text() if agents_path.exists() else ""
    agents_path.write_text(
        upsert_managed_block(existing_agents, render_codex_agents_md())
    )

    config_path = codex_dir / "config.toml"
    existing_config = config_path.read_text() if config_path.exists() else ""
    config_path.write_text(
        _upsert_global_toml_setting(
            existing_config,
            "project_doc_fallback_filenames",
            '["AGENTS.md", "CLAUDE.md"]',
        )
    )

    return agents_path, config_path


def _render_codex_mcp_block(command: str, args: list[str]) -> str:
    rendered_args = ", ".join(json.dumps(arg) for arg in args)
    return (
        "\n[mcp_servers.smartassist]\n"
        f"command = {json.dumps(command)}\n"
        f"args = [{rendered_args}]\n"
    )


def _ensure_project_instruction_file(filename: str, content: str) -> Path:
    project_root = Path.cwd().resolve()
    return _write_text_file(project_root / ".smartassist" / filename, content)


def _ensure_project_mcp_registration(log=None) -> Path:
    project_root = Path.cwd().resolve()
    project_mcp_path = project_root / ".mcp.json"
    command, args = _resolve_mcp_server_command()
    env = _mcp_env()

    if remove_user_mcp_servers():
        _record_setup_note(
            "MCP server: removed stale ~/.claude.json user registration",
            log=log,
        )
    if remove_project_state_mcp_servers(project_root):
        _record_setup_note(
            "MCP server: removed stale ~/.claude.json project registration",
            log=log,
        )
    if remove_legacy_mcp_servers():
        _record_setup_note(
            "MCP server: removed stale ~/.claude/mcp.json registration",
            log=log,
        )
    if remove_project_mcp_servers(project_root):
        _record_setup_note(
            f"MCP server: refreshed existing {project_mcp_path.name}",
            log=log,
        )

    claude_bin = shutil.which("claude")
    if claude_bin:
        env_args = []
        for key, value in env.items():
            env_args.extend(["-e", f"{key}={value}"])
        add_cmd = [
            claude_bin,
            "mcp",
            "add",
            "smartassist",
            "-s",
            "project",
            *env_args,
            "--",
            command,
            *args,
        ]
        result = subprocess.run(add_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            _record_setup_note(
                f"MCP server: registered via 'claude mcp add -s project' ({project_mcp_path})",
                log=log,
            )
            return project_mcp_path

        error = result.stderr.strip() or "unknown error"
        _record_setup_note(
            f"MCP server: 'claude mcp add -s project' failed ({error}), "
            "falling back to .mcp.json",
            log=log,
        )

    mcp_path = ensure_project_mcp_server(
        project_root=project_root,
        command=command,
        args=args,
        env=env,
    )
    _record_setup_note(
        f"MCP server: wrote project registration to {mcp_path}",
        log=log,
    )
    return mcp_path


def cmd_init(log=None):
    """Initialize SmartAssist in the current project."""
    from smartassist.store import initialize_store

    cwd = Path.cwd().resolve()
    data_dir = cwd / ".claude" / "smartassist"
    storage = data_dir / "data"
    lancedb = data_dir / "lancedb"
    force = "--force" in sys.argv
    already_initialized = data_dir.exists() and not force

    if already_initialized:
        print(f"SmartAssist already initialized at {data_dir}")
        print("  Re-run with --force to reinitialize")
    else:
        print(f"Initializing SmartAssist in {cwd}...")

    storage.mkdir(parents=True, exist_ok=True)
    lancedb.mkdir(parents=True, exist_ok=True)
    initialize_store(storage)
    _register_telemetry_project(storage)

    exclude_path = _ensure_local_git_excludes(
        cwd,
        [".claude/smartassist/", ".mcp.json", ".mcp.json.bak*"],
        summary=log,
    )
    if exclude_path is None:
        gitignore = cwd / ".gitignore"
        smartassist_entry = ".claude/smartassist/"
        if gitignore.exists():
            content = gitignore.read_text()
            if smartassist_entry not in content:
                with open(gitignore, "a") as f:
                    f.write(f"\n# SmartAssist data\n{smartassist_entry}\n")
                print("  Updated .gitignore")
        else:
            gitignore.write_text(f"# SmartAssist data\n{smartassist_entry}\n")
            print("  Created .gitignore")
    else:
        print("  Updated local git excludes")
        _cleanup_smartassist_gitignore(cwd, summary=log)
        _relocate_project_backup_noise(cwd, summary=log)

    mcp_path = _ensure_project_mcp_registration(log=log)

    if already_initialized:
        print(f"  MCP registration verified: {mcp_path}")
        _record_telemetry_event("project_initialized")
        return 0

    print(f"\n  Created: {data_dir}/")
    print(f"    data/       - canonical store + compatibility exports")
    print(f"      smartassist.db - runtime source of truth")
    print(f"    lancedb/    - vector database")
    print(f"  MCP:          {mcp_path}")
    print(f"\nSmartAssist initialized successfully!")
    print(f"\nNext steps:")
    print(f"  1. Add lessons: smartassist seed")
    print(f"  2. Vectorize:   smartassist vectorize")
    print(f"  3. Check:       smartassist health")
    _record_telemetry_event("project_initialized")
    return 0


def cmd_serve():
    """Start the MCP server."""
    from smartassist.mcp_server import serve

    serve()
    return 0


def cmd_health():
    """Run health checks."""
    from smartassist.tools.health_check import main

    return main()


def cmd_doctor():
    """Audit SmartAssist install readiness for the current environment."""
    from smartassist.tools.doctor import collect_doctor_report, report_to_text

    report = collect_doctor_report()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        print(report_to_text(report), end="")
    _record_telemetry_event(
        "doctor_ready" if report["overall_status"] == "ready" else "doctor_not_ready",
        metadata={"status": report["overall_status"]},
    )
    return 0 if report["overall_status"] == "ready" else 1


def cmd_migrate():
    """Migrate data from old rag-setup location."""
    if len(sys.argv) < 3:
        print("Usage: smartassist migrate <path-to-old-rag-setup>")
        print("  e.g. smartassist migrate ~/old-project/rag-setup")
        return 1

    old_path = Path(sys.argv[2]).expanduser().resolve()
    old_storage = old_path / "rag_knowledge"
    old_lancedb = old_path / "lancedb"

    if not old_storage.exists():
        print(f"Error: {old_storage} not found")
        return 1

    # Find current project's data dir
    from smartassist.config import get_storage_path, get_db_path

    try:
        storage = get_storage_path()
        db = get_db_path()
    except RuntimeError:
        print("Error: SmartAssist not initialized in current project.")
        print("  Run 'smartassist init' first.")
        return 1

    print(f"Migrating from: {old_storage}")
    print(f"           to:   {storage}")
    print()

    # Copy data files
    data_files = [
        "feedback_log.jsonl",
        "reliability_scores.json",
        "curated_lessons.json",
        "lesson_scores.json",
        "vectorization_log.json",
        "usage_log.jsonl",
        "commit_captures.json",
        "session_log.jsonl",
        "rag_prompt_counter.json",
    ]

    copied = 0
    for fname in data_files:
        src = old_storage / fname
        if src.exists():
            dst = storage / fname
            shutil.copy2(src, dst)
            size = src.stat().st_size
            print(f"  Copied {fname} ({size:,} bytes)")
            copied += 1

    # Copy LanceDB
    if old_lancedb.exists():
        print(f"\n  Copying LanceDB...")
        if db.exists():
            shutil.rmtree(db)
        shutil.copytree(old_lancedb, db)
        print(f"  Copied LanceDB directory")
        copied += 1

    # Copy lessons_learned directory if it exists
    old_lessons = old_storage / "lessons_learned"
    if old_lessons.exists():
        new_lessons = storage / "lessons_learned"
        if new_lessons.exists():
            shutil.rmtree(new_lessons)
        shutil.copytree(old_lessons, new_lessons)
        lesson_count = len(list(new_lessons.glob("*.md")))
        print(f"  Copied {lesson_count} lesson files")
        copied += 1

    print(f"\nMigration complete! {copied} items copied.")
    print(f"\nRun 'smartassist health' to verify.")
    return 0


def cmd_vectorize():
    """Re-vectorize curated lessons."""
    from smartassist.tools.cleanup_and_vectorize import main

    main()
    return 0


def cmd_maintenance():
    """Run maintenance tasks."""
    from smartassist.tools.maintenance import main

    main()
    return 0


def cmd_analyze():
    """Show usage analytics."""
    from smartassist.tools.analyze_usage import analyze

    analyze()
    return 0


def cmd_dashboard():
    """Generate HTML dashboard."""
    from smartassist.tools.generate_dashboard import main

    rc = main()
    if rc == 0:
        _record_telemetry_event("dashboard_opened")
    return rc


def cmd_seed():
    """Seed database from CLAUDE.md or deep LLM-powered codebase analysis.

    Basic:
        smartassist seed                    # Parse CLAUDE.md for conventions

    Deep (LLM-powered):
        smartassist seed --deep             # Auto-detect LLM, create architect-level lessons
        smartassist seed --deep --llm claude # Use Claude Code CLI (zero API key)
        smartassist seed --deep --llm codex # Use Codex CLI (zero API key)
        smartassist seed --deep --llm anthropic  # Use Anthropic API (needs ANTHROPIC_API_KEY)
        smartassist seed --deep --llm openai     # Use OpenAI API (needs OPENAI_API_KEY)
        smartassist seed --deep --llm anthropic --model claude-opus-4-20250514
        smartassist seed --deep --llm ollama --model llama3.1   # Local Ollama (free)
        smartassist seed --deep --llm custom --model <name>    # Any OpenAI-compatible API
        smartassist seed --deep --print     # Print prompt only (paste into any LLM session)

    Custom endpoint env vars:
        LLM_API_BASE=https://api.together.xyz/v1
        LLM_API_KEY=your-key
        LLM_MODEL=meta-llama/Llama-3-70b-chat-hf
    """
    if "--deep" not in sys.argv:
        from smartassist.hooks.seed_from_claudemd import seed_database

        seed_database()
        _record_telemetry_event("seed_completed", metadata={"mode": "claude_md"})
        return 0

    # --print: just output the prompt (original behavior)
    if "--print" in sys.argv:
        from smartassist.tools.deep_seed import run_deep_seed

        return run_deep_seed()

    # --llm: specify which LLM to use
    llm = None
    model = None
    if "--llm" in sys.argv:
        idx = sys.argv.index("--llm")
        if idx + 1 < len(sys.argv):
            llm = sys.argv[idx + 1]
    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        if idx + 1 < len(sys.argv):
            model = sys.argv[idx + 1]

    base_url = None
    api_key = None
    if "--base-url" in sys.argv:
        idx = sys.argv.index("--base-url")
        if idx + 1 < len(sys.argv):
            base_url = sys.argv[idx + 1]
    if "--api-key" in sys.argv:
        idx = sys.argv.index("--api-key")
        if idx + 1 < len(sys.argv):
            api_key = sys.argv[idx + 1]

    from smartassist.tools.llm_seed import run_llm_seed

    rc = run_llm_seed(llm=llm, model=model, base_url=base_url, api_key=api_key)
    if rc == 0:
        _record_telemetry_event(
            "seed_completed",
            metadata={"mode": "deep", "llm": llm or "auto"},
        )
    return rc


def _clean_stale_shell_aliases():
    """Remove old SmartAssist/rag-setup aliases from shell rc files.

    Previous versions required manual aliases in .zshrc/.bashrc.
    pipx install now provides all commands, so these aliases shadow
    the real binaries and must be removed.
    """
    stale_patterns = [
        "alias claude-sa=",
        "alias claude-save=",
        "alias rlhf-commit=",
    ]
    comment_patterns = [
        "# SmartAssist",
        "# RLHF Session End",
        "# RLHF Commit Hook",
    ]
    all_patterns = stale_patterns + comment_patterns

    removed = []
    for rc_name in (".zshrc", ".bashrc", ".bash_profile"):
        rc_path = Path.home() / rc_name
        if not rc_path.exists():
            continue

        lines = rc_path.read_text().splitlines(keepends=True)
        cleaned = []
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if any(stripped.startswith(p) for p in stale_patterns):
                removed.append((rc_name, stripped))
                i += 1
                continue

            # Only remove legacy comment banners when they directly precede a
            # stale alias line. Otherwise preserve user-authored comments.
            next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if stripped in comment_patterns and any(
                next_stripped.startswith(p) for p in stale_patterns
            ):
                removed.append((rc_name, stripped))
                i += 1
                continue

            cleaned.append(line)
            i += 1

        if len(cleaned) < len(lines):
            # Remove trailing blank lines left behind
            while (
                cleaned
                and cleaned[-1].strip() == ""
                and len(cleaned) > 1
                and cleaned[-2].strip() == ""
            ):
                cleaned.pop()
            rc_path.write_text("".join(cleaned))

    return removed


# ── Hook definitions (shared between setup and uninstall) ────────────────

HOOK_DEFS = [
    {
        "event": "UserPromptSubmit",
        "matcher": None,
        "command": "smartassist-prompt-inject",
    },
    {
        "event": "SessionStart",
        "matcher": "startup",
        "command": "smartassist-session-start",
    },
    {
        "event": "PreToolUse",
        "matcher": "Bash|Edit|Write",
        "command": "smartassist-commit-hook",
    },
    {
        "event": "PostToolUse",
        "matcher": "mcp__smartassist__rag_search",
        "command": "smartassist-show-lessons",
    },
    {
        "event": "SessionEnd",
        "matcher": "other",
        "command": "smartassist-session-end",
    },
]

SMARTASSIST_COMMANDS = [
    "smartassist-prompt-inject",
    "smartassist-session-start",
    "smartassist-session-end",
    "smartassist-commit-hook",
    "smartassist-show-lessons",
]


def _backup_file(path, summary, backup_dir: Path | None = None):
    """Create a timestamped backup of a file if it exists."""
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{path.name}.bak.{ts}"
    else:
        backup = path.with_name(f"{path.name}.bak.{ts}")
    shutil.copy2(path, backup)
    summary.append(f"Backup: {path.name} → {backup.name}")


def _configure_hooks(settings, summary):
    """Remove all SmartAssist hooks then add fresh. Prevents duplicates on re-run."""
    settings.setdefault("hooks", {})

    for hook_def in HOOK_DEFS:
        event = hook_def["event"]
        settings["hooks"].setdefault(event, [])

        # Remove ALL existing SmartAssist hooks for this event
        original_count = len(settings["hooks"][event])
        settings["hooks"][event] = [
            group
            for group in settings["hooks"][event]
            if not any(
                hook_def["command"] in inner.get("command", "")
                for inner in group.get("hooks", [])
            )
        ]
        removed = original_count - len(settings["hooks"][event])

        # Add fresh
        hook_group = {
            "hooks": [{"type": "command", "command": hook_def["command"]}],
        }
        if hook_def["matcher"]:
            hook_group["matcher"] = hook_def["matcher"]
        settings["hooks"][event].append(hook_group)

        if removed > 0:
            summary.append(f"Hook {event}: replaced ({hook_def['command']})")
        else:
            summary.append(f"Hook {event}: added ({hook_def['command']})")


def cmd_setup():
    """Configure Claude Code to use SmartAssist (MCP server + hooks + init).

    5-phase setup: pre-flight → backup → configure → init → validate.
    Idempotent — safe to run multiple times.
    """
    summary = []
    log_lines = []

    _record_telemetry_event("install_started", agent_type="claude")

    def log(msg):
        summary.append(msg)
        log_lines.append(f"[{datetime.now().isoformat()}] {msg}")

    # ── Phase 1: Pre-flight checks ──────────────────────────────────────
    print("Phase 1: Pre-flight checks...")

    # Python >= 3.10
    if sys.version_info < (3, 10):
        _record_telemetry_event(
            "setup_failed",
            agent_type="claude",
            metadata={"stage": "python_version"},
        )
        print(f"Error: Python >= 3.10 required (found {sys.version})")
        return 1
    log(f"Python: {sys.version_info.major}.{sys.version_info.minor} OK")

    # smartassist in PATH
    if not shutil.which("smartassist"):
        _record_telemetry_event(
            "setup_failed",
            agent_type="claude",
            metadata={"stage": "smartassist_path"},
        )
        print("Error: 'smartassist' command not found in PATH")
        print(
            "  Install with: pipx install git+https://github.com/jnrahme/SmartAssist.git"
        )
        return 1
    log("smartassist: found in PATH")

    # ~/.claude/ exists
    claude_dir = Path.home() / ".claude"
    if not claude_dir.exists():
        _record_telemetry_event(
            "setup_failed",
            agent_type="claude",
            metadata={"stage": "claude_dir"},
        )
        print("Error: ~/.claude/ directory not found")
        print("  Install Claude Code first: https://claude.ai/code")
        return 1
    log("~/.claude/: exists")

    # ── Phase 2: Backup config files ────────────────────────────────────
    print("Phase 2: Backing up config files...")

    mcp_path = claude_dir / "mcp.json"
    project_root = Path.cwd().resolve()
    project_mcp_path = project_root / ".mcp.json"
    claude_json_path = Path.home() / ".claude.json"
    settings_path = claude_dir / "settings.json"

    _backup_file(mcp_path, summary)
    _backup_file(
        project_mcp_path, summary, backup_dir=_project_backup_dir(project_root)
    )
    _backup_file(claude_json_path, summary)
    _backup_file(settings_path, summary)
    # Shell rc backup happens inside _clean_stale_shell_aliases if needed

    # ── Phase 3: Configure (idempotent) ─────────────────────────────────
    print("Phase 3: Configuring...")

    # 3a. Clean stale shell aliases
    removed_aliases = _clean_stale_shell_aliases()
    if removed_aliases:
        log(f"Shell cleanup: removed {len(removed_aliases)} stale aliases")
        for rc_name, line in removed_aliases:
            log(f"  {rc_name}: {line}")
    else:
        log("Shell cleanup: no stale aliases found")

    # 3b. Hooks — remove-then-add strategy via _configure_hooks()
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    _configure_hooks(settings, summary)
    from smartassist.config import atomic_write_json

    atomic_write_json(settings_path, settings)

    # 3c. Ensure ~/.local/bin in PATH
    local_bin = Path.home() / ".local" / "bin"
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    if str(local_bin) not in path_dirs:
        added_path = False
        for rc_name in (".zshrc", ".bashrc", ".bash_profile"):
            rc_path = Path.home() / rc_name
            if rc_path.exists():
                content = rc_path.read_text()
                path_line = 'export PATH="$HOME/.local/bin:$PATH"'
                if path_line not in content and ".local/bin" not in content:
                    _backup_file(rc_path, summary)
                    with open(rc_path, "a") as f:
                        f.write(f"\n# Added by SmartAssist\n{path_line}\n")
                    log(f"PATH: added ~/.local/bin to {rc_name}")
                    added_path = True
                    break
                else:
                    log(f"PATH: ~/.local/bin already in {rc_name}")
                    added_path = True
                    break
        if not added_path:
            log("PATH: warning — ~/.local/bin not in PATH, add it manually")
    else:
        log("PATH: ~/.local/bin already in PATH")

    # 3d. Verify all 7 CLI commands accessible
    required_commands = ["smartassist", "claude-sa"] + SMARTASSIST_COMMANDS
    missing = [cmd for cmd in required_commands if not shutil.which(cmd)]
    if missing:
        log(f"CLI commands: WARNING — not found in PATH: {', '.join(missing)}")
        log(f"  Fix: run 'pipx ensurepath' and restart your terminal")
    else:
        log(f"CLI commands: all {len(required_commands)} commands found")

    # ── Phase 4: Initialize project data ────────────────────────────────
    print("Phase 4: Initializing project data...")
    cmd_init(log=log)

    # ── Phase 5: Validate & report ──────────────────────────────────────
    print("Phase 5: Validating...")

    # Lightweight import check
    try:
        from smartassist.config import get_storage_path  # noqa: F401
        import mcp  # noqa: F401

        log("Import check: smartassist.config + mcp OK")
    except ImportError as e:
        log(f"Import check: FAILED — {e}")

    # Print summary
    print("\n--- SmartAssist Setup Summary ---")
    for line in summary:
        print(f"  {line}")

    # Write setup log
    setup_log = claude_dir / "smartassist_setup.log"
    try:
        with open(setup_log, "a") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"Setup run: {datetime.now().isoformat()}\n")
            for line in log_lines:
                f.write(f"  {line}\n")
        print(f"\n  Log written to: {setup_log}")
    except Exception:
        pass

    print("\nNext steps:")
    print("  smartassist seed       Seed lessons from CLAUDE.md")
    print("  smartassist health     Verify everything works")
    print("  claude-sa              Launch Claude Code with RAG monitor")
    _record_telemetry_event("setup_completed", agent_type="claude")
    return 0


def cmd_uninstall():
    """Remove SmartAssist from Claude Code config (MCP server + hooks).

    Project data is NOT deleted. Run `pipx uninstall smartassist` for full removal.
    """
    _record_telemetry_event("uninstall_requested")
    claude_dir = Path.home() / ".claude"
    removed = []
    project_root = Path.cwd().resolve()

    if remove_project_mcp_servers(project_root):
        removed.append(f"MCP server: removed from {project_root / '.mcp.json'}")
    else:
        removed.append("MCP server: no project-scoped .mcp.json registration found")

    if remove_project_state_mcp_servers(project_root):
        removed.append("MCP server: removed from ~/.claude.json project config")
    else:
        removed.append(
            "MCP server: no ~/.claude.json project-scoped registration found"
        )

    if remove_user_mcp_servers():
        removed.append("MCP server: removed from ~/.claude.json user config")
    else:
        removed.append("MCP server: no user-scoped registration found")

    if remove_legacy_mcp_servers():
        removed.append("MCP server: removed from ~/.claude/mcp.json legacy config")
    else:
        removed.append("MCP server: no legacy mcp.json registration found")

    # Remove all SmartAssist hooks from settings.json
    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        hooks = settings.get("hooks", {})
        hook_commands = {h["command"] for h in HOOK_DEFS}
        hooks_removed = 0

        for event in list(hooks.keys()):
            original = len(hooks[event])
            hooks[event] = [
                group
                for group in hooks[event]
                if not any(
                    inner.get("command", "") in hook_commands
                    for inner in group.get("hooks", [])
                )
            ]
            hooks_removed += original - len(hooks[event])
            # Remove empty event arrays
            if not hooks[event]:
                del hooks[event]

        settings["hooks"] = hooks
        from smartassist.config import atomic_write_json

        atomic_write_json(settings_path, settings)
        if hooks_removed:
            removed.append(f"Hooks: removed {hooks_removed} SmartAssist hooks")
        else:
            removed.append("Hooks: none found (already clean)")

    print("SmartAssist uninstalled from Claude Code config:")
    for line in removed:
        print(f"  {line}")
    print("\nProject data was NOT deleted.")
    print("For full removal: pipx uninstall smartassist")
    return 0


def cmd_compare_lessons():
    """Show A/B comparison of hook vs Claude lesson quality."""
    from smartassist.config import get_storage_path
    import json
    from collections import defaultdict

    try:
        storage = get_storage_path()
    except RuntimeError:
        print("Error: SmartAssist not initialized in current project.")
        return 1

    comparison_log = storage / "lesson_comparison.jsonl"
    if not comparison_log.exists():
        print(
            "No comparison data yet. Send feedback with context (>= 15 chars) to start collecting."
        )
        return 0

    entries = []
    with open(comparison_log, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        print("No comparison entries found.")
        return 0

    # Group by feedback_context
    groups = defaultdict(dict)
    for entry in entries:
        ctx = entry.get("feedback_context", "")
        source = entry.get("source", "")
        if source and ctx:
            groups[ctx][source] = entry

    pair_num = 0
    both_passed = 0
    hook_only = 0
    claude_only = 0
    both_failed = 0

    for ctx, sources in groups.items():
        hook_entry = sources.get("hook")
        claude_entry = sources.get("claude")
        if not hook_entry and not claude_entry:
            continue

        pair_num += 1
        pair_entry = hook_entry if hook_entry is not None else claude_entry
        sentiment = pair_entry.get("sentiment", "?") if pair_entry else "?"

        hook_passed = hook_entry.get("passed_gates", False) if hook_entry else False
        claude_passed = (
            claude_entry.get("passed_gates", False) if claude_entry else False
        )

        if hook_passed and claude_passed:
            both_passed += 1
        elif hook_passed and not claude_passed:
            hook_only += 1
        elif claude_passed and not hook_passed:
            claude_only += 1
        else:
            both_failed += 1

        print(f"\n\033[90m{'─' * 3} Comparison #{pair_num} {'─' * 40}\033[0m")
        print(f'  Context:   "{ctx}"')
        print(f"  Sentiment: {sentiment}")
        print()

        if hook_entry:
            hook_text = hook_entry.get("lesson_text") or "None (failed gates)"
            hook_mark = "\033[32m✓\033[0m" if hook_passed else "\033[31m✗\033[0m"
            print(f"  HOOK:   {hook_text} {hook_mark}")
        else:
            print(f"  HOOK:   \033[90m(not logged)\033[0m")

        if claude_entry:
            claude_text = claude_entry.get("lesson_text") or "None (failed gates)"
            claude_mark = "\033[32m✓\033[0m" if claude_passed else "\033[31m✗\033[0m"
            print(f"  CLAUDE: {claude_text} {claude_mark}")
        else:
            print(f"  CLAUDE: \033[90m(not logged yet)\033[0m")

    # Summary
    print(f"\n\033[90m{'─' * 3} Summary {'─' * 44}\033[0m")
    print(f"  Total pairs: {pair_num}")
    print(
        f"  Both passed: {both_passed}  |  Hook only: {hook_only}  |  Claude only: {claude_only}  |  Both failed: {both_failed}"
    )
    print()
    return 0


def cmd_version():
    """Show version information."""
    from smartassist import __version__

    print(f"smartassist {__version__}")
    return 0


def cmd_qa():
    """Run SmartAssist QA scenarios and generate demo artifacts."""
    if len(sys.argv) < 3 or sys.argv[2] in ("-h", "--help", "help"):
        print("Usage: smartassist qa <subcommand> [options]\n")
        print("Subcommands:")
        print(f"  {'list-scenarios':<18} Show available QA scenarios")
        print(f"  {'run':<18} Run deterministic QA scenarios")
        print(f"  {'clean':<18} Remove QA artifact directories")
        print(f"  {'demo':<18} Render a static HTML demo from a run directory")
        print("\nExamples:")
        print("  smartassist qa list-scenarios")
        print("  smartassist qa run")
        print("  smartassist qa run --scenario feedback_creates_active_lesson")
        print("  smartassist qa run --watch --open")
        print("  smartassist qa clean")
        print("  smartassist qa demo --run-dir qa-artifacts/qa-20260402_120000")
        return 0

    subcmd = sys.argv[2]

    if subcmd == "list-scenarios":
        from smartassist.qa.runner import list_scenarios

        for scenario in list_scenarios():
            live = "live" if scenario["live_claude"] else "deterministic"
            print(f"{scenario['name']:<40} {live:<13} {scenario['description']}")
        return 0

    if subcmd == "run":
        from smartassist.qa.runner import run_scenarios

        scenario_names: list[str] = []
        run_dir = None
        render_demo = True
        open_demo = False
        watch = False

        i = 3
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == "--scenario":
                if i + 1 >= len(sys.argv):
                    print("Error: --scenario requires a value")
                    return 1
                scenario_names.append(sys.argv[i + 1])
                i += 2
                continue
            if arg == "--run-dir":
                if i + 1 >= len(sys.argv):
                    print("Error: --run-dir requires a value")
                    return 1
                run_dir = sys.argv[i + 1]
                i += 2
                continue
            if arg == "--no-demo":
                render_demo = False
                i += 1
                continue
            if arg == "--open":
                open_demo = True
                i += 1
                continue
            if arg == "--watch":
                watch = True
                i += 1
                continue
            print(f"Unknown option: {arg}")
            return 1

        if watch:
            render_demo = True

        summary = run_scenarios(
            names=scenario_names or None,
            run_dir=run_dir,
            render_demo=render_demo,
            open_demo=open_demo,
            watch=watch,
        )
        print(f"Run directory: {summary['run_dir']}")
        print(f"Status: {summary['final_status']}")
        for scenario in summary["scenarios"]:
            status = str(scenario.get("status", "pending")).upper()
            print(f"  {status:<4} {scenario['name']}")
        return 0 if summary["final_status"] == "pass" else 1

    if subcmd == "clean":
        from smartassist.qa.runner import clean_runs

        run_dir = None
        i = 3
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == "--run-dir":
                if i + 1 >= len(sys.argv):
                    print("Error: --run-dir requires a value")
                    return 1
                run_dir = sys.argv[i + 1]
                i += 2
                continue
            print(f"Unknown option: {arg}")
            return 1

        removed = clean_runs(run_dir)
        if removed:
            for path in removed:
                print(f"Removed {path}")
        else:
            print("Nothing to clean.")
        return 0

    if subcmd == "demo":
        from smartassist.qa.demo import render_demo_site

        run_dir = None
        output = None
        open_demo = False
        auto_refresh = None

        i = 3
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg == "--run-dir":
                if i + 1 >= len(sys.argv):
                    print("Error: --run-dir requires a value")
                    return 1
                run_dir = sys.argv[i + 1]
                i += 2
                continue
            if arg == "--output":
                if i + 1 >= len(sys.argv):
                    print("Error: --output requires a value")
                    return 1
                output = sys.argv[i + 1]
                i += 2
                continue
            if arg == "--open":
                open_demo = True
                i += 1
                continue
            if arg == "--auto-refresh":
                if i + 1 >= len(sys.argv):
                    print("Error: --auto-refresh requires a value")
                    return 1
                try:
                    auto_refresh = int(sys.argv[i + 1])
                except ValueError:
                    print("Error: --auto-refresh must be an integer")
                    return 1
                i += 2
                continue
            print(f"Unknown option: {arg}")
            return 1

        if not run_dir:
            print("Error: smartassist qa demo requires --run-dir")
            return 1

        destination = render_demo_site(
            run_dir,
            output_path=output,
            open_browser=open_demo,
            auto_refresh_seconds=auto_refresh,
        )
        print(destination)
        return 0

    print(f"Unknown qa subcommand: {subcmd}")
    return 1


def cmd_telemetry():
    if len(sys.argv) < 3 or sys.argv[2] in ("-h", "--help", "help"):
        print("Usage: smartassist telemetry <subcommand> [options]\n")
        print("Subcommands:")
        print(f"  {'status':<18} Show telemetry config and queue status")
        print(f"  {'enable':<18} Enable anonymous telemetry [--endpoint URL]")
        print(f"  {'disable':<18} Disable anonymous telemetry")
        print(f"  {'export':<18} Export a sanitized telemetry bundle [--output PATH]")
        print(
            f"  {'flush':<18} POST the current bundle to the collector [--endpoint URL]"
        )
        print(
            f"  {'ingest':<18} Import a bundle into an aggregate DB [--input PATH] [--db PATH]"
        )
        print(
            f"  {'dashboard':<18} Generate aggregate KPI HTML [--db PATH] [--output PATH] [--open]"
        )
        print(
            f"  {'serve-collector':<18} Run a local telemetry collector [--db PATH] [--host HOST] [--port PORT]"
        )
        return 0

    from smartassist.telemetry import (
        DEFAULT_COLLECTOR_HOST,
        DEFAULT_COLLECTOR_PORT,
        disable_telemetry,
        enable_telemetry,
        export_bundle,
        flush_bundle,
        get_aggregate_db_path,
        get_telemetry_status,
        ingest_bundle,
        serve_collector,
    )
    from smartassist.tools.generate_telemetry_dashboard import generate_dashboard

    subcmd = sys.argv[2]

    if subcmd == "status":
        status = get_telemetry_status()
        enabled = "yes" if status["enabled"] else "no"
        install_id = status["install_id"] or "(not created yet)"
        endpoint = status["endpoint"] or "(not configured)"
        print(f"Telemetry enabled: {enabled}")
        print(f"Install ID: {install_id}")
        print(f"Endpoint: {endpoint}")
        print(f"Known projects: {status['known_projects']}")
        print(f"Queued events: {status['queued_events']}")
        print(f"Queue path: {status['queue_path']}")
        print(f"Aggregate DB: {status['aggregate_db_path']}")
        return 0

    if subcmd == "enable":
        endpoint = ""
        if "--endpoint" in sys.argv:
            idx = sys.argv.index("--endpoint")
            if idx + 1 >= len(sys.argv):
                print("Error: --endpoint requires a value")
                return 1
            endpoint = sys.argv[idx + 1]
        config = enable_telemetry(endpoint=endpoint)
        print("Anonymous telemetry enabled.")
        print(f"Install ID: {config['install_id']}")
        if config.get("endpoint"):
            print(f"Endpoint: {config['endpoint']}")
        return 0

    if subcmd == "disable":
        disable_telemetry()
        print("Anonymous telemetry disabled.")
        return 0

    if subcmd == "export":
        output = None
        if "--output" in sys.argv:
            idx = sys.argv.index("--output")
            if idx + 1 >= len(sys.argv):
                print("Error: --output requires a value")
                return 1
            output = sys.argv[idx + 1]
        destination, payload = export_bundle(output)
        print(destination)
        print(f"Events: {len(payload['lifecycle_events'])}")
        print(f"Daily rollups: {len(payload['daily_rollups'])}")
        print(f"Weekly rollups: {len(payload['weekly_rollups'])}")
        return 0

    if subcmd == "flush":
        endpoint = ""
        if "--endpoint" in sys.argv:
            idx = sys.argv.index("--endpoint")
            if idx + 1 >= len(sys.argv):
                print("Error: --endpoint requires a value")
                return 1
            endpoint = sys.argv[idx + 1]
        ok, result = flush_bundle(endpoint=endpoint)
        if not ok:
            print(result)
            return 1
        if not isinstance(result, dict):
            print("Collector returned an invalid response")
            return 1
        print(f"Flushed telemetry to {result['endpoint']}")
        print(json.dumps(result["response"], indent=2))
        return 0

    if subcmd == "ingest":
        if "--input" not in sys.argv:
            print("Error: smartassist telemetry ingest requires --input PATH")
            return 1
        input_idx = sys.argv.index("--input")
        if input_idx + 1 >= len(sys.argv):
            print("Error: --input requires a value")
            return 1
        input_path = Path(sys.argv[input_idx + 1]).expanduser().resolve()
        if not input_path.exists():
            print(f"Error: {input_path} does not exist")
            return 1
        db_path = get_aggregate_db_path()
        if "--db" in sys.argv:
            db_idx = sys.argv.index("--db")
            if db_idx + 1 >= len(sys.argv):
                print("Error: --db requires a value")
                return 1
            db_path = Path(sys.argv[db_idx + 1]).expanduser().resolve()
        payload = json.loads(input_path.read_text())
        result = ingest_bundle(db_path, payload)
        print(json.dumps(result, indent=2))
        return 0

    if subcmd == "dashboard":
        db_path = get_aggregate_db_path()
        output = None
        open_browser = "--open" in sys.argv
        if "--db" in sys.argv:
            db_idx = sys.argv.index("--db")
            if db_idx + 1 >= len(sys.argv):
                print("Error: --db requires a value")
                return 1
            db_path = Path(sys.argv[db_idx + 1]).expanduser().resolve()
        if "--output" in sys.argv:
            output_idx = sys.argv.index("--output")
            if output_idx + 1 >= len(sys.argv):
                print("Error: --output requires a value")
                return 1
            output = sys.argv[output_idx + 1]
        destination = generate_dashboard(
            db_path,
            output_path=output,
            open_browser=open_browser,
        )
        print(destination)
        return 0

    if subcmd == "serve-collector":
        db_path = get_aggregate_db_path()
        host = DEFAULT_COLLECTOR_HOST
        port = DEFAULT_COLLECTOR_PORT
        if "--db" in sys.argv:
            db_idx = sys.argv.index("--db")
            if db_idx + 1 >= len(sys.argv):
                print("Error: --db requires a value")
                return 1
            db_path = Path(sys.argv[db_idx + 1]).expanduser().resolve()
        if "--host" in sys.argv:
            host_idx = sys.argv.index("--host")
            if host_idx + 1 >= len(sys.argv):
                print("Error: --host requires a value")
                return 1
            host = sys.argv[host_idx + 1]
        if "--port" in sys.argv:
            port_idx = sys.argv.index("--port")
            if port_idx + 1 >= len(sys.argv):
                print("Error: --port requires a value")
                return 1
            try:
                port = int(sys.argv[port_idx + 1])
            except ValueError:
                print("Error: --port must be an integer")
                return 1
        return serve_collector(db_path, host=host, port=port)

    print(f"Unknown telemetry subcommand: {subcmd}")
    return 1


def cmd_setup_agent():
    """Register SmartAssist MCP server with a specific AI agent.

    Usage: smartassist setup-agent <agent>
    Agents: claude, codex, gemini, chatgpt, amp, opencode, all
    """
    if len(sys.argv) < 3:
        print("Usage: smartassist setup-agent <agent>")
        print("Agents: claude, codex, gemini, chatgpt, amp, opencode, all")
        return 1

    agent = sys.argv[2].lower()
    valid = {"claude", "codex", "gemini", "chatgpt", "amp", "opencode", "all"}
    if agent not in valid:
        print(f"Unknown agent: {agent}")
        print(f"Valid agents: {', '.join(sorted(valid))}")
        return 1

    agents = list(valid - {"all"}) if agent == "all" else [agent]

    for a in agents:
        if a == "claude":
            # Use existing setup for Claude
            print(f"[{a}] Running full Claude Code setup...")
            rc = cmd_setup()
            if rc != 0:
                return rc
        elif a == "codex":
            _setup_codex()
        elif a == "gemini":
            _setup_gemini()
        elif a == "chatgpt":
            _setup_chatgpt()
        elif a == "amp":
            _setup_amp()
        elif a == "opencode":
            _setup_opencode()
        _record_telemetry_event("agent_configured", agent_type=a)

    print("\nDone! SmartAssist is registered with: " + ", ".join(agents))
    return 0


def _setup_codex():
    """Register SmartAssist MCP with Codex."""
    import subprocess

    command, args = _resolve_mcp_server_command()
    print("[codex] Registering MCP server...")
    try:
        subprocess.run(
            [
                "codex",
                "mcp",
                "add",
                "smartassist",
                "--",
                command,
                *args,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        print("[codex] MCP server registered via 'codex mcp add'")
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
    ):
        # Fallback: write config.toml directly
        config_path = Path.home() / ".codex" / "config.toml"
        print(f"[codex] 'codex' CLI not found. Writing to {config_path}")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing = config_path.read_text() if config_path.exists() else ""
        if "smartassist" not in existing:
            with open(config_path, "a") as f:
                f.write(_render_codex_mcp_block(command, args))
            print(f"[codex] Written to {config_path}")
        else:
            print("[codex] Already registered")

    agents_path, config_path = _ensure_codex_instruction_setup()
    print(f"[codex] SmartAssist startup guidance written to: {agents_path}")
    print(f"[codex] Codex fallback filenames ensured in: {config_path}")


def _setup_gemini():
    """Print Gemini setup instructions."""
    adapters = Path(__file__).parent / "adapters" / "gemini"
    instructions_path = _ensure_project_instruction_file(
        "gemini-system-instructions.md",
        render_manual_system_instructions("Gemini"),
    )
    print("[gemini] Gemini uses HTTP function declarations, not local MCP.")
    print(f"[gemini] Import the function declarations from:")
    print(f"         {adapters / 'function-declarations.json'}")
    print(f"[gemini] Paste the SmartAssist system instructions from:")
    print(f"         {instructions_path}")
    print("[gemini] Point the HTTP endpoints at your SmartAssist server.")
    print("[gemini] Start the server: smartassist serve")


def _setup_chatgpt():
    """Print ChatGPT setup instructions."""
    adapters = Path(__file__).parent / "adapters" / "chatgpt"
    instructions_path = _ensure_project_instruction_file(
        "chatgpt-instructions.md",
        render_manual_system_instructions("ChatGPT"),
    )
    print("[chatgpt] ChatGPT uses OpenAPI custom actions, not local MCP.")
    print(f"[chatgpt] Import the OpenAPI spec from:")
    print(f"          {adapters / 'openapi.yaml'}")
    print(f"[chatgpt] Paste the SmartAssist instructions from:")
    print(f"          {instructions_path}")
    print("[chatgpt] Start the server: smartassist serve")


def _setup_amp():
    """Print Amp setup instructions."""
    skill_path = _write_text_file(
        Path.cwd().resolve() / ".agents" / "skills" / "smartassist-memory" / "SKILL.md",
        render_amp_skill(),
    )
    print(f"[amp] SmartAssist skill written to:")
    print(f"      {skill_path}")


def _setup_opencode():
    """Register SmartAssist with OpenCode."""
    import json as _json

    default_model = "anthropic/claude-sonnet-4-5"
    default_small_model = "anthropic/claude-haiku-4-5"
    command, args = _resolve_mcp_server_command()
    config_path = Path.cwd() / "opencode.json"
    instructions_path = _ensure_project_instruction_file(
        "opencode-instructions.md",
        render_opencode_instructions(),
    )
    print(f"[opencode] Writing MCP config to {config_path}")
    existing = {}
    if config_path.exists():
        try:
            existing = _json.loads(config_path.read_text())
        except Exception:
            pass
    if not isinstance(existing.get("model"), str) or not existing["model"].strip():
        existing["model"] = default_model
    if (
        not isinstance(existing.get("small_model"), str)
        or not existing["small_model"].strip()
    ):
        existing["small_model"] = default_small_model
    mcp = existing.setdefault("mcp", {})
    mcp["smartassist"] = {
        "type": "local",
        "command": [command, *args],
        "enabled": True,
    }
    instructions = existing.get("instructions", [])
    if not isinstance(instructions, list):
        instructions = []
    instruction_ref = ".smartassist/opencode-instructions.md"
    if instruction_ref not in instructions:
        instructions.append(instruction_ref)
    existing["instructions"] = instructions
    config_path.write_text(_json.dumps(existing, indent=2) + "\n")
    print("[opencode] Registered")
    print(f"[opencode] SmartAssist instructions written to {instructions_path}")


def main():
    commands = {
        "setup": cmd_setup,
        "telemetry": cmd_telemetry,
        "setup-agent": cmd_setup_agent,
        "doctor": cmd_doctor,
        "uninstall": cmd_uninstall,
        "init": cmd_init,
        "serve": cmd_serve,
        "health": cmd_health,
        "migrate": cmd_migrate,
        "vectorize": cmd_vectorize,
        "maintenance": cmd_maintenance,
        "analyze": cmd_analyze,
        "dashboard": cmd_dashboard,
        "qa": cmd_qa,
        "seed": cmd_seed,
        "compare-lessons": cmd_compare_lessons,
        "version": cmd_version,
    }

    if len(sys.argv) >= 2 and sys.argv[1] in ("-V", "--version"):
        return cmd_version()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("SmartAssist - Portable RAG learning system for Claude Code\n")
        print("Usage: smartassist <command> [options]\n")
        print("Commands:")
        print(f"  {'setup':<15} Configure Claude Code (MCP server + hooks + init)")
        print(f"  {'telemetry':<15} Manage anonymous telemetry and aggregate KPIs")
        print(
            f"  {'setup-agent':<15} Register with any agent: claude, codex, gemini, chatgpt, amp, opencode, all"
        )
        print(f"  {'doctor':<15} Audit install readiness and runtime wiring")
        print(f"  {'uninstall':<15} Remove SmartAssist from Claude Code config")
        print(f"  {'init':<15} Initialize SmartAssist in current project")
        print(f"  {'serve':<15} Start MCP server (stdio)")
        print(f"  {'health':<15} Run health checks")
        print(f"  {'migrate PATH':<15} Migrate data from old rag-setup location")
        print(f"  {'vectorize':<15} Re-vectorize curated lessons")
        print(f"  {'maintenance':<15} Run staleness + compaction checks")
        print(f"  {'analyze':<15} Show usage analytics")
        print(f"  {'dashboard':<15} Generate HTML dashboard")
        print(f"  {'qa':<15} Run QA scenarios and demo generation")
        print(
            f"  {'seed':<15} Seed from CLAUDE.md, or --deep for LLM-powered analysis (--llm claude|codex|anthropic|openai)"
        )
        print(
            f"  {'compare-lessons':<15} Show A/B comparison of hook vs Claude lessons"
        )
        print(f"  {'version':<15} Show version information")
        return 0

    cmd = sys.argv[1]
    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(f"Run 'smartassist --help' for available commands.")
        return 1

    try:
        return commands[cmd]() or 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
