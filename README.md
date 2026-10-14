# Personal-tracker

A personal API that collects data about my own life in one place, under my own control. It started with tracking what I read (**Reading**: novels and manhwa); then came **Watching** (films, series and anime) and **Listening** (what I listen to on Spotify). Sleep, fitness and other trackers may follow.

The home screen (`#home`) shows a card per tracker. A click takes you to that tracker's own space, with the tabs Dashboard, Library and Progress (`#reading`, `#watching/library`, ...).

The application is built with **event modeling** and **event sourcing**: everything that happens is stored as immutable events, and all overviews (read models) are built from them. That way no history is ever lost, and I can create new overviews over old data later.

---

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Mac/Linux
pip install -r requirements-dev.txt   # the app plus pytest; only the app: requirements.txt

uvicorn app.main:create_app --factory --reload
```

### Starting with an icon (Windows)

Install the shortcut once (from the project folder, after the venv exists):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1 -StartMenu
```

After that, **Progen** is on your desktop (with `-StartMenu` also in the Start menu, together with **Stop Progen**; with `-StopOnDesktop` Stop Progen is on the desktop too).

- **Progen** starts `launch.pyw` with `pythonw.exe` from `.venv`, so without a console window. If the server isn't running yet, it starts in the background (only on `127.0.0.1:8000`, without `--reload`) with its output in `data/launcher.log`. As soon as it answers, Progen opens as its own window in Edge (`msedge --app=...`), or in your default browser if Edge isn't there. If it doesn't start within 15 seconds, a message says where the log is.
- **Icon by the clock:** while Progen is running, there is a Progen icon in the system tray (under "hidden icons", just like WhatsApp or Spotify). Double-click opens Progen; right-click gives **Open Progen**, **Sync Spotify now** and **Stop Progen**. There is only ever one such icon: clicking the shortcut again only opens a new window. If the server isn't running, the shortcut always starts it, even if another icon is (still) there.
- **Closing the window doesn't stop the server**; it keeps running in the background, so the Spotify sync carries on.
- **Starting automatically when signing in:** turn on the **Start with Windows** switch in Settings > App. Progen then puts a value `Progen` in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (only for your user, no admin rights needed) that starts `launch.pyw --background`: server and icon, without a window. Turning it off removes that value again. If you move the project folder, turn the switch on again.
- **Stop Progen** (in the icon's menu, or `stop.pyw`) asks the server to stop cleanly through `POST /admin/shutdown`: running requests are finished and the database is closed; then the icon disappears. Uvicorn gets at most 5 seconds for that (`--timeout-graceful-shutdown`); if the process is still alive after 20 seconds, it stops itself hard (`app/shutdown.py`). The icon never shows a popup but a balloon notification, because a popup would block the icon. If Progen wasn't running anymore, the icon simply disappears. That route only works from this computer (127.0.0.1) and with the header `X-Progen-Shutdown`, so a website in your browser can't call it.
- The icon (`app/static/icon.ico`, 16 to 256 px, and the PNGs in `app/static/icons/` for the favicon and `manifest.json`) is made from `icon.svg` with `scripts/make_icon.py`: Edge or Chrome renders the SVG headless as a transparent PNG, and Pillow makes the rest from that. The tray icon loads the .ico at exactly the size Windows wants (20 px at 125%), and the launcher is DPI-aware; that way the icon and menu stay sharp. For the taskbar, the Progen window picks one of the favicon PNGs (32-96 px); those come from `icon-small.svg`, a simplified version (thick ring, big book), because the details of `icon.svg` merge together at taskbar size. Run it again when the icon changes, and then `install_shortcut.ps1` again.
- If you want automatic reloading while programming, stop Progen first (Stop Progen) and start as above with `uvicorn ... --reload`; they use the same port.

Settings live in `.env` in the project folder (not in git). Start with a copy of `.env.example`: `copy .env.example .env`. All settings are optional:

```
GOOGLE_BOOKS_API_KEY=...          # better covers from Google Books (without a key Google often drops out)
TMDB_API_KEY=...                  # covers and genres of films and series (Watching)
SPOTIFY_CLIENT_ID=...             # Listening: connection with Spotify (see Listening)
SPOTIFY_CLIENT_SECRET=...
SYNC_INTERVAL_MINUTES=30          # how often Progen syncs with Spotify automatically
PROGEN_BACKUP_DIR=D:\Backups\Progen  # another backup folder than OneDrive\Progen-backups
```

Write paths without quotes. A value that is already in the environment wins over `.env`.

- Web page: http://127.0.0.1:8000
- API documentation (Swagger): http://127.0.0.1:8000/docs
- Running the tests: `python -m pytest` (after `pip install -r requirements-dev.txt`)

The data of **all trackers** lives in one file, `data/tracker.db`. **This file is the only source of truth**: all read models are built from it at startup. Covers sit next to it in `data/covers/`. Neither is in git, so make backups (see below).

### Using Progen on your phone (Tailscale)

Mainly for logging chapters on the go. Progen keeps listening on `127.0.0.1` only; [Tailscale](https://tailscale.com) puts it on your **private** Tailscale network with an https address like `https://my-pc.tail1234.ts.net`. Nothing is opened to the internet.

1. Install Tailscale on the PC and on the iPhone, and sign in to the same account on both.
2. On the PC: `tailscale serve --bg 8000`. It shows your address (`https://<pc-name>.<tailnet>.ts.net`); `tailscale serve status` shows it again later.
3. Put that host name in `.env`: `PROGEN_EXTRA_HOSTS=my-pc.tail1234.ts.net` (comma-separated for more than one, no wildcards).
4. Set a password: `python -m app.auth set-password`. Only a scrypt hash and a random session secret go into `.env`, never the password. Restart Progen. With extra hosts but no password, Progen refuses to start and says why.
5. On the iPhone, open the address in Safari, sign in, then Share › **Add to Home Screen**. Progen opens full screen, like an app.

Through that address you always sign in (a session lasts 30 days; log out under Settings › Phone access); on the PC itself Progen stays one click without a password. Wrong passwords are rate limited. **The PC has to be on and Progen running**: turn on **Start with Windows** in Settings. Connecting Spotify still happens on the PC (its callback stays `http://127.0.0.1:8000/...`), and the API keys (Anthropic, TMDB, Spotify) never leave `.env` on the PC.

### Backups

- **Making one:** Settings > *Back up now*, or `python -m app.backup` (also works when the app isn't running; `--list` shows the backups).
- **Where:** `OneDrive\Progen-backups\` by default, so there is a copy in the cloud too. Pick another folder with `PROGEN_BACKUP_DIR` in `.env`.
- **What:** one backup holds all trackers at once, so they always fit together. Each backup is a folder named after the date and time (`2026-10-06_21-05-33`) with `tracker.db`, `covers/` and `info.json`. The copy is made with SQLite's backup API (safe while the server runs) and checked afterwards; a failed backup leaves nothing behind.
- **How many:** the 10 newest are kept (`KEEP_BACKUPS` in `app/backup.py`); older ones are removed.
- **Restoring:** Settings > *Restore* next to the backup you want. Progen first makes a backup of the current state automatically ("saved automatically before a restore"), so a restore can be undone too. Then the events are replaced, missing covers are put back and the read models are rebuilt.
- **Not in a backup:** `data/spotify_token.json` (your Spotify access), `data/spotify_sync.json` (when the last sync was) and `data/spotify_profile.json` + `spotify_avatar.webp` (your Spotify name and photo). After restoring on another computer, simply connect Spotify again.
- **Restoring by hand** (if the app doesn't start): stop the server, copy `tracker.db` and `covers/` from the backup folder to `data/` and start the server again.

---

## Architecture

```
Trigger (web page / importer)
   ↓
Command            request, can be refused              (StartSeries, LogProgress, ...)
   ↓
Command handler    load → let the aggregate decide → save
   ↓
Event store        append-only SQLite table             (SeriesStarted, ProgressLogged, ...)
   ↓
Projections        read models in memory                (Library, ReadingActivity)
   ↓
API (GET)          only reads from read models
```

Principles:

- **Events are facts.** They are `frozen` dataclasses, in the past tense, and are never changed or removed.
- **Commands are requests.** Imperative mood, and they can be refused with a `DomainError`.
- **The aggregate decides**, saves nothing itself, and can therefore be tested purely.
- **CQRS:** `POST` endpoints send commands, `GET` endpoints only read from read models.
- **Projections are disposable.** They are rebuilt from the event store at every start and updated synchronously through `store.subscribe`.
- **Vertical slices:** each domain has its own folder with everything in it. A new domain is a new folder.

### Project structure

```
app/
  main.py              wires everything together: event store, projections, handlers, routers
  event_types.py       ALL_EVENT_TYPES: every domain's EVENT_TYPES in one list (main, backup, import)
  eventstore.py        SQLite event store (append, transaction, load_stream, load_type, load_all, subscribe)
  clock.py             the one clock: utc_now, local_now and today (tests patch app.clock)
  security.py          protection against other websites (CSRF) and DNS rebinding; allowed host names
  auth.py              signing in through an extra host (your phone): password hash, session cookie, rate limit
  auth_web.py          /login and /logout
  shutdown.py          POST /admin/shutdown: stopping cleanly, only from this computer (for stop.pyw)
  desktop.py           talking to the server from the desktop (launcher, icon, stop.pyw)
  autostart.py         Start with Windows (Run key in the registry), on/off in Settings
  backup.py            making, keeping (10 newest) and restoring backups; also runs on its own
  domain.py            DomainError (shared by all trackers)
  covers.py            downloading, checking and storing covers as 300×450 WebP (shared)
  cover_search.py      searching several sources at once for covers and genres, fuzzy matching, cache (shared)
  genres.py            GenreSet: a fixed genre list and mapping genres from sources onto it (shared)
  charts.py            data for the bar charts: last days/weeks, heights, percentages, genre bars (shared)
  web.py               shared for the web page: templates, filters, icons, toasts, Backlog, covers, Tracker, home screen
  settings_web.py      Settings: where the data lives, backups and restore, Start with Windows
  static/
    trackly.css/.js    style, navigation, dialogs, cover picker, genre suggestions
    templates/         index.html (the shell) and the HTML fragments for htmx
  reading/
    events.py          SeriesStarted, ProgressLogged, StatusChanged, GenresChanged, SeriesRemoved (+ EVENT_TYPES)
    aggregate.py       ReadingSeries
    commands.py        StartSeries, LogProgress, ChangeStatus, SetGenres, RemoveSeries + ReadingCommandHandler
    projections.py     LibraryProjection, ReadingActivityProjection
    cover_sources.py   sources: AniList (manga), MangaUpdates, MyAnimeList, Open Library, Google Books
    genres.py          Reading's genre list
    api.py             /reading endpoints (JSON)
    web.py             /ui/reading endpoints (HTML fragments for htmx)
  listening/
    events.py          TrackPlayed (+ EVENT_TYPES)
    aggregate.py       Play (one moment = one play)
    commands.py        RecordPlay + ListeningCommandHandler, stream_id()
    projections.py     RecentlyPlayed, ListeningActivity, TopArtists, TopTracks
    spotify.py         the Spotify connection: signing in, tokens, sync (automation slice)
    export_import.py   importing your whole history from the Extended streaming history (automation slice)
    import_web.py      Settings > Import streaming history: preview and import in the background
    api.py             /listening endpoints (JSON)
    web.py             /ui/listening endpoints, and connect/callback/sync for Spotify
  watching/
    events.py          ShowAdded, ShowStatusChanged, ShowGenresChanged, ShowRemoved (+ EVENT_TYPES)
    aggregate.py       WatchItem
    commands.py        AddShow, ChangeShowStatus, SetShowGenres, RemoveShow + WatchingCommandHandler
    projections.py     WatchlistProjection, WatchActivityProjection
    cover_sources.py   sources: AniList (anime), TVmaze (series), TMDB (films and series, with a key)
    genres.py          Watching's genre list
    api.py             /watching endpoints (JSON)
    web.py             /ui/watching endpoints (HTML fragments for htmx)
  planner/
    events.py          PlanAdded, PlanRescheduled, PlanEdited, PlanDone, PlanReopened, PlanSkipped, PlanRemoved, ReminderSettingsChanged
    aggregate.py       Plan, ReminderSettings
    commands.py        AddPlan, ReschedulePlan, EditPlan, MarkPlanDone, ReopenPlan, SkipPlan, RemovePlan, ChangeReminderSettings + handler
    schedule.py        when a plan falls (working out repeats)
    projections.py     AgendaProjection (day, week, Someday, reminders), PlannerActivityProjection
    parse.py           quick entry in plain words (Dutch and English)
    ics.py             a plan as .ics for your iPhone (RFC 5545)
    api.py             /planner endpoints (JSON), including /planner/due for the notifications
    web.py             /ui/planner endpoints (HTML fragments for htmx)
  assistant/
    identity/          Sunny's design: IDENTITY, SOUL, USER (the system prompt), TOOLS and OUTCOME
    prompt.py          the system prompt (cacheable fixed part + today's date)
    tools.py           Sunny's tools: reading from the projections, writing only as a proposal (PendingAction)
    agent.py           the loop with Claude (the model comes from the setting; effort as a constant at the top)
    models.py          the models to choose from (the only place with model IDs)
    events.py, aggregate.py, commands.py   AssistantSettingsChanged, AssistantSettings, ChangeAssistantModel
    session.py         the conversation: messages, proposals, Confirm/Cancel (in memory, not in the event store)
    web.py             /ui/assistant: thin routes for the chat panel, Confirm/Cancel and Settings › Sunny
  wrapped/
    projections.py     WrappedProjection: the year in review from the events of all trackers (read only)
    web.py             /ui/wrapped (#wrapped) and the Wrapped card on the home screen
launch.pyw, stop.pyw   starting Progen (window + icon by the clock) and stopping it; for the shortcut
scripts/
  install_shortcut.ps1 shortcuts on the desktop / in the Start menu
  make_icon.py         makes app/static/icon.ico from icon.svg
tests/
  test_eventstore.py, test_api.py, test_web.py
  test_covers.py, test_cover_flow.py
  reading/, watching/, listening/, planner/, wrapped/, assistant/  given/when/then tests per rule
data/                  tracker.db, covers/ and the spotify_* files (not in git)
```

---

## Current status: the reading domain (done)

| Part        | Contents                                                        |
| ----------- | --------------------------------------------------------------- |
| Statuses    | Reading, On-Hold, Completed, Dropped, Plan to Read              |
| Events      | `SeriesStarted` (optional `cover`), `ProgressLogged`, `StatusChanged`, `GenresChanged`, `SeriesRemoved` |
| Commands    | `StartSeries`, `LogProgress`, `ChangeStatus`, `SetGenres`, `RemoveSeries` |
| Read models | CurrentlyReading, Library, ReadingActivity (per day / per week) |

Rules:

1. A title may only be in the library once (case and spaces are ignored). Rereading = status back to Reading.
2. Logging progress on On-Hold, Completed or Dropped puts the series back on Reading automatically.
3. Logging a lower chapter is allowed (a correction or rereading), but doesn't count in ReadingActivity.
4. A negative chapter is refused.
5. Changing a status to the same status is refused.
6. A removed series gets no more progress or status changes. Its title is free again afterwards: adding it again makes a new series (new `series_id`) that starts over. Chapters read in the old series keep counting in the statistics.
7. When adding, you choose the status (Reading by default). Another starting status is stored as `SeriesStarted` followed by `StatusChanged`; the starting chapter doesn't count as read, so adding a finished series doesn't inflate your statistics. After that, any status can go to any other.
8. Genres come from a fixed list (`GENRES` in `app/reading/genres.py`). `GenresChanged` always holds the complete new list; saving the same genres again produces no event. When adding, the genres go along as `GenresChanged` after `SeriesStarted`.
9. A cover is optional. A cover that fails (not an image, too large, network error) never blocks saving the series.
10. **Plan to Read** is the backlog: series you still want to start. Adding as Plan to Read follows rule 7 (`SeriesStarted` + `StatusChanged(READING → PLAN_TO_READ)`). Logging progress puts it on Reading through rule 2. Plan to Read never counts as "reading" (Currently reading, home screen).

Chapters are `float`, so chapters like 45.5 are possible.

**Gathering dust** (a panel on the Dashboard): series on Reading without progress or a status change in more than `STALE_READING_DAYS` = 21 days (at the top of `app/reading/web.py`), quiet the longest first (`LibraryProjection.stale(now, days)`). Plan to Read, On hold, Completed and Dropped never gather dust. Per series: +1 chapter, On hold or Drop (with confirmation), through the ordinary endpoints; **Not now** only hides it until you reload the page (no event). The home screen shows "N gathering dust".

## Watching (done)

| Part        | Contents |
| ----------- | ------ |
| Kinds       | Series, Anime, Movie |
| Statuses    | Watching, Rewatching, Completed, On hold, Dropped, Plan to Watch (no episodes, no ratings) |
| Events      | `ShowAdded` (with a starting status and optional `cover`), `ShowStatusChanged`, `ShowGenresChanged`, `ShowRemoved` |
| Commands    | `AddShow`, `ChangeShowStatus`, `SetShowGenres`, `RemoveShow` |
| Read models | Watchlist, WatchActivity (finished per day) |

Rules:

1. A title may be in the list once per kind: *Dune* the film and *Dune* the series can both be there.
2. Any status can go to any other; to the same status is refused.
3. Finished only counts when you **set** something to Completed. Adding as Completed is history and doesn't count in "Finished this month".
4. Genres from its own fixed list (`WATCH_GENRES` in `app/watching/genres.py`); otherwise as with Reading.
5. After removing, the title is free again.
6. **Plan to Watch** is the backlog: adding is simply `ShowAdded(status=PLAN_TO_WATCH)`. It doesn't count as "watching"; setting it from Plan to Watch to Completed does count in "Finished this month" (rule 3).
7. **Rewatching**: something you have seen before, once more. Counts as "watching now" (and can gather dust); setting it to Completed again counts as finished.

**Pick something** (the Library of Reading and Watching, only with a backlog): shows a random title from Plan to Read/Watch, optionally within one genre. *Another one* picks a different one; *Start* sends the ordinary status change (`ChangeStatus`/`ChangeShowStatus`). The picking itself (`GET /ui/<tracker>/pick`) only reads: no command, no event.

**Gathering dust**: as with Reading, but for titles on Watching that haven't been added or changed status in more than `STALE_WATCHING_DAYS` = 30 days (at the top of `app/watching/web.py`; `WatchlistProjection.stale(now, days)`). Looser, because without episodes "nothing changed for a long time" is a weaker signal. Changing genres doesn't count as activity.

The event classes are called `Show...`, because the event store only stores the class name; names must be unique across all trackers.

## Listening (done)

What I listen to on Spotify, tracked automatically. There is no form: plays come in through the Spotify sync (or `POST /listening/plays`).

| Part        | Contents |
| ----------- | ------ |
| Events      | `TrackPlayed` (played_at, track_id, track, artists, album, album_id, duration_ms, ms_played, source) |
| Commands    | `RecordPlay` |
| Streams     | one per moment: `play-<played_at in ISO, UTC>` |
| Read models | RecentlyPlayed (last 50), ListeningActivity (minutes per day and per week), TopArtists and TopTracks (per month) |
| Tabs        | Dashboard (minutes this week, top 5 artists this month), History (latest plays), Progress (minutes per day/week, top lists) |

Rules:

1. A play at a moment that is already known is skipped: no error, the handler returns an empty list (the API then answers `200` with `"stored": false` instead of `201`). The sync fetches overlapping data on purpose.
2. A play without a `track_id`, or with a negative `duration_ms` or `ms_played`, is refused (`DomainError`).
3. Plays are never changed or removed.
4. Minutes: `ms_played` when known, otherwise `duration_ms`. The Spotify API only gives a track's length, not how long you really listened; so minutes are an upper bound.
5. Days, weeks and months follow your own time zone; `played_at` is stored in UTC (a time without a time zone counts as UTC).
6. A track with several artists counts for each artist in TopArtists.

### Connecting Spotify

1. Create an app at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard). Set the **Redirect URI** to exactly `http://127.0.0.1:8000/listening/spotify/callback` (if Progen runs on another port: change it and also put it in `SPOTIFY_REDIRECT_URI`).
2. Put `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in `.env` and restart Progen.
3. Click **Connect Spotify** in Settings and give Progen access. Progen only asks for `user-read-recently-played`; a `state` parameter protects signing in against CSRF.
4. After coming back, Progen fetches your name and profile photo (for Listening's dashboard; after that again at most once a day) and runs a first sync right away. From then on Progen syncs every `SYNC_INTERVAL_MINUTES` minutes (30 by default) while the server runs, or right away through **Sync now**. **Disconnect** removes the tokens; your plays stay.

How it works (`app/listening/spotify.py`, an automation slice): the sync asks `GET /v1/me/player/recently-played?limit=50&after=<last played_at in ms>` and sends the same `RecordPlay` as the API for each track, with `source="api"`. Tokens live in `data/spotify_token.json`, not in the event store; the access token is refreshed automatically with the refresh token. On a 429 the background task waits out the `Retry-After`. Errors are logged (logger `progen.listening`) and never crash the app; the last sync and any error are shown in Settings. A lock makes sure the background sync, **Sync now** and the callback never run at the same time, so a play is never stored twice.

**Limitation:** Spotify returns at most the last **50** tracks per request. If you listen to more than 50 tracks between two syncs (with 30 minutes that is nearly impossible, but with Progen off for a day it isn't), the older ones can no longer be fetched through the API. Fill in missed tracks with an import (below).

### Importing your Spotify history

The live sync gets at most the last 50 tracks from Spotify. You bring in your whole history, and whatever the sync missed (the PC was off for a day), with the **Extended streaming history**:

1. Request it in Spotify: **Account › Privacy › Extended streaming history**. It takes a few days to weeks; you get a zip.
2. Unpack the zip. In Progen go to **Settings › Spotify › Import streaming history** and choose the `Streaming_History_Audio_*.json` files (several at once is fine).
3. You first see a preview, with nothing saved yet: how many records, how many are new, what is skipped and why, the period, and the top 5 artists of the new plays. A file that isn't an export is named.
4. Click **Import N plays**. Progen first makes a backup automatically ("saved automatically before an import") and then imports in the background, with progress in the panel. You can keep using Progen in the meantime, and the Spotify sync carries on too. At the end a message follows; the new plays show up in Listening and Wrapped right away, without a restart.

**You can do this as often as you like**: request a new export later and import it; only what Progen doesn't have yet is added. That way you fill in the gaps the live sync missed.

It also works from the command line (for example if the app doesn't start), with the same code:

```
.venv\Scripts\python.exe -m app.listening.export_import <files or folder> --dry-run
.venv\Scripts\python.exe -m app.listening.export_import <files or folder>
```

That makes a backup first too. A running server only sees plays imported this way after a restart (the projections are built at startup); the script says so at the end.

What is skipped, and why:

- **Not a track** (no `spotify_track_uri`): podcasts, audiobooks and local files.
- **Shorter than 30 seconds** (`MIN_MS_PLAYED`): the same limit Spotify uses for a "stream". Otherwise skipped tracks would inflate Top tracks.
- **Already in Progen**: **the same track within 30 seconds** (`MATCH_WINDOW`) of a play that is already there. The API gives times with milliseconds, the export whole seconds, and they can differ by a few seconds; that is why "exactly the same moment" isn't enough. That way a play the sync already had isn't added again, not even when you import the same file twice or a newer export overlaps the old one.

How it works (`app/listening/export_import.py`, an automation slice; the panel in `import_web.py`): each track goes through the same `RecordPlay` as the live sync, with `source="export"`; the aggregate's rules stay as they are. For "within 30 seconds", the import builds an index of all existing plays at the start (per track the sorted times). From the export we only keep what is in `TrackPlayed`; `ip_addr`, `platform`, `conn_country` and the rest go nowhere, and the uploaded file isn't stored. `ms_played` is the real listening time; a track's length isn't in the export, so `duration_ms` = `ms_played` (all minutes are counted with `ms_played`).

Speed: the event store has an index on `stream_id`, and the import saves 2,000 plays per transaction (`EventStore.transaction()`), with every `RecordPlay` still going through the aggregate. Between two portions the app and the sync can simply carry on. Measured: 100,000 plays in ±10 seconds, with all projections live (with a commit per play it would take ±6 minutes). With that many plays, starting Progen takes a few seconds longer.

## Planner (done)

Appointments and plans, as a replacement for the calendar on your iPhone (`#planner`, tabs **Week**, **Upcoming**, **Day** and **Someday**). **Upcoming** is one list with everything that is coming: one-off plans from today on that aren't done yet, under Today, Tomorrow, This week, Next week and then per month, and below that each repeat once ("Every Mon, Thu · 18:00", "Every month on the 12th") with its next occurrence that isn't skipped or ticked off. A search field filters on title and note; a click opens the plan. Read only (`AgendaProjection.upcoming`, `next_occurrence` in `schedule.py`). Below Someday there is a short **Done** list with the last 10 ticked-off plans without a date; **Not done** puts one back (`ReopenPlan`).

| Part        | Contents |
| ----------- | ------ |
| Plans       | Title, date (no date = Someday), time (no time = all day), duration, note, repeat |
| Repeat      | Every day, every week on one or more weekdays, every month on the same day (for the 31st in a short month: the last day), optionally until an end date |
| Events      | `PlanAdded`, `PlanRescheduled`, `PlanEdited`, `PlanDone`, `PlanReopened`, `PlanSkipped`, `PlanRemoved`, `ReminderSettingsChanged` (own stream `planner-settings`) |
| Commands    | `AddPlan`, `ReschedulePlan`, `EditPlan`, `MarkPlanDone`, `ReopenPlan`, `SkipPlan`, `RemovePlan`, `ChangeReminderSettings` |
| Read models | Agenda (day, week, Someday, reminders), PlannerActivity (how often a plan was moved; not shown yet) |

Date and time are **local time** (the PC's clock), not UTC like an event's `at`. Repeated occurrences are worked out in the projection (`app/planner/schedule.py`), not stored as separate events.

Rules:

1. A title may not be empty.
2. A time without a date is refused; so is a repeat without a date (so a repeat can't go to Someday).
3. Ticking off, reopening, moving, editing or skipping a removed plan is refused.
4. Ticking off goes per occurrence: with a repeat, per occurrence. Ticking off something that is already done is refused; reopening only works when it is done. A plan for Someday is done on the day you tick it off.
5. `PlanDone` or `PlanSkipped` on a day the plan doesn't fall on is refused. Skipping only works with a repeat, and each occurrence only once.
6. Moving to exactly the same date and time, or editing without changing anything, produces no event. Moving a repeat moves the whole series.
7. Weekly without weekdays = on the weekday of the start date; a repeat can't end before it starts.
8. Reminders are one setting for all plans (Settings > Planner reminders): 1 day and 3 hours before by default, both on. All-day plans get their reminder at 9:00 (adjustable): a day or more before on that day, shorter on the day itself. Saving the same setting again produces no event.

**Quick entry in plain words** (`app/planner/parse.py`, a small parser of our own). One field at the top; while you type, a preview shows what Progen makes of it, Enter saves. Whatever isn't recognized is the title. Dutch and English mixed:

| You type | Progen makes it |
| ------- | ------------------ |
| `Tandarts vr 14u` | Tandarts · the next Friday · 14:00 |
| `overmorgen om 9 bellen` | bellen · the day after tomorrow · 9:00 |
| `dentist tomorrow 3pm` | dentist · tomorrow · 15:00 |
| `12 okt verjaardag Sam`, `Party 12/10 20u30` | day first; without a year the next one |
| `Yoga next week tue 18:00`, `Kapper wo volgende week` | that weekday in the next week |
| `Sporten elke ma en do 7:00`, `every monday standup 9:30` | weekly on those days |
| `Vitamines elke dag`, `Huur elke maand 1 nov` | daily / monthly |
| `Kapper volgende maandag`, `Film vanavond 20u`, `Tandarts om 9 uur` | the next Monday / today 20:00 / 9:00 |
| `Meeting 14:00-15:30`, `Lunch 12u-13u` | a start time with a duration |
| `Learn Korean` | Someday |

Days: vandaag/today, morgen/tomorrow, overmorgen, weekdays in full or short (ma … zo, mon … sun); always the next one, and today counts as long as the time hasn't passed yet. Times: `14:00`, `14.30`, `14u`, `20u30`, `14 uur`, `om 9`, `9am`/`3pm`, and a time range like `14:00-15:30` (then the duration too); vanochtend/vanmiddag/vanavond/tonight = today, morgenavond = tomorrow; only a time = today (or tomorrow if it has passed). Short weekdays that are also ordinary words (`do`, `zo`, `ma`, `di`, `wo`, `sun` …) only count next to a time or after `op`/`on`/`elke`/`every`, so "do the dishes" and "zo snel mogelijk" simply stay titles. With **More** you set the note, duration and repeat precisely.

**To your iPhone:** in a plan's window, **Send to iPhone** gives an `.ics` file (`GET /ui/planner/<id>.ics`, written by hand following RFC 5545): the plan with its repeat (RRULE), skipped occurrences (EXDATE) and a reminder (VALARM) for each reminder in Settings at that moment. Mail it to yourself and open it on your iPhone.

**Notifications in Windows:** every minute the icon by the clock asks `GET /planner/due?since=…` (read only, no event) and shows new reminders as notifications, for example "Tomorrow: Tandarts at 14:00" or "In 3 hours: Tandarts at 14:00". The icon remembers what it already reported in memory; after a restart it reports nothing from before the start. **This only works while Progen is running**: so turn on **Start with Windows** in Settings.

## Wrapped

A year in review across Reading, Watching and Listening (`#wrapped`, or the Wrapped card on the home screen). Read only: no events or commands of its own. `WrappedProjection` is built from the events that already exist, so it works right away with all old data, and follows along live after that. Wrapped isn't a tracker: it isn't in Settings or the navigation bar and has no backup of its own (everything is already in the events).

| Tracker   | What is counted per year |
| --------- | ---------------------------- |
| Reading   | Chapters read, top 5 series, genres, newly started, finished, longest reading streak |
| Watching  | Finished per kind, genres of what you finished, their covers |
| Listening | Minutes (and hours), top 5 artists, top 5 tracks, busiest listening day |
| Together  | Chapters / finished / minutes per month, busiest month, most active day, the years with data |

Rules:

1. Days, months and years follow the local time zone (the event's `at`, for Listening `played_at`).
2. Chapters: only forward counts (`chapter - previous_chapter > 0`), just like ReadingActivity.
3. Reading's genres: a series' chapters read are split evenly over its genres, following the **last** `GenresChanged`. Titles and covers of removed series stay in the overview.
4. A removed and re-added series (new `series_id`, for example to get a cover) counts as **one series** in Wrapped: the same title the way Reading compares it (case and surrounding spaces don't count). Chapters of all versions count together; the title and cover come from the newest version (without a cover: the newest older one with a cover), the genres from the newest version with genres. Started only counts in the year of the first version, even if you really start it again long after.
5. **Finished** (Reading): a `StatusChanged` to Completed, and a series counts only once per year. The **starting status** doesn't count: adding as Completed is stored as `SeriesStarted` + `StatusChanged(READING → COMPLETED)` (Reading rule 7). A `StatusChanged` is a starting status if it directly follows the `SeriesStarted` of the same series (no other Reading event in between; plays from the Spotify sync don't count) **and** within 5 seconds. That way "added, and finished an hour later" does count.
6. **Started** (Reading): a series counts once, in the year you started it. Adding as Plan to Read or as Completed isn't starting; taking a series out of Plan to Read is.
7. **Finished** (Watching): only a `ShowStatusChanged` to Completed, just like WatchActivity. Adding as Completed doesn't count; from Plan to Watch to Completed does.
8. Listening minutes: `ms_played` when known, otherwise `duration_ms` (like ListeningActivity).
9. Busiest month and most active day: activity = chapters + finished titles + tracks played.
10. The **Compare with …** button (the previous year) shows the difference with the year before next to the big numbers.

### Covers

- With "Add ..." you can search for a cover, upload an image yourself or paste a link. Searching is a helper endpoint (`GET /ui/<tracker>/covers/search`), not a command or event.
- Watching searches AniList (anime), TVmaze (series) and TMDB (films and series; only with `TMDB_API_KEY`).
- Sources for Reading (`app/reading/cover_sources.py`), all asked at once:
  - [AniList](https://anilist.co): manga, manhwa and novels (`type: MANGA` also covers format `NOVEL`; there is no format filter). For many Chinese web novels only the manhua adaptation is here.
  - [MangaUpdates](https://www.mangaupdates.com): web novels too (type "Novel", no type filter). `hit_title` is the name MangaUpdates matched on, often an alternative name ("Coiling Dragon" for "Panlong"); it counts in the comparison.
  - [MyAnimeList](https://myanimelist.net) through [Jikan](https://jikan.moe): novels and light novels (`type=novel` and `type=lightnovel`, two requests), with genres and themes. Jikan allows at most 3 requests per second; Progen waits `JIKAN_MIN_INTERVAL` (0.4 s) between requests. On a 429 this source stops (whatever was already found counts). If Jikan is unreachable, Progen gives up after `JIKAN_CONNECT_TIMEOUT` (3 s) and skips the source for `JIKAN_COOLDOWN` (5 minutes), so searching doesn't keep waiting.
  - [Open Library](https://openlibrary.org): books, also web novels in print (Shadow Slave, for example)
  - [Google Books](https://books.google.com): optional with `GOOGLE_BOOKS_API_KEY` in `.env`; thumbnails that are too small (below `MIN_SOURCE_SIZE`) are skipped. The size check downloads through the same 5 MB limit as covers; a larger image counts as not big enough.
- A source is a function `source(client, search_term) -> list[SourceResult]` with `@cover_source("Name")` above it. Adding or removing one = changing the `SOURCES` list.
- Each source searches in three steps and stops as soon as something fits: the title as typed, normalized, and the longest single words (sources find nothing on a typo by themselves). All results are compared together with rapidfuzz, against **every** title a source returns (AniList: English, romaji, native and synonyms; MangaUpdates: title and `hit_title`; MyAnimeList: title, English, Japanese, synonyms and all `titles`); the best score counts. That way you also find "I Shall Seal the Heavens" when the source calls it "Wo Yu Feng Tian". Suffixes like "(Novel)" or ", Book 1" don't count; everything is sorted by best match and duplicate URLs disappear.
- If a source fails or hangs (deadline `SOURCE_DEADLINE`), we show the others. Only when all sources fail does an error follow.
- Short titles (up to 10 characters) must match more strictly, so "Frieren" doesn't find "Frieden". The part before a colon counts too ("Frieren" finds "Frieren: Beyond Journey's End"), but lower than an exact title, so a sequel always comes after the series itself.
- Adjustable at the top of `app/cover_search.py`: `COVER_MATCH_THRESHOLD`, `SHORT_TITLE_THRESHOLD`, `SUBTITLE_FACTOR`, `FALLBACK_WORDS`, `SOURCE_DEADLINE`, `MIN_SOURCE_SIZE`. Results are kept for 10 minutes per title, but only 1 minute if a source failed.
- When saving, the cover is downloaded and checked with Pillow, straightened (EXIF), cropped from the center to 300×450 (2:3) and stored as WebP with a UUID name in `data/covers/`. Only http/https, at most 5 MB, timeout 10 s. Only the file name is in the event.
- Genres: AniList (genres and tags with rank ≥ 60), MangaUpdates (genres), Google Books (categories) and Open Library (subjects) return genres. They are mapped onto the fixed list and checked in the form ahead of time; whatever you check or uncheck yourself stays when you browse to the next cover. Changing them later works through ⋯ > *Edit genres* on a card.
- Changing the cover of an existing series isn't possible yet; that needs a separate event (`SeriesCoverChanged`, for example).

## Sunny (the assistant)

Sunny is an AI assistant in the app: a round, dark button at the bottom right of every page except Settings. Click it and a chat panel slides in from the right. The conversation stays when you switch pages; **New chat** clears it, Esc closes the panel. The design is in `app/assistant/identity/` (IDENTITY, SOUL and USER together form the system prompt; TOOLS and OUTCOME describe what Sunny may do and when it is done).

What Sunny can do:

- **Answer questions from your own data**, without confirmation because nothing changes: "which track did I listen to most from 1 Sep to 15 Oct?", "how long haven't I read X?", "do I have plans next month?".
- **Track and change things in one sentence**: "I'm at chapter 120 of Solo Leveling", "set Severance to dropped", "I'm rewatching Frieren", "elke ma 12:00 gym". Sunny turns that into actions and first shows them as a **card** with one line per action (`Solo Leveling · chapter 119 → 120`, `New plan · gym · from Mon 12 Oct · 12:00 · every Mon`).
- **Nothing is saved before you click Confirm.** Confirm runs the actions through the same command handlers as the rest of the app (ordinary events, with a toast and a refresh of the open page); Cancel does nothing. **Removing** asks for confirmation once more after Confirm, with the name included.
- Never: Settings, backups, imports, connecting Spotify or shutting down. Sunny doesn't make up titles or numbers; whatever isn't in your tracker, it says so.

**Hold to talk:** hold the microphone button next to the input field, speak ("I'm at chapter 340 of Solo Leveling"), let go, and it goes to Sunny as if you had typed it; a short tap or sliding off sends nothing. The speech recognition is the browser's own (Edge sends the audio to Microsoft's speech service, Safari to Apple's); only the text reaches Sunny. Browsers without it don't show the button.

**Talking back:** after a spoken message (never a typed one) Sunny's answer is read aloud by the browser, and then it listens again without holding the button, so you can just keep talking; silence (3 seconds by default), "thanks"/"bedankt", a tap on the mic, Escape, closing the panel or typing ends it. A bare "yes"/"ja" or "no"/"nee" for an open card clicks Confirm or Cancel, except deleting, which always needs a click. The answer is read sentence by sentence while it streams in, and **After an answer, keep listening for** (Settings › Sunny: Don't, 3 or 5 seconds, per device) sets how long Sunny waits for you before the conversation ends. The voices are picked per language, or chosen per device in Settings › Sunny; "Same as English voice" reads Dutch with the English voice too (Edge's multilingual voices such as Brian do that well, and Automatic already does it for a voice with "Multilingual" in its name).

**"Hey Sunny":** with **Listen for "Hey Sunny"** on (Settings › Sunny, off by default, per device), saying "Hey Sunny" or "Sunless" opens Sunny and starts a voice conversation; "Hey Sunny, I'm at chapter 50 of Solo Leveling" in one go sends it straight away. A thin ring on Sunny's button shows the microphone is listening. It only works while Progen is open and visible (not minimised or in the background), it pauses while you hold the mic, while Sunny talks or answers, and on Settings, and the browser's speech service hears the audio the whole time (Microsoft in Edge, Apple in Safari). On the iPhone it needs Progen open with the screen on, and Safari may refuse to restart listening without a tap; then the switch turns off quietly with the hint "Tap the mic to talk". (The real iPhone way will later be "Hey Siri, Sunny" through a Siri shortcut.)

Setting up: put `ANTHROPIC_API_KEY` in `.env` (see `.env.example`) and restart Progen. Without a key, the panel only shows "Add ANTHROPIC_API_KEY to .env to summon Sunny from the shadows".

**Costs:** every message to Sunny goes to Anthropic's API and costs a little of your API credit. By default Sunny uses **Claude Haiku 5.5** with low effort and at most 1024 tokens per answer; the fixed system prompt and the tool list are cached, only the last 10 messages go along, and Sunny looks up one title instead of your whole library. Set a monthly limit in Anthropic's console. **You choose the model in Settings › Sunny**: Claude Haiku 5.5 (the default, fastest and cheapest), Claude Sonnet 5.5 (smarter, ±20× the price) or Claude Opus 5.5 (the smartest and the most expensive), with the price per model next to it. A change applies right away, without a restart. The choice is stored as an event (`AssistantSettingsChanged`, own stream `assistant-settings`), so it is in your backups; the models themselves are in `app/assistant/models.py`. Effort (low) and 1024 tokens per answer are the same for every model. The panel in Settings also says whether there is an `ANTHROPIC_API_KEY` in `.env` (the key itself is never shown), and below the chat panel's heading you see which model Sunny uses. What goes to Anthropic: your message, the latest messages of the conversation and the data Sunny looks up for that answer; nothing else.

**If the API doesn't cooperate**, Sunny says so in a warning balloon of its own (no crash), with **Try again** to send the same message again. Open Confirm cards keep working: they don't need the API.

| Sunny says | What you do |
| --- | --- |
| "My key expired. Or someone revoked it. …" | Create a new key at console.anthropic.com, put it in `.env` as `ANTHROPIC_API_KEY` and restart Progen. |
| "I'm out of credits. …" | Top up your credit in the console under Billing and try again. |
| "You hit the monthly limit you set. …" | Raise the monthly limit in the console, or wait until next month. |
| "This key isn't allowed to do that. …" | Check the key's workspace and permissions in the console. |
| "Too many requests at once. …" / "Anthropic's servers are having a bad day. …" | Wait a moment and try again (the SDK already retries a few times itself). |
| "I can't reach Anthropic. Is the internet on?" | Check your internet connection. |

With a key, permission, credit or limit problem, the message (with **Open console**) is also shown at the top as soon as you open the panel, until the next call succeeds again. Recognition is in `classify()` in `app/assistant/agent.py`; the search words for "out of credit" and "monthly limit" are constants at the top (`CREDIT_WORDS`, `SPEND_LIMIT_WORDS`), for when Anthropic changes the text. An unknown error gives "Something went wrong talking to Anthropic (status X)." and goes into the log in full.

How it works (`app/assistant/`): `prompt.py` builds the system prompt (the fixed part with `cache_control`, today's date as a separate block after it); `tools.py` has the tools as JSON schemas with `strict: true` (read tools read the projections directly, write tools only return a proposal); `agent.py` runs the loop (at most 5 rounds per message; the history goes along as plain text); `session.py` keeps the conversation (messages, proposals, Confirm/Cancel; during the API call, Confirm, Cancel and New chat keep working) and `web.py` has the thin routes. The conversation and proposals only live in the app's memory, not in the event store. Tests use a fake client and never talk to the real API.

## Security

Progen only runs on your own computer (`127.0.0.1`), but your browser can also send requests on behalf of a website you have open. So (`app/security.py`, applies automatically to every route):

- **Only `127.0.0.1` and `localhost` as an address**, plus the host names in `PROGEN_EXTRA_HOSTS` (your phone through Tailscale, see above). A website that points its own name at 127.0.0.1 (*DNS rebinding*) gets `400` everywhere, reading included.
- **Through an extra host you sign in first** (`app/auth.py`): a scrypt password hash in `.env`, a signed session cookie (HttpOnly, SameSite=Lax, Secure over https, 30 days), rate limiting on wrong passwords. `X-Forwarded-Proto` from `tailscale serve` is only believed from 127.0.0.1, and a request that came through a proxy always needs the login, even when it claims to be `localhost`.
- **Changes (POST, PUT, PATCH, DELETE) must show that they come from Progen or your own script**, with something another website can't send along without permission (CORS), and Progen doesn't give that:
  - `HX-Request: true`: the web page itself (htmx always sends this);
  - `Content-Type: application/json`: the JSON API and scripts (also for a `DELETE` without a body);
  - `X-Progen-Shutdown: 1`: Stop Progen.
- **If the browser says a request comes from another site** (`Origin` or `Sec-Fetch-Site: cross-site`), it is refused, even with the right header.
- Reading (`GET`) is always allowed; another website can't read the answers anyway (same-origin policy).
- Progen is never opened to the internet; for your phone it goes through your private Tailscale network.

---

## Roadmap

### 1. Cleaning up before the second domain

- [x] Move `DomainError` from `app/reading/aggregate.py` to a shared file `app/domain.py`, so every domain can use it. The exception handler in `main.py` then catches it for all domains.
- [x] Update `requirements.txt`: only what the app imports directly, pinned; pytest in `requirements-dev.txt`.
- [x] Check that `.venv/` and `data/` are in `.gitignore`.

### 2. Sleep domain

Manual entry first; automatic importing comes later (see item 5).

- [ ] Make an event model on a board (trigger → command → event → read model).
- [ ] Answer the open design questions:
  - What is a stream's id? Proposal: the date of the night (e.g. `2026-09-30`), so there can only be one record per night.
  - Which fields? Proposal: bedtime, wake-up time, optionally quality (1–5).
  - How do I correct a mistake? Through a separate event (e.g. `SleepCorrected`), not by changing the old event.
- [ ] Possible events: `SleepRecorded`, `SleepCorrected`.
- [ ] Possible read models: SleepThisWeek, average sleep duration per week.

### 3. Fitness domain

- [ ] Make an event model.
- [ ] Possible events: `WorkoutLogged` (kind, duration, optionally distance or sets).
- [ ] Possible read models: WorkoutHistory, totals per week.

### 4. Dashboard

- [x] Extend the web page with a section per domain.
- [x] Add charts: chapters per week, finished per month, listening minutes (CSS bars, no CDN needed). Sleep duration follows with the sleep domain.
- [x] Combine domains in a new projection: **Wrapped**, the year in review across all trackers (see [Wrapped](#wrapped)). Works right away with all old data, because the projection is built from the events.
- [ ] Connections between domains, for example: do I read more after a good night's sleep? (once there is a sleep domain)

### 5. Automatic importing

- [ ] Choose which wearable or app delivers the sleep and fitness data, and find out whether it has an API or export.
- [ ] Build an importer as an **automation slice**: a script that runs on a schedule, fetches new data, and sends exactly the same commands as the web page. The domain logic doesn't need to change for it.
- [ ] Prevent the same data from being imported twice (the stream id per night from item 2 helps here).

### 6. Security and hosting

- [x] A password before the app is reachable from another device: sign-in through Tailscale (`PROGEN_EXTRA_HOSTS`, see [Using Progen on your phone](#using-progen-on-your-phone-tailscale)).
- [ ] Reachable without Tailscale or with the PC off: not planned for now (that needs real hosting).
- [ ] Choose a place to run it (a Raspberry Pi at home, or a small VPS).
- [x] Backups of `data/tracker.db` and covers, with restoring (Settings).

### 7. Sunny, the assistant

- [x] Answer questions and propose actions in plain words; everything that writes only after Confirm (trust phase 1 from IDENTITY.md).
- [x] Missing pieces: top tracks and artists over a free period, the Rewatching status, plans in a period.
- [ ] Phase 2: simple logs (e.g. chapter +1) without Confirm, with an Undo button.
- [ ] Phase 3: a "heartbeat", for example a morning summary of your day.
- [x] Being able to switch models in Settings (Haiku, Sonnet or Opus 5.5).

### Ideas for later

- A rating or notes per series (new events, e.g. `SeriesRated`).
- Exporting all data to JSON or CSV.
- New domains as needed.

---

## How to: adding a new domain

Every domain follows the same steps as Reading and Watching. Existing domains don't need to change for it.

1. Make an **event model**: slices with trigger, command, event and read model, plus the rules as given/when/then.
2. **`app/<domain>/events.py`**: events as `@dataclass(frozen=True)`, names in the past tense, with an `at` field. Fields may be `datetime`, `date`, `time`, enums, tuples, optional (`X | None`) and nested frozen dataclasses; the event store turns them back into the right type. End the file with an `EVENT_TYPES` list.
3. **`aggregate.py`**: build the state in `_apply`, check the rules in the decision methods. Tests in `tests/<domain>/`.
4. **`commands.py`**: commands and a command handler (load → decide → save). Tests with a `:memory:` event store.
5. **`projections.py`**: read models with an `apply(event)` method. Also test that a rebuilt read model equals the live read model.
6. **`api.py`**: a `create_<domain>_router(...)` function. `GET` reads from projections, `POST` sends commands.
7. Wire it up:
   - add the domain's `EVENT_TYPES` to `ALL_EVENT_TYPES` in `app/event_types.py`
   - in `app/main.py`: add the new projections to `projections`
   - create the handler and add the router with `include_router`
   - add a `Tracker(...)` to `trackers` (name, icon, event after a change, summary for the home screen); then it shows up on the home screen, in Settings and in every backup by itself
8. **Web page**: `web.py` with `/ui/<domain>/dashboard`, `/library`, `/progress` and (if you can add something) `/add-form`; other tabs are possible too, like `/history` for Listening (pass `tabs` to `ui.tracker_tabs`), templates named `<domain>_*.html` with `ui.tracker_tabs(...)` at the top, a link in the navigation (`index.html`) and a line in `TRACKERS` in `trackly.js`. Shared helpers for charts are in `app/charts.py`, for covers in `app/covers.py` and `app/web.py`, and the clock is `app/clock.py`.
   - Event classes must be unique across all domains (the event store only keeps the class name).
9. `python -m pytest` must stay green, without warnings.

---

## Important agreements

- **Never change existing events.** Old events in the database must always stay readable. Adding a field is only allowed with a default value. If the meaning changes, make a new event.
- **Never rename an event class.** The class name is in the database (column `type`) and is used to rebuild events.
- **Never write to the database directly.** Everything goes through commands, so the rules are always checked.
- **First the test, then the code.** Every rule from the event model becomes a given/when/then test.
- **Use Claude Code for small pieces**, not for whole domains at once: let it explain, review or write one file, so I keep understanding how everything works.
