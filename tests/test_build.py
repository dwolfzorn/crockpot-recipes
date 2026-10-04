"""Tests for the build pipeline: enrich.py, check.py and build_html.py.

Run: python -m unittest discover -s tests -v   (or `npm test` to run every suite)
extract.py isn't covered: it needs the copyrighted PDF, which isn't in the repo.
"""
import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))

import build_html  # noqa: E402
import check  # noqa: E402
import enrich  # noqa: E402

RECIPES = json.loads((ROOT / "recipes.json").read_text(encoding="utf-8"))["recipes"]
BY_ID = {r["id"]: r for r in RECIPES}


def raw_recipe(**kw):
    """A minimal recipe as it appears in data/raw.json."""
    r = {"id": "test", "title": "Test Chicken", "section": "Meals", "page": 1, "servings": 4,
         "nutrition": {"per": "serving", "calories": 400, "protein": 40, "carbs": 30, "fat": 10},
         "ingredients": [{"name": None, "items": ["500g chicken breast", "200g rice"]}],
         "instructions": [{"text": "Cook everything in the slow cooker.", "highlights": []}],
         "notes": []}
    r.update(kw)
    return r


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_json(self, name, data):
        p = self.tmp / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p


# --- enrich.py -------------------------------------------------------------------------------
class TidyTitleTest(unittest.TestCase):
    def test_capitalizes_lowercase_words(self):
        self.assertEqual(enrich.tidy_title("Beef Stroganoff orzo"), "Beef Stroganoff Orzo")

    def test_keeps_small_words_lowercase(self):
        self.assertEqual(enrich.tidy_title("Chicken and Rice with Beans or Peas of Doom"),
                         "Chicken and Rice with Beans or Peas of Doom")

    def test_mac_n_cheese(self):
        self.assertEqual(enrich.tidy_title("Buffalo Mac N Cheese"), "Buffalo Mac n’ Cheese")


class MainProteinTest(unittest.TestCase):
    def test_from_title(self):
        self.assertEqual(enrich.main_protein(raw_recipe(title="Pulled Pork Bowls")), "pork")

    def test_beef_synonyms(self):
        for word in ("Brisket", "Steak", "Birria", "Chuck Roast", "Beef"):
            self.assertEqual(enrich.main_protein(raw_recipe(title=f"Spicy {word}")), "beef", word)

    def test_falls_back_to_first_ingredient(self):
        r = raw_recipe(title="Mystery Bowls",
                       ingredients=[{"name": None, "items": ["900g pork shoulder", "rice"]}])
        self.assertEqual(enrich.main_protein(r), "pork")

    def test_title_wins_over_ingredient(self):
        r = raw_recipe(title="Chicken Bowls",
                       ingredients=[{"name": None, "items": ["900g beef", "rice"]}])
        self.assertEqual(enrich.main_protein(r), "chicken")

    def test_none_when_unknown(self):
        r = raw_recipe(title="Veggie Soup", ingredients=[{"name": None, "items": ["carrots"]}])
        self.assertIsNone(enrich.main_protein(r))

    def test_no_ingredients(self):
        self.assertIsNone(enrich.main_protein(raw_recipe(title="Soup", ingredients=[])))


class BaseTagsTest(unittest.TestCase):
    def tags(self, *items):
        return enrich.base_tags(raw_recipe(ingredients=[{"name": None, "items": list(items)}]))

    def test_rice(self):
        self.assertEqual(self.tags("200g jasmine rice"), ["Rice"])

    def test_rice_vinegar_and_wine_are_not_rice(self):
        self.assertEqual(self.tags("2 Tbsp rice vinegar", "1 Tbsp rice wine"), [])

    def test_pasta_words(self):
        for word in ("pasta", "egg noodles", "macaroni", "orzo", "rigatoni", "penne",
                     "farfalle", "lasagna sheets", "spaghetti"):
            self.assertEqual(self.tags(word), ["Pasta"], word)

    def test_both(self):
        self.assertEqual(self.tags("rice", "orzo"), ["Rice", "Pasta"])


