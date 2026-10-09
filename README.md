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

### Starten met een icoon (Windows)

Eén keer de snelkoppeling installeren (vanuit de projectmap, nadat de venv bestaat):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1 -StartMenu
```

Daarna staat **Progen** op je bureaublad (met `-StartMenu` ook in het Startmenu, samen met **Stop Progen**; met `-StopOnDesktop` staat Stop Progen ook op het bureaublad).

- **Progen** start `launch.pyw` met `pythonw.exe` uit `.venv`, dus zonder consolevenster. Draait de server nog niet, dan start hij op de achtergrond (alleen op `127.0.0.1:8000`, zonder `--reload`) met de uitvoer in `data/launcher.log`. Zodra hij antwoordt, opent Progen als eigen venster in Edge (`msedge --app=...`), of in je standaardbrowser als Edge er niet is. Start hij niet binnen 15 seconden, dan zegt een melding waar het log staat.
- **Icoon bij de klok:** zolang Progen draait, staat er een Progen-icoon in het systeemvak (onder "verborgen pictogrammen", net als WhatsApp of Spotify). Dubbelklik opent Progen; rechtsklik geeft **Open Progen**, **Sync Spotify now** en **Stop Progen**. Er is altijd maar één zo'n icoon: nog eens op de snelkoppeling klikken opent alleen een nieuw venster. Draait de server niet, dan start de snelkoppeling hem altijd, ook als er (nog) een ander icoon is.
- **Het venster sluiten stopt de server niet**; hij blijft op de achtergrond draaien, zodat de Spotify-sync doorgaat.
- **Automatisch starten bij aanmelden:** zet in Settings > App de schakelaar **Start with Windows** aan. Progen zet dan een waarde `Progen` in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (alleen voor jouw gebruiker, geen beheerdersrechten nodig) die `launch.pyw --background` start: server en icoon, zonder venster. Uitzetten haalt die waarde weer weg. Verplaats je de projectmap, zet de schakelaar dan opnieuw aan.
- **Stop Progen** (in het menu van het icoon, of `stop.pyw`) vraagt de server netjes te stoppen via `POST /admin/shutdown`: lopende verzoeken worden afgerond en de database wordt gesloten; daarna verdwijnt het icoon. Uvicorn krijgt daar hooguit 5 seconden voor (`--timeout-graceful-shutdown`); leeft het proces na 20 seconden nog, dan stopt het zichzelf hard (`app/shutdown.py`). Het icoon toont nooit een popup maar een ballonmelding, want een popup zou het icoon blokkeren. Draaide Progen al niet meer, dan verdwijnt het icoon gewoon. Die route werkt alleen vanaf deze computer (127.0.0.1) en met de header `X-Progen-Shutdown`, zodat een website in je browser hem niet kan aanroepen.
- Het icoon (`app/static/icon.ico`, 16 t/m 256 px, en de PNG's in `app/static/icons/` voor het favicon en `manifest.json`) is gemaakt uit `icon.svg` met `scripts/make_icon.py`: Edge of Chrome rendert het SVG headless als transparante PNG, Pillow maakt er de rest van. Het icoon bij de klok laadt het .ico op precies de maat die Windows wil (20 px bij 125%), en de launcher is DPI-bewust; zo blijven icoon en menu scherp. Het Progen-venster kiest voor de taakbalk een van de favicon-PNG's (32-96 px); die komen uit `icon-small.svg`, een vereenvoudigde versie (dikke ring, groot boek), omdat de details van `icon.svg` op taakbalkformaat samenvallen. Opnieuw uitvoeren als het icoon verandert, en daarna `install_shortcut.ps1` opnieuw.
- Wil je tijdens het programmeren automatisch herladen, stop dan eerst Progen (Stop Progen) en start zoals hierboven met `uvicorn ... --reload`; ze gebruiken dezelfde poort.

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
- **Niet in een back-up:** `data/spotify_token.json` (je Spotify-toegang), `data/spotify_sync.json` (wanneer de laatste sync was) en `data/spotify_profile.json` + `spotify_avatar.webp` (je Spotify-naam en -foto). Na het terugzetten op een andere computer koppel je Spotify gewoon opnieuw.
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
  security.py          bescherming tegen andere websites (CSRF) en DNS rebinding
  shutdown.py          POST /admin/shutdown: netjes stoppen, alleen vanaf deze computer (voor stop.pyw)
  desktop.py           de server aanspreken vanaf het bureaublad (launcher, icoon, stop.pyw)
  autostart.py         Start with Windows (Run-sleutel in het register), aan/uit in Settings
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
  wrapped/
    projections.py     WrappedProjection: het jaaroverzicht uit de events van alle trackers (alleen lezen)
    web.py             /ui/wrapped (#wrapped) en de Wrapped-kaart op het startscherm
launch.pyw, stop.pyw   Progen starten (venster + icoon bij de klok) en stoppen; voor de snelkoppeling
scripts/
  install_shortcut.ps1 snelkoppelingen op het bureaublad / in het Startmenu
  make_icon.py         app/static/icon.ico maken uit icon.svg
tests/
  test_eventstore.py, test_api.py, test_web.py
  test_covers.py, test_cover_flow.py
  reading/, watching/, listening/, wrapped/  given/when/then-tests per regel
data/                  tracker.db, covers/ en de spotify_*-bestanden (niet in git)
```

