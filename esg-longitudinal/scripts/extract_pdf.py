#!/usr/bin/env python3
"""Extract text (and optionally tables) from a PDF using PyMuPDF / pdfplumber.

Writes the full text to <pdf>.txt. With --grep, prints only the pages whose text
matches any term, with page numbers — use that to jump straight to the figures and
keep the agent's context small instead of reading the whole report.

Usage:
    python extract_pdf.py --pdf data/raw/philips_2022.pdf
    python extract_pdf.py --pdf philips_2022.pdf --grep "circular,take-back,scope 3" --context 3
    python extract_pdf.py --pdf philips_2022.pdf --tables --pages 38-60
"""
import argparse
import os
import sys


def load_text_pages(path):
    import fitz
    doc = fitz.open(path)
    return [doc.load_page(i).get_text() for i in range(doc.page_count)]


def parse_pages(spec, n):
    """1-indexed, inclusive ranges -> sorted 0-indexed page list."""
    if not spec:
        return list(range(n))
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a) - 1, int(b)))
        elif part:
            out.add(int(part) - 1)
    return sorted(p for p in out if 0 <= p < n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--grep", default="", help="comma-separated terms (case-insensitive)")
    ap.add_argument("--context", type=int, default=2, help="lines of context around a match")
    ap.add_argument("--tables", action="store_true", help="also dump tables via pdfplumber")
    ap.add_argument("--pages", default="", help="limit to pages, e.g. 38-60 (1-indexed)")
    args = ap.parse_args()

    pages = load_text_pages(args.pdf)
    sel = parse_pages(args.pages, len(pages))

    txt_path = os.path.splitext(args.pdf)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        for i in sel:
            f.write(f"\n===== PAGE {i + 1} =====\n{pages[i]}")
    print(f"text written: {txt_path} ({len(sel)} pages)")

    if args.grep:
        terms = [t.strip().lower() for t in args.grep.split(",") if t.strip()]
        print(f"\n--- grep {terms} ---")
        for i in sel:
            lines = pages[i].splitlines()
            for j, ln in enumerate(lines):
                if any(t in ln.lower() for t in terms):
                    lo = max(0, j - args.context)
                    hi = min(len(lines), j + args.context + 1)
                    snippet = " / ".join(s.strip() for s in lines[lo:hi] if s.strip())
                    print(f"[p{i + 1}] {snippet[:240]}")

    if args.tables:
        try:
            import pdfplumber
        except ImportError:
            print("pdfplumber not installed; skipping --tables", file=sys.stderr)
            return
        print("\n--- tables ---")
        with pdfplumber.open(args.pdf) as pdf:
            for i in sel:
                for t in (pdf.pages[i].extract_tables() or []):
                    ncols = len(t[0]) if t else 0
                    print(f"[p{i + 1}] table {len(t)}x{ncols}")
                    for row in t[:30]:
                        cells = [(c or "").replace("\n", " ").strip() for c in row]
                        print("   ", " | ".join(cells))


if __name__ == "__main__":
    main()