class OverridePathTest(unittest.TestCase):
    def test_parse_path(self):
        self.assertEqual(enrich.parse_path("ingredients[1].items[2]"), ["ingredients", 1, "items", 2])
        self.assertEqual(enrich.parse_path("servings"), ["servings"])
        self.assertEqual(enrich.parse_path("nutrition.calories"), ["nutrition", "calories"])

    def test_parse_path_rejects_malformed(self):
        for bad in ("", "a..b", "a[x]", "a[1", ".a", "a.[1]", "items[-1]"):
            with self.assertRaises(ValueError, msg=bad):
                enrich.parse_path(bad)

    def test_set_path(self):
        r = raw_recipe()
        enrich.set_path(r, "ingredients[0].items[1]", "300g rice")
        enrich.set_path(r, "nutrition.calories", 410)
        self.assertEqual(r["ingredients"][0]["items"][1], "300g rice")
        self.assertEqual(r["nutrition"]["calories"], 410)

    def test_set_path_rejects_missing_targets(self):
        r = raw_recipe()
        with self.assertRaises(IndexError):
            enrich.set_path(r, "ingredients[0].items[9]", "x")
        with self.assertRaises(KeyError):
            enrich.set_path(r, "nutrition.sodium", 5)

    def test_apply_overrides_reports_touched_fields(self):
        recipes = [raw_recipe(protein=None, tags=[], favorite=False)]
        touched = enrich.apply_overrides(recipes, {"_comment": "ignored",
                                                   "test": {"servings": 6, "favorite": True}})
        self.assertEqual(touched, {"test": {"servings", "favorite"}})
        self.assertEqual(recipes[0]["servings"], 6)
        self.assertTrue(recipes[0]["favorite"])

    def test_apply_overrides_collects_all_errors(self):
        recipes = [raw_recipe()]
        with self.assertRaises(SystemExit) as cm:
            enrich.apply_overrides(recipes, {"nope": {"servings": 1}, "test": {"bogus": 1}})
        msg = str(cm.exception)
        self.assertIn("unknown recipe id 'nope'", msg)
        self.assertIn("test: path 'bogus' does not exist", msg)


class EnrichMainTest(TempDirTest):
    def run_enrich(self, recipes, overrides=None):
        raw = self.write_json("raw.json", {"recipes": recipes})
        ov = self.write_json("overrides.json", overrides) if overrides is not None else self.tmp / "none.json"
        out = self.tmp / "recipes.json"
        with mock.patch.multiple(enrich, RAW=raw, OVERRIDES=ov, OUT=out, ROOT=self.tmp), \
                redirect_stdout(io.StringIO()):
            enrich.main()
        return json.loads(out.read_text(encoding="utf-8"))

    def test_output_shape(self):
        data = self.run_enrich([raw_recipe(title="Test chicken")])
        self.assertEqual(data["title"], "Crockpot Recipes")
        self.assertEqual(data["recipeCount"], 1)
        r = data["recipes"][0]
        self.assertEqual(list(r), enrich.FIELDS)
        self.assertEqual(r["title"], "Test Chicken")
        self.assertEqual(r["protein"], "chicken")
        self.assertEqual(r["tags"], ["Rice"])
        self.assertIs(r["favorite"], False)

    def test_overrides_file_is_optional(self):
        self.assertEqual(self.run_enrich([raw_recipe()])["recipeCount"], 1)

    def test_override_favorite(self):
        r = self.run_enrich([raw_recipe()], {"test": {"favorite": True}})["recipes"][0]
        self.assertIs(r["favorite"], True)

    def test_overridden_protein_and_tags_are_not_rederived(self):
        r = self.run_enrich([raw_recipe()], {"test": {"protein": "beef", "tags": ["Pasta"]}})["recipes"][0]
        self.assertEqual((r["protein"], r["tags"]), ("beef", ["Pasta"]))

    def test_override_applies_after_title_tidy(self):
        r = self.run_enrich([raw_recipe(title="lower title")], {"test": {"title": "exact title"}})["recipes"][0]
        self.assertEqual(r["title"], "exact title")


# --- check.py --------------------------------------------------------------------------------
class CheckTest(TempDirTest):
    def run_check(self, recipes):
        data = self.write_json("recipes.json", {"recipes": recipes})
        out = io.StringIO()
        with mock.patch.object(check, "DATA", data), redirect_stdout(out):
            try:
                check.main()
                ok = True
            except SystemExit as e:
                ok = e.code in (0, None)
        return ok, out.getvalue()

    def assert_fails(self, mutate, expected):
        recipes = copy.deepcopy(RECIPES)
        mutate(recipes)
        ok, out = self.run_check(recipes)
        self.assertFalse(ok, f"check passed but should have reported: {expected}")
        self.assertIn(expected, out)

    def test_committed_data_passes(self):
        ok, out = self.run_check(RECIPES)
        self.assertTrue(ok, out)
        self.assertIn("check: OK", out)

    def r(self, recipes, rid="japanese-curry"):
        return next(x for x in recipes if x["id"] == rid)

    def test_duplicate_id(self):
        self.assert_fails(lambda rs: rs.append(copy.deepcopy(rs[0])), "duplicate id")

    def test_bad_macro(self):
        self.assert_fails(lambda rs: self.r(rs)["nutrition"].update(protein=-1), "bad protein")
        self.assert_fails(lambda rs: self.r(rs)["nutrition"].update(fat="lots"), "bad fat")

    def test_suspicious_calories(self):
        self.assert_fails(lambda rs: self.r(rs)["nutrition"].update(calories=1500), "suspicious calories")

    def test_per_serving_needs_servings(self):
        self.assert_fails(lambda rs: self.r(rs).update(servings=None), "expected per-serving nutrition")

    def test_batch_recipe_must_stay_batch(self):
        self.assert_fails(lambda rs: self.r(rs, "buffalo-chicken-dip")["nutrition"].update(per="serving"),
                          "expected batch nutrition")

    def test_empty_title(self):
        self.assert_fails(lambda rs: self.r(rs).update(title="  "), "empty title")

    def test_missing_protein(self):
        self.assert_fails(lambda rs: self.r(rs).update(protein="tofu"), "no protein tag")

    def test_favorite_must_be_bool(self):
        self.assert_fails(lambda rs: self.r(rs).update(favorite="yes"), "favorite must be true or false")

    def test_ingredients(self):
        self.assert_fails(lambda rs: self.r(rs).update(ingredients=[]), "no ingredients")
        self.assert_fails(lambda rs: self.r(rs)["ingredients"][0].update(items=[]), "empty ingredient group")
        self.assert_fails(lambda rs: self.r(rs)["ingredients"][0]["items"].append("(14oz) can"),
                          "broken line")

    def test_instructions(self):
        self.assert_fails(lambda rs: self.r(rs).update(instructions=[]), "no instructions")
        self.assert_fails(lambda rs: self.r(rs)["instructions"][0].update(text="Stir."), "suspiciously short")
        self.assert_fails(lambda rs: self.r(rs)["instructions"][0]["highlights"].append("not in the text"),
                          "highlight not in text")

    def test_expected_counts(self):
        self.assert_fails(lambda rs: rs.pop(), "expected 89 recipes")
        self.assert_fails(lambda rs: self.r(rs).update(tags=[]), "Rice, found")


