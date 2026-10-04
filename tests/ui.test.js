// Tests for the site itself (src/app.js, src/template.html, src/styles.css), run against the
// built index.html in jsdom.
// Run: npm run test:ui   (or `npm test` to run every suite). Rebuild index.html first.
const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const ROOT = path.join(__dirname, "..");
const HTML = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "src", "styles.css"), "utf8");
const RECIPES = JSON.parse(fs.readFileSync(path.join(ROOT, "recipes.json"), "utf8")).recipes;
const byId = (id) => RECIPES.find((r) => r.id === id);
const BASE_FAVS = RECIPES.filter((r) => r.favorite).map((r) => r.id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[’']/g, "");

// Loads the page. `local`/`session` pre-seed storage; `hash`/`search` set the starting URL.
function load({ hash = "", search = "", local = {}, session = {}, clipboard, storageThrows = false } = {}) {
  const calls = { clipboard: [], scrollTo: [] };
  const dom = new JSDOM(HTML, {
    url: "https://example.test/index.html" + search + hash,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(w) {
      w.scrollTo = (x, y) => calls.scrollTo.push(y);
      for (const [k, v] of Object.entries(local)) w.localStorage.setItem(k, typeof v === "string" ? v : JSON.stringify(v));
      for (const [k, v] of Object.entries(session)) w.sessionStorage.setItem(k, typeof v === "string" ? v : JSON.stringify(v));
      if (storageThrows) w.Storage.prototype.setItem = () => { throw new Error("QuotaExceededError"); };
      Object.defineProperty(w.navigator, "clipboard", {
        value: { writeText: clipboard || (async (t) => { calls.clipboard.push(t); }) },
      });
    },
  });
  const w = dom.window, d = w.document;
  const $ = (sel) => d.querySelector(sel);
  const $$ = (sel) => [...d.querySelectorAll(sel)];
  const page = {
    w, d, $, $$, calls,
    ids: () => $$(".card").map((a) => a.getAttribute("href").slice(2)),
    count: () => $("#count").textContent,
    chip: (group, label) => $$(`#${group} .chip`).find((c) => c.textContent === label),
    cardStar: (id) => $(`.card-fav[data-fav="${id}"]`),
    favs: () => JSON.parse(w.localStorage.getItem("cookbook-favorites") || "{}"),
    savedState: () => JSON.parse(w.sessionStorage.getItem("cookbook-state")),
    async search(q) {
      $("#q").value = q;
      $("#q").dispatchEvent(new w.Event("input"));
      await sleep(120); // input is debounced
    },
    async go(h) {
      w.location.hash = h;
      await sleep(10); // jsdom fires hashchange asynchronously
    },
    key(k) { d.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true })); },
    sort(v) { $("#sort").value = v; $("#sort").dispatchEvent(new w.Event("change")); },
    showing: () => ($("#detail").hidden ? "list" : "detail"),
  };
  return page;
}

// --- Recipe list ------------------------------------------------------------------------------
describe("recipe list", () => {
  test("shows every recipe in book order", () => {
    const p = load();
    assert.equal(p.$$(".card").length, RECIPES.length);
    assert.equal(p.count(), `${RECIPES.length} of ${RECIPES.length} recipes`);
    assert.deepEqual(p.ids(), [...RECIPES].sort((a, b) => a.page - b.page).map((r) => r.id));
    assert.equal(p.d.title, "Crockpot Recipes");
    assert.equal(p.showing(), "list");
  });

  test("cards show title, badges, page and macros", () => {
    const p = load();
    const r = byId("japanese-curry");
    const card = p.$('.card[href="#/japanese-curry"]');
    assert.equal(card.querySelector("h3").textContent, r.title);
    const tags = [...card.querySelectorAll(".tag")].map((t) => t.textContent);
    assert.ok(tags.includes(r.section));
    assert.ok(tags.includes(r.protein[0].toUpperCase() + r.protein.slice(1)));
    for (const t of r.tags) assert.ok(tags.includes(t));
    assert.ok(tags.includes(`p. ${r.page}`));
    const macros = [...card.querySelectorAll(".macros b")].map((b) => b.textContent);
    const n = r.nutrition;
    assert.deepEqual(macros, [String(n.calories), `${n.protein}g`, `${n.carbs}g`, `${n.fat}g`]);
    assert.match(card.textContent, new RegExp(`Per serving · makes ${r.servings}`));
  });

  test("batch-only recipes say macros are for the whole batch", () => {
    const p = load();
    const batch = RECIPES.find((r) => r.nutrition.per === "batch");
    assert.match(p.$(`.card[href="#/${batch.id}"]`).textContent, /Macros for the entire batch/);
  });
});

