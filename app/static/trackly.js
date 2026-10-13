// Progen's shell: navigation, dialogs, cover picker and toasts.
// All data and actions go through htmx; this script only handles what surrounds them.

const el = (id) => document.getElementById(id);


// ---- Navigation through the hash, so the back button and refreshing work ----
// #home, #settings, #wrapped(/year), and per tracker #<tracker>, #<tracker>/library, #<tracker>/progress.

// Per tracker: the text of the "Add ..." button (null = no button) and the tabs.
const TRACKERS = {
  reading: { add: "Add series", tabs: ["dashboard", "library", "progress"] },
  watching: { add: "Add title", tabs: ["dashboard", "library", "progress"] },
  listening: { add: null, tabs: ["dashboard", "history", "progress"] },  // plays come from Spotify
  planner: { add: null, tabs: ["week", "upcoming", "day", "someday"] },  // entry goes through the field at the top
};
const OLD_ADDRESSES = { dashboard: "reading", library: "reading/library", progress: "reading/progress" };

function currentRoute() {
  let hash = location.hash.slice(1);
  hash = OLD_ADDRESSES[hash] || hash;  // bookmarks from before the home screen keep working
  const [section, given] = hash.split("/");
  const tab = given || (TRACKERS[section] ? TRACKERS[section].tabs[0] : "");  // without a tab: the first one
  if (TRACKERS[section] && TRACKERS[section].tabs.includes(tab)) return { section, tab, url: `/ui/${section}/${tab}` };
  if (section === "settings") return { section, url: "/ui/settings" };
  if (section === "wrapped") {  // #wrapped, #wrapped/2025, #wrapped/2025/compare
    const [, year = "", extra = ""] = hash.split("/");
    return { section, url: `/ui/wrapped?year=${encodeURIComponent(year)}${extra === "compare" ? "&compare=1" : ""}` };
  }
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

  // The "Add ..." button belongs to the tracker you are on.
  const tracker = TRACKERS[route.section];
  document.querySelector(".nav-add").hidden = !(tracker && tracker.add);
  if (tracker && tracker.add) el("nav-add-label").textContent = tracker.add;

  const onSettings = route.section === "settings";  // Sunny is everywhere except on Settings
  el("sunny-fab").hidden = onSettings;
  if (onSettings) closeSunny();

  const name = route.section[0].toUpperCase() + route.section.slice(1);
  document.title = `${name}${route.tab && route.tab !== tracker.tabs[0] ? " · " + route.tab : ""} · Progen`;

  htmx.ajax("GET", route.url, { target: "#page", swap: "innerHTML" }).then(() => {
    // Only animate when navigating, not when refreshing after an action.
    main.classList.add("entering");
    setTimeout(() => main.classList.remove("entering"), 800);
    window.scrollTo({ top: 0 });
  });
}

window.addEventListener("hashchange", showPage);
document.addEventListener("DOMContentLoaded", showPage);


// ---- Dialogs ----

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
    event.target.close(); // click on the backdrop
  }
});

// The server sends one of these events along when an action succeeded.
for (const changed of ["reading-changed", "watching-changed", "planner-changed"]) {  // listening has no form
  document.addEventListener(changed, () => {
    if (el("add-dialog").open) {
      el("add-dialog").close();
      resetObjectUrl();
    }
    el("genre-dialog").close();
    el("pick-dialog").close();  // after Start
    el("plan-dialog").close();  // after an action in a plan
  });
}

// "Edit genres" in the ⋯ menu loads the form into #genre-editor; then open the dialog.
document.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target.id === "genre-editor") el("genre-dialog").showModal();
  if (event.detail.target.id === "pick-slot" && !el("pick-dialog").open) el("pick-dialog").showModal();
  if (event.detail.target.id === "plan-slot" && !el("plan-dialog").open) el("plan-dialog").showModal();
});


// ---- Choosing a cover in the form ----
// Three sources: search (htmx fills #cover-preview), an upload or a pasted
// link. Only one source is ever active. The server does the real check and
// the saving; this is only the preview. The form is loaded again and again,
// so we listen on document instead of on the fields themselves.

const MAX_COVER_BYTES = 5 * 1024 * 1024;
const COVER_HINT = "Search for a cover by title, upload an image, or paste a link.";
const GENRE_HINT = "optional · found covers fill these in";
let coverSkips = 0;   // how many search results in a row didn't load
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

