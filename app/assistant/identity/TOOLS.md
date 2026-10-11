# Sunny: tools (step E "Equip" + step N "Narrow")

One agent, one lane: Sunny only works on the tracker data. Tools call the existing **command handlers and projections directly** (no HTTP round trip). Every tool returns **compact JSON** (only the fields needed) to save tokens.

## Read tools (run straight away, no confirmation)
| Tool | Input | Built on | Status |
|---|---|---|---|
| `find_item` | `query`, optional `domain` (reading/watching/plan) | library / watchlist / agenda, fuzzy title search | new (small) |
| `reading_overview` | optional `status` | library projection: title, chapter, status, `updated_at` | exists |
| `watching_overview` | optional `status` | watchlist projection | exists |
| `top_tracks` | `from`, `to`, `limit` | listening plays | **needs a free period** (now per month only) |
| `top_artists` | `from`, `to`, `limit` | listening plays | **needs a free period** |
| `listening_minutes` | `from`, `to` | activity projection | exists (per day) |
| `plans_between` | `from`, `to` | agenda | **new: range** (now per day only) |
| `history_between` | `from`, `to`, optional `domain` (reading/watching) | reading and watch history projections: chapters (first → last), status changes, finished shows, totals | new |
| `notices` | none | the same notices as on Sunny's button (overdue plans, a streak about to end, dusty titles, an old backlog), minus the ones put away with "Not today"; fixed lines, no AI | new |
| `item_history` | `domain` (reading/watching), `id` from `find_item` | the same history: added, every status change with its date, times completed/rewatched, reading days; earlier removed versions included | new |

History comes from the events that already exist, so it covers all old data. Limits: a show added as already completed has no finish date, and Watching has no episodes, only status periods. A chapter correction on the same day (12 → 120 → 12) takes the typo back.

## Write tools (only propose; execute after Confirm)
| Tool | Input | Built on |
|---|---|---|
| `set_reading_progress` | `series_id`, `chapter` | reading progress command |
| `set_reading_status` | `series_id`, `status` | reading status command |
| `add_series` | title, kind | reading add command |
| `set_watch_status` | `show_id`, `status` (incl. **new `rewatching`**) | watching status command |
| `add_show` | title / TMDB id | watching add command |
| `add_plan` | `text` in plain language, e.g. "elke ma 12:00 gym" | existing planner parser (`app/planner/parse.py`) |
| `plan_done` / `plan_skip` / `plan_reschedule` / `plan_edit` | `plan_id`, ... | planner commands |
| `delete_item` | domain + id | delete commands; **extra confirmation with the name** |

## How Confirm works
1. Progen types a task. The model calls read tools when needed, then write tools.
2. Write tools are **not executed**. The backend stores them as a "pending action" and shows a card: `Solo Leveling: chapter 119 → 120` with **Confirm / Cancel**.
3. Confirm → the backend runs the command (a normal event in the event store, so it lands in the history like any other change). Sunny says in one line that it's done.
4. Cancel → nothing happens. Sunny asks what should be different.

## Model and token saving
- Default model: **Claude Haiku 5.5** (cheap and fast). Optional switch in Settings to Sonnet 5.5 for harder questions.
- The system prompt (identity + soul + user + tool list) is cached (prompt caching), so it costs close to nothing per message.
- Chat history: only the last ~10 messages are sent along. A new page keeps the panel, and "New chat" clears it.
- Sunny never fetches the whole library when one title is enough (`find_item` first).
