#!/usr/bin/env python3
"""
LLM fallback pass: reclassify rows where Persona='Unknown' by calling Ollama.

Only touches rows where rule-based engine returned empty (now stamped 'Unknown')
but Title / Headline / Summary exist. Maps LLM persona output back to the
canonical list, then re-runs downstream fields (Score, Need, Opportunity,
Outreach, Next Action, Seniority) — leaving non-empty existing values alone.

Usage:
    python llm_unknown.py \
        --input  v19c.csv \
        --output v20.csv \
        --ollama-host http://localhost:11434 \
        --ollama-model kimi-k2.6:cloud \
        --limit 1300 \
        --save-every 25
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import os
import re
import sys
import tempfile
import time
from typing import Dict, Optional


def _atomic_write_csv(path, fieldnames, rows, extrasaction="ignore"):
    """Write CSV atomically: stream to a temp file in the same dir, then
    os.replace() onto `path`. A crash mid-write truncates the temp (not
    `path`), so the prior checkpoint survives and --resume sees a complete
    CSV instead of a partial write."""
    out_dir = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".csv", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as out:
            w = csv.DictWriter(out, fieldnames=fieldnames, extrasaction=extrasaction)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "cpc", os.path.join(HERE, "climatepoint_classifier.py")
)
cpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cpc)


CANONICAL = [
    "Sustainability Buyer",
    "Product / R&D Buyer",
    "Operations / Supply Chain Buyer",
    "Founder / Executive Sponsor",
    "Investor / Fund Persona",
    "Marketing / Commercial",
    "Technical / Analyst",
    "Partner / Channel",
    "Low-fit / Other",
]


def normalize_persona(raw: str) -> Optional[str]:
    if not raw:
        return None
    txt = raw.strip()
    # Strip leading numbers / bullets
    txt = re.sub(r"^\s*\d+[\.\):]\s*", "", txt)
    # Take first line if multi-line
    txt = txt.splitlines()[0] if txt else ""
    low = txt.lower()
    # Direct match
    for p in CANONICAL:
        if p.lower() in low:
            return p
    # Loose aliases
    aliases = {
        "sustainability": "Sustainability Buyer",
        "esg": "Sustainability Buyer",
        "product": "Product / R&D Buyer",
        "r&d": "Product / R&D Buyer",
        "research": "Product / R&D Buyer",
        "supply chain": "Operations / Supply Chain Buyer",
        "operations": "Operations / Supply Chain Buyer",
        "procurement": "Operations / Supply Chain Buyer",
        "founder": "Founder / Executive Sponsor",
        "ceo": "Founder / Executive Sponsor",
        "executive": "Founder / Executive Sponsor",
        "investor": "Investor / Fund Persona",
        "fund": "Investor / Fund Persona",
        "venture": "Investor / Fund Persona",
        "vc": "Investor / Fund Persona",
        "marketing": "Marketing / Commercial",
        "commercial": "Marketing / Commercial",
        "sales": "Marketing / Commercial",
        "analyst": "Technical / Analyst",
        "technical": "Technical / Analyst",
        "partner": "Partner / Channel",
        "channel": "Partner / Channel",
        "consultant": "Partner / Channel",
        "low-fit": "Low-fit / Other",
        "low fit": "Low-fit / Other",
        "other": "Low-fit / Other",
    }
    for k, v in aliases.items():
        if k in low:
            return v
    return None


def reclassify_row(row: Dict[str, str], ollama: cpc.OllamaClient) -> bool:
    title = (row.get("Title") or "").strip()
    headline = (row.get("Headline") or "").strip()
    summary = (row.get("Summary") or "").strip()
    company = (row.get("Company") or "").strip()
    domain = (row.get("Domain") or "").strip()

    if not (title or headline or summary):
        return False

    try:
        raw = ollama.classify_persona(title, company, summary, headline)
    except Exception as e:
        print(f"  [llm error] {e}", file=sys.stderr)
        return False

    persona = normalize_persona(raw)
    if not persona:
        return False

    row["Persona"] = persona

    seniority = cpc.detect_seniority(title)
    score = cpc.score_lead(persona, seniority, title, company, summary, domain)
    need = cpc.classify_need(persona, title, company, summary)
    opportunity = cpc.map_opportunity(persona, need)
    angle = cpc.build_outreach_angle(persona, company, need, ollama=None, title=title, summary=summary)
    next_action = cpc.determine_next_action(score, persona)

    row["Lead Score (1-10)"] = str(score)
    row["Need State"] = need
    row["Opportunity Type"] = opportunity
    row["Outreach Angle"] = angle
    row["Next Action"] = next_action
    if not (row.get("Seniority") or "").strip():
        row["Seniority"] = seniority

    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--ollama-host", default="http://localhost:11434")
    ap.add_argument("--ollama-model", default="kimi-k2.6:cloud")
    ap.add_argument("--ollama-api-key", default=os.getenv("OLLAMA_API_KEY"))
    ap.add_argument("--limit", type=int, default=10**9)
    ap.add_argument("--save-every", type=int, default=25)
    ap.add_argument("--sleep", type=float, default=0.0)
    args = ap.parse_args()

    ollama = cpc.OllamaClient(args.ollama_host, args.ollama_model, args.ollama_api_key)

    # Patch _chat to disable thinking-mode (kimi-k2.6:cloud emits answer only via
    # thinking field when think=True, leaving content empty). think=False forces
    # direct answer in content.
    def _chat_no_think(prompt: str, temperature: float = 0.2, num_predict: int = 80) -> str:
        if ollama._client is None:
            raise RuntimeError("Ollama client not initialized")
        resp = ollama._client.chat(
            model=ollama.model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": temperature, "num_predict": num_predict},
            think=False,
        )
        return (resp.message.content or "").strip()
    ollama._chat = _chat_no_think

    with open(args.input, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    def save():
        _atomic_write_csv(args.output, fieldnames, rows)
        print(f"  [saved] {args.output}", flush=True)

    targets = [i for i, r in enumerate(rows)
               if (r.get("Persona") or "").strip() == "Unknown"]
    print(f"Unknown rows: {len(targets)}", flush=True)

    processed = 0
    reclassified = 0
    for n, idx in enumerate(targets, start=1):
        if processed >= args.limit:
            break
        row = rows[idx]
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        title = (row.get("Title") or "").strip()[:80]
        print(f"[{n}/{len(targets)}] row {idx}: {first} {last} | {title}", flush=True)
        ok = reclassify_row(row, ollama)
        if ok:
            reclassified += 1
            print(f"  -> {row['Persona']} | score {row.get('Lead Score (1-10)')}", flush=True)
        else:
            print(f"  -> kept Unknown", flush=True)
        processed += 1
        if processed % args.save_every == 0:
            save()
        if args.sleep > 0:
            time.sleep(args.sleep)

    save()
    print(f"\nDone. processed={processed} reclassified={reclassified}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