// --- Search -----------------------------------------------------------------------------------
describe("search", () => {
  const haystack = (r) => norm([r.title, ...r.ingredients.map((g) => (g.name || "") + " " + g.items.join(" ")),
    ...r.instructions.map((s) => s.text), ...r.notes].join(" "));
  const expected = (q) => RECIPES.filter((r) => norm(q).split(/\s+/).filter(Boolean).every((t) => haystack(r).includes(t)))
    .map((r) => r.id).sort();

  for (const q of ["orzo", "brisket", "cottage cheese", "chicken orzo", "shreddable"]) {
    test(`"${q}" matches titles, ingredients and instructions`, async () => {
      const p = load();
      await p.search(q);
      const want = expected(q);
      assert.ok(want.length > 0);
      assert.deepEqual([...p.ids()].sort(), want);
      assert.equal(p.count(), `${want.length} of ${RECIPES.length} recipes`);
    });
  }

  test("ignores case, accents and apostrophes", async () => {
    const p = load();
    const jal = RECIPES.find((r) => r.title.includes("Jalapeño"));
    await p.search("JALAPENO POPPER");
    assert.ok(p.ids().includes(jal.id));
    await p.search("mac n cheese");
    assert.ok(p.ids().length >= RECIPES.filter((r) => r.title.includes("Mac n’ Cheese")).length);
  });

  test("results rank title matches, then ingredient matches, then book order", async () => {
    const p = load();
    const q = "chicken";
    await p.search(q);
    const ing = (r) => norm(r.ingredients.map((g) => (g.name || "") + " " + g.items.join(" ")).join(" "));
    const score = (r) => (norm(r.title).includes(q) ? 2 : ing(r).includes(q) ? 1 : 0);
    const ranked = p.ids().map(byId);
    assert.ok(new Set(ranked.map(score)).size > 1, "query should produce mixed scores");
    ranked.forEach((r, i) => {
      if (i === 0) return;
      const prev = ranked[i - 1];
      assert.ok(score(prev) > score(r) || (score(prev) === score(r) && prev.page < r.page), `${prev.id} before ${r.id}`);
    });
  });

  test("highlights matches and shows matching ingredients", async () => {
    const p = load();
    await p.search("cottage cheese");
    const card = p.$(".card");
    assert.ok(card.querySelector("mark"));
    const per = card.querySelector(".per");
    assert.match(per.innerHTML, /<mark>cottage<\/mark>/i);
  });

  test("single-letter terms are not highlighted", async () => {
    const p = load();
    await p.search("a");
    assert.equal(p.$$(".card h3 mark").length, 0);
  });

  test("no results shows the query, safely escaped", async () => {
    const p = load();
    await p.search("<img src=x>");
    assert.equal(p.$$(".card").length, 0);
    assert.equal(p.$(".empty").textContent, "No recipes match “<img src=x>”.");
    assert.equal(p.$(".empty img"), null);
  });

  test("clear button appears with a query and resets the search", async () => {
    const p = load();
    assert.equal(p.$("#clear").hidden, true);
    await p.search("orzo");
    assert.equal(p.$("#clear").hidden, false);
    p.$("#clear").click();
    assert.equal(p.$("#q").value, "");
    assert.equal(p.$$(".card").length, RECIPES.length);
    assert.equal(p.$("#clear").hidden, true);
    assert.equal(p.d.activeElement, p.$("#q"));
  });

  test("?q= in the URL starts a search and beats the saved search", () => {
    const p = load({ search: "?q=orzo", session: { "cookbook-state": { q: "brisket" } } });
    assert.equal(p.$("#q").value, "orzo");
    assert.deepEqual([...p.ids()].sort(), expected("orzo"));
  });

  test('"/" focuses the search box on the list but not on a recipe', async () => {
    const p = load();
    p.key("/");
    assert.equal(p.d.activeElement, p.$("#q"));
    p.$("#q").blur();
    await p.go("#/japanese-curry");
    p.key("/");
    assert.notEqual(p.d.activeElement, p.$("#q"));
  });
});

