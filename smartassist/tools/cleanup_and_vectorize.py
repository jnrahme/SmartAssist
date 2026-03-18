#!/usr/bin/env python3
"""
Vectorize the RAG Knowledge Base from curated lessons.

Loads hand-crafted best practice lessons from curated_lessons.json,
generates embeddings, and rebuilds the LanceDB vector database.

Usage:
    smartassist vectorize [--dry-run]
"""

import json
import sys
import hashlib
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

from smartassist.config import get_storage_path, get_db_path, EMBEDDING_MODEL

# Patterns that indicate a non-actionable lesson
SKIP_PATTERNS = [
    r"^this has been addressed",
    r"^done\b",
    r"^fixed\b",
    r"^resolved\b",
    r"^acknowledged\b",
    r"^nit:",
    r"^lgtm",
    r"^good catch",
    r"^thanks",
    r"^ok\b",
    r"^yes\b",
    r"^already fixed",
    r"^already removed",
    r"^already addressed",
    r"^already resolved",
    r"^from stage\b",
    r"^same here\b",
    r"^good call",
    r"^fair point",
    r"^nice catch",
    r"^great catch",
    r"^you'?re right",
    r"^correct[,.]",
    r"^true[,.]",
    r"^agreed",
    r"^not related to my",
    r"^not related to this",
    r"^interesting\b",
    r"^curious\b",
    r"^we already have",
    r"^i already",
    r"^we already",
    r"^i've already",
    r"^we've already",
    r"^this will be implemented",
    r"^this was not done by",
    r"^it was already",
    r"^this was already",
    r"^apply always",
    r"^tested fine",
    r"^not a big issue",
    r"^good point",
    r"^sounds good",
    r"^makes sense",
    r"^fair enough",
    r"^understood",
    r"^fyi\b",
    r"^btw\b",
    r"^imo\b",
    r"^imho\b",
    r"^tbh\b",
    r"^afaik\b",
    r"^i'll\b",
    r"^i will\b",
    r"^will do\b",
    r"^will fix\b",
    r"^will update\b",
    r"^will address\b",
    r"^will check\b",
    r"^good to\b",
    r"^great to\b",
    r"^closing this",
    r"^reopening this",
    r"^merging this",
    r"^rebasing",
    r"^correction on",
]

# Minimum correction length to be useful
MIN_CORRECTION_LEN = 60


def load_events(path) -> List[Dict]:
    """Load all events from JSONL file."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def normalize_text(text: str) -> str:
    """Normalize text for deduplication comparison."""
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s]', '', text)
    return text


def is_skip_pattern(text: str) -> bool:
    """Check if text matches a non-actionable pattern."""
    lower = text.lower().strip()
    for pattern in SKIP_PATTERNS:
        if re.match(pattern, lower):
            return True
    return False


def is_pure_question(text: str) -> bool:
    """Detect questions that don't contain actionable advice."""
    stripped = text.rstrip()
    if not stripped.endswith("?"):
        return False
    if len(stripped) > 150:
        return False
    lower = stripped.lower()
    actionable_hints = [
        "should we", "shouldn't", "can we", "can't we",
        "don't we", "why not", "why don't", "let's",
        "instead of", "rather than", "better to",
    ]
    return not any(hint in lower for hint in actionable_hints)


def is_code_fragment(text: str) -> bool:
    """Detect entries that are just code blocks with no explanation."""
    stripped = text.strip()
    return stripped.startswith("```") and len(stripped) < 120


def is_at_mention_noise(text: str) -> bool:
    """Detect short @mention-heavy entries."""
    if not re.search(r"@[\w-]+", text):
        return False
    return len(text) < 80


def is_screenshot_or_image(text: str) -> bool:
    """Detect entries that are primarily screenshots or images."""
    stripped = text.strip()
    no_imgs = re.sub(r'<img[^>]*/?>', '', stripped)
    no_imgs = re.sub(r'!\[.*?\]\(.*?\)', '', no_imgs)
    return len(no_imgs.strip()) < 50


