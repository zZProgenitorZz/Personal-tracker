// De schil van Progen: navigatie, vensters, coverkiezer en toasts.
// Alle data en acties lopen via htmx; dit script regelt alleen wat daaromheen zit.

const el = (id) => document.getElementById(id);


// ---- Navigatie via de hash, zodat terugknop en verversen werken ----
// #home, #settings, en per tracker #<tracker>, #<tracker>/library, #<tracker>/progress.

// Per tracker: de tekst van de knop "Add ..." (null = geen knop) en de tabbladen.
const TRACKERS = {
  reading: { add: "Add series", tabs: ["dashboard", "library", "progress"] },
  watching: { add: "Add title", tabs: ["dashboard", "library", "progress"] },
  listening: { add: null, tabs: ["dashboard", "history", "progress"] },  // plays komen uit Spotify
};
const OLD_ADDRESSES = { dashboard: "reading", library: "reading/library", progress: "reading/progress" };

function currentRoute() {
  let hash = location.hash.slice(1);
  hash = OLD_ADDRESSES[hash] || hash;  // bladwijzers van vóór het startscherm blijven werken
  const [section, tab = "dashboard"] = hash.split("/");
  if (TRACKERS[section] && TRACKERS[section].tabs.includes(tab)) return { section, tab, url: `/ui/${section}/${tab}` };
  if (section === "settings") return { section, url: "/ui/settings" };
  return { section: "home", url: "/ui/home" };
}

function currentTracker() {
  const { section } = currentRoute();
  return TRACKERS[section] && TRACKERS[section].add ? section : null;
}

function showPage() {
  const route = currentRoute();
  const main = el("page");

  document.querySelectorAll("[data-page]").forEach((link) => {
    const active = link.dataset.page === route.section;
    link.classList.toggle("active", active);
    link.toggleAttribute("aria-current", active);
  });

  // De knop "Add ..." hoort bij de tracker waar je bent.
  const tracker = TRACKERS[route.section];
  document.querySelector(".nav-add").hidden = !(tracker && tracker.add);
  if (tracker && tracker.add) el("nav-add-label").textContent = tracker.add;

  const name = route.section[0].toUpperCase() + route.section.slice(1);
  document.title = `${name}${route.tab && route.tab !== "dashboard" ? " · " + route.tab : ""} · Progen`;

  htmx.ajax("GET", route.url, { target: "#page", swap: "innerHTML" }).then(() => {
    // Alleen bij navigeren animeren, niet bij het verversen na een actie.
    main.classList.add("entering");
    setTimeout(() => main.classList.remove("entering"), 800);
    window.scrollTo({ top: 0 });
  });
}

window.addEventListener("hashchange", showPage);
document.addEventListener("DOMContentLoaded", showPage);


// ---- Vensters ----

function openAddForm() {
  const tracker = currentTracker();
  if (!tracker) return;
  htmx.ajax("GET", `/ui/${tracker}/add-form`, { target: "#add-form-slot", swap: "innerHTML" })
    .then(() => {
      el("add-dialog").showModal();
      el("item-title").focus();
    });
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-open-add]")) {
    openAddForm();
  } else if (event.target.closest("[data-close-dialog]")) {
    event.target.closest("dialog").close();
  } else if (event.target.tagName === "DIALOG") {
    event.target.close(); // klik op de achtergrond
  }
});

// De server stuurt een van deze events mee als een actie gelukt is.
for (const changed of ["reading-changed", "watching-changed"]) {  // listening heeft geen formulier
  document.addEventListener(changed, () => {
    if (el("add-dialog").open) {
      el("add-dialog").close();
      resetObjectUrl();
    }
    el("genre-dialog").close();
  });
}

// "Edit genres" in het ⋯-menu laadt het formulier in #genre-editor; dan het venster openen.
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id === "genre-editor") el("genre-dialog").showModal();
});


// ---- Cover kiezen in het formulier ----
// Drie bronnen: zoeken (htmx vult #cover-preview), een upload of een geplakte
// link. Er is altijd maar één bron actief. De server doet de echte controle en
// het opslaan; dit is alleen de preview. Het formulier wordt steeds opnieuw
// geladen, daarom luisteren we op document in plaats van op de velden zelf.

const MAX_COVER_BYTES = 5 * 1024 * 1024;
const COVER_HINT = "Search for a cover by title, upload an image, or paste a link.";
const GENRE_HINT = "optional · found covers fill these in";
let coverSkips = 0;   // hoeveel zoekresultaten op rij niet laadden
let objectUrl = null;

function setCaption(text, isError = false) {
  const caption = el("cover-caption");
  caption.textContent = text;
  caption.classList.toggle("is-error", isError);
}

function showPreview(src, alt) {
  const img = document.createElement("img");
  img.className = "cover-img";
  img.alt = alt;
  img.onerror = () => {
    img.remove();
    setCaption("No preview for this link. Progen will still try to download it when you save.", true);
  };
  img.src = src;
  el("cover-preview").replaceChildren(img);
  el("cover-clear").hidden = false;
}

function clearSearch() {
  el("cover-preview").replaceChildren();
}

function resetObjectUrl() {
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
}

function resetCover() {
  resetGenres();
  clearSearch();
  el("cover-file").value = "";
  el("cover-link").value = "";
  el("cover-clear").hidden = true;
  resetObjectUrl();
  setCaption(COVER_HINT);
}

function coverTitle() {
  return el("item-title").value.trim() || "this title";
}

