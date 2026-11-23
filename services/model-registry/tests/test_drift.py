"""The drift statistics: pure functions, pinned to ADR-0065 and the contract."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from app import drift

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = REPO / "contracts/model-registry/examples"
REFERENCE = {
    "bin_edges": [0, 10, 20, 30, 40],
    "proportions": [0.4, 0.3, 0.2, 0.1],
}
LABELS = [
    {"label_code": "CHAMPIONS", "share": 0.12},
    {"label_code": "LOYAL", "share": 0.31},
    {"label_code": "AT_RISK", "share": 0.33},
    {"label_code": "LOST", "share": 0.24},
]


def spread(n: int) -> list[float | None]:
    """n values spread over the reference's bins in the reference's shares."""
    values: list[float | None] = []
    for index, share in enumerate(REFERENCE["proportions"]):
        values += [REFERENCE["bin_edges"][index] + 5.0] * round(share * n)
    return values


def segments(n: int, shares: list[float]) -> list[str]:
    out: list[str] = []
    for label, share in zip(LABELS, shares, strict=True):
        out += [label["label_code"]] * round(share * n)
    return out


def test_gamma_matches_closed_forms() -> None:
    assert drift.gammaincc(1, 1) == pytest.approx(math.exp(-1), rel=1e-12)
    # Q(1/2, x) = erfc(sqrt(x)); Q(3/2, x) = erfc(sqrt(x)) + 2 sqrt(x/pi) exp(-x)
    for x in (0.2, 1.0, 3.0, 12.0):
        assert drift.gammaincc(0.5, x) == pytest.approx(
            math.erfc(math.sqrt(x)), rel=1e-9
        )
        assert drift.gammaincc(1.5, x) == pytest.approx(
            math.erfc(math.sqrt(x)) + 2 * math.sqrt(x / math.pi) * math.exp(-x),
            rel=1e-9,
        )


def test_the_contract_example_chi_square_p_value_is_reproduced() -> None:
    report = json.loads((EXAMPLES / "drift-report.json").read_text())
    stat = report["variables"][0]["statistics"][1]
    p_value = drift.gammaincc(stat["degrees_of_freedom"] / 2, stat["value"] / 2)
    assert p_value == pytest.approx(stat["p_value"], rel=1e-2)


def test_the_contract_example_psi_is_reproduced_from_its_bins() -> None:
    report = json.loads((EXAMPLES / "drift-report.json").read_text())
    for variable in report["variables"]:
        bins = [
            drift.Bin(
                b["label"],
                b["lower"],
                b["upper"],
                b["reference_share"],
                b["current_share"],
                0,
            )
            for b in variable["bins"]
        ]
        assert drift.psi(bins) == pytest.approx(
            variable["statistics"][0]["value"], abs=3e-3
        )


def test_psi_of_a_fixed_distribution_is_zero_and_of_a_shifted_one_is_not() -> None:
    fixed = drift.feature_bins(REFERENCE, spread(1000))
    assert drift.psi(fixed) == pytest.approx(0, abs=1e-9)
    shifted = drift.feature_bins(REFERENCE, [35.0] * 800 + [5.0] * 200)
    assert drift.psi(shifted) > drift.PSI_ALARM


def test_an_empty_bin_is_floored_before_the_logarithm() -> None:
    bins = drift.feature_bins(REFERENCE, [5.0] * 100)
    empty = bins[1]
    assert empty.current_share == 0
    assert empty.contribution == pytest.approx((0.0005 - 0.3) * math.log(0.0005 / 0.3))
    assert math.isfinite(drift.psi(bins))


def test_a_value_past_either_edge_goes_to_the_edge_bin_and_none_to_its_own_bin() -> (
    None
):
    bins = drift.feature_bins(REFERENCE, [-5.0, 99.0, None])
    assert [b.count for b in bins] == [1, 0, 0, 1, 1]
    assert bins[-1].label == drift.NO_VALUE


def test_chi_square_of_matching_counts_is_zero_with_p_one() -> None:
    bins = drift.segment_bins(LABELS, segments(1000, [0.12, 0.31, 0.33, 0.24]))
    value, p_value, dof = drift.chi_square(bins, 1000)
    assert value == pytest.approx(0, abs=1e-9)
    assert p_value == pytest.approx(1.0)
    assert dof == 3


def test_segment_shares_alarm_needs_both_statistics() -> None:
    shifted = [0.04, 0.15, 0.39, 0.42]
    big = drift.judge(
        drift.SEGMENT_SHARES,
        drift.segment_bins(LABELS, segments(1000, shifted)),
        1000,
        None,
    )
    assert [s.level for s in big.statistics] == ["ALARM", "ALARM"]
    assert big.state == "ALARM_PENDING"
    # The same shares on twenty customers: PSI still alarms, the test cannot.
    small = drift.judge(
        drift.SEGMENT_SHARES,
        drift.segment_bins(LABELS, segments(20, shifted)),
        20,
        None,
    )
    assert small.statistics[0].level == "ALARM"
    assert small.statistics[1].level == "NORMAL"
    assert small.state == "WATCH"


def test_persistence_needs_two_windows_and_a_continuing_alarm_alerts_each_time() -> (
    None
):
    shifted = drift.feature_bins(REFERENCE, [35.0] * 800 + [5.0] * 200)
    first = drift.judge("R", shifted, 1000, None)
    second = drift.judge("R", shifted, 1000, first.state)
    third = drift.judge("R", shifted, 1000, second.state)
    assert [first.state, second.state, third.state] == [
        "ALARM_PENDING",
        "ALARM",
        "ALARM",
    ]
    # The contract (listDriftAlerts): an alarm that continues raises a further
    # alert in each report whose variable is still at ALARM.
    assert not drift.raises_alert(first.state)
    assert drift.raises_alert(second.state)
    assert drift.raises_alert(third.state)


def test_a_recovered_window_resets_the_persistence() -> None:
    fixed = drift.feature_bins(REFERENCE, spread(1000))
    assert drift.judge("R", fixed, 1000, "ALARM_PENDING").state == "NORMAL"


def test_watch_is_a_state_and_never_an_alert() -> None:
    values = [5.0] * 270 + [15.0] * 290 + [25.0] * 240 + [35.0] * 200
    watch = drift.judge("R", drift.feature_bins(REFERENCE, values), 1000, None)
    assert watch.statistics[0].level == "WATCH"
    assert watch.state == "WATCH"
    assert not drift.raises_alert(watch.state)


def test_the_same_inputs_give_the_same_report() -> None:
    values = [35.0] * 700 + [5.0] * 300
    one = drift.judge("R", drift.feature_bins(REFERENCE, values), 1000, "ALARM_PENDING")
    two = drift.judge(
        "R",
        drift.feature_bins(REFERENCE, list(reversed(values))),
        1000,
        "ALARM_PENDING",
    )
    assert one == two


def test_no_sentence_the_monitor_can_say_names_the_outcome_claim() -> None:
    bins = drift.feature_bins(REFERENCE, [35.0] * 800 + [5.0] * 200)
    sentences = [drift.review_text("R", bins)]
    sentences.append(
        drift.review_text(
            drift.SEGMENT_SHARES,
            drift.segment_bins(LABELS, segments(1000, [0.04, 0.15, 0.39, 0.42])),
        )
    )
    for sentence in sentences:
        assert "concept drift" not in sentence.lower()
        assert "distribution change" in sentence
        assert len(sentence) <= 500
    for source in (REPO / "services/model-registry/app").glob("*.py"):
        for number, line in enumerate(source.read_text().splitlines(), 1):
            if re.search(r"concept[ _-]?drift", line, re.I):
                # The one place the claim is made is the outcome test's result
                # (ADR-0065, ADR-0089); everywhere else it is only disowned.
                allowed = (source.name == "drift.py" and "never" in line) or (
                    source.name == "outcome.py"
                    and line.strip() == 'CLAIM = "CONCEPT_DRIFT"'
                )
                assert allowed, (source, number)
    assert drift.review_code("R") == "REVIEW_FEATURE_DISTRIBUTION"
    assert drift.review_code(drift.SEGMENT_SHARES) == "REVIEW_SEGMENT_MIX"


def test_a_label_the_reference_never_saw_is_kept_in_a_bin_of_its_own() -> None:
    assigned = segments(1000, [0.04, 0.15, 0.39, 0.42]) + ["NEWCOMERS"] * 250
    bins = drift.segment_bins(LABELS, assigned)
    assert [b.label for b in bins][-1] == "NEWCOMERS"
    assert bins[-1].reference_share == 0.0 and bins[-1].count == 250
    assert sum(b.count for b in bins) == len(assigned)
    judged = drift.judge(drift.SEGMENT_SHARES, bins, len(assigned), None)
    assert judged.state == "ALARM_PENDING"


def test_a_reference_of_one_segment_records_psi_and_cannot_alarm() -> None:
    bins = drift.segment_bins([{"label_code": "LOYAL", "share": 1.0}], ["LOYAL"] * 300)
    judged = drift.judge(drift.SEGMENT_SHARES, bins, 300, "ALARM_PENDING")
    assert [s.statistic for s in judged.statistics] == ["PSI"]
    assert judged.state == "NORMAL"


def test_the_review_text_keeps_its_closing_sentence_however_long_the_labels() -> None:
    long_labels = [
        {"label_code": f"{i}_" + "A_VERY_LONG_SEGMENT_LABEL" * 3, "share": 0.25}
        for i in range(4)
    ]
    assigned = [long_labels[0]["label_code"]] * 900 + [
        long_labels[1]["label_code"]
    ] * 100
    text = drift.review_text(
        drift.SEGMENT_SHARES, drift.segment_bins(long_labels, assigned)
    )
    assert len(text) <= drift.REVIEW_TEXT_MAX
    assert text.endswith("stopped predicting anything.")
