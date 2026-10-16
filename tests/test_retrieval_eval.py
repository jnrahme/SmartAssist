"""Tests for the retrieval benchmark scoring."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "retrieval_eval", Path(__file__).parent.parent / "benchmarks" / "retrieval_eval.py"
)
retrieval_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retrieval_eval)


@pytest.mark.parametrize(
    "case,context,recall,noise,forbidden",
    [
        ({"expect": ["L1", "L2"]}, "- [L1] [git] a\n- [L3] [git] b", 0.5, False, []),
        ({"expect": []}, "- [L1] [git] a", None, True, []),
        ({"expect": []}, "", None, False, []),
        ({"expect": ["L1"], "forbid": ["L9"]}, "[L1] x [L9] y", 1.0, False, ["L9"]),
    ],
)
def test_score_case(case, context, recall, noise, forbidden):
    scored = retrieval_eval.score_case(case, context)

    assert scored["recall"] == recall
    assert scored["noise_injection"] is noise
    assert scored["forbidden_hits"] == forbidden
