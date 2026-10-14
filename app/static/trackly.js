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
  voiceStop();  // see "talking back": stop reading aloud and listening
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
  document.querySelectorAll(".sunny-mic").forEach((button) => { button.disabled = busy; });  // no talking over Sunny
  wakeUpdate();
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
  const voice = micSpokenNext;  // set by micSend just before requestSubmit
  micSpokenNext = false;
  if (!voice) voiceConversationEnd();  // typing ends a voice conversation
  askSunny(text, false, voice);
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-sunny-retry]");
  if (!button || sunnyBusy) return;
  // "fresh": the connection dropped before Progen kept the message, so it goes as a new one.
  askSunny(button.dataset.sunnyRetry, !button.dataset.sunnyFresh);
  el("sunny-input")?.focus();
});

async function askSunny(text, retry, voice = false) {
  const bubble = fromTemplate("sunny-thinking");
  el("sunny-log").append(bubble);
  scrollSunny();
  sunnyBusyState(true);
  let finished = false;
  try {
    const response = await fetch("/ui/assistant/stream", {
      method: "POST",
      headers: { "HX-Request": "true" },  // a change: the CSRF check wants to see it comes from the page
      body: new URLSearchParams({ message: text, retry: retry ? "1" : "", voice: voice ? "1" : "" }),
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
      finished = [problem];
    }
  } finally {
    sunnyBusyState(false);
  }
  if (voice && Array.isArray(finished)) voiceTalkBack(finished);  // a spoken message gets a spoken answer
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
    return entries;  // the new entries, for reading them aloud after a spoken message
  }
  if (follow) scrollSunny();  // only when you were already at the bottom
  return false;
}

document.addEventListener("htmx:afterSwap", (event) => {
  if (["sunny-log", "sunny-body"].includes(event.detail.target.id) || event.detail.target.closest?.("#sunny-log")) {
    scrollSunny();
  }
});


// ---- Sunny: hold to talk ----
// The browser's own speech recognition (Microsoft's service in Edge, Apple's in Safari): hold the mic,
// speak, let go, and the text is sent as if typed (through the same submit handler, so busy check,
// bubble and streaming are the same). Only that text reaches Sunny. Without support no button.
// Escape, pointercancel or sliding off the button cancels; a tap shorter than MIC_MIN_MS sends nothing.

const SpeechRecognizer = window.SpeechRecognition || window.webkitSpeechRecognition;
const MIC_MIN_MS = 300;
const MIC_SLIDE_OFF = 24;  // px outside the button that still counts as "on it"
const MIC_ERRORS = {
  "not-allowed": "Microphone blocked. Allow it in the browser's site settings.",
  "service-not-allowed": "Microphone blocked. Allow it in the browser's site settings.",
  "audio-capture": "No microphone found.",
  "network": "Speech recognition needs the internet.",
};
let micTake = null;  // the recording in progress
let micSpokenNext = false;  // the next submit came from the microphone (see "talking back")
let micPressed = false;     // the mic button is held down right now

function showMic() {
  document.querySelectorAll(".sunny-mic").forEach((button) => { button.hidden = !SpeechRecognizer; });
}
document.addEventListener("DOMContentLoaded", showMic);
document.addEventListener("htmx:afterSwap", showMic);  // the panel body is loaded with htmx

function micHint(text) {
  const input = el("sunny-input");
  if (!input) return;
  input.dataset.idle ||= input.placeholder;
  input.placeholder = text;
  clearTimeout(micHint.timer);
  if (text !== "Listening…") micHint.timer = setTimeout(() => { input.placeholder = input.dataset.idle; }, 2500);
}

function micRecording(take, on) {
  el("sunny-form")?.classList.toggle("is-recording", on);
  take.button.setAttribute("aria-pressed", String(on));
  if (on) micHint("Listening…");
}

function micBegin(button, pointerId, handsFree, quiet = false) {
  // Only one recognizer at a time: the wake word listener has to be really stopped first.
  wakeHalt(() => {
    if (micTake || sunnyBusy || (!handsFree && !micPressed)) return;  // let go before it was free
    micStart(button, pointerId, handsFree);
    if (micTake) micTake.quiet = quiet;
  });
}