CONVERSATIONAL_STARTS = [
    "mmm", "hmm", "haha", "lol", "huh",
]


def is_conversational_noise(text: str) -> bool:
    """Detect entries that start with conversational filler."""
    lower = text.lower().strip()
    return any(lower.startswith(s) for s in CONVERSATIONAL_STARTS)


DEFENSIVE_STARTS = [
    "this is how it was", "this is expected", "this is intentional",
    "this is an old", "this is not a pattern", "this is not ",
    "this is a deliberate", "this is a mock", "this is technically",
    "this is to keep", "this is by design", "this is the expected",
    "this isn't", "this is a ",
    "that is because", "that's because", "that is not",
    "that was ",
    "it's not in scope", "it's not true", "it's not a trap",
    "it's pretty obvious", "it's much better than",
    "it is conversation", "it is really", "it is actually",
    "it is used in ", "it is not necessary",
    "the reason is", "it updates the local", "those are the names",
    "in this case we want", "this was the", "this matches the",
    "this was my only", "this was a ",
    "module-level state is intentional",
    "there is no need", "there is no existing", "there is never",
    "there is a default behavior",
    "there is commented",
]


def is_defensive_explanation(text: str) -> bool:
    """Detect entries where author defends existing code (not a lesson)."""
    lower = text.lower().strip()
    return any(lower.startswith(d) for d in DEFENSIVE_STARTS)


def is_personal_coordination(text: str) -> bool:
    """Detect short @person coordination entries with no lesson."""
    if not re.search(r'@[\w-]+', text):
        return False
    cleaned = re.sub(r'@[\w-]+\s*', '', text).strip()
    return len(cleaned) < 80


def is_acknowledgment(text: str) -> bool:
    """Detect entries that are just acknowledgments, not lessons."""
    lower = text.lower().strip()
    ack_phrases = [
        "thank you for review", "thanks for the review",
        "looks like i addressed", "addressed all",
        "i triggered a new build", "triggered a build",
        "will be removed here", "created a ticket",
        "it would create conflict", "i will skip on this",
        "no issues found", "code review\n\nno issues",
    ]
    return any(p in lower for p in ack_phrases)


def is_why_question(text: str) -> bool:
    """Detect 'Why X?' style entries."""
    lower = text.lower().strip()
    return lower.startswith("why")


def is_narrative_or_status(text: str) -> bool:
    """Detect personal narratives, status updates, and past-tense reports."""
    lower = text.lower().strip()
    narrative_starts = [
        "i removed ", "i added ", "i changed ", "i was ",
        "i have ", "i saw ", "i guess ", "i triggered ",
        "i didn't ", "i reverted ", "i have a broader",
        "we are ", "we were ", "we have this",
        "for now,", "for now ",
        "changed from ", "changed.",
        "same as ", "same issue", "same fix", "same pattern",
        "same comment", "same.",
    ]
    return any(lower.startswith(s) for s in narrative_starts)


def is_observation_not_lesson(text: str) -> bool:
    """Detect observations and hedged statements that aren't actionable lessons."""
    lower = text.lower().strip()
    observation_starts = [
        "looks like ", "not sure ", "the issue ",
        "the problem ", "currently,", "currently ",
        "regarding ", "as far as ",
    ]
    return any(lower.startswith(s) for s in observation_starts)


def is_scope_discussion(text: str) -> bool:
    """Detect entries about ticket scope, not code lessons."""
    lower = text.lower().strip()
    scope_phrases = [
        "out of scope", "separate ticket", "another ticket",
        "not in scope", "different ticket", "follow-up ticket",
        "create a ticket", "created a ticket",
    ]
    return any(p in lower for p in scope_phrases)