// Genres die bij een gevonden cover horen, alvast aanvinken in het formulier.
// Zelf aangevinkte of uitgezette genres blijven zoals jij ze zette; alleen de
// vorige suggestie wordt vervangen als je naar de volgende cover bladert.
function suggestGenres(genres) {
  for (const box of document.querySelectorAll("#add-dialog [name=genres]")) {
    if (box.dataset.touched) continue;
    box.checked = genres.includes(box.value);
  }
  const found = genres.length ? `Genres from this cover: ${genres.join(", ")}` : "No genres found for this cover";
  el("genre-hint").textContent = found + " · change them freely";
}

function resetGenres() {
  for (const box of document.querySelectorAll("#add-dialog [name=genres]")) delete box.dataset.touched;
  el("genre-hint").textContent = GENRE_HINT;
}

// Zoekresultaat geladen: deze telt.
function coverLoaded(img) {
  coverSkips = 0;
  suggestGenres(img && img.dataset.genres ? img.dataset.genres.split(",") : []);
  el("cover-file").value = "";
  el("cover-link").value = "";
  el("cover-clear").hidden = false;
}

// Zoekresultaat laadt niet: sla hem over, maar ga hooguit één ronde rond.
function coverFailed(img) {
  const total = Number(img.dataset.coverTotal);
  img.remove();
  if (coverSkips < total - 1) {
    coverSkips += 1;
    htmx.ajax("GET", el("find-cover").getAttribute("hx-get"), {
      target: "#cover-preview",
      values: { title: el("item-title").value, index: el("cover-index").value },
    });
  } else {
    clearSearch();
    setCaption("None of the covers found could be loaded. Try uploading one instead.", true);
  }
}

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target.matches("#add-dialog [name=genres]")) {
    target.dataset.touched = "1";
  } else if (target.id === "cover-file") {
    const file = target.files[0];
    if (!file) return;
    if (file.size > MAX_COVER_BYTES) {
      target.value = "";
      setCaption("That file is larger than 5 MB. Pick a smaller image.", true);
      return;
    }
    el("cover-link").value = "";
    resetObjectUrl();
    objectUrl = URL.createObjectURL(file);
    showPreview(objectUrl, `Cover of ${coverTitle()}`);
    setCaption(`Uploading ${file.name} when you save.`);
  } else if (target.id === "cover-link") {
    const url = target.value.trim();
    if (!url) return resetCover();
    if (!/^https?:\/\//i.test(url)) {
      setCaption("Use a link that starts with http:// or https://", true);
      return;
    }
    el("cover-file").value = "";
    showPreview(url, `Cover of ${coverTitle()}`);
    setCaption("Downloading this image when you save.");
  }
});

// Een andere titel betekent andere zoekresultaten: de oude vervallen.
document.addEventListener("input", (event) => {
  if (event.target.id === "item-title" && el("cover-preview").querySelector("[name=cover_url]")) {
    clearSearch();
    el("cover-clear").hidden = true;
    setCaption(COVER_HINT);
  }
});

document.addEventListener("click", (event) => {
  if (event.target.id === "cover-clear") resetCover();
});


// ---- Optiemenu op een kaart ----

function closeMenus(except) {
  document.querySelectorAll("details.card-menu[open]").forEach((menu) => {
    if (menu !== except) menu.removeAttribute("open");
  });
}

document.addEventListener("click", (event) => {
  const menu = event.target.closest("details.card-menu");
  closeMenus(event.target.closest(".menu-item") ? null : menu);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeMenus(null);
});


// ---- Bevestigen voor iets ingrijpends ----
// Een knop met hx-confirm="<naam>" vraagt eerst om bevestiging in een eigen
// venster; data-confirm-kind kiest de tekst. Annuleren is de standaard:
// Enter of Escape doet niets.

const CONFIRM_TEXTS = {
  remove: {
    title: (name) => `Remove ${name}?`,
    body: "The series leaves your library, <strong>including its progress</strong>. You can add it again later, but it starts over as a new series. Chapters you've read still count in your stats.",
    action: "Remove",
  },
  "remove-show": {
    title: (name) => `Remove ${name}?`,
    body: "It leaves your list. You can add it again later. Titles you finished still count in your stats.",
    action: "Remove",
  },
  restore: {
    title: (date) => `Restore the backup from ${date}?`,
    body: "All your trackers go back to how they were then. <strong>Anything you logged after that disappears from the app.</strong> Progen first saves a backup of how things are now, so you can always go back.",
    action: "Restore",
  },
};

let confirmed = null;

document.addEventListener("htmx:confirm", (event) => {
  if (!event.detail.question) return;
  event.preventDefault();
  const text = CONFIRM_TEXTS[event.detail.elt.dataset.confirmKind] || CONFIRM_TEXTS.remove;
  el("confirm-title").textContent = text.title(event.detail.question);
  el("confirm-body").innerHTML = text.body;  // vaste tekst hierboven, geen invoer van buiten
  el("confirm-action").textContent = text.action;
  confirmed = () => event.detail.issueRequest(true);
  const d = el("confirm-dialog");
  d.returnValue = "";
  d.showModal();
});

el("confirm-dialog").addEventListener("close", (event) => {
  if (event.target.returnValue === "confirm" && confirmed) confirmed();
  confirmed = null;
});


// ---- Toasts ----

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "toast toast-error";
  toast.setAttribute("role", "alert");
  toast.innerHTML = '<span class="toast-icon">!</span><span></span>';
  toast.lastElementChild.textContent = message;
  el("toasts").append(toast);
}

document.addEventListener("animationend", (event) => {
  if (event.animationName === "toast-out") event.target.remove();
});

document.addEventListener("htmx:responseError", (event) => {
  showToast(`Something went wrong (${event.detail.xhr.status})`);
});

document.addEventListener("htmx:sendError", () => {
  showToast("Can't reach the server. Is it still running?");
});