// Checks the genres that go with a found cover in the form ahead of time.
// Genres you checked or unchecked yourself stay as you set them; only the
// previous suggestion is replaced when you browse to the next cover.
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

// Search result loaded: this one counts.
function coverLoaded(img) {
  coverSkips = 0;
  suggestGenres(img && img.dataset.genres ? img.dataset.genres.split(",") : []);
  el("cover-file").value = "";
  el("cover-link").value = "";
  el("cover-clear").hidden = false;
}

// Search result doesn't load: skip it, but go around at most once.
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

// Another title means other search results: the old ones expire.
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


// ---- Options menu on a card ----

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


// ---- Confirming something drastic ----
// A button with hx-confirm="<name>" first asks for confirmation in a dialog of
// its own; data-confirm-kind picks the text. Cancel is the default:
// Enter or Escape does nothing.

const CONFIRM_TEXTS = {
  remove: {
    title: (name) => `Remove ${name}?`,
    body: "The series leaves your library, <strong>including its progress</strong>. You can add it again later, but it starts over as a new series. Chapters you've read still count in your stats.",
    action: "Remove",
  },
  "sunny-delete": {
    title: (name) => `Delete ${name}?`,
    body: "Sunny proposed to delete this from your tracker. <strong>This can't be undone from the app.</strong>",
    action: "Delete",
  },
  "remove-plan": {
    title: (name) => `Remove ${name}?`,
    body: "It leaves your planner, including every time it repeats. Plans you sent to your iPhone stay there.",
    action: "Remove",
  },
  "remove-show": {
    title: (name) => `Remove ${name}?`,
    body: "It leaves your list. You can add it again later. Titles you finished still count in your stats.",
    action: "Remove",
  },
  drop: {
    title: (name) => `Drop ${name}?`,
    body: "It moves to Dropped. Nothing is deleted: your progress stays, and you can pick it up again anytime.",
    action: "Drop",
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
  el("confirm-body").innerHTML = text.body;  // fixed text above, no input from outside
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


// ---- Gathering dust: "Not now" ----
// Hides a title only in this view: no event, nothing saved. The dashboard
// refreshes after every action; this list keeps the hidden titles hidden until you reload the page.

const dustHidden = new Set();

function hideDust(root) {
  for (const row of root.querySelectorAll(".dust-row")) {
    if (dustHidden.has(row.dataset.dustId)) row.hidden = true;
  }
  for (const panel of root.querySelectorAll(".dust")) {
    panel.hidden = !panel.querySelector(".dust-row:not([hidden])");
    const more = panel.querySelector(".dust-more");
    if (more) more.hidden = !more.querySelector(".dust-row:not([hidden])");
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-dust-dismiss]");
  if (!button) return;
  dustHidden.add(button.closest(".dust-row").dataset.dustId);
  hideDust(document);
});

document.addEventListener("htmx:afterSwap", () => hideDust(document));


// ---- Planner: after "Add" you can type the next plan right away ----
// The page refreshes after every change; if the cursor was in the input field, it comes back.

let planTextHadFocus = false;
document.addEventListener("planner-changed", () => {
  planTextHadFocus = document.activeElement && document.activeElement.id === "plan-text";
});
document.addEventListener("htmx:afterSwap", () => {
  if (planTextHadFocus && el("plan-text")) {
    el("plan-text").focus();
    planTextHadFocus = false;
  }
});


// ---- Planner · Upcoming: searching the list (only in the browser) ----
// Hides plans that don't match, and groups that become empty because of it. Stays after a refresh.

let upcomingQuery = "";

function filterUpcoming() {
  const field = document.querySelector("[data-upcoming-search]");
  if (!field) return;
  if (field.value !== upcomingQuery) field.value = upcomingQuery;
  const words = upcomingQuery.toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0;
  for (const row of document.querySelectorAll("[data-search]")) {
    const match = words.every((w) => row.dataset.search.includes(w));
    row.closest("li").hidden = !match;
    shown += match;
  }
  for (const group of document.querySelectorAll(".upcoming-group")) {
    group.hidden = !group.querySelector("li:not([hidden])");
  }
  const none = document.querySelector(".upcoming-nomatch");
  if (none) none.hidden = shown > 0 || !words.length;
}

document.addEventListener("input", (event) => {
  if (!event.target.matches("[data-upcoming-search]")) return;
  upcomingQuery = event.target.value;
  filterUpcoming();
});
document.addEventListener("htmx:afterSwap", filterUpcoming);


// ---- Sunny: the panel ----
// The conversation lives on the server (in memory), so it stays when you switch pages.

function openSunny() {
  const panel = el("sunny-panel");
  panel.classList.add("is-open");
  panel.removeAttribute("inert");
  panel.setAttribute("aria-hidden", "false");
  el("sunny-fab").setAttribute("aria-expanded", "true");
  scrollSunny();
  setTimeout(() => el("sunny-input") && el("sunny-input").focus(), 250);
}

function closeSunny() {
  const panel = el("sunny-panel");
  if (!panel.classList.contains("is-open")) return;
  panel.classList.remove("is-open");
  panel.setAttribute("inert", "");
  panel.setAttribute("aria-hidden", "true");
  el("sunny-fab").setAttribute("aria-expanded", "false");
}

function scrollSunny() {
  const log = el("sunny-log");
  if (log) log.scrollTop = log.scrollHeight;
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-sunny-open]")) {
    el("sunny-panel").classList.contains("is-open") ? closeSunny() : openSunny();
  } else if (event.target.closest("[data-sunny-close]")) {
    closeSunny();
  }
});