// --- Filters ----------------------------------------------------------------------------------
describe("filters", () => {
  test("chips are built from the data", () => {
    const p = load();
    const labels = (g) => p.$$(`#${g} .chip`).map((c) => c.textContent);
    assert.deepEqual(new Set(labels("sectionChips")), new Set(["All", ...RECIPES.map((r) => r.section)]));
    assert.deepEqual(new Set(labels("proteinChips")), new Set(["All", "Chicken", "Pork", "Beef"]));
    assert.deepEqual(new Set(labels("tagChips")), new Set(["All", "Rice", "Pasta"]));
    for (const g of ["sectionChips", "proteinChips", "tagChips"]) {
      assert.equal(p.chip(g, "All").getAttribute("aria-pressed"), "true");
    }
  });

  const cases = [
    ["sectionChips", "Proteins", (r) => r.section === "Proteins"],
    ["sectionChips", "Meals", (r) => r.section === "Meals"],
    ["proteinChips", "Beef", (r) => r.protein === "beef"],
    ["proteinChips", "Pork", (r) => r.protein === "pork"],
    ["tagChips", "Rice", (r) => r.tags.includes("Rice")],
    ["tagChips", "Pasta", (r) => r.tags.includes("Pasta")],
  ];
  for (const [group, label, pred] of cases) {
    test(`${label} filter`, () => {
      const p = load();
      p.chip(group, label).click();
      assert.deepEqual([...p.ids()].sort(), RECIPES.filter(pred).map((r) => r.id).sort());
      assert.equal(p.chip(group, label).getAttribute("aria-pressed"), "true");
      assert.equal(p.chip(group, "All").getAttribute("aria-pressed"), "false");
      p.chip(group, "All").click();
      assert.equal(p.$$(".card").length, RECIPES.length);
    });
  }

  test("filters and search combine", async () => {
    const p = load();
    p.chip("sectionChips", "Meals").click();
    p.chip("proteinChips", "Chicken").click();
    p.chip("tagChips", "Rice").click();
    await p.search("honey");
    const want = RECIPES.filter((r) => r.section === "Meals" && r.protein === "chicken" && r.tags.includes("Rice")
      && norm(JSON.stringify(r)).includes("honey")).map((r) => r.id).sort();
    assert.ok(want.length > 0);
    assert.deepEqual([...p.ids()].sort(), want);
  });

  test("no matches without a query shows a generic message", () => {
    // Favorite only one chicken recipe, then filter to beef favorites: nothing left, but favorites exist.
    const chicken = RECIPES.find((r) => r.protein === "chicken").id;
    const local = { "cookbook-favorites": Object.fromEntries([...BASE_FAVS.map((id) => [id, false]), [chicken, true]]) };
    const p = load({ local });
    p.$("#favOnly").click();
    p.chip("proteinChips", "Beef").click();
    assert.equal(p.$$(".card").length, 0);
    assert.equal(p.$(".empty").textContent, "No recipes match these filters.");
  });
});

// --- Sorting ----------------------------------------------------------------------------------
describe("sorting", () => {
  const isBatch = (id) => byId(id).nutrition.per === "batch";
  const nonIncreasing = (xs) => xs.every((x, i) => i === 0 || xs[i - 1] >= x);

  test("A–Z", () => {
    const p = load();
    p.sort("title");
    assert.deepEqual(p.ids(), [...RECIPES].sort((a, b) => a.title.localeCompare(b.title)).map((r) => r.id));
  });

  test("calories low to high, batch-only recipes last", () => {
    const p = load();
    p.sort("cal-asc");
    const ids = p.ids();
    const cals = ids.filter((id) => !isBatch(id)).map((id) => -byId(id).nutrition.calories);
    assert.ok(nonIncreasing(cals));
    assert.ok(isBatch(ids.at(-1)));
  });

  test("protein high to low, batch-only recipes last", () => {
    const p = load();
    p.sort("pro-desc");
    const ids = p.ids();
    assert.ok(nonIncreasing(ids.filter((id) => !isBatch(id)).map((id) => byId(id).nutrition.protein)));
    assert.ok(isBatch(ids.at(-1)));
  });

  test("protein per calorie", () => {
    const p = load();
    p.sort("ratio");
    assert.ok(nonIncreasing(p.ids().map((id) => byId(id).nutrition.protein / byId(id).nutrition.calories)));
  });

  test("back to book order", () => {
    const p = load();
    p.sort("title");
    p.sort("book");
    assert.deepEqual(p.ids(), [...RECIPES].sort((a, b) => a.page - b.page).map((r) => r.id));
  });
});

