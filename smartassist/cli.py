#!/usr/bin/env python3
"""
SmartAssist CLI - Portable RAG learning system for Claude Code.

Usage:
    smartassist setup         Configure Claude Code (MCP server + hooks + init)
    smartassist uninstall     Remove SmartAssist from Claude Code config
    smartassist init          Initialize SmartAssist in current project
    smartassist serve         Start MCP server (stdio)
    smartassist health        Run health checks
    smartassist migrate PATH  Migrate data from old rag-setup location
    smartassist vectorize     Re-vectorize curated lessons
    smartassist maintenance   Run staleness + compaction checks
    smartassist analyze       Show usage analytics
    smartassist dashboard     Generate HTML dashboard
    smartassist seed          Seed database from CLAUDE.md conventions
    smartassist version       Show version information
"""

import os
import sys
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


def cmd_init():
    """Initialize SmartAssist in the current project."""
    cwd = Path.cwd()
    data_dir = cwd / ".claude" / "smartassist"
    storage = data_dir / "data"
    lancedb = data_dir / "lancedb"

    if data_dir.exists():
        print(f"SmartAssist already initialized at {data_dir}")
        print("  Re-run with --force to reinitialize")
        if "--force" not in sys.argv:
            return 0

    print(f"Initializing SmartAssist in {cwd}...")

    storage.mkdir(parents=True, exist_ok=True)
    lancedb.mkdir(parents=True, exist_ok=True)

    # Create empty data files
    feedback_log = storage / "feedback_log.jsonl"
    if not feedback_log.exists():
        feedback_log.write_text("")

    reliability = storage / "reliability_scores.json"
    if not reliability.exists():
        reliability.write_text("{}")

    vectorization_log = storage / "vectorization_log.json"
    if not vectorization_log.exists():
        vectorization_log.write_text(
            json.dumps(
                {
                    "total_vectorized": 0,
                    "last_vectorization": None,
                }
            )
        )

    # Update .gitignore
    gitignore = cwd / ".gitignore"
    smartassist_entry = ".claude/smartassist/"
    if gitignore.exists():
        content = gitignore.read_text()
        if smartassist_entry not in content:
            with open(gitignore, "a") as f:
                f.write(f"\n# SmartAssist data\n{smartassist_entry}\n")
            print(f"  Updated .gitignore")
    else:
        gitignore.write_text(f"# SmartAssist data\n{smartassist_entry}\n")
        print(f"  Created .gitignore")

    print(f"\n  Created: {data_dir}/")
    print(f"    data/       - feedback logs, scores, curated lessons")
    print(f"    lancedb/    - vector database")
    print(f"\nSmartAssist initialized successfully!")
    print(f"\nNext steps:")
    print(f"  1. Add lessons: smartassist seed")
    print(f"  2. Vectorize:   smartassist vectorize")
    print(f"  3. Check:       smartassist health")
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

    return main()


def cmd_seed():
    """Seed database from CLAUDE.md conventions."""
    from smartassist.hooks.seed_from_claudemd import seed_database

    seed_database()
    return 0


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
        for line in lines:
            stripped = line.strip()
            if any(stripped.startswith(p) for p in all_patterns):
                removed.append((rc_name, stripped))
            else:
                cleaned.append(line)

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
        "matcher": "Bash",
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


