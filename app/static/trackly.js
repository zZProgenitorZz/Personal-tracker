// Navigatie via de hash (#library), zodat terugknop en verversen werken.
// Alle data en acties lopen via htmx; dit script regelt alleen de schil.

const PAGES = ["dashboard", "library", "progress", "settings"];

function currentPage() {
  const page = location.hash.slice(1);
  return PAGES.includes(page) ? page : "dashboard";
}

function showPage() {
  const page = currentPage();
  const main = document.getElementById("page");

  document.querySelectorAll("[data-page]").forEach((link) => {
    const active = link.dataset.page === page;
    link.classList.toggle("active", active);
    link.toggleAttribute("aria-current", active);
  });
  document.title = `${page[0].toUpperCase()}${page.slice(1)} · Progen`;

  htmx.ajax("GET", `/ui/${page}`, { target: "#page", swap: "innerHTML" }).then(() => {
    // Alleen bij navigeren animeren, niet bij het verversen na een actie.
    main.classList.add("entering");
    setTimeout(() => main.classList.remove("entering"), 800);
    window.scrollTo({ top: 0 });
  });
}

window.addEventListener("hashchange", showPage);
document.addEventListener("DOMContentLoaded", showPage);


// ---- Dialoog "Add series" ----

const dialog = () => document.getElementById("add-dialog");

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-open-add]")) {
    dialog().showModal();
  } else if (event.target.closest("[data-close]")) {
    dialog().close();
  } else if (event.target.closest("[data-close-dialog]")) {
    event.target.closest("dialog").close();
  } else if (event.target.tagName === "DIALOG") {
    event.target.close(); // klik op de achtergrond
  }
});

// De server stuurt dit event mee als een actie gelukt is.
document.addEventListener("reading-changed", () => {
  const d = dialog();
  if (d.open) {
    d.close();
    d.querySelector("form").reset();
    resetCover();
  }
  el("genre-dialog").close();
});

// "Edit genres" in het ⋯-menu laadt het formulier in #genre-editor; dan het venster openen.
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id === "genre-editor") el("genre-dialog").showModal();
});


// ---- Cover kiezen in het formulier ----
// Drie bronnen: zoeken bij AniList (htmx vult #cover-preview), een upload of
// een geplakte link. Er is altijd maar één bron actief. De server doet de echte
// controle en het opslaan; dit is alleen de preview.

const MAX_COVER_BYTES = 5 * 1024 * 1024;
const COVER_HINT = "Search for a cover by title, upload an image, or paste a link.";
const el = (id) => document.getElementById(id);
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

function resetCover() {
  resetGenres();
  clearSearch();
  el("cover-file").value = "";
  el("cover-link").value = "";
  el("cover-clear").hidden = true;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
  setCaption(COVER_HINT);
}

function coverTitle() {
  return el("series-title").value.trim() || "this series";
}

// Genres die bij een gevonden cover horen, alvast aanvinken in het formulier.
// Zelf aangevinkte of uitgezette genres blijven zoals jij ze zette; alleen de
// vorige suggestie wordt vervangen als je naar de volgende cover bladert.
function suggestGenres(genres) {
  for (const box of document.querySelectorAll("#add-dialog [name=genres]")) {
    if (box.dataset.touched) continue;
    box.checked = genres.includes(box.value);
    box.dataset.suggested = box.checked ? "1" : "";
  }
  const found = genres.length ? `Genres from this cover: ${genres.join(", ")}` : "No genres found for this cover";
  el("genre-hint").textContent = found + " · change them freely";
}

document.addEventListener("change", (event) => {
  if (event.target.matches("#add-dialog [name=genres]")) event.target.dataset.touched = "1";
});

function resetGenres() {
  for (const box of document.querySelectorAll("#add-dialog [name=genres]")) {
    delete box.dataset.touched;
    delete box.dataset.suggested;
  }
  el("genre-hint").textContent = "optional · found covers fill these in";
}

// Zoekresultaat geladen: deze telt.
function coverLoaded(img) {
  coverSkips = 0;
  const genres = (img && img.dataset.genres) ? img.dataset.genres.split(",") : [];
  suggestGenres(genres);
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
    htmx.ajax("GET", "/ui/covers/search", {
      target: "#cover-preview",
      values: { title: el("series-title").value, index: el("cover-index").value },
    });
  } else {
    clearSearch();
    setCaption("None of the covers found could be loaded. Try uploading one instead.", true);
  }
}

el("cover-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > MAX_COVER_BYTES) {
    event.target.value = "";
    setCaption("That file is larger than 5 MB. Pick a smaller image.", true);
    return;
  }
  el("cover-link").value = "";
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = URL.createObjectURL(file);
  showPreview(objectUrl, `Cover of ${coverTitle()}`);
  setCaption(`Uploading ${file.name} when you save.`);
});

el("cover-link").addEventListener("change", (event) => {
  const url = event.target.value.trim();
  if (!url) return resetCover();
  if (!/^https?:\/\//i.test(url)) {
    setCaption("Use a link that starts with http:// or https://", true);
    return;
  }
  el("cover-file").value = "";
  showPreview(url, `Cover of ${coverTitle()}`);
  setCaption("Downloading this image when you save.");
});

// Een andere titel betekent andere zoekresultaten: de oude vervallen.
el("series-title").addEventListener("input", () => {
  if (el("cover-preview").querySelector("[name=cover_url]")) {
    clearSearch();
    el("cover-clear").hidden = true;
    setCaption(COVER_HINT);
  }
});

el("cover-clear").addEventListener("click", resetCover);


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
  restore: {
    title: (date) => `Restore the backup from ${date}?`,
    body: "Your library goes back to how it was then. <strong>Anything you logged after that disappears from the app.</strong> Progen first saves a backup of how things are now, so you can always go back.",
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

document.getElementById("confirm-dialog").addEventListener("close", (event) => {
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
  document.getElementById("toasts").append(toast);
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