// --- Remembered state -------------------------------------------------------------------------
describe("remembered list state", () => {
  test("search, filters, sort and favorites filter are saved for the session", async () => {
    const p = load();
    p.chip("proteinChips", "Beef").click();
    p.sort("pro-desc");
    p.$("#favOnly").click();
    await p.search("steak");
    assert.deepEqual(p.savedState(),
      { q: "steak", section: "All", protein: "Beef", tag: "All", sort: "pro-desc", favOnly: true });
  });

  test("saved state is restored on load", () => {
    const p = load({ session: { "cookbook-state": { q: "steak", protein: "Beef", tag: "Rice", sort: "title", favOnly: true } } });
    assert.equal(p.$("#q").value, "steak");
    assert.equal(p.$("#sort").value, "title");
    assert.equal(p.chip("proteinChips", "Beef").getAttribute("aria-pressed"), "true");
    assert.equal(p.chip("tagChips", "Rice").getAttribute("aria-pressed"), "true");
    assert.equal(p.$("#favOnly").getAttribute("aria-pressed"), "true");
    for (const id of p.ids()) {
      const r = byId(id);
      assert.ok(r.protein === "beef" && r.tags.includes("Rice") && r.favorite);
    }
  });

  test("corrupt saved state is ignored", () => {
    const p = load({ session: { "cookbook-state": "{not json" } });
    assert.equal(p.$$(".card").length, RECIPES.length);
  });
});

