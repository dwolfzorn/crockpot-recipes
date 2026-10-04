"""Step 1: read the cookbook PDF and write the raw recipe data to data/raw.json.

This step only transcribes what is on the page. Cleanup, tags and manual corrections
happen in enrich.py, so they can change without re-reading the PDF.

Usage: python build/extract.py   (needs the PDF in the repo root and `pip install pymupdf`)
"""
import json
import re
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "Stealth Health Slow Cooker Cookbook.pdf"
OUT = ROOT / "data" / "raw.json"

# --- Book layout ---------------------------------------------------------------------------
TOC_PAGES = (18, 19, 20)
SECTIONS = [("Meals", 22, 81), ("Proteins", 83, 111)]
FOOTER_Y = 765            # "back to table of contents  NN" sits below this
COLUMN_SPLIT_X = 400      # ingredient columns start at x≈262 and x≈421-458
STEP_NUMBER_MAX_X = 35    # step numbers ("1.") sit in the left margin
NOTE_MAX_X = 21           # footnotes ("*Fresh pineapple...") start further left than steps

# --- PDF quirks ------------------------------------------------------------------------------
# Ingredient lines wrap inside narrow columns, and the PDF doesn't mark which lines are
# continuations. These rules decide when a line is the tail of the previous ingredient.
QTY_TAIL = re.compile(r"(^|\s)[\d.,½⅓⅔¼¾⅛]+\s*(g|ml|oz)?\s*\([^)]*\)$")  # line ends in "45g (3 Tbsp)"
JOIN_ENDINGS = (",", "-", "&", "/", " or", " and", " of", " to", " with", " include", " +")
# Capitalized words that only ever appear as the tail of a wrapped line, keyed by the
# word that ends the previous line ("Parmigiano" / "Reggiano", "Lee" / "Kum Kee").
WRAPPED_BEFORE_CAPITAL = ("Parmigiano", "Lee", "Lucky", "Sweet")
WRAPPED_BEFORE_CAPITAL_SUFFIX = ("%", "sugar-free")
# Lowercase lines that start a new ingredient rather than continuing one.
ITEM_START_WORDS = ("handful", "salt", "pinch", "dash", "splash", "juice", "chopped")
# Footnotes printed inline inside a step (pp. 80, 111) rather than below the steps.
INLINE_NOTE = re.compile(r"\s?\*(Buffalo “Wing” sauce[^*]*?mixture\.)\*?")


# --- Text helpers ----------------------------------------------------------------------------
def spans(page):
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    x0, y0, x1, y1 = s["bbox"]
                    out.append(dict(x0=x0, y0=y0, x1=x1, y1=y1, size=s["size"], font=s["font"],
                                    text=s["text"], bold="Bold" in s["font"] or "-Bo" in s["font"]))
    return out


def clean(t):
    t = t.replace(" ", " ").replace("�", "–")
    return re.sub(r"\s+", " ", t).strip()


def join_spans(line_spans):
    """Return [(text, bold)] for the spans on one line, re-inserting spaces the PDF dropped
    between visually separated spans (e.g. "yellow" + "onion")."""
    runs, prev = [], None
    for s in line_spans:
        t = s["text"]
        if (prev is not None and s["x0"] - prev["x1"] > 1.5
                and not prev["text"].endswith((" ", "(")) and not t.startswith(" ")):
            t = " " + t
        runs.append((t, s["bold"]))
        prev = s
    return runs


def group_lines(items, tol=4):
    """Group spans into visual lines (fraction glyphs sit ~2pt lower than their line)."""
    lines = []
    for s in sorted(items, key=lambda s: (s["y0"], s["x0"])):
        for ln in lines:
            if abs(ln["y"] - s["y0"]) <= tol:
                ln["spans"].append(s)
                break
        else:
            lines.append({"y": s["y0"], "spans": [s]})
    for ln in lines:
        ln["spans"].sort(key=lambda s: s["x0"])
        ln["x0"] = ln["spans"][0]["x0"]
        ln["x1"] = max(s["x1"] for s in ln["spans"])
        ln["text"] = clean("".join(t for t, _ in join_spans(ln["spans"])))
    return sorted(lines, key=lambda l: l["y"])


def slugify(t):
    return re.sub(r"[^a-z0-9]+", "-", t.lower().replace("’", "")).strip("-")


def number(pattern, text):
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    v = float(m.group(1))
    return int(v) if v == int(v) else v