document.addEventListener("keydown", (event) => {
  // Esc closes the panel, unless a dialog is open: that closes first.
  if (event.key === "Escape" && !document.querySelector("dialog[open]")) closeSunny();
});

// ---- Sunny: sending and the streamed answer ----
// The form goes through fetch and reads /ui/assistant/stream line by line (NDJSON): text pieces,
// status lines while a tool runs, and last the rendered entries. The input is cleared and keeps
// focus at once; while an answer streams, Enter waits (the typed text stays for later).

let sunnyBusy = false;

function sunnyAtBottom() {
  const log = el("sunny-log");
  return !log || log.scrollHeight - log.scrollTop - log.clientHeight < 40;
}

function sunnyBusyState(busy) {
  sunnyBusy = busy;
  el("sunny-form")?.classList.toggle("is-busy", busy);
}

function fromTemplate(id) {
  return el(id).content.firstElementChild.cloneNode(true);
}

document.addEventListener("submit", (event) => {
  if (event.target.id !== "sunny-form") return;
  event.preventDefault();
  if (sunnyBusy) return;  // one answer at a time; the text stays in the field
  const input = el("sunny-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  input.focus();
  const bubble = document.createElement("div");
  bubble.className = "sunny-msg from-user";
  bubble.textContent = text;
  el("sunny-log").append(bubble);
  scrollSunny();
  askSunny(text, false);
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-sunny-retry]");
  if (!button || sunnyBusy) return;
  // "fresh": the connection dropped before Progen kept the message, so it goes as a new one.
  askSunny(button.dataset.sunnyRetry, !button.dataset.sunnyFresh);
  el("sunny-input")?.focus();
});

async function askSunny(text, retry) {
  const bubble = fromTemplate("sunny-thinking");
  el("sunny-log").append(bubble);
  scrollSunny();
  sunnyBusyState(true);
  let finished = false;
  try {
    const response = await fetch("/ui/assistant/stream", {
      method: "POST",
      headers: { "HX-Request": "true" },  // a change: the CSRF check wants to see it comes from the page
      body: new URLSearchParams({ message: text, retry: retry ? "1" : "" }),
    });
    if (response.status === 401) {  // signed out (through your phone): to the login page
      finished = true;
      location.href = response.headers.get("HX-Redirect") || "/login";
      return;
    }
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end).trim();
        buffer = buffer.slice(end + 1);
        if (line) finished = sunnyLine(JSON.parse(line), bubble) || finished;
      }
    }
    if (!finished) throw new Error("the answer stopped halfway");
  } catch {
    if (!finished) {
      const problem = fromTemplate("sunny-offline");
      const again = problem.querySelector("[data-sunny-retry]");
      again.dataset.sunnyRetry = text;
      again.dataset.sunnyFresh = "1";
      bubble.replaceWith(problem);
      scrollSunny();
    }
  } finally {
    sunnyBusyState(false);
  }
}