// --- Recipe page ------------------------------------------------------------------------------
describe("recipe page", () => {
  test("opens from a card link and shows the recipe", async () => {
    const p = load();
    await p.go(p.$('.card[href="#/japanese-curry"]').getAttribute("href"));
    const r = byId("japanese-curry");
    assert.equal(p.showing(), "detail");
    assert.equal(p.$("#list").hidden, true);
    assert.equal(p.$("#top").hidden, true);
    assert.equal(p.$("#detail h2").textContent, r.title);
    assert.equal(p.d.title, `${r.title} · Crockpot Recipes`);
    assert.match(p.$("#detail .meta").textContent, new RegExp(`Book page ${r.page}`));
    assert.equal(p.$(".serv-card b").textContent, String(r.servings));
    assert.match(p.$(".macro-card .cap").textContent, /per serving/);
  });

  test("opens directly from a #/id link", () => {
    const p = load({ hash: "#/pepper-steak" });
    assert.equal(p.showing(), "detail");
    assert.equal(p.$("#detail h2").textContent, byId("pepper-steak").title);
  });

  test("unknown ids fall back to the list", () => {
    const p = load({ hash: "#/not-a-recipe" });
    assert.equal(p.showing(), "list");
    assert.equal(p.d.title, "Crockpot Recipes");
  });

  test("shows grouped ingredients, numbered steps with bold call-outs, and notes", () => {
    const r = RECIPES.find((x) => x.notes.length && x.ingredients.some((g) => g.name)
      && x.instructions.some((s) => s.highlights.length));
    const p = load({ hash: "#/" + r.id });
    assert.deepEqual(p.$$(".group h4").map((h) => h.textContent), r.ingredients.filter((g) => g.name).map((g) => g.name));
    assert.deepEqual(p.$$(".ing li span").map((s) => s.textContent), r.ingredients.flatMap((g) => g.items));
    assert.equal(p.$$(".steps li").length, r.instructions.length);
    const step = r.instructions.findIndex((s) => s.highlights.length);
    const strong = p.$$(".steps li")[step].querySelectorAll("strong");
    assert.ok(strong.length > 0);
    for (const s of strong) assert.ok(r.instructions[step].highlights.includes(s.textContent));
    assert.deepEqual(p.$$(".notes p").map((n) => n.textContent), r.notes.map((n) => "* " + n));
  });

  test("batch-only recipes say so and have no serving count", () => {
    const r = RECIPES.find((x) => x.nutrition.per === "batch");
    const p = load({ hash: "#/" + r.id });
    assert.match(p.$(".macro-card .cap").textContent, /entire batch/);
    assert.equal(p.$(".serv-card b").textContent, "—");
    assert.match(p.$(".serv-card").textContent, /Not listed/);
  });

  test("back button and Escape return to the list", async () => {
    const p = load({ hash: "#/japanese-curry" });
    p.$("#back").click();
    await sleep(10);
    assert.equal(p.showing(), "list");
    assert.equal(p.d.title, "Crockpot Recipes");
    await p.go("#/japanese-curry");
    p.key("Escape");
    await sleep(10);
    assert.equal(p.showing(), "list");
  });

  test("returning to the list restores its scroll position", async () => {
    const p = load();
    Object.defineProperty(p.w, "scrollY", { value: 750, configurable: true });
    await p.go("#/japanese-curry");
    assert.equal(p.calls.scrollTo.at(-1), 0);
    await p.go("");
    assert.equal(p.calls.scrollTo.at(-1), 750);
  });

  test("ingredients and steps check off, and Clear checks resets them", () => {
    const p = load({ hash: "#/japanese-curry" });
    const li = p.$(".ing li");
    li.querySelector("span").click();
    assert.ok(li.classList.contains("done"));
    assert.equal(li.querySelector("input").checked, true);
    li.querySelector("span").click();
    assert.ok(!li.classList.contains("done"));
    li.querySelector("input").click(); // clicking the box itself toggles once
    assert.equal(li.querySelector("input").checked, true);
    assert.ok(li.classList.contains("done"));
    const step = p.$(".steps li");
    step.click();
    assert.ok(step.classList.contains("done"));
    p.$("#reset").click();
    assert.equal(p.$$(".done").length, 0);
    assert.equal(li.querySelector("input").checked, false);
  });

  test("Copy link copies the page URL", async () => {
    const p = load({ hash: "#/japanese-curry" });
    p.$("#copy").click();
    await sleep(0);
    assert.deepEqual(p.calls.clipboard, ["https://example.test/index.html#/japanese-curry"]);
    assert.equal(p.$("#copy").textContent, "Copied!");
  });

  test("Copy link explains what to do when the clipboard is blocked", async () => {
    const p = load({ hash: "#/japanese-curry", clipboard: async () => { throw new Error("denied"); } });
    p.$("#copy").click();
    await sleep(0);
    assert.equal(p.$("#copy").textContent, "Copy the address bar URL");
  });
});

