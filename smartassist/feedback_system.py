"""
RLHF Feedback System - Signal Capture and Classification
Implements the "Signal Capture" component from the diagram
"""

import json
import time
from enum import Enum
from typing import Dict, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

from smartassist.config import get_storage_path


class FeedbackSignal(Enum):
    """Signal types from user feedback"""
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    CORRECTION = "correction"
    ANGRY = "angry"
    HAPPY = "happy"    # :) alias for thumbs_up behavior
    SAD = "sad"        # :( alias for thumbs_down behavior


class FeedbackCategory(Enum):
    """Categories for feedback classification"""
    CODE_EDIT = "code_edit"
    GIT = "git"
    TESTING = "testing"
    PR_REVIEW = "pr_review"
    SEARCH = "search"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    DEBUGGING = "debugging"


@dataclass
class FeedbackEvent:
    """A single feedback event"""
    signal: str  # FeedbackSignal value
    intensity: int  # 1-5 scale
    category: str  # FeedbackCategory value
    context: str  # What was happening
    query: str  # User's original query
    response: str  # Agent's response
    correction: Optional[str] = None  # User's correction if provided
    timestamp: float = None
    session_id: str = "default"

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class FeedbackCapture:
    """
    Signal Capture component - Detects and classifies user feedback
    """

    def __init__(self, storage_path: str = None):
        if storage_path is None:
            self.storage_path = get_storage_path()
        else:
            self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True)

        # Feedback log (JSONL)
        self.log_file = self.storage_path / "feedback_log.jsonl"

        # Lessons learned directory
        self.lessons_dir = self.storage_path / "lessons_learned"
        self.lessons_dir.mkdir(exist_ok=True)

    def capture_thumbs_up(
        self,
        query: str,
        response: str,
        category: FeedbackCategory,
        intensity: int = 5,
        context: str = ""
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            signal=FeedbackSignal.THUMBS_UP.value,
            intensity=intensity,
            category=category.value,
            context=context,
            query=query,
            response=response
        )
        self._store_event(event)
        return event

    def capture_thumbs_down(
        self,
        query: str,
        response: str,
        category: FeedbackCategory,
        intensity: int = 3,
        context: str = "",
        correction: Optional[str] = None
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            signal=FeedbackSignal.THUMBS_DOWN.value,
            intensity=intensity,
            category=category.value,
            context=context,
            query=query,
            response=response,
            correction=correction
        )
        self._store_event(event)
        return event

    def capture_correction(
        self,
        query: str,
        response: str,
        correction: str,
        category: FeedbackCategory,
        intensity: int = 4,
        context: str = ""
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            signal=FeedbackSignal.CORRECTION.value,
            intensity=intensity,
            category=category.value,
            context=context,
            query=query,
            response=response,
            correction=correction
        )
        self._store_event(event)
        return event

    def capture_angry(
        self,
        query: str,
        response: str,
        category: FeedbackCategory,
        intensity: int = 5,
        context: str = ""
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            signal=FeedbackSignal.ANGRY.value,
            intensity=intensity,
            category=category.value,
            context=context,
            query=query,
            response=response
        )
        self._store_event(event)
        return event

    def capture_happy(
        self,
        query: str,
        response: str,
        category: FeedbackCategory,
        intensity: int = 5,
        context: str = ""
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            signal=FeedbackSignal.HAPPY.value,
            intensity=intensity,
            category=category.value,
            context=context,
            query=query,
            response=response
        )
        self._store_event(event)
        return event

    def capture_sad(
        self,
        query: str,
        response: str,
        category: FeedbackCategory,
        intensity: int = 3,
        context: str = "",
        correction: Optional[str] = None
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            signal=FeedbackSignal.SAD.value,
            intensity=intensity,
            category=category.value,
            context=context,
            query=query,
            response=response,
            correction=correction
        )
        self._store_event(event)
        return event

    def _store_event(self, event: FeedbackEvent):
        """Store event to JSONL log and write lesson file"""
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + '\n')
        self._write_lesson_file(event)

    def _write_lesson_file(self, event: FeedbackEvent):
        """Write a markdown lesson file for corrections and negative feedback."""
        if event.signal not in (
            FeedbackSignal.THUMBS_DOWN.value,
            FeedbackSignal.CORRECTION.value,
            FeedbackSignal.ANGRY.value,
            FeedbackSignal.SAD.value,
        ):
            return

        # Generate unique filename: category + counter
        existing = list(self.lessons_dir.glob(f"{event.category}_*.md"))
        idx = len(existing) + 1
        filename = f"{event.category}_{idx:03d}.md"
        filepath = self.lessons_dir / filename

        lines = [
            f"# Lesson: {event.category}",
            "",
            f"**Signal:** {event.signal} (intensity {event.intensity}/5)",
            f"**When:** {event.query}",
            "",
            f"**Wrong:** {event.response}",
        ]
        if event.correction:
            lines.append(f"**Correct:** {event.correction}")
        if event.context:
            lines.append(f"**Context:** {event.context}")

        filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def get_recent_feedback(
        self,
        category: Optional[FeedbackCategory] = None,
        limit: int = 10
    ) -> list[FeedbackEvent]:
        """Retrieve recent feedback events"""
        if not self.log_file.exists():
            return []

        events = []
        with open(self.log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    if category is None or data['category'] == category.value:
                        events.append(FeedbackEvent(**data))

        # Return most recent first
        return sorted(events, key=lambda e: e.timestamp, reverse=True)[:limit]

    def get_stats(self) -> Dict:
        """Get feedback statistics"""
        if not self.log_file.exists():
            return {'total_events': 0}

        events = []
        with open(self.log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line))

        stats = {
            'total_events': len(events),
            'by_signal': {},
            'by_category': {},
            'avg_intensity': 0
        }

        if events:
            for event in events:
                signal = event['signal']
                stats['by_signal'][signal] = stats['by_signal'].get(signal, 0) + 1
                category = event['category']
                stats['by_category'][category] = stats['by_category'].get(category, 0) + 1
            stats['avg_intensity'] = sum(e['intensity'] for e in events) / len(events)

        return stats
