# Personal-tracker

Een persoonlijke API die gegevens over mijn eigen leven op één plek verzamelt, in eigen beheer. Het begon met het bijhouden van wat ik lees (**Reading**: novels en manhwa); daarna kwamen **Watching** (films, series en anime) en **Listening** (wat ik op Spotify luister). Slaap, fitness en andere trackers kunnen volgen.

Het startscherm (`#home`) toont een kaart per tracker. Een klik brengt je naar de eigen omgeving van die tracker, met de tabbladen Dashboard, Library en Progress (`#reading`, `#watching/library`, ...).

De applicatie is opgezet met **event modeling** en **event sourcing**: alles wat er gebeurt wordt opgeslagen als onveranderlijke events, en alle overzichten (read models) worden daaruit opgebouwd. Daardoor gaat er nooit geschiedenis verloren en kan ik later nieuwe overzichten maken over oude data.

---

## Snel starten

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Mac/Linux
pip install -r requirements.txt

uvicorn app.main:create_app --factory --reload
```

Instellingen staan in `.env` in de projectmap (niet in git). Begin met een kopie van `.env.example`: `copy .env.example .env`. Alle instellingen zijn optioneel:

```
GOOGLE_BOOKS_API_KEY=...          # betere covers uit Google Books (zonder sleutel valt Google vaak af)
TMDB_API_KEY=...                  # covers en genres van films en series (Watching)
SPOTIFY_CLIENT_ID=...             # Listening: koppeling met Spotify (zie Listening)
SPOTIFY_CLIENT_SECRET=...
SYNC_INTERVAL_MINUTES=30          # hoe vaak Progen automatisch met Spotify synct
PROGEN_BACKUP_DIR=D:\Backups\Progen  # andere back-upmap dan OneDrive\Progen-backups
```

Paden zonder aanhalingstekens schrijven. Een waarde die al in de omgeving staat, gaat voor op `.env`.

- Webpagina: http://127.0.0.1:8000
- API-documentatie (Swagger): http://127.0.0.1:8000/docs
- Tests draaien: `python -m pytest`

Data van **alle trackers** staat in één bestand, `data/tracker.db`. **Dit bestand is de enige bron van waarheid**: alle read models worden er bij het opstarten uit opgebouwd. Covers staan ernaast in `data/covers/`. Geen van beide staat in git; maak dus back-ups (zie hieronder).

### Back-ups

- **Maken:** Settings > *Back up now*, of `python -m app.backup` (werkt ook als de app niet draait; `--list` toont de back-ups).
- **Waar:** standaard `OneDrive\Progen-backups\`, zodat er ook een kopie in de cloud staat. Een andere map kies je met `PROGEN_BACKUP_DIR` in `.env`.
- **Wat:** één back-up bevat alle trackers tegelijk, zodat ze altijd bij elkaar passen. Per back-up een map met datum en tijd als naam (`2026-10-06_21-05-33`) met `tracker.db`, `covers/` en `info.json`. De kopie wordt gemaakt met de back-up-API van SQLite (veilig terwijl de server draait) en daarna gecontroleerd; een mislukte back-up laat niets achter.
- **Hoeveel:** de 10 nieuwste blijven (`KEEP_BACKUPS` in `app/backup.py`); oudere worden verwijderd.
- **Terugzetten:** Settings > *Restore* bij de gewenste back-up. Progen maakt eerst automatisch een back-up van de huidige stand ("saved automatically before a restore"), zodat ook terugzetten terug te draaien is. Daarna worden de events vervangen, ontbrekende covers teruggezet en de read models opnieuw opgebouwd.
- **Niet in een back-up:** `data/spotify_token.json` (je Spotify-toegang) en `data/spotify_sync.json` (wanneer de laatste sync was). Na het terugzetten op een andere computer koppel je Spotify gewoon opnieuw.
- **Met de hand terugzetten** (als de app niet start): stop de server, kopieer `tracker.db` en `covers/` uit de back-upmap naar `data/` en start de server opnieuw.

---

## Architectuur

```
Trigger (webpagina / importer)
   ↓
Command            verzoek, kan geweigerd worden        (StartSeries, LogProgress, ...)
   ↓
