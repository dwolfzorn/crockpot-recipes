"""Step 3: sanity-check recipes.json. Exits non-zero if anything looks wrong.

Usage: python build/check.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "recipes.json"

# Expected totals. If you intentionally change the data or tag rules, update these.
EXPECTED = {"recipes": 89, "Meals": 60, "Proteins": 29, "Rice": 23, "Pasta": 27}
BATCH_ONLY = {"buffalo-chicken-dip"}   # macros given for the whole batch, no serving count


def main():
    recipes = json.loads(DATA.read_text(encoding="utf-8"))["recipes"]
    errors = []

    def err(r, msg):
        errors.append(f"p{r.get('page', '?')} {r.get('id', '?')}: {msg}")

    ids = Counter(r["id"] for r in recipes)
    errors += [f"duplicate id '{i}'" for i, n in ids.items() if n > 1]

    for r in recipes:
        n = r["nutrition"]
        for k in ("calories", "protein", "carbs", "fat"):
            if not isinstance(n.get(k), (int, float)) or n[k] < 0:
                err(r, f"bad {k}: {n.get(k)!r}")
        if r["id"] in BATCH_ONLY:
            if n["per"] != "batch":
                err(r, "expected batch nutrition")
        elif n["per"] != "serving" or not r["servings"]:
            err(r, f"expected per-serving nutrition with servings, got {n['per']}/{r['servings']}")
        elif n["calories"] > 1000:
            err(r, f"suspicious calories per serving: {n['calories']}")
        if not r["title"].strip():
            err(r, "empty title")
        if r["protein"] not in ("chicken", "beef", "pork"):
            err(r, f"no protein tag ({r['protein']!r})")
        if not isinstance(r["favorite"], bool):
            err(r, f"favorite must be true or false, got {r['favorite']!r}")
        if not r["ingredients"]:
            err(r, "no ingredients")
        for g in r["ingredients"]:
            if not g["items"]:
                err(r, f"empty ingredient group '{g['name']}'")
            for item in g["items"]:
                if not item.strip() or item[0] in "(),&":
                    err(r, f"ingredient looks like a broken line: {item!r}")
        if not r["instructions"]:
            err(r, "no instructions")
        for i, step in enumerate(r["instructions"], 1):
            if len(step["text"]) < 15:
                err(r, f"step {i} is suspiciously short: {step['text']!r}")
            for h in step["highlights"]:
                if h not in step["text"]:
                    err(r, f"step {i} highlight not in text: {h!r}")

    counts = Counter({"recipes": len(recipes)})
    for r in recipes:
        counts[r["section"]] += 1
        counts.update(r["tags"])
    for key, want in EXPECTED.items():
        if counts[key] != want:
            errors.append(f"expected {want} {key}, found {counts[key]} (update EXPECTED if intentional)")

    if errors:
        print(f"check: {len(errors)} problem(s)")
        for e in errors:
            print("  " + e)
        sys.exit(1)
    print(f"check: OK ({len(recipes)} recipes, {counts['Rice']} rice, {counts['Pasta']} pasta)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
