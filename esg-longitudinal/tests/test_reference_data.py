import json
import pathlib

from conftest import _load

snapshot = _load("snapshot")
ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED_ENABLERS = {"ecodesign", "rnd", "data_infrastructure", "training",
                     "partnerships", "reverse_logistics", "finance", "policy"}
VALID_R = {f"R{i}" for i in range(10)}


def test_enablers_present_and_well_formed():
    data = json.loads((ROOT / "circular-economy-10rs.json").read_text(encoding="utf-8"))
    enablers = {e["id"] for e in data["enablers"]}
    assert enablers == EXPECTED_ENABLERS
    for e in data["enablers"]:
        assert e["name"] and e["description"]
        assert e["supports"] and all(r in VALID_R for r in e["supports"])


def test_enabler_ids_match_snapshot_validator():
    data = json.loads((ROOT / "circular-economy-10rs.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in data["enablers"]} == snapshot.VALID_ENABLER