Command handler    laden → aggregate laten beslissen → opslaan
   ↓
Event store        append-only SQLite-tabel             (SeriesStarted, ProgressLogged, ...)
   ↓
Projecties         read models in het geheugen          (Library, ReadingActivity)
   ↓
API (GET)          leest alleen uit read models
```

Principes:

- **Events zijn feiten.** Ze zijn `frozen` dataclasses, in de verleden tijd, en worden nooit aangepast of verwijderd.
- **Commands zijn verzoeken.** Gebiedende wijs, en ze kunnen geweigerd worden met een `DomainError`.
- **De aggregate beslist**, slaat zelf niets op, en is daardoor puur te testen.
- **CQRS:** `POST`-endpoints sturen commands, `GET`-endpoints lezen alleen uit read models.
- **Projecties zijn weggooibaar.** Ze worden bij elke start opnieuw opgebouwd uit de event store en synchroon bijgewerkt via `store.subscribe`.
- **Vertical slices:** elk domein heeft een eigen map met alles erin. Een nieuw domein is een nieuwe map.

### Projectstructuur

```
app/
  main.py              koppelt alles: event store, projecties, handlers, routers
  eventstore.py        SQLite event store (append, load_stream, load_all, subscribe)
  backup.py            back-ups maken, bewaren (10 nieuwste) en terugzetten; ook los te draaien
  domain.py            DomainError (gedeeld door alle trackers)
  covers.py            covers downloaden, controleren en als 300×450 WebP bewaren (gedeeld)
  cover_search.py      covers en genres zoeken bij meerdere bronnen tegelijk, fuzzy matching, cache (gedeeld)
  genres.py            GenreSet: een vaste genrelijst en genres uit bronnen daarnaar vertalen (gedeeld)
  web.py               gedeeld voor de webpagina: templates, toasts, Tracker, startscherm, Settings
  static/
    trackly.css/.js    stijl, navigatie, dialogen, coverkiezer, genre-suggesties
    templates/         index.html (de schil) en de HTML-fragmenten voor htmx
  reading/
    events.py          SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved
    aggregate.py       ReadingSeries
    commands.py        StartSeries, LogProgress, ChangeStatus, SetGenres, RemoveSeries + ReadingCommandHandler
    projections.py     LibraryProjection, ReadingActivityProjection
    cover_sources.py   bronnen: AniList (manga), MangaUpdates, Open Library, Google Books
    genres.py          de genrelijst van Reading
    api.py             /reading endpoints (JSON)
    web.py             /ui/reading endpoints (HTML-fragmenten voor htmx)
  listening/
    events.py          TrackPlayed
    aggregate.py       Play (één moment = één play)
    commands.py        RecordPlay + ListeningCommandHandler, stream_id()
    projections.py     RecentlyPlayed, ListeningActivity, TopArtists, TopTracks
    spotify.py         de Spotify-koppeling: inloggen, tokens, sync (automation-slice)
    api.py             /listening endpoints (JSON)
    web.py             /ui/listening endpoints, en connect/callback/sync voor Spotify
  watching/
    events.py          ShowAdded, ShowStatusChanged, ShowGenresChanged, ShowRemoved
    aggregate.py       WatchItem
    commands.py        AddShow, ChangeShowStatus, SetShowGenres, RemoveShow + WatchingCommandHandler
    projections.py     WatchlistProjection, WatchActivityProjection
    cover_sources.py   bronnen: AniList (anime), TVmaze (series), TMDB (films en series, met sleutel)
    genres.py          de genrelijst van Watching
    api.py             /watching endpoints (JSON)
    web.py             /ui/watching endpoints (HTML-fragmenten voor htmx)
tests/
  test_eventstore.py, test_api.py, test_web.py
  test_covers.py, test_cover_flow.py
  reading/, watching/, listening/  given/when/then-tests per regel