def is_negative_opinion(text: str) -> bool:
    """Detect negative I-opinions that can't be converted to imperative form."""
    lower = text.lower().strip()
    negative_starts = [
        "i don't think", "i don't see", "i don't like",
        "i don't mind", "i don't know", "i'm not sure",
        "i am not sure", "i don't ",
    ]
    return any(lower.startswith(s) for s in negative_starts)


# Tier 1: ALWAYS filter
ALWAYS_FILTER_STARTERS = [
    r"^i['\u2019]?[dms]?\s",
    r"^is\s+(there|this|that|it|the|get|thi)\b",
    r"^do\s+we\b",
    r"^are\s+(we|you|this|that|these|those|the)\b",
    r"^does\s",
    r"^did\s",
    r"^was\s+(this|that|it|the)\b",
    r"^were\s",
    r"^has\s",
    r"^have\s+we\b",
    r"^wouldn['\u2019]?t\s",
    r"^won['\u2019]?t\s",
    r"^can['\u2019]?t\s+we\b",
    r"^isn['\u2019]?t\s+(it|this|that)\b",
    r"^not\s+true\b",
]

# Tier 2: Filter UNLESS strong prescriptive content is present
CONDITIONAL_FILTER_STARTERS = [
    r"^this\s",
    r"^that\s",
    r"^those\s",
    r"^these\s",
    r"^it['\u2019]?s?\s",
    r"^we['\u2019]?\s",
    r"^some\s+of\b",
    r"^most\s+of\b",
    r"^all\s+of\b",
    r"^none\s+of\b",
    r"^many\s+of\b",
    r"^both\s",
    r"^either\s",
]

PRESCRIPTIVE_MARKERS = [
    "should", "shouldn't", "must ",
    "better to", "instead of", "rather than",
    "more consistent", "more generic", "more explicit",
    "type safer", "type safe",
    "consider ", "prefer ", "avoid ", "don't use",
    "let's ", "let us ",
    "not necessary", "not required", "not needed",
    "not really necessary", "no need to",
    "doesn't check", "doesn't validate", "doesn't test",
    "anti-pattern", "anti pattern", "violation",
    "please remove", "please fix", "please use",
    "please add", "please move", "please check",
    "worth to", "worth memoiz", "worth extract",
    "worth creating", "worth having",
    "can be simplified", "can be type", "can be moved",
    "can be updated", "can be replaced",
    "move to a ", "moved to a ", "extract to",
    "simplif",
]


def is_non_imperative_comment(text: str) -> bool:
    """Catch-all filter for non-imperative discussion comments."""
    lower = text.lower().strip()

    if any(re.match(p, lower) for p in ALWAYS_FILTER_STARTERS):
        return True

    if text.rstrip().endswith("?"):
        return True

    if any(re.match(p, lower) for p in CONDITIONAL_FILTER_STARTERS):
        if any(m in lower for m in PRESCRIPTIVE_MARKERS):
            return False
        return True

    return False


def convert_question_to_lesson(text: str) -> str:
    """Convert 'can we use colors from the theme?' to imperative form."""
    stripped = text.rstrip().rstrip("?").strip()
    lower = stripped.lower()

    for prefix in ["can we ", "can't we ", "why don't we ", "why not "]:
        if lower.startswith(prefix):
            return stripped[len(prefix):].capitalize()
    if lower.startswith("don't we "):
        return stripped[len("don't we "):].capitalize()
    if lower.startswith("shouldn't "):
        return stripped[len("shouldn't "):].capitalize()
    if lower.startswith("let's "):
        return stripped[len("let's "):].capitalize()
    return stripped