---

## Huidige status: leesdomein (klaar)

| Onderdeel   | Inhoud                                                          |
| ----------- | --------------------------------------------------------------- |
| Statussen   | Reading, On-Hold, Completed, Dropped, Plan to Read              |
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
10. **Plan to Read** is de backlog: series die je nog wilt beginnen. Toevoegen als Plan to Read volgt regel 7 (`SeriesStarted` + `StatusChanged(READING → PLAN_TO_READ)`). Voortgang loggen zet hem via regel 2 op Reading. Plan to Read telt nergens mee als "reading" (Currently reading, startscherm).

Hoofdstukken zijn `float`, zodat hoofdstukken als 45.5 mogelijk zijn.

**Gathering dust** (paneel op het Dashboard): series op Reading zonder voortgang of statuswijziging in meer dan `STALE_READING_DAYS` = 21 dagen (bovenaan `app/reading/web.py`), langst stil eerst (`LibraryProjection.stale(now, days)`). Plan to Read, On hold, Completed en Dropped zijn nooit stoffig. Per serie: +1 hoofdstuk, On hold of Drop (met bevestiging), via de gewone endpoints; **Not now** verbergt hem alleen tot je de pagina herlaadt (geen event). Het startscherm toont "N gathering dust".

## Watching (klaar)

| Onderdeel   | Inhoud |
| ----------- | ------ |
| Soorten     | Series, Anime, Movie |
| Statussen   | Watching, Completed, On hold, Dropped, Plan to Watch (geen afleveringen, geen cijfers) |
| Events      | `ShowAdded` (met beginstatus en optioneel `cover`), `ShowStatusChanged`, `ShowGenresChanged`, `ShowRemoved` |
| Commands    | `AddShow`, `ChangeShowStatus`, `SetShowGenres`, `RemoveShow` |
| Read models | Watchlist, WatchActivity (afgerond per dag) |

Regels:

1. Een titel mag één keer per soort in de lijst staan: *Dune* de film en *Dune* de serie mogen allebei.
2. Elke status kan naar elke andere; naar dezelfde status wordt geweigerd.
3. Afgerond telt alleen als je iets op Completed **zet**. Toevoegen als Completed is geschiedenis en telt niet mee in "Finished this month".
4. Genres uit een eigen vaste lijst (`WATCH_GENRES` in `app/watching/genres.py`); verder zoals bij Reading.
5. Na verwijderen is de titel weer vrij.
6. **Plan to Watch** is de backlog: toevoegen is gewoon `ShowAdded(status=PLAN_TO_WATCH)`. Telt niet mee als "watching"; van Plan to Watch naar Completed zetten telt wél in "Finished this month" (regel 3).

**Pick something** (Library van Reading en Watching, alleen met een backlog): toont een willekeurige titel uit Plan to Read/Watch, eventueel binnen één genre. *Another one* kiest een andere; *Start* stuurt de gewone statuswijziging (`ChangeStatus`/`ChangeShowStatus`). Het kiezen zelf (`GET /ui/<tracker>/pick`) is alleen lezen: geen command, geen event.