data/                  tracker.db, covers/, spotify_token.json, spotify_sync.json (niet in git)
```

---

## Huidige status: leesdomein (klaar)

| Onderdeel   | Inhoud                                                          |
| ----------- | --------------------------------------------------------------- |
| Statussen   | Reading, On-Hold, Completed, Dropped                            |
| Events      | `SeriesStarted` (optioneel `cover`), `ProgressLogged`, `StatusChanged`, `GenresChanged`, `SeriesRemoved` |
| Commands    | `StartSeries`, `LogProgress`, `ChangeStatus`, `SetGenres`, `RemoveSeries` |
| Read models | CurrentlyReading, Library, ReadingActivity (per dag / per week) |

Regels:

1. Een titel mag maar één keer in de bibliotheek staan (hoofdletters en spaties worden genegeerd). Herlezen = status terug naar Reading.
2. Voortgang loggen bij On-Hold, Completed of Dropped zet de serie automatisch terug op Reading.
3. Een lager hoofdstuk loggen mag (correctie of herlezen), maar telt niet mee in ReadingActivity.
4. Een negatief hoofdstuk wordt geweigerd.
5. Een status wijzigen naar dezelfde status wordt geweigerd.
6. Een verwijderde serie krijgt geen voortgang of status meer. Haar titel is daarna weer vrij: opnieuw toevoegen maakt een nieuwe serie (nieuwe `series_id`) die opnieuw begint. Gelezen hoofdstukken van de oude serie blijven meetellen in de statistieken.
7. Bij het toevoegen kies je de status (standaard Reading). Een andere beginstatus wordt opgeslagen als `SeriesStarted` gevolgd door `StatusChanged`; het beginhoofdstuk telt niet mee als gelezen, zodat een afgeronde serie toevoegen je statistieken niet opblaast. Daarna kan elke status naar elke andere.
8. Genres komen uit een vaste lijst (`GENRES` in `app/reading/genres.py`). `GenresChanged` bevat steeds de volledige nieuwe lijst; dezelfde genres opnieuw opslaan levert geen event op. Bij het toevoegen gaan genres mee als `GenresChanged` na `SeriesStarted`.
9. Een cover is optioneel. Een cover die niet lukt (geen afbeelding, te groot, netwerkfout) blokkeert het opslaan van de serie nooit.

Hoofdstukken zijn `float`, zodat hoofdstukken als 45.5 mogelijk zijn.

## Watching (klaar)

| Onderdeel   | Inhoud |
| ----------- | ------ |
| Soorten     | Series, Anime, Movie |
| Statussen   | Watching, Completed, On hold, Dropped (geen afleveringen, geen cijfers) |
| Events      | `ShowAdded` (met beginstatus en optioneel `cover`), `ShowStatusChanged`, `ShowGenresChanged`, `ShowRemoved` |
| Commands    | `AddShow`, `ChangeShowStatus`, `SetShowGenres`, `RemoveShow` |
| Read models | Watchlist, WatchActivity (afgerond per dag) |

Regels:

1. Een titel mag één keer per soort in de lijst staan: *Dune* de film en *Dune* de serie mogen allebei.
2. Elke status kan naar elke andere; naar dezelfde status wordt geweigerd.
3. Afgerond telt alleen als je iets op Completed **zet**. Toevoegen als Completed is geschiedenis en telt niet mee in "Finished this month".
4. Genres uit een eigen vaste lijst (`WATCH_GENRES` in `app/watching/genres.py`); verder zoals bij Reading.
5. Na verwijderen is de titel weer vrij.

De event-klassen heten `Show...`, omdat de event store alleen de klassenaam opslaat; namen moeten uniek zijn over alle trackers heen.

## Listening (klaar)

Wat ik op Spotify luister, automatisch bijgehouden. Er is geen formulier: plays komen binnen via de Spotify-sync (of `POST /listening/plays`).

| Onderdeel   | Inhoud |
| ----------- | ------ |
| Events      | `TrackPlayed` (played_at, track_id, track, artists, album, album_id, duration_ms, ms_played, source) |
| Commands    | `RecordPlay` |
| Streams     | één per moment: `play-<played_at in ISO, UTC>` |
| Read models | RecentlyPlayed (laatste 50), ListeningActivity (minuten per dag en per week), TopArtists en TopTracks (per maand) |
| Tabbladen   | Dashboard (minuten deze week, top 5 artiesten deze maand), History (laatste plays), Progress (minuten per dag/week, toplijsten) |

Regels:

1. Een play op een moment dat al bekend is, wordt overgeslagen: geen fout, de handler geeft een lege lijst terug (de API antwoordt dan `200` met `"stored": false` in plaats van `201`). De sync haalt bewust overlappende data op.
2. Een play zonder `track_id`, of met een negatieve `duration_ms` of `ms_played`, wordt geweigerd (`DomainError`).
3. Plays worden nooit gewijzigd of verwijderd.
4. Minuten: `ms_played` als die bekend is, anders `duration_ms`. De Spotify-API geeft alleen de lengte van een nummer, niet hoe lang je echt luisterde; minuten zijn dus een bovengrens.
5. Dagen, weken en maanden volgen je eigen tijdzone; `played_at` wordt in UTC opgeslagen (een tijd zonder tijdzone geldt als UTC).
6. Een nummer met meerdere artiesten telt in TopArtists voor elke artiest.

### Spotify koppelen

1. Maak een app op [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard). Zet als **Redirect URI** precies `http://127.0.0.1:8000/listening/spotify/callback` (draait Progen op een andere poort: pas die aan en zet hem ook in `SPOTIFY_REDIRECT_URI`).
2. Zet `SPOTIFY_CLIENT_ID` en `SPOTIFY_CLIENT_SECRET` in `.env` en herstart Progen.
3. Klik in Settings op **Connect Spotify** en geef Progen toegang. Progen vraagt alleen `user-read-recently-played`; een `state`-parameter beschermt het inloggen tegen CSRF.
4. Na het terugkomen draait meteen een eerste sync. Daarna synct Progen elke `SYNC_INTERVAL_MINUTES` minuten (standaard 30) zolang de server draait, of direct via **Sync now**. **Disconnect** verwijdert de tokens; je plays blijven.