def _backup_file(path, summary):
    """Create a timestamped backup of a file if it exists."""
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
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

    def log(msg):
        summary.append(msg)
        log_lines.append(f"[{datetime.now().isoformat()}] {msg}")

    # ── Phase 1: Pre-flight checks ──────────────────────────────────────
    print("Phase 1: Pre-flight checks...")

    # Python >= 3.10
    if sys.version_info < (3, 10):
        print(f"Error: Python >= 3.10 required (found {sys.version})")
        return 1
    log(f"Python: {sys.version_info.major}.{sys.version_info.minor} OK")

    # smartassist in PATH
    if not shutil.which("smartassist"):
        print("Error: 'smartassist' command not found in PATH")
        print("  Install with: pipx install .")
        return 1
    log("smartassist: found in PATH")

    # ~/.claude/ exists
    claude_dir = Path.home() / ".claude"
    if not claude_dir.exists():
        print("Error: ~/.claude/ directory not found")
        print("  Install Claude Code first: https://claude.ai/code")
        return 1
    log("~/.claude/: exists")

    # ── Phase 2: Backup config files ────────────────────────────────────
    print("Phase 2: Backing up config files...")

    mcp_path = claude_dir / "mcp.json"
    settings_path = claude_dir / "settings.json"

    _backup_file(mcp_path, summary)
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

    # 3b. MCP server
    data_dir = str(Path.cwd() / ".claude" / "smartassist")
    smartassist_path = shutil.which("smartassist")
    if smartassist_path:
        real_path = Path(smartassist_path).resolve()
        venv_python = str(real_path.parent / "python")
        if not Path(venv_python).exists():
            venv_python = smartassist_path
            mcp_args = ["serve"]
        else:
            mcp_args = ["-m", "smartassist.mcp_server"]
    else:
        venv_python = "smartassist"
        mcp_args = ["serve"]

    claude_bin = shutil.which("claude")
    if claude_bin:
        subprocess.run(
            [claude_bin, "mcp", "remove", "smartassist", "-s", "user"],
            capture_output=True,
        )
        env_args = [
            "-e",
            f"SMARTASSIST_DATA_DIR={data_dir}",
            "-e",
            f"HOME={Path.home()}",
            "-e",
            "PATH=/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:"
            + str(Path.home() / ".local" / "bin"),
            "-e",
            "TMPDIR=/tmp",
        ]
        add_cmd = [
            claude_bin,
            "mcp",
            "add",
            "smartassist",
            "-s",
            "user",
            *env_args,
            "--",
            venv_python,
            *mcp_args,
        ]
        result = subprocess.run(add_cmd, capture_output=True, text=True)
        if result.returncode == 0:
            log("MCP server: registered via 'claude mcp add -s user'")
        else:
            log(
                f"MCP server: 'claude mcp add' failed ({result.stderr.strip()}), "
                "falling back to mcp.json"
            )
            claude_bin = None

    if not claude_bin:
        if mcp_path.exists():
            mcp_config = json.loads(mcp_path.read_text())
        else:
            mcp_config = {}
        mcp_config.setdefault("mcpServers", {})
        mcp_config["mcpServers"]["smartassist"] = {
            "command": venv_python,
            "args": mcp_args,
            "env": {
                "SMARTASSIST_DATA_DIR": data_dir,
                "HOME": str(Path.home()),
                "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:"
                + str(Path.home() / ".local" / "bin"),
                "TMPDIR": "/tmp",
            },
        }
        mcp_path.write_text(json.dumps(mcp_config, indent=2) + "\n")
        log("MCP server: added to ~/.claude/mcp.json (fallback)")

    # 3c. Hooks — remove-then-add strategy via _configure_hooks()
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    _configure_hooks(settings, summary)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")

    # 3d. Ensure ~/.local/bin in PATH
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

    # 3e. Verify all 7 CLI commands accessible
    required_commands = ["smartassist", "claude-sa"] + SMARTASSIST_COMMANDS
    missing = [cmd for cmd in required_commands if not shutil.which(cmd)]
    if missing:
        log(f"CLI commands: WARNING — not found in PATH: {', '.join(missing)}")
        log(f"  Fix: run 'pipx ensurepath' and restart your terminal")
    else:
        log(f"CLI commands: all {len(required_commands)} commands found")

    # ── Phase 4: Initialize project data ────────────────────────────────
    print("Phase 4: Initializing project data...")
    cmd_init()

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
    return 0


def cmd_uninstall():
    """Remove SmartAssist from Claude Code config (MCP server + hooks).

    Project data is NOT deleted. Run `pipx uninstall smartassist` for full removal.
    """
    claude_dir = Path.home() / ".claude"
    removed = []

    # Remove smartassist from mcp.json
    mcp_path = claude_dir / "mcp.json"
    if mcp_path.exists():
        mcp_config = json.loads(mcp_path.read_text())
        if "smartassist" in mcp_config.get("mcpServers", {}):
            del mcp_config["mcpServers"]["smartassist"]
            mcp_path.write_text(json.dumps(mcp_config, indent=2) + "\n")
            removed.append("MCP server: removed from mcp.json")
        else:
            removed.append("MCP server: not found in mcp.json (already clean)")

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
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
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
        sentiment = (hook_entry or claude_entry).get("sentiment", "?")

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


def main():
    commands = {
        "setup": cmd_setup,
        "uninstall": cmd_uninstall,
        "init": cmd_init,
        "serve": cmd_serve,
        "health": cmd_health,
        "migrate": cmd_migrate,
        "vectorize": cmd_vectorize,
        "maintenance": cmd_maintenance,
        "analyze": cmd_analyze,
        "dashboard": cmd_dashboard,
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
        print(f"  {'uninstall':<15} Remove SmartAssist from Claude Code config")
        print(f"  {'init':<15} Initialize SmartAssist in current project")
        print(f"  {'serve':<15} Start MCP server (stdio)")
        print(f"  {'health':<15} Run health checks")
        print(f"  {'migrate PATH':<15} Migrate data from old rag-setup location")
        print(f"  {'vectorize':<15} Re-vectorize curated lessons")
        print(f"  {'maintenance':<15} Run staleness + compaction checks")
        print(f"  {'analyze':<15} Show usage analytics")
        print(f"  {'dashboard':<15} Generate HTML dashboard")
        print(f"  {'seed':<15} Seed database from CLAUDE.md conventions")
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