**Gathering dust**: zoals bij Reading, maar voor titels op Watching die meer dan `STALE_WATCHING_DAYS` = 30 dagen niet zijn toegevoegd of van status gewisseld (bovenaan `app/watching/web.py`; `WatchlistProjection.stale(now, days)`). Ruimer, want zonder afleveringen is "lang niets veranderd" een zwakker signaal. Genres aanpassen telt niet als activiteit.

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
4. Na het terugkomen haalt Progen je naam en profielfoto op (voor het dashboard van Listening; daarna hooguit één keer per dag opnieuw) en draait meteen een eerste sync. Daarna synct Progen elke `SYNC_INTERVAL_MINUTES` minuten (standaard 30) zolang de server draait, of direct via **Sync now**. **Disconnect** verwijdert de tokens; je plays blijven.

Hoe het werkt (`app/listening/spotify.py`, een automation-slice): de sync vraagt `GET /v1/me/player/recently-played?limit=50&after=<laatste played_at in ms>` en stuurt voor elk nummer hetzelfde `RecordPlay` als de API, met `source="api"`. Tokens staan in `data/spotify_token.json`, niet in de event store; de access token wordt automatisch ververst met de refresh token. Bij een 429 wacht de achtergrondtaak de `Retry-After` af. Fouten worden gelogd (logger `progen.listening`) en laten de app nooit crashen; de laatste sync en een eventuele fout staan in Settings.

**Beperking:** Spotify geeft per verzoek maximaal de laatste **50** nummers. Luister je tussen twee syncs meer dan 50 nummers (bij 30 minuten is dat haast onmogelijk, maar staat Progen een dag uit wel), dan zijn de oudere via de API niet meer op te halen.

## Wrapped

Een jaaroverzicht over Reading, Watching en Listening heen (`#wrapped`, of de Wrapped-kaart op het startscherm). Alleen lezen: geen eigen events of commands. `WrappedProjection` wordt opgebouwd uit de events die er al zijn, dus hij werkt meteen met alle oude data, en loopt daarna live mee. Wrapped is geen tracker: hij staat niet in Settings, niet in de navigatiebalk en heeft geen eigen back-up (alles zit al in de events).

| Tracker   | Wat er per jaar geteld wordt |
| --------- | ---------------------------- |
| Reading   | Gelezen hoofdstukken, top 5 series, genres, nieuw begonnen, afgerond, langste leesreeks |
| Watching  | Afgerond per soort, genres van wat je afrondde, de covers daarvan |
| Listening | Minuten (en uren), top 5 artiesten, top 5 nummers, drukste luisterdag |
| Samen     | Per maand hoofdstukken / afgerond / minuten, drukste maand, actiefste dag, de jaren met data |

Regels:

1. Dagen, maanden en jaren volgen de lokale tijdzone (`at` van het event, bij Listening `played_at`).
2. Hoofdstukken: alleen vooruit telt (`chapter - previous_chapter > 0`), net als ReadingActivity.
3. Genres van Reading: de gelezen hoofdstukken van een serie worden gelijk verdeeld over haar genres, volgens de **laatste** `GenresChanged`. Titels en covers van verwijderde series blijven in het overzicht.
4. Een verwijderde en opnieuw toegevoegde serie (nieuwe `series_id`, bijvoorbeeld om een cover te krijgen) telt in Wrapped als **één serie**: zelfde titel zoals Reading die vergelijkt (hoofdletters en spaties eromheen tellen niet). Hoofdstukken van alle versies tellen samen; titel en cover komen van de nieuwste versie (zonder cover: de nieuwste oudere met cover), genres van de nieuwste versie met genres. Begonnen telt alleen in het jaar van de eerste versie, ook als je hem lang daarna echt opnieuw begint.
5. **Afgerond** (Reading): een `StatusChanged` naar Completed, en een serie telt maar één keer per jaar. De **beginstatus** telt niet: toevoegen als Completed wordt opgeslagen als `SeriesStarted` + `StatusChanged(READING → COMPLETED)` (Reading regel 7). Een `StatusChanged` is een beginstatus als hij direct volgt op de `SeriesStarted` van dezelfde serie (geen ander Reading-event ertussen; plays van de Spotify-sync tellen niet) **en** binnen 5 seconden. Zo telt "toegevoegd, en een uur later uitgelezen" wel.
6. **Begonnen** (Reading): een serie telt één keer, in het jaar dat je eraan begon. Toevoegen als Plan to Read of als Completed is niet beginnen; een serie uit Plan to Read halen wel.
7. **Afgerond** (Watching): alleen een `ShowStatusChanged` naar Completed, net als WatchActivity. Toevoegen als Completed telt niet; van Plan to Watch naar Completed wel.
8. Listening-minuten: `ms_played` als die bekend is, anders `duration_ms` (zoals ListeningActivity).
9. Drukste maand en actiefste dag: activiteit = hoofdstukken + afgeronde titels + gespeelde nummers.
10. De knop **Compare with …** (vorig jaar) laat bij de grote getallen het verschil met het jaar ervoor zien.

