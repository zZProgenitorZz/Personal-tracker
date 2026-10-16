# Sunny: tools (step E "Equip" + step N "Narrow")

One agent, one lane: Sunny only works on the tracker data. Tools call the existing **command handlers and projections directly** (no HTTP round trip). Every tool returns **compact JSON** (only the fields needed) to save tokens.

17 tools: 7 read, 10 write. Only the write tools are **strict** (the API allows at most 20 strict tools); read tools turn bad input into an error the model can fix, and may leave out a nullable field.

## Read tools (run straight away, no confirmation; not strict)
| Tool | Input | Built on |
|---|---|---|
| `find_item` | `query`, optional `domain` (reading/watching/plan) | library / watchlist / agenda: nicknames first, then fuzzy and sound-alike title search (shared with the quick log); every row has a `score` (0-100) |
| `library` | `domain` (reading/watching), optional `status` (checked against that domain) | library projection (title, chapter, status, `updated_at`) or watchlist projection (title, kind, status) |
| `listening_stats` | `from`, `to`, optional `limit` (5, max 25) | listening projections: minutes, days listened, total `plays`, top artists and top tracks; `top_track_tie` / `top_artist_tie` = how many share first place (only when tied, counted over the whole period) |
| `plans_between` | `from`, `to` | agenda, repeats worked out |
| `history_between` | `from`, `to`, optional `domain` (reading/watching/fitness) | reading and watch history projections: chapters (first → last), status changes, finished shows, totals; fitness: gym visits (day, start, end, minutes; null = departure missed) and steps per day, latest 40 rows, totals over the whole period. Without a domain, fitness is only included when there is something. Fitness has no write tool and isn't in the write tools' `domain` |
| `notices` | none | the same notices as on Sunny's button (overdue plans, a streak about to end, dusty titles, an old backlog), minus the ones put away with "Not today"; fixed lines, no AI |
| `item_history` | `domain` (reading/watching), `id` from `find_item` | the same history: added, every status change with its date, times completed/rewatched, reading days; earlier removed versions included |

History comes from the events that already exist, so it covers all old data. Limits: a show added as already completed has no finish date, and Watching has no episodes, only status periods. A chapter correction on the same day (12 → 120 → 12) takes the typo back.

## Write tools (only propose; execute after Confirm; strict)
| Tool | Input | Built on |
|---|---|---|
| `set_reading_progress` | `series_id`, `chapter` | reading progress command; a step forward of at most 50 as the only change in an answer is saved right away with Undo (the tool result says so), unless that is off in Settings |
| `set_status` | `domain` (reading/watching), `id`, `status` (checked against the domain; incl. `rewatching` for watching) | reading or watching status command |
| `add_item` | `title`, `kind` (novel/manhwa → reading, movie/series/anime → watching), optional `status` (default reading / watching) | reading or watching add command |
| `add_plan` | `text` in plain language, e.g. "elke ma 12:00 gym" | existing planner parser (`app/planner/parse.py`) |
| `plan_done` / `plan_skip` / `plan_reschedule` / `plan_edit` | `plan_id`, ... | planner commands |
| `remember_alias` | `heard`, `series_id` | a nickname for a misheard title (an event, `AliasAdded`), after Progen told you which one was meant |
| `delete_item` | domain + id | delete commands; **extra confirmation with the name** |

**Without Claude:** a short "Solo Leveling 120" (or "ik ben bij hoofdstuk 120 van Solo Leveling") is logged by `app/assistant/quick.py` before any API call, when the title matches one series clearly and the step is small; Sunny answers with a fixed line and an Undo button.

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
