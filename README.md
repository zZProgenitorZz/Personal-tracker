# Personal-tracker

Een persoonlijke API die gegevens over mijn eigen leven op één plek verzamelt, in eigen beheer. Het begon met het bijhouden van wat ik lees (novels en manhwa); slaap, fitness en andere domeinen volgen.

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

Instellingen staan in `.env` in de projectmap (niet in git; `.env.example` laat zien wat erin kan). Optioneel is `GOOGLE_BOOKS_API_KEY`, voor betere covers uit Google Books; zonder sleutel geldt een krap gedeeld quotum en valt Google vaak af. Een waarde die al in de omgeving staat, gaat voor op `.env`.

- Webpagina: http://127.0.0.1:8000
- API-documentatie (Swagger): http://127.0.0.1:8000/docs
- Tests draaien: `python -m pytest`

Data staat in `data/tracker.db`. **Dit bestand is de enige bron van waarheid**: alle read models worden er bij het opstarten uit opgebouwd. Covers staan ernaast in `data/covers/`. Maak van allebei regelmatig een back-up.

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
  covers.py            covers downloaden, controleren en als 300×450 WebP bewaren (gedeeld)
  web.py               gedeeld voor de webpagina: templates, filters, toasts
  static/
    index.html         de schil van de webpagina (Progen)
    trackly.css/.js    stijl, navigatie, dialogen, coverkiezer
    templates/         HTML-fragmenten voor htmx
  reading/
    events.py          SeriesStarted, ProgressLogged, StatusChanged, SeriesRemoved
    aggregate.py       ReadingSeries + DomainError
    commands.py        StartSeries, LogProgress, ChangeStatus, RemoveSeries + ReadingCommandHandler
    projections.py     LibraryProjection, ReadingActivityProjection
    cover_search.py    covers zoeken bij alle bronnen tegelijk, fuzzy matching, cache
    cover_sources.py   de bronnen: AniList, MangaUpdates, Open Library, Google Books
    api.py             /reading endpoints (JSON)
    web.py             /ui endpoints (HTML-fragmenten voor htmx)
tests/
  test_eventstore.py, test_api.py, test_web.py
  test_covers.py, test_cover_flow.py
  reading/             given/when/then-tests per regel
data/                  tracker.db en covers/ (niet in git)
```

---

## Huidige status: leesdomein (klaar)

| Onderdeel   | Inhoud                                                          |
| ----------- | --------------------------------------------------------------- |
| Statussen   | Reading, On-Hold, Completed, Dropped                            |
| Events      | `SeriesStarted` (optioneel `cover`), `ProgressLogged`, `StatusChanged`, `SeriesRemoved` |
| Commands    | `StartSeries`, `LogProgress`, `ChangeStatus`, `RemoveSeries`    |
| Read models | CurrentlyReading, Library, ReadingActivity (per dag / per week) |

Regels:

1. Een titel mag maar één keer in de bibliotheek staan (hoofdletters en spaties worden genegeerd). Herlezen = status terug naar Reading.
2. Voortgang loggen bij On-Hold, Completed of Dropped zet de serie automatisch terug op Reading.
3. Een lager hoofdstuk loggen mag (correctie of herlezen), maar telt niet mee in ReadingActivity.
4. Een negatief hoofdstuk wordt geweigerd.
5. Een status wijzigen naar dezelfde status wordt geweigerd.
6. Een verwijderde serie krijgt geen voortgang of status meer. Haar titel is daarna weer vrij: opnieuw toevoegen maakt een nieuwe serie (nieuwe `series_id`) die opnieuw begint. Gelezen hoofdstukken van de oude serie blijven meetellen in de statistieken.
7. Een cover is optioneel. Een cover die niet lukt (geen afbeelding, te groot, netwerkfout) blokkeert het opslaan van de serie nooit.

Hoofdstukken zijn `float`, zodat hoofdstukken als 45.5 mogelijk zijn.

### Covers

- Bij "Add series" kun je een cover zoeken, zelf een afbeelding uploaden of een link plakken. Zoeken is een hulp-endpoint (`GET /ui/covers/search`), geen command of event.
- Bronnen (`app/reading/cover_sources.py`), allemaal tegelijk bevraagd:
  - [AniList](https://anilist.co): manga, manhwa, light novels
  - [MangaUpdates](https://www.mangaupdates.com): ook webnovels (type "Novel")
  - [Open Library](https://openlibrary.org): boeken, ook webnovels in print (bijvoorbeeld Shadow Slave)
  - [Google Books](https://books.google.com): optioneel met `GOOGLE_BOOKS_API_KEY` in `.env`; te kleine thumbnails (onder `MIN_SOURCE_SIZE`) worden overgeslagen
- Een bron is een functie `bron(client, zoekterm) -> list[SourceResult]` met `@cover_source("Naam")` erboven. Toevoegen of weghalen = de lijst `SOURCES` aanpassen.
- Elke bron zoekt in drie stappen en stopt zodra iets past: de titel zoals getypt, genormaliseerd, en de langste losse woorden (bronnen vinden zelf niets bij een typfout). Alle resultaten worden samen vergeleken met rapidfuzz, waarbij achtervoegsels als "(Novel)" of ", Book 1" niet meetellen, en gesorteerd op beste match; dubbele URL's verdwijnen.
- Faalt of hangt een bron (deadline `SOURCE_DEADLINE`), dan tonen we de andere. Alleen als alle bronnen falen, volgt een foutmelding.
- Instelbaar bovenaan `app/reading/cover_search.py`: `COVER_MATCH_THRESHOLD`, `FALLBACK_WORDS`, `SOURCE_DEADLINE`, `MIN_SOURCE_SIZE`. Resultaten worden 10 minuten per titel bewaard, maar maar 1 minuut als een bron faalde.
- Bij het opslaan wordt de cover gedownload en met Pillow gecontroleerd, rechtgezet (EXIF), vanuit het midden bijgesneden tot 300×450 (2:3) en als WebP met een UUID-naam in `data/covers/` gezet. Alleen http/https, maximaal 5 MB, timeout 10 s. Alleen de bestandsnaam staat in het event.
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
- [ ] Automatische back-up van `data/tracker.db`.

### Ideeën voor later

- Status **Plan to Read** voor series die ik nog wil beginnen (alleen een extra waarde in `Status`).
- Beoordeling of notities per serie (nieuwe events, bijv. `SeriesRated`).
- Export van alle data naar JSON of CSV.
- Nieuwe domeinen naar behoefte.

---

## Werkwijze: een nieuw domein toevoegen

Elk domein volgt dezelfde stappen als het leesdomein. Bestaande domeinen hoeven daarvoor niet te veranderen.

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
8. **Webpagina** uitbreiden met een sectie voor het domein.
9. `python -m pytest` moet groen blijven, zonder warnings.

---

## Belangrijke afspraken

- **Bestaande events nooit wijzigen.** Oude events in de database moeten altijd leesbaar blijven. Een veld toevoegen mag alleen met een standaardwaarde. Verandert de betekenis, maak dan een nieuw event.
- **Een event-klasse nooit hernoemen.** De klassenaam staat in de database (kolom `type`) en wordt gebruikt om events terug te bouwen.
- **Nooit rechtstreeks in de database schrijven.** Alles gaat via commands, zodat de regels altijd gecontroleerd worden.
- **Eerst de test, dan de code.** Elke regel uit het event model wordt een given/when/then-test.
- **Claude Code gebruiken voor kleine stukken**, niet voor hele domeinen tegelijk: laat het uitleggen, reviewen of één bestand schrijven, zodat ik blijf begrijpen hoe alles werkt.
