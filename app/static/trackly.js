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
  }
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


// ---- Bevestigen voor iets onomkeerbaars ----
// Een knop met hx-confirm="<naam>" vraagt eerst om bevestiging in een eigen
// venster. Annuleren is de standaard: Enter of Escape verwijdert niets.

let confirmed = null;

document.addEventListener("htmx:confirm", (event) => {
  if (!event.detail.question) return;
  event.preventDefault();
  const d = document.getElementById("confirm-dialog");
  d.querySelector("[data-confirm-name]").textContent = event.detail.question;
  confirmed = () => event.detail.issueRequest(true);
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
