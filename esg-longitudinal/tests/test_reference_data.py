import json
import pathlib

from conftest import _load

snapshot = _load("snapshot")
ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED_ENABLERS = {"ecodesign", "rnd", "data_infrastructure", "measurement",
                     "traceability", "procurement", "training", "partnerships",
                     "reverse_logistics", "finance", "policy"}
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


def test_circular_indicators_have_valid_r_hint():
    import re
    text = (ROOT / "references" / "indicators.yaml").read_text(encoding="utf-8")
    circular = text.split("circular:", 1)[1].split("\nbiodiversity:", 1)[0]
    hints = re.findall(r"r_hint:\s*([R0-9|]+)", circular)
    assert len(hints) >= 8  # one per circular indicator
    for h in hints:
        for tok in h.split("|"):
            assert re.fullmatch(r"R[0-9]", tok), f"bad r_hint token {tok}"


def test_skill_documents_new_columns():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    v1_cols = ["item_type", "r_strategy", "enabler_topic",
               "target_end_year", "target_has_kpi", "target_status"]
    v2_cols = ["smart_specific", "smart_achievable", "smart_relevant", "substance",
               "planetary_alignment", "impact_scope", "priority_internal",
               "importance_external", "linked_targets", "assessment_notes"]
    for col in v1_cols + v2_cols:
        assert col in text, f"SKILL.md missing {col}"
    for section in ["Classification layer", "Target anatomy",
                    "Year-over-year target status", "Target quality (SMART+)"]:
        assert section in text, f"SKILL.md missing section: {section}"


def test_evals_reference_19_column_schema():
    data = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "13-column" not in blob and "13 column" not in blob
    # the full 19-column header appears verbatim in at least one assertion
    header = ("entity,lei,domain,indicator,value,unit,period,status,source,"
              "source_url,page,quote,retrieved_at,item_type,r_strategy,"
              "enabler_topic,target_end_year,target_has_kpi,target_status")
    assert header in blob
    # a classification assertion exists
    assert "r_strategy" in blob and "target_status" in blob
    ids = [e["id"] for e in data["evals"]]
    assert 4 in ids  # new longitudinal target-status eval
