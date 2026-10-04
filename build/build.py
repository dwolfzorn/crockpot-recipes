"""Run the whole pipeline: [extract] -> enrich -> check -> build_html.

Usage:
  python build/build.py             # rebuild from the committed data/raw.json
  python build/build.py --extract   # re-read the PDF first (needs the PDF + pymupdf)
"""
import sys

import build_html
import check
import enrich


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if "--extract" in sys.argv:
        import extract  # imported lazily so the normal build doesn't need pymupdf
        extract.main()
    enrich.main()
    check.main()
    build_html.main()


if __name__ == "__main__":
    main()