# --- Table of contents -----------------------------------------------------------------------
def parse_toc(doc):
    """Map page number -> title. Titles and page numbers are parallel columns."""
    toc = {}
    for p in TOC_PAGES:
        ss = spans(doc[p - 1])
        nums = sorted((s for s in ss if re.fullmatch(r"\s*\d+\s*", s["text"]) and s["x0"] > 300),
                      key=lambda s: s["y0"])
        titles = sorted((s for s in ss if s["x0"] < 100 and s["size"] < 13.5 and s["y0"] > 75),
                        key=lambda s: s["y0"])
        assert len(nums) == len(titles), (p, len(nums), len(titles))
        for n, t in zip(nums, titles):
            toc[int(n["text"])] = clean(t["text"])
    return toc


# --- Ingredients -----------------------------------------------------------------------------
def first_word_width(line):
    word = line["text"].split(" ")[0]
    return pymupdf.get_text_length(word, fontname="helv", fontsize=line["spans"][0]["size"])


def is_continuation(prev, line, col_right):
    """Is `line` the wrapped tail of the previous ingredient line?"""
    t, p = line["text"], prev["text"]
    if p.count("(") > p.count(")") or p.endswith(JOIN_ENDINGS):
        return True
    if t.startswith("OR ") or t[0] in "&(),":     # "...brisket or chuck eye roast" / "OR lean steak"
        return True
    if t[0].isupper():
        return (p.split(" ")[-1] in WRAPPED_BEFORE_CAPITAL
                or p.endswith(WRAPPED_BEFORE_CAPITAL_SUFFIX) or bool(QTY_TAIL.search(p)))
    if t[0].islower() and not t.lower().startswith(ITEM_START_WORDS):
        # word-wrap test: the first word wouldn't have fit on the previous line
        return prev["x1"] + first_word_width(line) + 3 > col_right - 25
    return False


def is_header(line):
    return all(s["bold"] for s in line["spans"] if s["text"].strip(" :"))


def parse_column(col_spans, col_right):
    groups, current, prev = [], None, None
    for ln in group_lines(col_spans):
        if is_header(ln):
            name = ln["text"].strip().rstrip(":").strip()
            if current is not None and not current["items"] and prev is None:
                current["name"] += " " + name          # header split over two lines
            else:
                current = {"name": name, "items": []}
                groups.append(current)
            prev = None
            continue
        if current is None:
            current = {"name": None, "items": []}
            groups.append(current)
        if prev is not None and current["items"] and is_continuation(prev, ln, col_right):
            current["items"][-1] += " " + ln["text"]
        else:
            current["items"].append(ln["text"])
        prev = ln
    return groups


def parse_ingredients(items):
    """Ingredients sit in up to two columns, each split into groups by bold headers."""
    left = [s for s in items if s["x0"] < COLUMN_SPLIT_X]
    right = [s for s in items if s["x0"] >= COLUMN_SPLIT_X]
    groups = []
    for col, max_right in ((left, COLUMN_SPLIT_X + 15), (right, None)):
        if not col:
            continue
        lines = group_lines(col)
        col_right = max(max(l["x1"] for l in lines), min(l["x0"] for l in lines) + 150)
        if max_right:
            col_right = min(col_right, max_right)
        groups += parse_column(col, col_right)
    for g in groups:
        # re-join words hyphenated across lines ("fire- roasted"), but keep "Chicago- style"
        g["items"] = [re.sub(r"(\w)- ([a-z])", r"\1-\2", i) for i in g["items"]]
    return [g for g in groups if g["items"]]


# --- Instructions ----------------------------------------------------------------------------
def build_step(runs, notes):
    """Turn (text, bold) runs into {"text", "highlights"}; bold runs are the ingredient call-outs."""
    text = clean("".join(t for t, _ in runs))
    m = INLINE_NOTE.search(text)
    if m:
        if m.group(1) not in notes:
            notes.append(m.group(1))
        text = clean(text[:m.start()] + " " + text[m.end():])
    highlights, cur = [], ""
    for t, bold in runs + [("", False)]:
        if bold:
            cur += t
        elif t and not t.strip() and cur:      # whitespace between bold runs keeps the phrase going
            cur += t
        else:
            h = clean(cur).strip(" ,.;:*")
            if h and h in text and h not in highlights:
                highlights.append(h)
            cur = ""
    return {"text": text, "highlights": highlights}


