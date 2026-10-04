"""Step 2: data/raw.json + data/overrides.json -> recipes.json.

Tidies titles, applies manual corrections from overrides.json, and derives the
filter fields (protein, rice/pasta tags). Fields set by an override are never re-derived.

Usage: python build/enrich.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw.json"
OVERRIDES = ROOT / "data" / "overrides.json"
OUT = ROOT / "recipes.json"
SITE_TITLE = "Crockpot Recipes"

# Main protein: checked against the title first, then the first (main) ingredient.
PROTEIN_RULES = [("chicken", r"chicken"), ("pork", r"pork"), ("beef", r"beef|brisket|steak|birria|chuck")]
# Base-starch tags, matched against every ingredient line. Rice excludes "rice vinegar"/"rice wine".
TAG_RULES = [
    ("Rice", r"\brice\b(?!\s*(wine|vinegar))"),
    ("Pasta", r"pasta|noodle|macaroni|orzo|rigatoni|penne|farfalle|lasagna|spaghetti"),
]
# Field order in recipes.json.
FIELDS = ["id", "title", "section", "protein", "tags", "page", "servings", "nutrition",
          "ingredients", "instructions", "notes"]


def tidy_title(t):
    """Normalize casing quirks from the book's TOC ("Beef Stroganoff orzo", "Mac N Cheese")."""
    t = t.replace("Mac N Cheese", "Mac n’ Cheese")
    keep = {"n’", "&", "and", "or", "of", "with"}
    return " ".join(w if w in keep or not w[0].islower() else w[0].upper() + w[1:] for w in t.split())


def main_protein(recipe):
    first = recipe["ingredients"][0]["items"][0] if recipe["ingredients"] else ""
    for text in (recipe["title"], first):
        for name, pattern in PROTEIN_RULES:
            if re.search(pattern, text, re.I):
                return name
    return None


def base_tags(recipe):
    items = [i for g in recipe["ingredients"] for i in g["items"]]
    return [tag for tag, pattern in TAG_RULES if any(re.search(pattern, i, re.I) for i in items)]


# --- Overrides -------------------------------------------------------------------------------
PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def parse_path(path):
    """'ingredients[1].items[2]' -> ['ingredients', 1, 'items', 2]"""
    keys = [int(i) if i else k for k, i in PATH_TOKEN.findall(path)]
    rebuilt = "".join(f"[{k}]" if isinstance(k, int) else f".{k}" for k in keys).lstrip(".")
    if not keys or rebuilt != path:
        raise ValueError(path)
    return keys


def set_path(obj, path, value):
    keys = parse_path(path)
    target = obj
    for k in keys[:-1]:
        target = target[k]
    last = keys[-1]
    if isinstance(target, list) and not 0 <= last < len(target):
        raise IndexError(last)
    if isinstance(target, dict) and last not in target:
        raise KeyError(last)
    target[last] = value


def apply_overrides(recipes, overrides):
    """Returns {recipe_id: set of top-level fields that were overridden}."""
    by_id = {r["id"]: r for r in recipes}
    touched = {}
    errors = []
    for rid, changes in overrides.items():
        if rid.startswith("_"):            # "_comment" etc.
            continue
        if rid not in by_id:
            errors.append(f"unknown recipe id '{rid}'")
            continue
        for path, value in changes.items():
            try:
                set_path(by_id[rid], path, value)
                touched.setdefault(rid, set()).add(parse_path(path)[0])
            except (KeyError, IndexError, TypeError, ValueError):
                errors.append(f"{rid}: path '{path}' does not exist")
    if errors:
        sys.exit("overrides.json has problems:\n  " + "\n  ".join(errors))
    return touched


def main():
    recipes = json.loads(RAW.read_text(encoding="utf-8"))["recipes"]
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}

    for r in recipes:
        r["title"] = tidy_title(r["title"])
        r.setdefault("protein", None)    # derived below, but must exist so overrides can set them
        r.setdefault("tags", [])
    touched = apply_overrides(recipes, overrides)
    for r in recipes:
        done = touched.get(r["id"], set())
        if "protein" not in done:
            r["protein"] = main_protein(r)
        if "tags" not in done:
            r["tags"] = base_tags(r)

    recipes = [{k: r[k] for k in FIELDS} for r in recipes]
    data = {"title": SITE_TITLE, "recipeCount": len(recipes), "recipes": recipes}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n = sum(len(v) for k, v in overrides.items() if not k.startswith("_"))
    print(f"enrich: {len(recipes)} recipes, {n} override(s) applied -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