### Covers

- Bij "Add ..." kun je een cover zoeken, zelf een afbeelding uploaden of een link plakken. Zoeken is een hulp-endpoint (`GET /ui/<tracker>/covers/search`), geen command of event.
- Watching zoekt bij AniList (anime), TVmaze (series) en TMDB (films en series; alleen met `TMDB_API_KEY`).
- Bronnen voor Reading (`app/reading/cover_sources.py`), allemaal tegelijk bevraagd:
  - [AniList](https://anilist.co): manga, manhwa en novels (`type: MANGA` omvat ook format `NOVEL`; er is geen formatfilter). Van veel Chinese webnovels staat hier alleen de manhua-bewerking.
  - [MangaUpdates](https://www.mangaupdates.com): ook webnovels (type "Novel", geen typefilter). `hit_title` is de naam waarop MangaUpdates matchte, vaak een alternatieve naam ("Coiling Dragon" voor "Panlong"); die telt mee in de vergelijking.
  - [MyAnimeList](https://myanimelist.net) via [Jikan](https://jikan.moe): novels en light novels (`type=novel` en `type=lightnovel`, twee verzoeken), met genres en thema's. Jikan staat maximaal 3 verzoeken per seconde toe; Progen wacht `JIKAN_MIN_INTERVAL` (0,4 s) tussen verzoeken. Bij een 429 stopt deze bron (wat al gevonden is, telt mee). Is Jikan onbereikbaar, dan geeft Progen na `JIKAN_CONNECT_TIMEOUT` (3 s) op en slaat de bron `JIKAN_COOLDOWN` (5 minuten) over, zodat zoeken niet steeds wacht.
  - [Open Library](https://openlibrary.org): boeken, ook webnovels in print (bijvoorbeeld Shadow Slave)
  - [Google Books](https://books.google.com): optioneel met `GOOGLE_BOOKS_API_KEY` in `.env`; te kleine thumbnails (onder `MIN_SOURCE_SIZE`) worden overgeslagen
- Een bron is een functie `bron(client, zoekterm) -> list[SourceResult]` met `@cover_source("Naam")` erboven. Toevoegen of weghalen = de lijst `SOURCES` aanpassen.
- Elke bron zoekt in drie stappen en stopt zodra iets past: de titel zoals getypt, genormaliseerd, en de langste losse woorden (bronnen vinden zelf niets bij een typfout). Alle resultaten worden samen vergeleken met rapidfuzz, tegen **elke** titel die een bron meegeeft (AniList: Engels, romaji, native en synonyms; MangaUpdates: titel en `hit_title`; MyAnimeList: titel, Engels, Japans, synonyms en alle `titles`); de beste score telt. Zo vind je "I Shall Seal the Heavens" ook als de bron hem "Wo Yu Feng Tian" noemt. Achtervoegsels als "(Novel)" of ", Book 1" tellen niet mee; alles wordt gesorteerd op beste match en dubbele URL's verdwijnen.
- Faalt of hangt een bron (deadline `SOURCE_DEADLINE`), dan tonen we de andere. Alleen als alle bronnen falen, volgt een foutmelding.
- Korte titels (tot 10 tekens) moeten strenger kloppen, zodat "Frieren" niet "Frieden" vindt. Het deel vóór een dubbele punt telt ook mee ("Frieren" vindt "Frieren: Beyond Journey's End"), maar lager dan een exacte titel, zodat een vervolg altijd ná de serie zelf komt.
- Instelbaar bovenaan `app/cover_search.py`: `COVER_MATCH_THRESHOLD`, `SHORT_TITLE_THRESHOLD`, `SUBTITLE_FACTOR`, `FALLBACK_WORDS`, `SOURCE_DEADLINE`, `MIN_SOURCE_SIZE`. Resultaten worden 10 minuten per titel bewaard, maar maar 1 minuut als een bron faalde.
- Bij het opslaan wordt de cover gedownload en met Pillow gecontroleerd, rechtgezet (EXIF), vanuit het midden bijgesneden tot 300×450 (2:3) en als WebP met een UUID-naam in `data/covers/` gezet. Alleen http/https, maximaal 5 MB, timeout 10 s. Alleen de bestandsnaam staat in het event.
- Genres: AniList (genres en tags met rank ≥ 60), MangaUpdates (genres), Google Books (categorieën) en Open Library (onderwerpen) leveren genres mee. Die worden vertaald naar de vaste lijst en in het formulier alvast aangevinkt; wat je zelf aan- of uitvinkt, blijft staan als je naar een volgende cover bladert. Later aanpassen kan via ⋯ > *Edit genres* op een kaart.
- Een cover van een bestaande serie wijzigen kan nog niet; daar is een apart event voor nodig (bijvoorbeeld `SeriesCoverChanged`).

## Beveiliging

Progen draait alleen op je eigen computer (`127.0.0.1`), maar je browser kan ook verzoeken sturen namens een website die je open hebt. Daarom (`app/security.py`, geldt automatisch voor elke route):

- **Alleen `127.0.0.1` en `localhost` als adres.** Een website die zijn eigen naam naar 127.0.0.1 laat wijzen (*DNS rebinding*), krijgt overal `400`, ook bij lezen.
- **Wijzigingen (POST, PUT, PATCH, DELETE) moeten aantonen dat ze van Progen of een eigen script komen**, met iets wat een andere website niet zonder toestemming (CORS) kan meesturen, en die geeft Progen niet:
  - `HX-Request: true`: de webpagina zelf (htmx stuurt dit altijd mee);
  - `Content-Type: application/json`: de JSON-API en scripts (ook bij een `DELETE` zonder body);
  - `X-Progen-Shutdown: 1`: Stop Progen.
- **Zegt de browser dat een verzoek van een andere site komt** (`Origin` of `Sec-Fetch-Site: cross-site`), dan wordt het geweigerd, ook met de juiste header.
- Lezen (`GET`) is altijd toegestaan; een andere website kan de antwoorden toch niet lezen (same-origin policy).
- Komt Progen ooit buiten je eigen computer (zie Roadmap 6), dan is daarnaast inloggen nodig.

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

- [x] De webpagina uitbreiden met een sectie per domein.
- [x] Grafieken toevoegen: hoofdstukken per week, afgerond per maand, luisterminuten (CSS-balken, geen CDN nodig). Slaapduur volgt met het slaapdomein.
- [x] Domeinen combineren in een nieuwe projectie: **Wrapped**, het jaaroverzicht over alle trackers (zie [Wrapped](#wrapped)). Werkt meteen met alle oude data, omdat de projectie uit de events wordt opgebouwd.
- [ ] Verbanden tussen domeinen, bijvoorbeeld: lees ik meer na een goede nacht slaap? (zodra er een slaapdomein is)

### 5. Automatisch importeren

- [ ] Kiezen welke wearable of app de slaap- en fitnessdata levert, en uitzoeken of die een API of export heeft.
- [ ] Een importer bouwen als **automation-slice**: een script dat op een schema draait, nieuwe data ophaalt, en precies dezelfde commands stuurt als de webpagina. De domeinlogica hoeft daarvoor niet te veranderen.
- [ ] Voorkomen dat dezelfde data twee keer wordt geïmporteerd (de stream-id per nacht uit punt 2 helpt hierbij).

### 6. Beveiliging en hosting

- [ ] Een API-key of wachtwoord toevoegen voordat de app buiten het thuisnetwerk bereikbaar wordt.
- [ ] Een plek kiezen om hem te laten draaien (Raspberry Pi thuis, of een kleine VPS).
- [x] Back-ups van `data/tracker.db` en covers, met terugzetten (Settings).

### Ideeën voor later

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