Hoe het werkt (`app/listening/spotify.py`, een automation-slice): de sync vraagt `GET /v1/me/player/recently-played?limit=50&after=<laatste played_at in ms>` en stuurt voor elk nummer hetzelfde `RecordPlay` als de API, met `source="api"`. Tokens staan in `data/spotify_token.json`, niet in de event store; de access token wordt automatisch ververst met de refresh token. Bij een 429 wacht de achtergrondtaak de `Retry-After` af. Fouten worden gelogd (logger `progen.listening`) en laten de app nooit crashen; de laatste sync en een eventuele fout staan in Settings.

**Beperking:** Spotify geeft per verzoek maximaal de laatste **50** nummers. Luister je tussen twee syncs meer dan 50 nummers (bij 30 minuten is dat haast onmogelijk, maar staat Progen een dag uit wel), dan zijn de oudere via de API niet meer op te halen.

### Covers

- Bij "Add ..." kun je een cover zoeken, zelf een afbeelding uploaden of een link plakken. Zoeken is een hulp-endpoint (`GET /ui/<tracker>/covers/search`), geen command of event.
- Watching zoekt bij AniList (anime), TVmaze (series) en TMDB (films en series; alleen met `TMDB_API_KEY`).
- Bronnen voor Reading (`app/reading/cover_sources.py`), allemaal tegelijk bevraagd:
  - [AniList](https://anilist.co): manga, manhwa, light novels
  - [MangaUpdates](https://www.mangaupdates.com): ook webnovels (type "Novel")
  - [Open Library](https://openlibrary.org): boeken, ook webnovels in print (bijvoorbeeld Shadow Slave)
  - [Google Books](https://books.google.com): optioneel met `GOOGLE_BOOKS_API_KEY` in `.env`; te kleine thumbnails (onder `MIN_SOURCE_SIZE`) worden overgeslagen
- Een bron is een functie `bron(client, zoekterm) -> list[SourceResult]` met `@cover_source("Naam")` erboven. Toevoegen of weghalen = de lijst `SOURCES` aanpassen.
- Elke bron zoekt in drie stappen en stopt zodra iets past: de titel zoals getypt, genormaliseerd, en de langste losse woorden (bronnen vinden zelf niets bij een typfout). Alle resultaten worden samen vergeleken met rapidfuzz, waarbij achtervoegsels als "(Novel)" of ", Book 1" niet meetellen, en gesorteerd op beste match; dubbele URL's verdwijnen.
- Faalt of hangt een bron (deadline `SOURCE_DEADLINE`), dan tonen we de andere. Alleen als alle bronnen falen, volgt een foutmelding.
- Korte titels (tot 10 tekens) moeten strenger kloppen, zodat "Frieren" niet "Frieden" vindt. Het deel vóór een dubbele punt telt ook mee ("Frieren" vindt "Frieren: Beyond Journey's End"), maar lager dan een exacte titel, zodat een vervolg altijd ná de serie zelf komt.
- Instelbaar bovenaan `app/cover_search.py`: `COVER_MATCH_THRESHOLD`, `SHORT_TITLE_THRESHOLD`, `SUBTITLE_FACTOR`, `FALLBACK_WORDS`, `SOURCE_DEADLINE`, `MIN_SOURCE_SIZE`. Resultaten worden 10 minuten per titel bewaard, maar maar 1 minuut als een bron faalde.
- Bij het opslaan wordt de cover gedownload en met Pillow gecontroleerd, rechtgezet (EXIF), vanuit het midden bijgesneden tot 300×450 (2:3) en als WebP met een UUID-naam in `data/covers/` gezet. Alleen http/https, maximaal 5 MB, timeout 10 s. Alleen de bestandsnaam staat in het event.
- Genres: AniList (genres en tags met rank ≥ 60), MangaUpdates (genres), Google Books (categorieën) en Open Library (onderwerpen) leveren genres mee. Die worden vertaald naar de vaste lijst en in het formulier alvast aangevinkt; wat je zelf aan- of uitvinkt, blijft staan als je naar een volgende cover bladert. Later aanpassen kan via ⋯ > *Edit genres* op een kaart.
- Een cover van een bestaande serie wijzigen kan nog niet; daar is een apart event voor nodig (bijvoorbeeld `SeriesCoverChanged`).

---

## Roadmap

### 1. Opruimen voordat het tweede domein komt

- [ ] `DomainError` verplaatsen van `app/reading/aggregate.py` naar een gedeeld bestand `app/domain.py`, zodat elk domein hem kan gebruiken. De exception handler in `main.py` vangt hem dan voor alle domeinen.
- [ ] `requirements.txt` bijwerken (`pip freeze > requirements.txt`).
- [ ] Controleren dat `.venv/` en `data/` in `.gitignore` staan.

### 2. Slaapdomein

Eerst handmatig invoeren; automatisch importeren komt later (zie punt 5).

- [ ] Event model maken op een board (trigger → command → event → read model).
- [ ] Open ontwerpvragen beantwoorden:
  - Wat is het id van een stream? Voorstel: de datum van de nacht (bijv. `2026-09-30`), zodat er per nacht maar één registratie kan zijn.
  - Welke velden? Voorstel: bedtijd, opstaan, eventueel kwaliteit (1–5).
  - Hoe corrigeer ik een fout? Via een apart event (bijv. `SleepCorrected`), niet door het oude event aan te passen.
- [ ] Mogelijke events: `SleepRecorded`, `SleepCorrected`.
- [ ] Mogelijke read models: SleepThisWeek, gemiddelde slaapduur per week.

### 3. Fitnessdomein

- [ ] Event model maken.
- [ ] Mogelijke events: `WorkoutLogged` (soort, duur, eventueel afstand of sets).
- [ ] Mogelijke read models: WorkoutHistory, totalen per week.

### 4. Dashboard

- [ ] De webpagina uitbreiden met een sectie per domein.
- [ ] Grafieken toevoegen (bijvoorbeeld met Chart.js via een CDN): hoofdstukken per week, slaapduur per nacht.
- [ ] Domeinen combineren in een nieuwe projectie, bijvoorbeeld: lees ik meer na een goede nacht slaap? Dit werkt meteen met alle oude data, omdat projecties uit de events worden opgebouwd.

### 5. Automatisch importeren

- [ ] Kiezen welke wearable of app de slaap- en fitnessdata levert, en uitzoeken of die een API of export heeft.
- [ ] Een importer bouwen als **automation-slice**: een script dat op een schema draait, nieuwe data ophaalt, en precies dezelfde commands stuurt als de webpagina. De domeinlogica hoeft daarvoor niet te veranderen.
- [ ] Voorkomen dat dezelfde data twee keer wordt geïmporteerd (de stream-id per nacht uit punt 2 helpt hierbij).

### 6. Beveiliging en hosting

- [ ] Een API-key of wachtwoord toevoegen voordat de app buiten het thuisnetwerk bereikbaar wordt.
- [ ] Een plek kiezen om hem te laten draaien (Raspberry Pi thuis, of een kleine VPS).
- [x] Back-ups van `data/tracker.db` en covers, met terugzetten (Settings).

### Ideeën voor later

- Status **Plan to Read** voor series die ik nog wil beginnen (alleen een extra waarde in `Status`).
- Beoordeling of notities per serie (nieuwe events, bijv. `SeriesRated`).
- Export van alle data naar JSON of CSV.
- Listening: importeren van de Spotify **Extended streaming history** (de export die je bij Spotify aanvraagt). Die bevat alles sinds je account bestaat, met echte `ms_played`; importeren als `RecordPlay` met `source="export"`, dubbele plays worden vanzelf overgeslagen.
- Nieuwe domeinen naar behoefte.

---

## Werkwijze: een nieuw domein toevoegen

Elk domein volgt dezelfde stappen als Reading en Watching. Bestaande domeinen hoeven daarvoor niet te veranderen.

1. **Event model** maken: slices met trigger, command, event en read model, plus de regels als given/when/then.
2. **`app/<domein>/events.py`**: events als `@dataclass(frozen=True)`, namen in de verleden tijd, met een `at`-veld.
3. **`aggregate.py`**: toestand opbouwen in `_apply`, regels checken in de beslismethodes. Tests in `tests/<domein>/`.
4. **`commands.py`**: commands en een command handler (laden → beslissen → opslaan). Tests met een `:memory:` event store.
5. **`projections.py`**: read models met een `apply(event)`-methode. Test ook dat een herbouwd read model gelijk is aan het live read model.
6. **`api.py`**: een `create_<domein>_router(...)`-functie. `GET` leest uit projecties, `POST` stuurt commands.
7. **`app/main.py`** koppelen:
   - de nieuwe events toevoegen aan `EVENT_TYPES`
   - de nieuwe projecties toevoegen aan `projections`
   - de handler aanmaken en de router toevoegen met `include_router`
   - een `Tracker(...)` toevoegen aan `trackers` (naam, icoon, event na een wijziging, samenvatting voor het startscherm); dan staat hij vanzelf op het startscherm, in Settings en in elke back-up
8. **Webpagina**: `web.py` met `/ui/<domein>/dashboard`, `/library`, `/progress` en (als je iets kunt toevoegen) `/add-form`; andere tabbladen kan ook, zoals `/history` bij Listening (geef `tabs` mee aan `ui.tracker_tabs`), templates met `ui.tracker_tabs(...)` bovenaan, een link in de navigatie (`index.html`) en een regel in `TRACKERS` in `trackly.js`.
   - Event-klassen moeten uniek zijn over alle domeinen heen (de event store bewaart alleen de klassenaam).
9. `python -m pytest` moet groen blijven, zonder warnings.

---

## Belangrijke afspraken

- **Bestaande events nooit wijzigen.** Oude events in de database moeten altijd leesbaar blijven. Een veld toevoegen mag alleen met een standaardwaarde. Verandert de betekenis, maak dan een nieuw event.
- **Een event-klasse nooit hernoemen.** De klassenaam staat in de database (kolom `type`) en wordt gebruikt om events terug te bouwen.
- **Nooit rechtstreeks in de database schrijven.** Alles gaat via commands, zodat de regels altijd gecontroleerd worden.
- **Eerst de test, dan de code.** Elke regel uit het event model wordt een given/when/then-test.
- **Claude Code gebruiken voor kleine stukken**, niet voor hele domeinen tegelijk: laat het uitleggen, reviewen of één bestand schrijven, zodat ik blijf begrijpen hoe alles werkt.