// --- Favorites --------------------------------------------------------------------------------
describe("favorites", () => {
  const notFav = RECIPES.find((r) => !r.favorite).id;

  test("favorites from recipes.json are starred by default", () => {
    assert.ok(BASE_FAVS.length > 0, "expected some favorites in recipes.json");
    const p = load();
    for (const r of RECIPES) {
      const star = p.cardStar(r.id);
      assert.equal(star.getAttribute("aria-pressed"), String(r.favorite), r.id);
      assert.equal(star.textContent, r.favorite ? "★" : "☆");
      assert.equal(star.getAttribute("aria-label"), (r.favorite ? "Remove from" : "Add to") + " favorites");
    }
  });

  test("starring a card saves it locally without opening the recipe", async () => {
    const p = load();
    p.cardStar(notFav).click();
    await sleep(10);
    assert.equal(p.showing(), "list");
    assert.equal(p.w.location.hash, "");
    assert.equal(p.cardStar(notFav).textContent, "★");
    assert.equal(p.cardStar(notFav).getAttribute("aria-label"), "Remove from favorites");
    assert.deepEqual(p.favs(), { [notFav]: true });
    p.cardStar(notFav).click();
    assert.equal(p.cardStar(notFav).textContent, "☆");
    assert.deepEqual(p.favs(), {}, "only changes from recipes.json are stored");
  });

  test("a default favorite can be unstarred locally", () => {
    const p = load();
    p.cardStar(BASE_FAVS[0]).click();
    assert.deepEqual(p.favs(), { [BASE_FAVS[0]]: false });
    assert.equal(p.cardStar(BASE_FAVS[0]).getAttribute("aria-pressed"), "false");
  });

  test("local favorites take precedence over recipes.json on load", () => {
    const p = load({ local: { "cookbook-favorites": { [BASE_FAVS[0]]: false, [notFav]: true } } });
    assert.equal(p.cardStar(BASE_FAVS[0]).getAttribute("aria-pressed"), "false");
    assert.equal(p.cardStar(notFav).getAttribute("aria-pressed"), "true");
  });

  for (const bad of ["{not json", "null"]) {
    test(`unreadable local favorites (${bad}) fall back to recipes.json`, () => {
      const p = load({ local: { "cookbook-favorites": bad } });
      assert.equal(p.$$('.card-fav[aria-pressed="true"]').length, BASE_FAVS.length);
    });
  }

  test("starring still works when storage is unavailable", () => {
    const p = load({ storageThrows: true });
    p.cardStar(notFav).click();
    assert.equal(p.cardStar(notFav).textContent, "★");
  });

  test("Favorites filter shows only starred recipes", () => {
    const p = load({ local: { "cookbook-favorites": { [notFav]: true } } });
    const fav = p.$("#favOnly");
    assert.equal(fav.getAttribute("aria-pressed"), "false");
    fav.click();
    assert.equal(fav.getAttribute("aria-pressed"), "true");
    assert.deepEqual([...p.ids()].sort(), [...BASE_FAVS, notFav].sort());
    fav.click();
    assert.equal(p.$$(".card").length, RECIPES.length);
  });

  test("unstarring while filtering removes the card", () => {
    const p = load();
    p.$("#favOnly").click();
    p.cardStar(BASE_FAVS[0]).click();
    assert.ok(!p.ids().includes(BASE_FAVS[0]));
    assert.equal(p.$$(".card").length, BASE_FAVS.length - 1);
  });

  test("Favorites filter combines with search and other filters", async () => {
    const p = load();
    p.$("#favOnly").click();
    p.chip("proteinChips", "Beef").click();
    const beefFavs = BASE_FAVS.filter((id) => byId(id).protein === "beef");
    assert.deepEqual([...p.ids()].sort(), beefFavs.sort());
    await p.search("pepper");
    assert.ok(p.ids().every((id) => byId(id).favorite && norm(JSON.stringify(byId(id))).includes("pepper")));
  });

  test("empty favorites explain how to add one", () => {
    const local = { "cookbook-favorites": Object.fromEntries(BASE_FAVS.map((id) => [id, false])) };
    const p = load({ local });
    p.$("#favOnly").click();
    assert.equal(p.$(".empty").textContent, "No favorites yet. Tap the ☆ on a recipe to add it.");
  });

  test("favorites filtered out by other filters say so", async () => {
    const p = load();
    p.$("#favOnly").click();
    await p.search("zzzz");
    assert.equal(p.$(".empty").textContent, "No recipes match “zzzz”.");
  });

  test("the recipe page star toggles the favorite and the list reflects it", async () => {
    const p = load();
    await p.go("#/" + notFav);
    const star = p.$(".detail-fav");
    assert.equal(star.textContent, "☆");
    star.click();
    assert.equal(star.textContent, "★");
    assert.equal(star.getAttribute("aria-pressed"), "true");
    assert.equal(star.getAttribute("title"), "Remove from favorites");
    assert.deepEqual(p.favs(), { [notFav]: true });
    await p.go("");
    assert.equal(p.cardStar(notFav).textContent, "★");
  });

  test("the recipe page star shows default favorites", () => {
    const p = load({ hash: "#/" + BASE_FAVS[0] });
    assert.equal(p.$(".detail-fav").getAttribute("aria-pressed"), "true");
  });
});

// --- Print ------------------------------------------------------------------------------------
test("printing hides the header, navigation, tools and stars", () => {
  const print = CSS.slice(CSS.indexOf("@media print"));
  const hidden = print.slice(0, print.indexOf("{ display: none"));
  for (const sel of ["header.top", ".back", ".tools", ".hint", ".fav"]) assert.ok(hidden.includes(sel), sel);
});
