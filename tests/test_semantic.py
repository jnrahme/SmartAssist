"""Tests for the opt-in semantic retrieval service and its hook integration."""

import json
import threading
import time

import pytest

from smartassist.hooks import prompt_inject
from smartassist.semantic import client, server, socket_path


class FakeEmbedder:
    """Cosine is 1.0 for lessons sharing the query's first word, else 0.2."""

    def similarity(self, query, lessons):
        head = query.split()[0].lower()
        return {i: (1.0 if head in t.lower() else 0.2) for i, t in lessons}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTASSIST_RUNTIME_DIR", str(tmp_path / "rt"))
    return tmp_path / "rt"


@pytest.fixture
def running_server(runtime):
    path = socket_path()
    thread = threading.Thread(target=server.serve, args=(FakeEmbedder(), path, 2), daemon=True)
    thread.start()
    for _ in range(100):
        if path.exists():
            break
        time.sleep(0.02)
    yield path
    thread.join(timeout=5)


def test_socket_path_is_short_enough_for_af_unix(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTASSIST_RUNTIME_DIR", str(tmp_path / ("x" * 120)))

    assert len(str(socket_path())) <= 104


@pytest.mark.parametrize(
    "env,settings,expected",
    [("1", None, True), ("", {"semantic_retrieval": True}, True), ("", {"semantic_retrieval": False}, False), ("", None, False)],
)
def test_is_enabled(tmp_path, monkeypatch, env, settings, expected):
    monkeypatch.setenv("SMARTASSIST_SEMANTIC", env)
    if settings is not None:
        (tmp_path / "settings.json").write_text(json.dumps(settings))

    assert client.is_enabled(tmp_path) is expected


def test_unavailable_service_returns_none_and_starts_it(runtime, monkeypatch):
    started = []
    monkeypatch.setattr(client, "ensure_running", lambda: started.append(True))

    assert client.similarity_scores("query", [{"id": "L1", "lesson": "x"}]) is None
    assert started == [True]


def test_round_trip_through_real_socket(running_server):
    scores = client.similarity_scores(
        "stash changes", [{"id": "L1", "lesson": "never stash"}, {"id": "L2", "lesson": "use pnpm"}]
    )

    assert scores == {"L1": 1.0, "L2": 0.2}


def test_server_reports_unknown_op(running_server):
    assert client.request({"op": "nope"}) is None


def test_embedding_cache_round_trips_without_pickle(tmp_path):
    cache = server.EmbeddingCache(tmp_path / "cache.json")
    cache.add(["a lesson"], [[0.1, 0.2]])

    reloaded = server.EmbeddingCache(tmp_path / "cache.json")

    assert reloaded.get("a lesson") == [0.1, 0.2]
    assert reloaded.missing(["a lesson", "new"]) == ["new"]


class TestHybridRanking:
    LESSONS = [
        {"id": "L1", "lesson": "Never stash uncommitted changes when switching branches", "category": "git", "origin": "create_lesson"},
        {"id": "L2", "lesson": "Use pnpm for every install", "category": "git", "origin": "create_lesson"},
        {"id": "L3", "lesson": "Never stash, seeded copy", "category": "git", "origin": "seed"},
    ]

    def _rank(self, monkeypatch, cosines, scores=None):
        monkeypatch.setattr(client, "is_enabled", lambda _p: True)
        monkeypatch.setattr(client, "similarity_scores", lambda q, l: cosines)
        tokens = prompt_inject.tokenize("never stash my changes")
        idf = prompt_inject.build_idf(self.LESSONS)
        return prompt_inject.semantic_search("never stash my changes", tokens, self.LESSONS, idf, scores or {}, None)

    def test_below_threshold_and_down_weighted_seed_copy_are_dropped(self, monkeypatch):
        ranked = self._rank(monkeypatch, {"L1": 0.8, "L2": 0.3, "L3": 0.8})

        assert [r["id"] for r in ranked] == ["L1"]

    def test_candidates_far_below_best_match_are_dropped(self, monkeypatch):
        ranked = self._rank(monkeypatch, {"L1": 0.9, "L2": 0.62, "L3": 0.0})

        assert [r["id"] for r in ranked] == ["L1"]

    def test_large_stored_boost_cannot_outrank_relevance(self, monkeypatch):
        ranked = self._rank(
            monkeypatch, {"L1": 0.85, "L2": 0.66, "L3": 0.0}, scores={"L2": {"boost": 3.0}}
        )

        assert ranked[0]["id"] == "L1"

    def test_disabled_or_unavailable_returns_none(self, monkeypatch):
        assert self._rank(monkeypatch, None) is None


@pytest.mark.parametrize("boost,expected", [(1.0, 1.0), (3.0, 1.2), (0.0, 0.9), (2.0, 1.1)])
def test_boost_factor_is_bounded(boost, expected):
    assert prompt_inject.boost_factor(boost) == pytest.approx(expected)


def test_queries_get_the_retrieval_instruction_but_lessons_do_not(tmp_path):
    from smartassist.semantic import QUERY_INSTRUCTION

    seen = []

    class RecordingModel:
        def encode(self, texts, normalize_embeddings):
            seen.extend(texts)

            class Vectors(list):
                def tolist(self):
                    return list(self)

            return Vectors([[1.0, 0.0] for _ in texts])

    embedder = server.Embedder.__new__(server.Embedder)
    embedder.model = RecordingModel()
    embedder.cache = server.EmbeddingCache(tmp_path / "cache.json")

    embedder.similarity("stash changes", [("L1", "never stash")])

    assert seen == ["never stash", QUERY_INSTRUCTION + "stash changes"]
