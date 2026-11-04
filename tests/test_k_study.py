"""The F23-03 study's tables, over a small dataset (#815)."""

from analysis import k_study as study
from analysis.dataset import generate
from analysis.normalization_study import Features, n_minmax


def test_the_study_reports_every_k_with_its_labels(monkeypatch):
    monkeypatch.setattr(study, "K_RANGE", range(2, 5))
    monkeypatch.setattr(study, "SEEDS", (1, 2))
    f = Features(generate(customers=300))
    rows = study.study(f, n_minmax(f))
    assert [r["k"] for r in rows] == [2, 3, 4]
    assert all(r["silhouette"] is not None and r["sample"] is None for r in rows)
    assert "CHAMPION, LOST" in study.label_table(rows)
    assert "| 4 |" in study.k_table(rows)
