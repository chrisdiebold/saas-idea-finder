from ideafinder.db.models import EvalItem, EvalResult
from ideafinder.pipeline.evaluation import score


def pair(predicted: bool, gold: bool, p_kinds=(), g_kinds=(), seconds=2.0):
    return (
        EvalResult(
            run="r",
            model="m",
            predicted_signal=predicted,
            predicted_kinds=list(p_kinds),
            seconds=seconds,
            output_tokens=100,
        ),
        EvalItem(has_signal=gold, kinds=list(g_kinds)),
    )


def test_precision_recall_and_kind_agreement():
    rows = [
        pair(True, True, ["pain"], ["pain", "request"]),  # TP, kinds overlap
        pair(True, True, ["payment"], ["request"]),  # TP, kinds differ
        pair(True, False),  # FP
        pair(False, True),  # FN
        pair(False, False),  # TN
    ]
    s = score(rows)
    assert s.items == 5
    assert round(s.precision, 3) == round(2 / 3, 3)
    assert s.recall == 2 / 3
    assert s.kind_agreement == 0.5
    assert s.median_seconds == 2.0 and s.output_tokens_per_second == 50


def test_no_rows_no_score():
    assert score([]) is None
