import csv
import subprocess
import sys
import pathlib

from conftest import _load

diff = _load("diff")
snapshot = _load("snapshot")

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


V1_COLS_19 = ["entity", "lei", "domain", "indicator", "value", "unit", "period", "status",
              "source", "source_url", "page", "quote", "retrieved_at",
              "item_type", "r_strategy", "enabler_topic",
              "target_end_year", "target_has_kpi", "target_status"]


def _write(path, rows, cols=None):
    cols = cols or snapshot.COLS
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _t(**over):
    row = {"entity": "Royal Philips", "indicator": "circular_revenue_pct",
           "period": "2022", "status": "target", "value": "25",
           "target_end_year": "2025", "target_status": "on_track"}
    row.update(over)
    return row


def test_target_rows_picks_latest_period():
    snap = {("Royal Philips", "circular_revenue_pct", "2021"): _t(period="2021", value="20"),
            ("Royal Philips", "circular_revenue_pct", "2022"): _t(period="2022", value="25")}
    tr = diff.target_rows(snap)
    assert tr[("Royal Philips", "circular_revenue_pct")]["value"] == "25"


def test_changed_and_dropped_targets_render(tmp_path):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    # old: a 25%/2025 target + a takeback target that will be dropped
    _write(old, [_t(value="25", target_end_year="2025"),
                 _t(indicator="product_takeback_scope", value="all", target_end_year="2024")])
    # new: 25% target moved to 2030; takeback target gone; a brand-new target added
    _write(new, [_t(value="25", target_end_year="2030"),
                 _t(indicator="recycled_input_pct", value="50", target_end_year="2028")])
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "diff.py"), "--old", str(old), "--new", str(new)],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    body = out.stdout
    assert "Target movements" in body
    assert "Changed targets" in body and "2025" in body and "2030" in body
    assert "Dropped targets" in body and "product_takeback_scope" in body
    assert "New targets" in body and "recycled_input_pct" in body


def test_target_vs_actual_pairs(tmp_path):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    # target row keyed by its end year (period=2025) so it never collides with the
    # actual (period=2022) under the (entity, indicator, period) snapshot key.
    _write(old, [_t(value="25", target_end_year="2025", period="2025")])
    _write(new, [_t(value="25", target_end_year="2025", period="2025"),
                 {"entity": "Royal Philips", "indicator": "circular_revenue_pct",
                  "period": "2022", "status": "found", "value": "18",
                  "source_url": "http://x", "quote": "18%"}])
    out = subprocess.run(
        [sys.executable, str(SCRIPTS / "diff.py"), "--old", str(old), "--new", str(new)],
        capture_output=True, text=True)
    assert "Target vs latest actual" in out.stdout
    assert "18" in out.stdout and "25" in out.stdout


def _run_diff(old, new):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "diff.py"), "--old", str(old), "--new", str(new)],
        capture_output=True, text=True)


def test_quality_reassessed_all_four_fields(tmp_path):
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025", substance="symbolic", planetary_alignment="insufficient",
                    priority_internal="low", importance_external="low", assessment_notes="n")])
    _write(new, [_t(period="2025", substance="substantive", planetary_alignment="pb_aligned",
                    priority_internal="high", importance_external="high", assessment_notes="n")])
    out = _run_diff(old, new)
    assert out.returncode == 0, out.stderr
    b = out.stdout
    assert "Quality reassessed" in b
    for f in ("substance", "planetary_alignment", "priority_internal", "importance_external"):
        assert f in b


def test_quality_only_change_still_renders(tmp_path):
    # value + end year identical; only a materiality field changes; no found actual
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025", substance="symbolic", assessment_notes="n")])
    _write(new, [_t(period="2025", substance="substantive", assessment_notes="n")])
    out = _run_diff(old, new)
    assert out.returncode == 0, out.stderr
    b = out.stdout
    assert "Target movements" in b
    assert "Quality reassessed" in b
    assert "substance: symbolic -> substantive" in b


def test_newly_assessed_not_reassessed(tmp_path):
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025")])  # no materiality fields set
    _write(new, [_t(period="2025", substance="substantive", assessment_notes="n")])
    out = _run_diff(old, new)
    assert out.returncode == 0, out.stderr
    b = out.stdout
    assert "Newly assessed" in b
    assert "Quality reassessed" not in b  # blank->value is NOT a reassessment


def test_no_churn_against_19col_baseline(tmp_path):
    # old physically omits v2 columns -> DictReader yields absent keys (None on .get)
    old, new = tmp_path / "old.csv", tmp_path / "new.csv"
    _write(old, [_t(period="2025")], cols=V1_COLS_19)
    _write(new, [_t(period="2025", substance="substantive", assessment_notes="n")])
    out = _run_diff(old, new)
    assert out.returncode == 0, out.stderr  # regression drop of `or ""` would CRASH -> catch it
    b = out.stdout
    assert "Quality reassessed" not in b  # first-time assessment, not a reassessment
    assert "Newly assessed" in b          # positive proof the absent-key path ran (not empty stdout)
