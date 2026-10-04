(function () {
  const DATA = JSON.parse(document.getElementById("recipe-data").textContent);
  const RECIPES = DATA.recipes;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const norm = (s) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[’']/g, "");

  const SITE_TITLE = "Crockpot Recipes";

  // Pre-compute searchable text per recipe.
  for (const r of RECIPES) {
    r._title = norm(r.title);
    r._ing = norm(r.ingredients.map((g) => (g.name || "") + " " + g.items.join(" ")).join(" "));
    r._all = r._title + " " + r._ing + " " + norm(r.instructions.map((s) => s.text).join(" ") + " " + r.notes.join(" "));
  }

  // Favorites: recipes.json sets the defaults; each browser keeps its own changes in localStorage
  // as {id: true|false}, storing only entries that differ from the default.
  const FAV_KEY = "cookbook-favorites";
  let favLocal = {};
  try { favLocal = JSON.parse(localStorage.getItem(FAV_KEY) || "{}") || {}; } catch (e) {}
  const isFav = (r) => r.id in favLocal ? favLocal[r.id] : !!r.favorite;
  function toggleFav(r) {
    const next = !isFav(r);
    if (next === !!r.favorite) delete favLocal[r.id]; else favLocal[r.id] = next;
    try { localStorage.setItem(FAV_KEY, JSON.stringify(favLocal)); } catch (e) {}
    return next;
  }
  const favButton = (r, cls) => {
    const on = isFav(r);
    return `<button class="${cls}" data-fav="${esc(r.id)}" aria-pressed="${on}" aria-label="${on ? "Remove from" : "Add to"} favorites" title="${on ? "Remove from" : "Add to"} favorites">${on ? "★" : "☆"}</button>`;
  };
  const syncFavButton = (btn, on) => {
    btn.setAttribute("aria-pressed", on);
    btn.setAttribute("aria-label", (on ? "Remove from" : "Add to") + " favorites");
    btn.title = btn.getAttribute("aria-label");
    btn.firstChild.textContent = on ? "★" : "☆";
  };

  const state = { q: "", section: "All", protein: "All", tag: "All", sort: "book", favOnly: false };
  try { Object.assign(state, JSON.parse(sessionStorage.getItem("cookbook-state") || "{}")); } catch (e) {}
  const urlQ = new URLSearchParams(location.search).get("q");  // shareable searches: page.html?q=brisket
  if (urlQ !== null) state.q = urlQ;

  function chips(el, key, options) {
    el.innerHTML = options.map((o) => `<button class="chip" data-v="${esc(o)}" aria-pressed="${state[key] === o}">${esc(o)}</button>`).join("");
    el.onclick = (e) => {
      const b = e.target.closest(".chip"); if (!b) return;
      state[key] = b.dataset.v;
      el.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", c === b));
      renderList();
    };
  }
  const cap = (s) => s[0].toUpperCase() + s.slice(1);
  chips($("sectionChips"), "section", ["All", ...new Set(RECIPES.map((r) => r.section))]);
  chips($("proteinChips"), "protein", ["All", ...[...new Set(RECIPES.map((r) => r.protein).filter(Boolean))].map(cap)]);
  chips($("tagChips"), "tag", ["All", ...new Set(RECIPES.flatMap((r) => r.tags))]);
  // Badges shared by cards and the detail view.
  const badges = (r) => `<span class="tag">${esc(r.section)}</span>`
    + (r.protein ? `<span class="tag">${cap(r.protein)}</span>` : "")
    + r.tags.map((t) => `<span class="tag base">${esc(t)}</span>`).join("");

  function macroGrid(n) {
    return `<div class="m cal"><b>${n.calories}</b><span>Cal</span></div>
      <div class="m pro"><b>${n.protein}g</b><span>Protein</span></div>
      <div class="m carb"><b>${n.carbs}g</b><span>Carbs</span></div>
      <div class="m fat"><b>${n.fat}g</b><span>Fat</span></div>`;
  }
  const servingsText = (r) => r.nutrition.per === "batch" ? "Macros for the entire batch" : `Per serving · makes ${r.servings}`;

  function highlight(text, terms) {
    let out = esc(text);
    for (const t of terms) {
      if (t.length < 2) continue;
      const re = new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig");
      out = out.replace(re, "<mark>$1</mark>");
    }
    return out;
  }

  function filtered() {
    const terms = norm(state.q).split(/\s+/).filter(Boolean);
    let list = RECIPES.filter((r) =>
      (state.section === "All" || r.section === state.section) &&
      (state.protein === "All" || r.protein === state.protein.toLowerCase()) &&
      (state.tag === "All" || r.tags.includes(state.tag)) &&
      (!state.favOnly || isFav(r)) &&
      terms.every((t) => r._all.includes(t)));
    const score = (r) => terms.reduce((s, t) => s + (r._title.includes(t) ? 2 : r._ing.includes(t) ? 1 : 0), 0);
    const per = (r) => r.nutrition.per === "batch" ? null : r.nutrition; // batch macros sort last
    const sorters = {
      book: (a, b) => (terms.length ? score(b) - score(a) : 0) || a.page - b.page,
      title: (a, b) => a.title.localeCompare(b.title),
      "cal-asc": (a, b) => (per(a)?.calories ?? 1e9) - (per(b)?.calories ?? 1e9),
      "pro-desc": (a, b) => (per(b)?.protein ?? -1) - (per(a)?.protein ?? -1),
      ratio: (a, b) => (b.nutrition.protein / b.nutrition.calories) - (a.nutrition.protein / a.nutrition.calories),
    };
    return { list: list.sort(sorters[state.sort]), terms };
  }

  function renderList() {
    const { list, terms } = filtered();
    try { sessionStorage.setItem("cookbook-state", JSON.stringify(state)); } catch (e) {}
    $("clear").hidden = !state.q;
    $("count").textContent = `${list.length} of ${RECIPES.length} recipes`;
    $("grid").innerHTML = list.length ? list.map((r) => {
      const matchIng = terms.length && !terms.every((t) => r._title.includes(t))
        ? r.ingredients.flatMap((g) => g.items).filter((i) => terms.some((t) => norm(i).includes(t))).slice(0, 2)
        : [];
      return `<div class="card-wrap">${favButton(r, "fav card-fav")}<a class="card" href="#/${r.id}">
        <h3>${highlight(r.title, terms)}</h3>
        <div class="meta">${badges(r)}<span class="tag">p. ${r.page}</span></div>
        ${matchIng.length ? `<div class="per">${matchIng.map((i) => highlight(i, terms)).join(" · ")}</div>` : ""}
        <div class="macros">${macroGrid(r.nutrition)}</div>
        <div class="per">${servingsText(r)}</div>
      </a></div>`;
    }).join("") : `<div class="empty">${state.favOnly && !RECIPES.some(isFav)
      ? "No favorites yet. Tap the ☆ on a recipe to add it."
      : state.q ? `No recipes match “${esc(state.q)}”.` : "No recipes match these filters."}</div>`;
  }

  function stepHtml(step) {
    // Bold the ingredient call-outs from the book, longest first, without overlapping.
    let html = esc(step.text);
    const hs = [...step.highlights].sort((a, b) => b.length - a.length);
    const marks = [];
    for (const h of hs) {
      const eh = esc(h);
      const i = html.indexOf(eh);
      if (i < 0 || marks.some(([s, e]) => i < e && i + eh.length > s)) continue;
      marks.push([i, i + eh.length]);
    }
    marks.sort((a, b) => b[0] - a[0]);
    for (const [s, e] of marks) html = html.slice(0, s) + "<strong>" + html.slice(s, e) + "</strong>" + html.slice(e);
    return html;
  }

  function renderDetail(r) {
    const d = $("detail");
    d.innerHTML = `
      <button class="back" id="back">← All recipes</button>
      <div class="titlerow"><h2>${esc(r.title)}</h2>${favButton(r, "fav detail-fav")}</div>
      <div class="meta">${badges(r)}<span class="tag">Book page ${r.page}</span>
        <div class="tools"><button class="tool" id="reset">Clear checks</button><button class="tool" id="copy">Copy link</button></div></div>
      <div class="band">
        <div class="stat serv-card"><span class="cap">Servings</span>
          <div class="m"><b>${r.servings ?? "—"}</b>${r.servings ? "" : "<span>Not listed</span>"}</div></div>
        <div class="stat macro-card"><span class="cap">${r.nutrition.per === "batch" ? "Macros · entire batch" : "Macros · per serving"}</span>
          <div class="cells">${macroGrid(r.nutrition)}</div></div>
      </div>
      <div class="cols">
        <div class="panel">
          <h3>Ingredients</h3>
          ${r.ingredients.map((g) => `<div class="group">${g.name ? `<h4>${esc(g.name)}</h4>` : ""}
            <ul class="ing">${g.items.map((i) => `<li><input type="checkbox" tabindex="-1"><span>${esc(i)}</span></li>`).join("")}</ul></div>`).join("")}
          <p class="hint">Tap items to check them off.</p>
        </div>
        <div class="panel">
          <h3>Instructions</h3>
          <ol class="steps">${r.instructions.map((s) => `<li><div>${stepHtml(s)}</div></li>`).join("")}</ol>
          ${r.notes.length ? `<div class="notes">${r.notes.map((n) => `<p>* ${esc(n)}</p>`).join("")}</div>` : ""}
        </div>
      </div>`;
    d.querySelectorAll(".ing li").forEach((li) => li.addEventListener("click", (e) => {
      const cb = li.querySelector("input");
      if (e.target !== cb) cb.checked = !cb.checked;
      li.classList.toggle("done", cb.checked);
    }));
    d.querySelectorAll(".steps li").forEach((li) => li.addEventListener("click", () => li.classList.toggle("done")));
    d.querySelector(".detail-fav").onclick = (e) => syncFavButton(e.currentTarget, toggleFav(r));
    $("back").onclick = () => { location.hash = ""; };
    $("reset").onclick = () => d.querySelectorAll(".done").forEach((el) => {
      el.classList.remove("done"); const cb = el.querySelector("input"); if (cb) cb.checked = false;
    });
    $("copy").onclick = async (e) => {
      const btn = e.currentTarget;
      try { await navigator.clipboard.writeText(location.href); btn.textContent = "Copied!"; }
      catch (err) { btn.textContent = "Copy the address bar URL"; }
      setTimeout(() => (btn.textContent = "Copy link"), 1800);
    };
    document.title = r.title + " · " + SITE_TITLE;
  }

  let listScroll = 0;
  function route() {
    const id = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
    const r = id && RECIPES.find((x) => x.id === id);
    if (r) {
      if (!$("list").hidden) listScroll = window.scrollY;
      $("list").hidden = true; $("top").hidden = true; $("detail").hidden = false;
      renderDetail(r);
      window.scrollTo(0, 0);
    } else {
      $("detail").hidden = true; $("list").hidden = false; $("top").hidden = false;
      renderList();  // favorites may have changed on the recipe page
      document.title = SITE_TITLE;
      window.scrollTo(0, listScroll);
    }
  }

  const q = $("q");
  q.value = state.q;
  let t;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value; renderList(); }, 80); });
  $("clear").onclick = () => { q.value = ""; state.q = ""; renderList(); q.focus(); };
  $("grid").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-fav]"); if (!btn) return;
    const r = RECIPES.find((x) => x.id === btn.dataset.fav);
    const on = toggleFav(r);
    if (state.favOnly && !on) renderList(); else syncFavButton(btn, on);
  });
  const favOnly = $("favOnly");
  favOnly.setAttribute("aria-pressed", state.favOnly);
  favOnly.onclick = () => { state.favOnly = !state.favOnly; favOnly.setAttribute("aria-pressed", state.favOnly); renderList(); };
  $("sort").value = state.sort;
  $("sort").onchange = (e) => { state.sort = e.target.value; renderList(); };
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== q && !$("list").hidden) { e.preventDefault(); q.focus(); }
    if (e.key === "Escape" && !$("detail").hidden) location.hash = "";
  });
  window.addEventListener("hashchange", route);
  renderList();
  route();
})();