function micStart(button, pointerId, handsFree = false) {
  // handsFree: in a voice conversation, after Sunny has spoken. No holding: the end of the sentence sends.
  const input = el("sunny-input");
  const recognition = new SpeechRecognizer();
  recognition.lang = navigator.language || "en-US";  // Dutch and English mixed; find_item copes with typos
  recognition.interimResults = true;                 // what is heard shows up live in the field
  recognition.continuous = !handsFree;               // holding: a pause doesn't stop it, letting go does
  const take = { recognition, button, input, before: input.value, started: Date.now(), text: "", state: "recording",
                 handsFree };
  recognition.onresult = (event) => {
    if (take.state === "cancelled" || take.state === "failed") return;
    take.text = [...event.results].map((result) => result[0].transcript).join("").trim();
    input.value = take.text;
  };
  recognition.onerror = (event) => {
    if (take.state === "cancelled" || event.error === "aborted") return;
    take.state = "failed";
    input.value = take.before;
    if (handsFree) {
      // Hands-free quietly ends the conversation; Safari may refuse to listen without a tap.
      voiceConversationEnd();
      if (event.error.includes("not-allowed")) micHint("Tap the mic to answer");
      return;
    }
    const message = MIC_ERRORS[event.error];
    message ? showToast(message) : micHint("Didn't catch that.");
  };
  recognition.onend = () => micEnd(take);
  try {
    if (!handsFree) button.setPointerCapture(pointerId);
    recognition.start();
  } catch {
    if (handsFree) {
      voiceConversationEnd();
      micHint("Tap the mic to answer");
    } else {
      showToast("Speech recognition didn't start. Try again.");
    }
    return;
  }
  micTake = take;
  micRecording(take, true);
  if (handsFree) {
    take.silence = setTimeout(() => {  // nothing said for a while: the conversation is over
      if (micTake === take && !take.text) {
        micCancel();
        voiceConversationEnd();
      }
    }, VOICE_SILENCE_MS);
  }
}

function micRelease() {
  const take = micTake;
  if (!take || take.handsFree || !["recording", "ended"].includes(take.state)) return;
  if (Date.now() - take.started < MIC_MIN_MS) {
    micCancel();
    if (!take.quiet) micHint("Hold to talk");
  } else if (take.state === "ended") {
    micSend(take);  // the recognizer already stopped by itself while you held on
  } else {
    take.state = "stopping";  // wait for the last result and "end", then send
    take.recognition.stop();
  }
}

function micCancel() {
  const take = micTake;
  if (!take) return;
  take.state = "cancelled";
  take.input.value = take.before;
  take.recognition.abort();
  micDone(take);
}

function micEnd(take) {
  if (take.state === "stopping" || (take.handsFree && take.state === "recording")) {
    micSend(take);  // let go, or (hands-free) the end of the sentence
  } else if (take.state === "recording") {
    take.state = "ended";  // stopped by itself (Safari after a long pause): sent when you let go
  } else {
    micDone(take);  // cancelled or failed
  }
}

function micSend(take) {
  micDone(take);
  if (!take.text) {
    take.input.value = take.before;
    if (take.handsFree) voiceConversationEnd();
    else micHint("Didn't catch that.");
    return;
  }
  if (voiceHandled(take.text)) {  // "thanks", or "yes"/"no" for an open card: not a message for Sunny
    take.input.value = take.before;
    return;
  }
  take.input.value = take.text;
  micSpokenNext = true;  // a spoken message: Sunny answers out loud and keeps listening
  el("sunny-form").requestSubmit();  // the one way to send: the submit handler above
}

function micDone(take) {
  clearTimeout(take.silence);
  if (micTake !== take) return;
  micTake = null;
  micRecording(take, false);
  if (take.input.placeholder === "Listening…") take.input.placeholder = take.input.dataset.idle;
  wakeUpdate();
}

