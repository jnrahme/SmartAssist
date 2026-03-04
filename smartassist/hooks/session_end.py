#!/usr/bin/env python3
"""
Session End Hook - Captures learnings when ending session.
Called by Claude Code during session end.
"""

import sys
import json
import subprocess
from datetime import datetime

from smartassist.config import get_storage_path


def capture_session_learning():
    """Capture what was learned during this session"""
    try:
        storage_path = get_storage_path()

        # Load Thompson scores directly (avoid heavy imports)
        from smartassist.thompson_sampling import ThompsonSamplingModel
        thompson = ThompsonSamplingModel(str(storage_path))

        scores = thompson.get_all_reliabilities()
        weak = thompson.get_weak_categories(threshold=0.70)

        print("\n" + "=" * 60)
        print("SESSION LEARNING SUMMARY")
        print("=" * 60)

        print("\nCurrent Reliability by Category:")
        if scores:
            for cat, score in scores.items():
                status = "WEAK" if score < 0.70 else "OK"
                print(f"  {status} {cat}: {score:.1%}")
        else:
            print("  No data yet")

        if weak:
            print(f"\nWeak areas to focus on: {', '.join(weak)}")

        # Read feedback stats
        feedback_log = storage_path / "feedback_log.jsonl"
        total = 0
        if feedback_log.exists():
            with open(feedback_log) as f:
                total = sum(1 for line in f if line.strip())
        print(f"\nTotal feedback events: {total}")

        print("\n" + "=" * 60)
        print("Give feedback during sessions to improve performance!")
        print("   Say: 'thumbs up for X' or 'thumbs down for Y'")
        print("=" * 60 + "\n")

        # Save session metadata
        session_data = {
            'timestamp': datetime.now().isoformat(),
            'scores': scores,
            'weak_categories': weak
        }

        session_log = storage_path / "session_log.jsonl"
        with open(session_log, 'a') as f:
            f.write(json.dumps(session_data) + '\n')

        # Trigger vectorization of new learnings
        print("\nUpdating RAG database with new learnings...")
        try:
            subprocess.run(
                [sys.executable, "-m", "smartassist.hooks.vectorize_learnings"],
                capture_output=True, timeout=30
            )
        except Exception as ve:
            print(f"Vectorization skipped: {ve}")

    except Exception as e:
        print(f"# RLHF Note: Could not capture session learning ({str(e)})")


def main():
    capture_session_learning()


if __name__ == "__main__":
    main()
    sys.exit(0)
