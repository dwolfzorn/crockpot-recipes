"""Step 4: bundle recipes.json + src/ into a single self-contained index.html.

Usage: python build/build_html.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DATA = ROOT / "recipes.json"
OUT = ROOT / "index.html"


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    # Escape "</" so the inlined JSON/JS can't close their <script> tags early.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (SRC / "template.html").read_text(encoding="utf-8")
    for marker, content in (("/*STYLES*/", (SRC / "styles.css").read_text(encoding="utf-8")),
                            ("/*APP_JS*/", (SRC / "app.js").read_text(encoding="utf-8")),
                            ("/*RECIPES_JSON*/", payload)):
        assert marker in html, f"{marker} missing from template.html"
        html = html.replace(marker, content.rstrip("\n"))
    OUT.write_text(html, encoding="utf-8")
    print(f"build: {OUT.name} ({OUT.stat().st_size / 1024:.0f} KB, {len(data['recipes'])} recipes)")


if __name__ == "__main__":
    main()