document.addEventListener("pointerdown", (event) => {
  const button = event.target.closest(".sunny-mic");
  if (!button || !SpeechRecognizer || button.disabled) return;
  event.preventDefault();  // no focus jump, no text selection
  voiceUnlock();  // iPhone: speech may only play after a tap
  const quiet = voiceConversation || voiceSpeaking;  // this tap stops Sunny talking or listening
  micPressed = true;
  voiceStop();
  if (sunnyBusy || micTake) return;
  micBegin(button, event.pointerId, false, quiet);  // a short tap then just stops; no "Hold to talk" hint
});
document.addEventListener("pointerup", (event) => {
  micPressed = false;
  if (micTake && event.target.closest(".sunny-mic")) micRelease();
});
document.addEventListener("pointermove", (event) => {
  // With pointer capture there is no pointerleave while holding, so sliding off is checked here.
  if (!micTake || micTake.handsFree || !["recording", "ended"].includes(micTake.state)) return;
  const box = micTake.button.getBoundingClientRect();
  const off = event.clientX < box.left - MIC_SLIDE_OFF || event.clientX > box.right + MIC_SLIDE_OFF
    || event.clientY < box.top - MIC_SLIDE_OFF || event.clientY > box.bottom + MIC_SLIDE_OFF;
  if (off) micCancel();
});
document.addEventListener("pointercancel", () => {
  micPressed = false;
  if (micTake && !micTake.handsFree) micCancel();
});
document.addEventListener("contextmenu", (event) => {
  if (event.target.closest(".sunny-mic")) event.preventDefault();  // a long press is not a menu
});
window.addEventListener("keydown", (event) => {
  // Before the panel's own Escape handler: Escape while recording only cancels the recording.
  // It also stops Sunny reading aloud and ends a voice conversation.
  if (event.key !== "Escape") return;
  const recording = Boolean(micTake);
  voiceStop();
  if (recording) event.stopPropagation();
}, true);


// ---- Sunny: talking back ----
// After a spoken message (never a typed one) the browser reads Sunny's answer aloud (speechSynthesis,
// free and on the device), then listens again without holding: a voice conversation. It ends on
// silence, a closing word ("thanks"), a tap on the mic, Escape, closing the panel or typing. A bare
// "yes" or "no" for an open card clicks its Confirm or Cancel button; deleting always needs a click.
// The voices are picked per language, or chosen in Settings › Sunny (stored on this device).

const SPEECH = window.speechSynthesis;
const VOICE_RATE = 1.05;
const VOICE_PITCH = 0.9;          // a little lower: drier
const VOICE_RELISTEN_MS = 400;    // so the microphone doesn't hear the end of Sunny's own sentence
const VOICE_SILENCE_MS = 7000;    // nothing said for this long: the conversation is over
const VOICE_BEST = /natural|online|enhanced|premium/i;  // Edge's and Apple's better voices
const VOICE_SAME = "same";        // Dutch voice: "Same as English voice"
const VOICE_MULTILINGUAL = /multilingual/i;  // e.g. Edge's Brian: reads Dutch fine too
const VOICE_TEST = { nl: "Hoi Progen. Dit is mijn stem.", en: "Hello, Progen. This is my voice." };
const NL_WORDS = new Set("de het een je niet ik van dat en op te met voor maar ook nog wel er zijn heb geen wat hoe jij mijn naar dan al deze die hoofdstuk niks".split(" "));
const EN_WORDS = new Set("the a an you not i of that and to it on for with this was no what how my your have just are chapter nothing all".split(" "));
const CLOSERS = new Set(["stop", "klaar", "dat was het", "laat maar", "thanks", "thank you", "bedankt", "thats all"]);
const YES = new Set(["ja", "ja doe maar", "doe maar", "yes", "yep", "confirm", "bevestig", "ok", "oké", "okay", "go"]);
const NO = new Set(["nee", "no", "cancel", "annuleer", "laat maar zitten"]);
let voiceConversation = false;  // listening again after Sunny has spoken
let voiceSpeaking = false;
let voiceTurn = 0;              // bumped by voiceStop, so a cancelled sentence doesn't start listening
let voiceUnlocked = false;
let voiceAwaitCard = false;     // a spoken Confirm/Cancel: read Sunny's short line when it arrives