def sanitize_to_lesson(text: str) -> str:
    """Transform a raw PR review comment into an imperative lesson."""
    result = text.strip()

    result = re.sub(r'<img[^>]*>', '', result)
    result = re.sub(r'!\[.*?\]\(.*?\)', '', result)
    result = re.sub(r'@[\w-]+\s+', '', result)
    result = re.sub(r'https?://github\.com/\S+', '', result)
    result = re.sub(r'https?://[\w.-]*atlassian\.net/\S+', '', result)
    result = re.sub(r'https?://jenkins\.\S+', '', result)
    result = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', result)

    conversational_prefixes = [
        r"^i think we (?:should |can |need to )",
        r"^i think ",
        r"^i believe ",
        r"^i would ",
        r"^i'd ",
        r"^i feel (?:like )?",
        r"^i assume (?:that )?",
        r"^we should (?:probably )?",
        r"^we could (?:probably )?",
        r"^we need to ",
        r"^we can ",
        r"^you should ",
        r"^you could ",
        r"^you need to ",
        r"^please ",
        r"^pls ",
        r"^it seems (?:like |that )?",
        r"^it looks like ",
        r"^it would be (?:better |cleaner |nicer )(?:to |if )",
        r"^it's (?:better |cleaner |nicer )(?:to |if )",
        r"^there is (?:a |an )?",
        r"^there's (?:a |an )?",
        r"^so,?\s+",
        r"^but,?\s+",
        r"^and,?\s+",
        r"^also,?\s+",
        r"^just\s+",
        r"^maybe\s+",
        r"^perhaps\s+",
        r"^probably\s+",
        r"^yeah,?\s*(?:but\s+)?",
        r"^yes,?\s*(?:but\s+)?",
        r"^no,?\s+",
        r"^well,?\s+",
        r"^ok so\s+",
        r"^ok,?\s+",
        r"^i noticed (?:that )?",
        r"^i see (?:that )?",
        r"^i saw (?:that |somewhere )?",
        r"^note:?\s*",
        r"^\[nitpick\]\s*",
        r"^nit:?\s*",
    ]

    changed = True
    while changed:
        changed = False
        lower = result.lower()
        for pattern in conversational_prefixes:
            m = re.match(pattern, lower)
            if m:
                result = result[m.end():]
                changed = True
                break

    result = result.strip()
    if result and result[0].islower():
        result = result[0].upper() + result[1:]

    result = re.sub(
        r'[\U0001F600-\U0001F9FF\U0001F300-\U0001F5FF\U0001FA00-\U0001FAFF'
        r'\U00002702-\U000027B0\U0001F1E0-\U0001F1FF\U0000FE0F'
        r'\U0000200D\U00002600-\U000026FF\U00002700-\U000027BF]+',
        '', result
    )

    result = re.sub(r'\n{3,}', '\n\n', result)
    result = result.strip()

    return result


def get_dedup_key(correction: str) -> str:
    """Generate a dedup key from the first 200 chars of normalized correction."""
    norm = normalize_text(correction[:200])
    return hashlib.md5(norm.encode()).hexdigest()


def clean_correction_text(event: Dict) -> Optional[str]:
    """Extract the best lesson text from an event."""
    correction = event.get("correction", "")
    if not correction:
        return None

    correction = correction.strip()

    if is_skip_pattern(correction):
        return None
    if len(correction) < MIN_CORRECTION_LEN:
        return None
    if is_code_fragment(correction):
        return None
    if is_at_mention_noise(correction):
        return None
    if is_pure_question(correction):
        return None
    if is_screenshot_or_image(correction):
        return None
    if is_conversational_noise(correction):
        return None
    if is_defensive_explanation(correction):
        return None
    if is_personal_coordination(correction):
        return None
    if is_acknowledgment(correction):
        return None
    if is_why_question(correction):
        return None
    if is_narrative_or_status(correction):
        return None
    if is_observation_not_lesson(correction):
        return None
    if is_scope_discussion(correction):
        return None
    if is_negative_opinion(correction):
        return None

    if correction.rstrip().endswith("?"):
        correction = convert_question_to_lesson(correction)

    correction = sanitize_to_lesson(correction)

    if len(correction) < MIN_CORRECTION_LEN:
        return None

    if is_non_imperative_comment(correction):
        return None

    return correction


def format_text_for_vector(category: str, lesson: str) -> str:
    """Create a dense text blob for vectorization."""
    return f"[{category}] {lesson}"


