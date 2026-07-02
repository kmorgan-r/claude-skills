import csv
import subprocess
import sys
import pathlib

from conftest import _load

diff = _load("diff")
snapshot = _load("snapshot")

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=snapshot.COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in snapshot.COLS})


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