# --- build_html.py ---------------------------------------------------------------------------
class BuildHtmlTest(TempDirTest):
    def build(self, recipes, src=None):
        data = self.write_json("recipes.json", {"title": "T", "recipeCount": len(recipes), "recipes": recipes})
        out = self.tmp / "index.html"
        with mock.patch.multiple(build_html, DATA=data, OUT=out, SRC=src or build_html.SRC), \
                redirect_stdout(io.StringIO()):
            build_html.main()
        return out.read_text(encoding="utf-8")

    def test_inlines_everything(self):
        html = self.build(RECIPES[:1])
        for marker in ("/*STYLES*/", "/*APP_JS*/", "/*RECIPES_JSON*/"):
            self.assertNotIn(marker, html)
        self.assertIn((ROOT / "src" / "styles.css").read_text(encoding="utf-8").strip()[:200], html)
        self.assertIn((ROOT / "src" / "app.js").read_text(encoding="utf-8").strip()[:200], html)
        self.assertNotIn('src="', html.split("<body>")[0])  # no external files

    def test_escapes_script_close_in_data(self):
        r = copy.deepcopy(RECIPES[0])
        r["notes"] = ["</script><script>alert(1)</script>"]
        html = self.build([r])
        payload = html.split('<script id="recipe-data" type="application/json">')[1].split("</script>")[0]
        self.assertEqual(json.loads(payload)["recipes"][0]["notes"], r["notes"])

    def test_missing_marker_fails(self):
        src = self.tmp / "src"
        src.mkdir()
        for f in ("styles.css", "app.js"):
            (src / f).write_text("x", encoding="utf-8")
        (src / "template.html").write_text("/*STYLES*/ /*APP_JS*/", encoding="utf-8")
        with self.assertRaises(AssertionError):
            self.build(RECIPES[:1], src=src)


# --- Committed outputs -----------------------------------------------------------------------
class CommittedOutputsTest(TempDirTest):
    """recipes.json and index.html must match what the build produces from the sources."""

    def test_outputs_are_up_to_date(self):
        recipes_out = self.tmp / "recipes.json"
        html_out = self.tmp / "index.html"
        with mock.patch.object(enrich, "OUT", recipes_out), mock.patch.object(enrich, "ROOT", self.tmp), \
                mock.patch.multiple(build_html, DATA=recipes_out, OUT=html_out), redirect_stdout(io.StringIO()):
            enrich.main()
            build_html.main()
        msg = "out of date: run `python build/build.py` and commit the result"
        self.assertEqual(recipes_out.read_text(encoding="utf-8"),
                         (ROOT / "recipes.json").read_text(encoding="utf-8"), "recipes.json " + msg)
        self.assertEqual(html_out.read_text(encoding="utf-8"),
                         (ROOT / "index.html").read_text(encoding="utf-8"), "index.html " + msg)


class FavoritesDataTest(unittest.TestCase):
    """The favorites chosen in data/overrides.json."""

    def test_chosen_favorites(self):
        for rid in ("chili-garlic-chicken-fried-rice", "honey-chipotle-chicken-burrito-bowls",
                    "honey-harissa-chicken-rice-bowls", "chimichurri-steak", "pepper-steak"):
            self.assertIs(BY_ID[rid]["favorite"], True, rid)

    def test_everything_else_defaults_to_not_favorite(self):
        overrides = json.loads((ROOT / "data" / "overrides.json").read_text(encoding="utf-8"))
        chosen = {rid for rid, ch in overrides.items() if isinstance(ch, dict) and ch.get("favorite") is True}
        for r in RECIPES:
            self.assertIs(r["favorite"], r["id"] in chosen, r["id"])


if __name__ == "__main__":
    unittest.main()
