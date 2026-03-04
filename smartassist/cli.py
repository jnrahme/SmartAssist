#!/usr/bin/env python3
"""
SmartAssist CLI - Portable RAG learning system for Claude Code.

Usage:
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

import sys
import json
import shutil
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
        vectorization_log.write_text(json.dumps({
            "total_vectorized": 0,
            "last_vectorization": None,
        }))

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


def cmd_version():
    """Show version information."""
    from smartassist import __version__
    print(f"smartassist {__version__}")
    return 0


def main():
    commands = {
        "init": cmd_init,
        "serve": cmd_serve,
        "health": cmd_health,
        "migrate": cmd_migrate,
        "vectorize": cmd_vectorize,
        "maintenance": cmd_maintenance,
        "analyze": cmd_analyze,
        "dashboard": cmd_dashboard,
        "seed": cmd_seed,
        "version": cmd_version,
    }

    if len(sys.argv) >= 2 and sys.argv[1] in ("-V", "--version"):
        return cmd_version()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("SmartAssist - Portable RAG learning system for Claude Code\n")
        print("Usage: smartassist <command> [options]\n")
        print("Commands:")
        print(f"  {'init':<15} Initialize SmartAssist in current project")
        print(f"  {'serve':<15} Start MCP server (stdio)")
        print(f"  {'health':<15} Run health checks")
        print(f"  {'migrate PATH':<15} Migrate data from old rag-setup location")
        print(f"  {'vectorize':<15} Re-vectorize curated lessons")
        print(f"  {'maintenance':<15} Run staleness + compaction checks")
        print(f"  {'analyze':<15} Show usage analytics")
        print(f"  {'dashboard':<15} Generate HTML dashboard")
        print(f"  {'seed':<15} Seed database from CLAUDE.md conventions")
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
