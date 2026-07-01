#!/usr/bin/env python3
"""Download a PDF to disk and report basic info. No API key required.

Validates that the downloaded bytes are actually a PDF — a common failure is
grabbing an HTML landing page or hitting a redirect. Exits non-zero if not a PDF.

Usage:
    python fetch_pdf.py --url <pdf-url> --out data/raw/philips_2022.pdf
"""
import argparse
import os
import sys
import urllib.request

UA = "Mozilla/5.0 (compatible; esg-longitudinal/1.0)"


def download(url, out):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    try:
        import requests
        r = requests.get(url, headers={"User-Agent": UA}, timeout=90, allow_redirects=True)
        r.raise_for_status()
        data = r.content
    except ImportError:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
    with open(out, "wb") as f:
        f.write(data)
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data = download(args.url, args.out)
    is_pdf = data[:5] == b"%PDF-"
    pages = None
    if is_pdf:
        try:
            import fitz
            pages = fitz.open(args.out).page_count
        except Exception:
            pass

    print(f"saved: {args.out}")
    print(f"bytes: {len(data)}")
    print(f"is_pdf: {is_pdf}")
    if pages is not None:
        print(f"pages: {pages}")
    if not is_pdf:
        print("WARNING: not a PDF — likely an HTML landing page or redirect. "
              "Open the URL, find the direct .pdf link, and retry.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