def parse_steps(items):
    """Returns (steps, notes). Footnotes start with '*' at the far left, below the steps."""
    note_starts = [s["y0"] for s in items if s["x0"] < NOTE_MAX_X and s["text"].strip().startswith("*")]
    note_spans = [s for s in items if note_starts and s["y0"] >= min(note_starts) - 2]
    notes = []
    for ln in group_lines(note_spans):
        if ln["text"].startswith("*") or not notes:
            notes.append(ln["text"].lstrip("*").strip())
        else:
            notes[-1] += " " + ln["text"]

    items = [s for s in items if s not in note_spans]
    nums = sorted((s for s in items if re.fullmatch(r"\s*\d+\.\s*", s["text"]) and s["x0"] < STEP_NUMBER_MAX_X),
                  key=lambda s: s["y0"])
    body = [s for s in items if s not in nums]
    if not nums:
        return ([{"text": clean(" ".join(l["text"] for l in group_lines(body))), "highlights": []}]
                if body else []), notes

    steps = []
    for i, n in enumerate(nums):
        y_end = nums[i + 1]["y0"] - 4 if i + 1 < len(nums) else float("inf")
        runs = []
        for ln in group_lines([s for s in body if n["y0"] - 4 <= s["y0"] < y_end]):
            runs.append((" ", False))
            runs.extend(join_spans(ln["spans"]))
        steps.append(build_step(runs, notes))
    return steps, notes


# --- Recipe page -----------------------------------------------------------------------------
def parse_recipe(page, pno, toc, section):
    ss = [s for s in spans(page) if not (s["y0"] > FOOTER_Y and "IntroRust" in s["font"])]
    ing_head = next((s for s in ss if s["text"].strip().lower() == "ingredients"), None)
    ins_head = next((s for s in ss if s["text"].strip().lower() == "instructions"), None)
    if not ing_head or not ins_head:
        return None, ["missing ingredients/instructions heading"]
    warn = []

    title_spans = sorted((s for s in ss if s["size"] > 20 and s["y0"] < 90 and "IntroRust" in s["font"]),
                         key=lambda s: (s["y0"], s["x0"]))
    title = toc.get(pno) or clean(" ".join(s["text"] for s in title_spans))

    band = " ".join(s["text"] for s in ss if 80 < s["y0"] < 170)   # servings + macros band
    nutrition = {
        "per": "batch" if re.search(r"entire batch", band, re.I) else "serving",
        "calories": number(r"(\d+)\*?\s*Calories", band),
        "protein": number(r"(\d+(?:\.\d+)?)\s*g\*?\s*Protein", band),
        "carbs": number(r"(\d+(?:\.\d+)?)\s*g\*?\s*Carbs", band),
        "fat": number(r"(\d+(?:\.\d+)?)\s*g\*?\s*Fat", band),
    }
    servings = number(r"makes\s*(\d+)", band)
    warn += [f"missing {k}" for k, v in nutrition.items() if v is None]
    if servings is None and nutrition["per"] == "serving":
        warn.append("missing servings")

    # The ingredient columns can run past the "instructions" heading (which sits under the photo
    # on the left), so split ingredients from steps at the first step number instead.
    step1 = [s["y0"] for s in ss if re.fullmatch(r"\s*1\.\s*", s["text"]) and s["x0"] < STEP_NUMBER_MAX_X]
    split_y = (min(step1) if step1 else ins_head["y1"]) - 4
    ingredients = parse_ingredients([s for s in ss if ing_head["y1"] < s["y0"] < split_y and s["x0"] > 200])
    steps, notes = parse_steps([s for s in ss if s["y0"] >= split_y])
    if not ingredients:
        warn.append("no ingredients")
    if not steps:
        warn.append("no steps")

    return {
        "id": slugify(title),
        "title": title,
        "section": section,
        "page": pno,
        "servings": servings,
        "nutrition": nutrition,
        "ingredients": ingredients,
        "instructions": steps,
        "notes": notes,
    }, warn


def main():
    if not PDF.exists():
        sys.exit(f"{PDF.name} not found in the repo root; skip this step and use the committed data/raw.json.")
    doc = pymupdf.open(PDF)
    toc = parse_toc(doc)
    recipes, problems = [], []
    for section, start, end in SECTIONS:
        for pno in range(start, end + 1):
            recipe, warn = parse_recipe(doc[pno - 1], pno, toc, section)
            if warn:
                problems.append((pno, warn))
            if recipe:
                recipes.append(recipe)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"recipes": recipes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"extract: {len(recipes)} recipes -> {OUT.relative_to(ROOT)}")
    missing = sorted(set(toc) - {r["page"] for r in recipes})
    if missing:
        print("  TOC pages without a parsed recipe:", missing)
    for pno, w in problems:
        print(f"  p{pno}: {', '.join(w)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