function voiceLocal(name) {
  try { return localStorage.getItem(name) || ""; } catch { return ""; }
}

function voiceSave(name, value) {
  try { value ? localStorage.setItem(name, value) : localStorage.removeItem(name); } catch { /* not stored */ }
}

function voiceWords(text) {
  // "That's all!" -> "thats all": lower case, no punctuation, single spaces
  return text.toLowerCase().replace(/['’]/g, "").replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();
}

function voiceLanguage(text) {
  const words = voiceWords(text).split(" ");
  const nl = words.filter((w) => NL_WORDS.has(w)).length;
  const en = words.filter((w) => EN_WORDS.has(w)).length;
  return nl > en ? "nl" : "en";
}

function voiceFor(language) {
  if (!SPEECH) return null;
  if (language === "nl") {
    // One voice for both languages: chosen in Settings, or automatic when the English voice is multilingual.
    const dutch = voiceLocal("sunny-voice-nl");
    const english = dutch === VOICE_SAME || !dutch ? voiceFor("en") : null;
    if (dutch === VOICE_SAME || (english && VOICE_MULTILINGUAL.test(english.name))) return english;
  }
  const voices = SPEECH.getVoices();
  const chosen = voiceLocal(`sunny-voice-${language}`);
  if (chosen) {
    const own = voices.find((v) => v.voiceURI === chosen);
    if (own) return own;
  }
  const fitting = voices.filter((v) => v.lang.toLowerCase().startsWith(language));
  return fitting.find((v) => VOICE_BEST.test(v.name)) || fitting[0] || null;
}

function voicePlain(text) {
  // What a person would say: links become their text, no markdown, list markers or arrows.
  return text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").replace(/[*_`#]+/g, "")
    .replace(/^\s*(?:[-•]|\d+[.)])\s+/gm, "").replace(/\s*[→·]\s*/g, ", ").replace(/\s+/g, " ").trim();
}

function voiceSay(text, then, language) {
  // Reads `text` aloud, then calls `then` (unless voiceStop came in between).
  const turn = ++voiceTurn;
  const after = () => {
    if (turn !== voiceTurn) return;
    voiceSpeaking = false;
    wakeUpdate();
    then?.();
  };
  const plain = voicePlain(text);
  if (!SPEECH || !plain) { after(); return; }
  SPEECH.cancel();
  const utterance = new SpeechSynthesisUtterance(plain);
  const lang = language || voiceLanguage(plain);
  const voice = voiceFor(lang);
  if (voice) utterance.voice = voice;
  utterance.lang = voice ? voice.lang : (lang === "nl" ? "nl-NL" : "en-US");
  utterance.rate = VOICE_RATE;
  utterance.pitch = VOICE_PITCH;
  utterance.onend = after;
  utterance.onerror = after;
  voiceSpeaking = true;
  wakeUpdate();  // paused while Sunny talks, or it would hear itself say "Sunny"
  SPEECH.speak(utterance);
}

function voiceStop() {
  voiceTurn++;
  voiceSpeaking = false;
  voiceAwaitCard = false;
  SPEECH?.cancel();
  voiceConversationEnd();
  if (micTake) micCancel();
}

function voiceConversationEnd() {
  voiceConversation = false;
  if (micTake?.handsFree) micCancel();
  wakeUpdate();  // the conversation is over: listen for "Hey Sunny" again (if that's on)
}

function voiceUnlock() {
  // iPhone: Safari only plays speech after a tap. An empty sentence during the first tap unlocks it.
  if (voiceUnlocked || !SPEECH) return;
  voiceUnlocked = true;
  SPEECH.speak(new SpeechSynthesisUtterance(""));
}

function voiceListen() {
  // Hands-free: the same recognizer as hold to talk, started without a tap.
  const button = document.querySelector(".sunny-mic");
  if (!voiceConversation || sunnyBusy || micTake || !button || button.hidden) return;
  setTimeout(() => {
    if (voiceConversation && !sunnyBusy && !micTake) micBegin(button, null, true);
  }, VOICE_RELISTEN_MS);
}

function voiceReplyText(entries) {
  // What Sunny said in these entries: bubbles and problems, not buttons or cards.
  return entries.map((entry) => {
    if (entry.classList.contains("sunny-problem")) return entry.querySelector(".sunny-problem-body p")?.textContent;
    if (entry.classList.contains("sunny-bubble")) return entry.textContent;
    return "";
  }).filter(Boolean).join(" ");
}

function voiceTalkBack(entries) {
  // After a spoken message: read the answer, then keep listening (not after a problem).
  voiceConversation = true;
  const problem = entries.some((entry) => entry.classList.contains("sunny-problem"));
  if (problem) voiceConversation = false;
  voiceSay(voiceReplyText(entries), voiceListen);
}

function voiceOpenCard() {
  // The open card of Sunny's last answer: a pending card with nothing from Progen after it.
  const log = el("sunny-log");
  const card = log ? [...log.querySelectorAll(".sunny-card.is-pending")].pop() : null;
  if (!card) return null;
  let next = card.nextElementSibling;
  while (next) {
    if (next.classList.contains("from-user")) return null;
    next = next.nextElementSibling;
  }
  return card;
}

function voiceHandled(text) {
  // Spoken words that are not a message for Sunny. True when handled here.
  const words = voiceWords(text);
  if (voiceConversation && CLOSERS.has(words)) {
    voiceConversationEnd();
    return true;
  }
  const card = voiceOpenCard();
  if (!card || words.split(" ").length > 4 || !(YES.has(words) || NO.has(words))) return false;
  const confirm = card.querySelector('[hx-post^="/ui/assistant/confirm/"]');
  if (YES.has(words) && confirm?.dataset.confirmKind === "sunny-delete") {
    voiceConversationEnd();
    sunnyLocalLine("Deleting needs a click.");  // never delete by voice
    return true;
  }
  const button = YES.has(words) ? confirm : card.querySelector('[hx-post^="/ui/assistant/cancel/"]');
  if (!button) return false;
  voiceConversation = true;
  voiceAwaitCard = true;  // read Sunny's short line ("Done.") when the card has been swapped
  button.click();         // the same htmx button: the same route and the same toast
  return true;
}

function sunnyLocalLine(text) {
  // A fixed line from Sunny, shown and read aloud here: no API call.
  const bubble = document.createElement("div");
  bubble.className = "sunny-msg from-sunny sunny-bubble";
  bubble.textContent = text;
  el("sunny-log").append(bubble);
  scrollSunny();
  voiceSay(text, null, "en");
}

document.addEventListener("htmx:afterSettle", (event) => {
  // After a spoken Confirm or Cancel: read Sunny's short line, then keep listening.
  const path = event.detail.pathInfo?.requestPath || "";
  if (!voiceAwaitCard || !/^\/ui\/assistant\/(confirm|cancel)\//.test(path)) return;
  voiceAwaitCard = false;
  const line = [...document.querySelectorAll("#sunny-log .sunny-bubble")].pop();
  voiceSay(line ? line.textContent : "", voiceListen);
});

document.addEventListener("input", (event) => {
  if (event.isTrusted && event.target.id === "sunny-input") voiceConversationEnd();  // typing ends it
});

document.addEventListener("click", (event) => {
  if (event.target.closest('[hx-post="/ui/assistant/reset"]')) voiceStop();  // New chat
  const test = event.target.closest("[data-voice-test]");
  if (test) {
    voiceUnlock();
    voiceSay(VOICE_TEST.nl, () => voiceSay(VOICE_TEST.en, null, "en"), "nl");
  }
});

// Settings › Sunny: a Dutch and an English voice from this device (or Automatic), kept in localStorage.
function voiceSettings() {
  const block = el("sunny-voices");
  if (!block) return;
  block.hidden = !SPEECH;
  if (!SPEECH) return;
  const voices = SPEECH.getVoices();
  for (const language of ["nl", "en"]) {
    const select = el(`sunny-voice-${language}`);
    const chosen = voiceLocal(`sunny-voice-${language}`);
    const fitting = voices.filter((v) => v.lang.toLowerCase().startsWith(language));
    const same = language === "nl" ? [new Option("Same as English voice", VOICE_SAME, false, chosen === VOICE_SAME)] : [];
    select.replaceChildren(new Option("Automatic", ""), ...same,
      ...fitting.map((v) => new Option(`${v.name} (${v.lang})`, v.voiceURI, false, v.voiceURI === chosen)));
  }
}
document.addEventListener("change", (event) => {
  const select = event.target.closest("[data-voice-language]");
  if (select) voiceSave(`sunny-voice-${select.dataset.voiceLanguage}`, select.value);
});
document.addEventListener("htmx:afterSwap", voiceSettings);
SPEECH?.addEventListener?.("voiceschanged", voiceSettings);  // getVoices() is often empty at first


// ---- Sunny: wake word ----
// "Hey Sunny" or "Sunless" opens Sunny without the button (Settings › Sunny, off by default, per device).
// A light listener of its own that only looks for the wake word: no input field, no bubbles. It runs
// while Progen is open and visible and nothing else uses the microphone; wakeUpdate() decides, and
// is called whenever that may change. The browser's speech service hears the audio the whole time.
// After the wake word it hands over to the voice conversation above (hands-free, "yes", "thanks").

const WAKE = {
  names: ["sunny", "sonny", "sunnie"],                         // only as the first word, or after a greeting
  greetings: ["hey", "hi", "hee", "he", "hé", "oké"],          // "hey sunny", "oké sunny", ...
  anywhere: ["sunless", "sun less", "sunlis", "sunlit"],       // rare enough to count anywhere
};
const WAKE_SETTING = "sunny-wake";
const WAKE_RESTART_MS = 300;
const WAKE_MAX_BACKOFF_MS = 10000;
const WAKE_TITLE = 'Listening for "Hey Sunny"';
let wake = null;          // the running listener: { recognition, ended: [callbacks], woken }
let wakeTimer = null;
let wakeFailures = 0;
let wakeEverStarted = false;

function wakeOn() {
  return voiceLocal(WAKE_SETTING) === "1";
}

function wakeMatch(transcript) {
  // The text after the wake word, or null. Per sentence, so "sunny" can be "the first word".
  for (const sentence of transcript.split(/[.!?]+/)) {
    const words = voiceWords(sentence).split(" ").filter(Boolean);
    let start = -1;
    if (WAKE.names.includes(words[0])) start = 1;
    else if (WAKE.greetings.includes(words[0]) && WAKE.names.includes(words[1])) start = 2;
    for (const phrase of WAKE.anywhere) {
      const parts = phrase.split(" ");
      const at = words.findIndex((_, i) => parts.every((part, j) => words[i + j] === part));
      if (at >= 0 && (start < 0 || at + parts.length < start)) start = at + parts.length;
    }
    if (start >= 0) return words.slice(start).join(" ");
  }
  return null;
}

function wakeShouldRun() {
  return wakeOn() && Boolean(SpeechRecognizer) && Boolean(el("sunny-form")) && !micTake && !micPressed
    && !voiceSpeaking && !voiceConversation && !voiceAwaitCard && !sunnyBusy
    && document.visibilityState !== "hidden" && !location.hash.startsWith("#settings");
}

function wakeUpdate() {
  if (wake?.woken) return;  // handling a wake word right now
  if (!wakeShouldRun()) {
    clearTimeout(wakeTimer);
    wakeTimer = null;
    if (wake) wakeHalt();
    return;
  }
  if (wake || wakeTimer) return;
  const delay = Math.min(WAKE_MAX_BACKOFF_MS, WAKE_RESTART_MS * 2 ** wakeFailures);
  wakeTimer = setTimeout(() => { wakeTimer = null; wakeStart(); }, delay);
}

function wakeStart() {
  if (wake || !wakeShouldRun()) return;
  const recognition = new SpeechRecognizer();
  recognition.lang = navigator.language || "en-US";
  recognition.continuous = true;
  recognition.interimResults = true;  // react to the first guess, not the end of the sentence
  const listener = { recognition, ended: [], woken: null };
  recognition.onstart = () => {
    wakeEverStarted = true;
    wakeSign(true);
  };
  recognition.onresult = (event) => {
    if (listener.woken) {
      // Woken: wait until the sentence with the wake word is finished, for a command in the same breath.
      const result = event.results[listener.woken.index];
      if (!result) return;
      listener.woken.rest = wakeMatch(result[0].transcript) ?? listener.woken.rest;
      if (result.isFinal) wakeAct(listener);
      return;
    }
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const rest = wakeMatch(event.results[i][0].transcript);
      if (rest === null) continue;
      wakeFailures = 0;
      listener.woken = { index: i, rest };
      openSunny();
      if (event.results[i].isFinal) wakeAct(listener);
      return;
    }
  };
  recognition.onerror = (event) => {
    if (event.error === "aborted" || event.error === "no-speech") return;  // a normal end
    if (event.error.includes("not-allowed")) {
      wakeSave(false);
      if (wakeEverStarted) {
        micHint("Tap the mic to talk");  // Safari: no restart without a tap
      } else {
        showToast("Microphone blocked. Allow it in the browser's site settings.");
      }
      return;
    }
    wakeFailures++;  // network, audio-capture, ...: try again later and later
  };
  recognition.onend = () => {
    wakeSign(false);
    if (wake === listener) wake = null;
    listener.ended.splice(0).forEach((then) => then());
    if (listener.woken && !listener.acted) wakeAct(listener);  // ended before the sentence was final
    else wakeUpdate();
  };
  wake = listener;
  try {
    recognition.start();
  } catch {
    wake = null;
    wakeFailures++;
    wakeUpdate();
  }
}

function wakeHalt(then) {
  // Stops the listener; `then` runs once the browser has really let go of the microphone.
  const listener = wake;
  if (!listener) { then?.(); return; }
  if (then) listener.ended.push(then);
  if (!listener.stopping) {
    listener.stopping = true;
    listener.recognition.abort();
  }
}

function wakeAct(listener) {
  // Hand over to the voice conversation: the command said in the same breath, or "Yes?" and listen.
  if (listener.acted) return;
  listener.acted = true;
  const rest = listener.woken.rest.trim();
  listener.woken = null;
  voiceConversation = true;
  const go = () => {
    const input = el("sunny-input");
    if (!input || !el("sunny-form")) { voiceConversationEnd(); return; }
    if (rest) {
      if (voiceHandled(rest)) return;
      input.value = rest;
      micSpokenNext = true;  // the same way as hands-free: voice=1, the same submit handler
      el("sunny-form").requestSubmit();
    } else {
      voiceSay("Yes?", voiceListen, "en");
    }
  };
  wake === listener ? wakeHalt(go) : go();
}

function wakeSign(listening) {
  // A small, calm ring on Sunny's button: you can always see the microphone is on.
  const fab = el("sunny-fab");
  if (!fab) return;
  fab.classList.toggle("is-wake-listening", listening);
  fab.title = listening ? WAKE_TITLE : "Ask Sunny";
}

function wakeSave(on) {
  voiceSave(WAKE_SETTING, on ? "1" : "");
  const toggle = el("sunny-wake");
  if (toggle) toggle.checked = on;
  wakeUpdate();
}

document.addEventListener("change", (event) => {
  if (event.target.id === "sunny-wake") wakeSave(event.target.checked);
});
document.addEventListener("htmx:afterSwap", () => {
  const toggle = el("sunny-wake");
  if (toggle) {
    toggle.checked = wakeOn();
    el("sunny-wake-row").hidden = !SpeechRecognizer;
  }
  wakeUpdate();  // the panel body (with the form) may have just arrived
});
document.addEventListener("visibilitychange", wakeUpdate);  // hidden or minimised: not listening
window.addEventListener("hashchange", wakeUpdate);           // paused on Settings
document.addEventListener("DOMContentLoaded", wakeUpdate);


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