def load_curated_lessons(path) -> List[Dict]:
    """Load hand-crafted lessons from curated_lessons.json."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lessons = json.load(f)
        return lessons if isinstance(lessons, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: could not load curated lessons from {path}: {exc}")
        return []


def _update_vectorization_log(storage_path, total_in_db: int):
    """Mark the curated rebuild as the latest vectorization state."""
    feedback_log = storage_path / "feedback_log.jsonl"
    total_feedback = 0
    if feedback_log.exists():
        total_feedback = sum(1 for line in feedback_log.read_text().splitlines() if line.strip())

    from smartassist.config import atomic_write_json

    vectorization_log = storage_path / "vectorization_log.json"
    atomic_write_json(vectorization_log, {
        "total_vectorized": total_feedback,
        "last_vectorization": datetime.now().isoformat(),
        "total_documents_in_rag": total_in_db,
    })


def main():
    dry_run = "--dry-run" in sys.argv

    storage_path = get_storage_path()
    db_path = get_db_path()
    curated_path = storage_path / "curated_lessons.json"

    print("=" * 60)
    print("RAG Knowledge Base - Curated Lessons Vectorizer")
    print("=" * 60)

    # Step 1: Load curated lessons
    lessons = load_curated_lessons(curated_path)
    print(f"\nLoaded {len(lessons)} curated lessons from {curated_path.name}")

    # Category breakdown
    by_cat: Dict[str, int] = {}
    for entry in lessons:
        cat = entry["category"]
        by_cat[cat] = by_cat.get(cat, 0) + 1
    print(f"\nBy category:")
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat:15s}: {count}")

    # Step 2: Build text blobs
    texts = []
    for entry in lessons:
        text = format_text_for_vector(entry["category"], entry["lesson"])
        texts.append(text)

    # Show samples
    print(f"\nSample lessons:")
    for t in texts[:5]:
        print(f"  {t}")
    print()

    if dry_run:
        print("[DRY RUN] No changes made. Run without --dry-run to apply.")
        return

    # Step 3: Generate embeddings
    print("Loading embedding model...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBEDDING_MODEL)

    print(f"Generating embeddings for {len(texts)} lessons...")
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=64)

    # Step 4: Build LanceDB records
    now = time.time()
    data = []
    for i, (entry, emb) in enumerate(zip(lessons, embeddings)):
        data.append({
            "id": i + 1,
            "text": texts[i],
            "vector": emb.tolist(),
            "category": entry["category"],
            "timestamp": now,
        })

    # Step 5: Rebuild the table
    print(f"\nRebuilding LanceDB at {db_path}...")
    import lancedb
    db = lancedb.connect(str(db_path))
    table = db.create_table("documents", data=data, mode="overwrite")
    print(f"  Created table with {table.count_rows()} documents")

    # Create full-text search index for hybrid search
    try:
        table.create_fts_index("text", replace=True)
        print(f"  Created FTS index on 'text' column")
    except Exception as e:
        print(f"  Warning: Could not create FTS index: {e}")

    # Step 6: Verify
    print(f"\nVerification:")
    print(f"  Table rows: {table.count_rows()}")
    assert table.count_rows() == len(lessons), (
        f"Row count mismatch: {table.count_rows()} != {len(lessons)}"
    )
    _update_vectorization_log(storage_path, table.count_rows())

    # Test searches
    test_queries = [
        "how to style components with theme colors",
        "jest mock setup best practices",
        "commit message format",
    ]
    for query in test_queries:
        vec = model.encode(query)
        results = table.search(vec).limit(3).to_list()
        print(f"\n  Search: \"{query}\"")
        for r in results:
            dist = r.get("_distance", 0)
            text = r.get("text", "")[:120]
            print(f"    dist={dist:.3f} -> {text}")

    print(f"\nDone! LanceDB rebuilt with {len(data)} curated lessons.")


if __name__ == "__main__":
    main()
