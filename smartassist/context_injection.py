"""
Context Injection - Inject lessons learned into system prompt
Queries Thompson model for weak categories and retrieves negative feedback
"""

from typing import List, Dict
from pathlib import Path
from smartassist.feedback_system import FeedbackCapture, FeedbackCategory, FeedbackSignal
from smartassist.thompson_sampling import ThompsonSamplingModel


class ContextInjection:
    """
    Context Injection component - Retrieves and formats lessons for system prompt
    """

    def __init__(self, storage_path: str = None):
        self.feedback = FeedbackCapture(storage_path)
        self.thompson = ThompsonSamplingModel(storage_path)

    def get_context_for_prompt(
        self,
        query: str,
        weak_threshold: float = 0.70,
        max_lessons: int = 5
    ) -> Dict[str, any]:
        """
        Generate context to inject into system prompt
        """
        weak_categories = self.thompson.get_weak_categories(weak_threshold)

        if not weak_categories:
            return {
                'weak_categories': [],
                'relevant_lessons': [],
                'formatted_context': '',
                'should_inject': False
            }

        lessons = self._retrieve_lessons(weak_categories, max_lessons)
        formatted_context = self._format_context(weak_categories, lessons)

        return {
            'weak_categories': weak_categories,
            'relevant_lessons': lessons,
            'formatted_context': formatted_context,
            'should_inject': True,
            'reliability_scores': self.thompson.get_all_reliabilities()
        }

    def _retrieve_lessons(
        self,
        weak_categories: List[str],
        max_lessons: int = 5
    ) -> List[Dict]:
        """Retrieve lessons from feedback log and vector store"""
        lessons = []

        for category in weak_categories[:3]:
            try:
                cat_enum = FeedbackCategory(category)
                recent_feedback = self.feedback.get_recent_feedback(
                    category=cat_enum,
                    limit=3
                )

                for event in recent_feedback:
                    if event.signal in [
                        FeedbackSignal.THUMBS_DOWN.value,
                        FeedbackSignal.ANGRY.value,
                        FeedbackSignal.CORRECTION.value
                    ]:
                        lessons.append({
                            'category': event.category,
                            'signal': event.signal,
                            'query': event.query,
                            'response': event.response[:200] + '...',
                            'correction': event.correction,
                            'context': event.context,
                            'intensity': event.intensity
                        })

                        if len(lessons) >= max_lessons:
                            break

                if len(lessons) >= max_lessons:
                    break

            except ValueError:
                continue

        return lessons

    def _format_context(
        self,
        weak_categories: List[str],
        lessons: List[Dict]
    ) -> str:
        """Format lessons into context string for system prompt"""
        if not weak_categories and not lessons:
            return ""

        context = []
        context.append("=" * 60)
        context.append("LESSONS LEARNED FROM PAST MISTAKES")
        context.append("=" * 60)

        if weak_categories:
            context.append(f"\nWeak Performance Areas (Success Rate <70%):")
            for cat in weak_categories:
                reliability = self.thompson.get_reliability(cat)
                context.append(f"  - {cat}: {reliability:.1%} reliability")

        if lessons:
            context.append(f"\nRecent Failures to Avoid:\n")
            for i, lesson in enumerate(lessons, 1):
                context.append(f"[Lesson {i}] Category: {lesson['category']}")
                context.append(f"  Signal: {lesson['signal']} (Intensity: {lesson['intensity']})")
                context.append(f"  User Query: \"{lesson['query']}\"")
                context.append(f"  Wrong Response: \"{lesson['response']}\"")

                if lesson['correction']:
                    context.append(f"  Correct Approach: \"{lesson['correction']}\"")

                if lesson['context']:
                    context.append(f"  Context: {lesson['context']}")

                context.append("")

        context.append("=" * 60)
        context.append("Apply these lessons to avoid repeating mistakes.\n")

        return "\n".join(context)

    def inject_into_prompt(
        self,
        base_prompt: str,
        query: str,
        weak_threshold: float = 0.70
    ) -> str:
        """Inject lessons into system prompt"""
        context_data = self.get_context_for_prompt(query, weak_threshold)

        if not context_data['should_inject']:
            return base_prompt

        adapted_prompt = f"""{context_data['formatted_context']}

{base_prompt}"""

        return adapted_prompt

    def get_stats(self) -> Dict:
        """Get context injection statistics"""
        return {
            'thompson_stats': self.thompson.get_stats(),
            'feedback_stats': self.feedback.get_stats(),
            'weak_categories': self.thompson.get_weak_categories(),
            'total_lessons': len(self.feedback.get_recent_feedback(limit=1000))
        }