function sunnyLine(message, bubble) {
  const follow = sunnyAtBottom();
  const thinking = bubble.querySelector(".sunny-thinking");
  if (message.t === "text") {
    thinking.hidden = true;  // the answer has started; the flame comes back if a tool runs
    bubble.querySelector(".sunny-stream-text").textContent += message.v;  // model text: never innerHTML
  } else if (message.t === "status") {
    thinking.hidden = false;
    bubble.querySelector(".sunny-thinking-line").textContent = message.v;
  } else if (message.t === "done") {
    // The real entries, rendered by the server like everywhere else (cards, problems, escaping).
    const holder = document.createElement("div");
    holder.innerHTML = message.html;
    const entries = [...holder.children];
    bubble.replaceWith(...entries);
    entries.forEach((entry) => htmx.process(entry));
    scrollSunny();
    return true;
  }
  if (follow) scrollSunny();  // only when you were already at the bottom
  return false;
}

document.addEventListener("htmx:afterSwap", (event) => {
  if (["sunny-log", "sunny-body"].includes(event.detail.target.id) || event.detail.target.closest?.("#sunny-log")) {
    scrollSunny();
  }
});


// ---- Sunny: notices on the button ----
// The notices block in the panel brings the badge along (out of band); the button glows while it has a number.

document.addEventListener("htmx:afterSettle", () => {
  const badge = el("sunny-badge");
  if (badge) el("sunny-fab").classList.toggle("has-notices", !badge.hidden);
});


// ---- Sunny: planner reminders while the page is open ----
// Every minute, the same /planner/due the tray icon asks. Each reminder shows once per window
// (sessionStorage, so also after an htmx swap or a reload). Not on Settings: they wait until you leave.

const REMINDER_CHECK_MS = 60 * 1000;
const POP_CLOSE_MS = 600 * 1000;
let popTimer = null;

function stored(name, fallback) {
  try {
    const value = sessionStorage.getItem(name);
    return value === null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

function store(name, value) {
  try {
    sessionStorage.setItem(name, JSON.stringify(value));
  } catch {
    // private window or blocked storage: reminders still show, maybe twice
  }
}

function localIso(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
         `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

async function checkReminders() {
  if (location.hash.startsWith("#settings")) return;  // Sunny stays hidden on Settings
  const now = localIso(new Date());
  const since = stored("sunny-due-since", now);
  let due;
  try {
    const response = await fetch(`/planner/due?since=${encodeURIComponent(since)}`);
    if (!response.ok) return;
    due = await response.json();
  } catch {
    return;  // Progen isn't answering: next time from the same moment
  }
  store("sunny-due-since", now);
  const seen = new Set(stored("sunny-due-seen", []));
  const fresh = due.filter((item) => !seen.has(item.key));
  if (!fresh.length) return;
  fresh.forEach((item) => seen.add(item.key));
  store("sunny-due-seen", [...seen].slice(-200));
  showReminders(fresh);
}

function showReminders(items) {
  const list = el("sunny-pop-list");
  for (const item of items) {
    const li = document.createElement("li");
    const text = document.createElement("p");
    text.textContent = item.text;
    const done = document.createElement("button");
    done.className = "btn btn-ghost btn-sm";
    done.type = "button";
    done.textContent = "Done";
    done.setAttribute("hx-post", `/ui/planner/plans/${encodeURIComponent(item.plan_id)}/done`);
    done.setAttribute("hx-vals", JSON.stringify({ on: item.on }));
    done.setAttribute("hx-swap", "none");
    done.dataset.popClose = "";
    const ok = document.createElement("button");
    ok.className = "btn-link";
    ok.type = "button";
    ok.textContent = "Got it";
    ok.dataset.popClose = "";
    const actions = document.createElement("div");
    actions.className = "sunny-pop-actions";
    actions.append(done, ok);
    li.append(text, actions);
    list.append(li);
    htmx.process(li);
  }
  const pop = el("sunny-pop");
  pop.hidden = false;
  clearTimeout(popTimer);
  popTimer = setTimeout(closePop, POP_CLOSE_MS);  // the Windows notification still has it
}

function closePop() {
  el("sunny-pop").hidden = true;
  el("sunny-pop-list").replaceChildren();
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-pop-close]");
  if (!button) return;
  // After htmx has sent "Done": remove only this line, and the pop-in when it was the last one.
  setTimeout(() => {
    button.closest("li").remove();
    if (!el("sunny-pop-list").children.length) closePop();
  }, 0);
});

window.addEventListener("hashchange", () => {
  if (location.hash.startsWith("#settings")) closePop();
});
document.addEventListener("DOMContentLoaded", () => {
  checkReminders();
  setInterval(checkReminders, REMINDER_CHECK_MS);
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
