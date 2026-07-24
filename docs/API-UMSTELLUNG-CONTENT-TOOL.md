# API-Umstellung: unsere REST-API v1 entfällt, es gilt die Upstream-Agent-API

**Stand: 24.07.2026.** Geschrieben für das Team, das das **Content-Tool**
(`Social Media Content Tool`, Datei `src/services/orbita-social.ts`) anpasst.

## Worum es geht

Beim Nachziehen des Upstreams am 24.07.2026 wurde unsere selbstgebaute
REST-API v1 (Commit `0836ab9` vom 12.06.) **ersatzlos entfernt**. An ihrer
Stelle steht jetzt die offizielle **Agent-API** des Upstreams, die es seit dem
31.05. gibt – am selben Ort (`apps/api`), aber mit mehr Funktionen und laufender
Pflege.

Warum ersetzt statt behalten: beide Fassungen liegen unter `apps/api`, hätten
sich beim Merge gegenseitig blockiert, und die Upstream-Variante kann mehr –
Analytics-Router, Rechte je Key, Idempotency-Keys, Audit-Log und einen
eingebauten MCP-Server auf derselben Basis.

**Wichtig: der alte Key funktioniert nicht mehr.** Die alte Key-Tabelle
(`api_workspace_api_key`) wird von keinem Code mehr gelesen. Es braucht einen
neuen Key aus der neuen Key-Verwaltung (siehe unten).

## Das Wichtigste in Kürze

| | vorher (unsere v1) | jetzt (Upstream) |
|---|---|---|
| Basis-URL | `https://social.orbita-media.de/api/v1/` | **unverändert** |
| Authentifizierung | `Authorization: Bearer <64-hex>` | **unverändert im Prinzip**, aber neuer Key aus der Weboberfläche |
| Key-Verwaltung | Management-Command auf dem Server | Weboberfläche: Organisation → API Keys |
| Rate-Limit | 120/min pro Key | 120/min schreibend, 300/min lesend, 1000/min je Workspace |
| Ein Post für mehrere Kanäle | **ja**, ein Aufruf mit `platform_posts: [...]` | **nein**, ein Aufruf **pro Kanal** |
| Analytics | gab es nicht | `GET /analytics/accounts/{id}` und `/analytics/posts/{id}` |
| Interaktive Doku | – | `https://social.orbita-media.de/api/v1/docs` |

Die **eine wirklich unangenehme Änderung** ist die vorletzte Zeile: die neue
API legt pro Aufruf einen Post für **genau einen** Social-Account an. Wer auf
fünf Kanälen posten will, macht fünf Aufrufe. Das Feld `platform_posts` gibt es
nicht mehr.

## Endpunkt-Übersicht (live aus der laufenden Instanz gezogen)

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/api/v1/me/` | Wofür ist mein Key freigeschaltet? (Workspace, Rechte, Speicher, erlaubte Konten) |
| GET | `/api/v1/accounts/` | verbundene Konten, auf die dieser Key wirken darf |
| POST | `/api/v1/media/` | Medien-Upload (multipart) |
| GET | `/api/v1/media/` | Medienliste, neueste zuerst, mit Filtern |
| GET | `/api/v1/media/{media_id}` | einzelnes Medium |
| POST | `/api/v1/posts/` | Entwurf anlegen **oder** direkt einplanen |
| GET | `/api/v1/posts/{post_id}` | Post lesen (inkl. Status je Plattform) |
| PATCH | `/api/v1/posts/{post_id}` | Entwurfsfelder ändern |
| POST | `/api/v1/posts/{post_id}/schedule` | Entwurf einplanen |
| POST | `/api/v1/posts/{post_id}/cancel` | geplanten Post zurück auf Entwurf |
| GET | `/api/v1/analytics/accounts/{account_id}` | Kanal-Kennzahlen |
| GET | `/api/v1/analytics/posts/{post_id}` | Kennzahlen je Post und Plattform |
| POST | `/api/v1/mcp/` | MCP-Endpunkt (JSON-RPC), für Claude – nicht für das Content-Tool |

Maschinenlesbar: `GET /api/v1/openapi.json`. Die Datei ist die verbindliche
Quelle, diese Tabelle nur die Zusammenfassung.

## Was sich pro Aufruf ändert

### Posts anlegen

**Vorher** – ein Aufruf, mehrere Kanäle, verschachtelt:

```json
{
  "caption": "Basistext",
  "scheduled_at": "2026-07-01T09:00:00+02:00",
  "platform_posts": [
    { "social_account_id": "…", "status": "scheduled", "scheduled_at": "…" },
    { "social_account_id": "…", "status": "scheduled" }
  ],
  "media": [ { "media_asset_id": "…", "position": 0 } ]
}
```

**Jetzt** – ein Aufruf je Kanal, flach:

```json
{
  "social_account_id": "…",
  "caption": "Basistext",
  "title": "",
  "first_comment": "",
  "internal_notes": "",
  "media_asset_ids": ["…"],
  "action": "schedule",
  "scheduled_at": "2026-07-01T07:00:00Z",
  "idempotency_key": "buch-1846-instagram-2026-07-01"
}
```

Feld für Feld:

| vorher | jetzt | Anmerkung |
|---|---|---|
| `platform_posts[].social_account_id` | `social_account_id` | jetzt **ein** Konto je Aufruf, auf oberster Ebene |
| `platform_posts[].status` | `action` | `"draft"` oder `"schedule"` |
| `media[].media_asset_id` | `media_asset_ids` | einfache Liste von IDs, die Reihenfolge zählt |
| `media[].position` | entfällt | ergibt sich aus der Reihenfolge |
| `scheduled_at` | `scheduled_at` | **UTC**, Pflicht bei `action: "schedule"` |
| `platform_specific_caption` | `platform_overrides[]` | Liste mit `social_account_id` plus überschriebenen Feldern |
| `tags` | entfällt | am Post nicht mehr vorgesehen |
| – | `internal_notes` | **neu**: interne Notiz, wird nie veröffentlicht |
| – | `idempotency_key` | **neu**: gleicher Key plus gleicher Rumpf liefert die erste Antwort erneut. Bei Sammel-Läufen unbedingt nutzen, dann erzeugen Wiederholungen keine Doppel-Posts |

Pflichtfelder sind nur `social_account_id` und `caption`.

### Medien hochladen

Fast unverändert (multipart, Feld `file`). Zusätzlich möglich: `alt_text`,
`title`, `folder_id`, `tags`, `idempotency_key`. Antwort ist jetzt deutlich
reichhaltiger (unter anderem `processing_status`, `thumbnail_url`,
`aspect_ratio`, `duration`). Relevant bleibt die `id`.

### Konten abfragen

`GET /api/v1/accounts/` liefert je Konto zusätzlich `char_limit`,
`needs_title` und `supports_first_comment`. Das ist praktisch: das Content-Tool
kann damit **vor** dem Absenden prüfen, ob ein Text zu lang ist, ob ein Titel
verlangt wird (YouTube, Pinterest) und ob ein erster Kommentar überhaupt
unterstützt wird (bei TikTok, Pinterest, Bluesky und Google Business wird
`first_comment` beim Veröffentlichen sonst **stillschweigend verworfen**).

### Löschen

Posts lassen sich über die API **nicht** löschen. Geplante Posts werden mit
`POST /posts/{id}/cancel` zurück auf Entwurf gesetzt; Entwürfe entfernt man in
der Weboberfläche. Veröffentlichte Posts bleiben dauerhaft als Nachweis stehen.

## Fehler und Grenzen

- **429** bei Überschreiten des Rate-Limits, mit `Retry-After`-Kopfzeile und
  einem JSON-Rumpf mit `tier`, `limit`, `remaining`, `retry_after`. Die Kopfzeilen
  `X-RateLimit-*` kommen **nur** bei 429, nicht bei jeder Antwort.
- Zusätzlich gilt je Konto eine rollierende 24-Stunden-Grenze (Instagram 25 pro
  Tag, LinkedIn 100 pro Tag und so weiter) – ebenfalls als 429.
- **403**, wenn die `social_account_id` nicht in der Freigabeliste des Keys steht.

## Neuen API-Key erzeugen

In der Weboberfläche: **Organisation → API Keys → „Issue new key"**. Der Key wird
genau einmal im Klartext angezeigt – danach nicht mehr, auch nicht in der
Datenbank (dort liegt nur ein HMAC).

Der Key hängt an **einem** Workspace und an einer **ausdrücklichen Liste von
Social-Accounts**. Die Rechte werden je Key gesetzt: `create_posts`,
`publish_directly`, `upload_media`, `view_analytics`.

### Die Reihenfolge ist zwingend: erst Konto verbinden, dann Key

`issue_api_key` bricht mit
„An API key must allowlist at least one connected account." ab, wenn die Liste
leer ist – das ist Absicht (`apps/api_keys/models.py`: „size >= 1"). Ein Key kann
also **nicht auf Vorrat** erzeugt werden.

Praktisch heisst das: **pro Marke zuerst mindestens einen Kanal verbinden**, dann
den Key ausstellen. Am schnellsten geht Bluesky oder Mastodon – beide brauchen
keine Developer-App und keinen Freigabeprozess.

### Die beiden Workspaces (angelegt am 24.07.2026)

| Workspace | ID | Zweck |
|---|---|---|
| `Orbita Media Verlag` | `42f5b8c3-d7cc-4be3-8289-ad8c2407def7` | Influencer-Bücher, Hauptaccount |
| `Lucid Page Media` | `6f3b5abd-ea64-4e07-8c73-e703c5eb9936` | KI-Bücher, später eigener Account |

Beide liegen in der Organisation `Orbita Media GmbH`, Inhaber ist
`kontakt@orbita-media.de`.

Das Content-Tool braucht **zwei** Keys, einen je Workspace. Das verhindert
zuverlässig, dass ein KI-Buch auf dem Influencer-Kanal landet.

## Was das Content-Tool konkret ändern muss

Alles in `src/services/orbita-social.ts`, plus die Umgebungsvariablen:

1. **Key austauschen.** `ORBITA_SOCIAL_API_KEY` zeigt auf einen Key, den es
   nicht mehr gibt. Ersetzen durch die neuen Keys aus dem Vault – sinnvollerweise
   zwei Variablen, eine je Marke.
2. **Post-Erzeugung umbauen:** aus einem Aufruf mit `platform_posts: [...]`
   werden N Aufrufe mit `social_account_id`. Die zurückgegebenen Post-IDs
   einzeln merken.
3. **`status` → `action`**, `media[]` → `media_asset_ids[]`, `scheduled_at` in
   **UTC** senden.
4. **`idempotency_key` setzen** – bei Sammel-Läufen über viele Bücher der
   wirksamste Schutz gegen Doppel-Posts nach einem Netzfehler.
5. **`tags` entfernen**, optional `internal_notes` nutzen.
6. **Analytics anbinden:** die Analytics-Seite des Content-Tools zeigt bisher
   gezählte Demo-Objekte. Ab jetzt gibt es echte Zahlen über
   `GET /api/v1/analytics/accounts/{id}` und `/analytics/posts/{id}`.
7. **Vorabprüfung einbauen:** `char_limit`, `needs_title` und
   `supports_first_comment` aus `GET /accounts/` auswerten, bevor gesendet wird.

Zum Gegenlesen beim Umbau: `https://social.orbita-media.de/api/v1/docs` zeigt
die Endpunkte interaktiv mit allen Feldern.

## Zugangsdaten

Keys gehören ausschliesslich in den Vault, nie ins Repo und nie in eine
eingecheckte `.env`. Vorgesehene Item-Namen, sobald die Kanäle verbunden sind:

- `Orbita Social API-Key – Orbita Media Verlag`
- `Orbita Social API-Key – Lucid Page Media`

Abrufen mit `pwsh ~/.claude/tools/cred.ps1 get "<Name>"`.

**Stand 24.07.2026: diese beiden Items existieren noch nicht**, weil noch kein
Social-Account verbunden ist und Keys ohne Konto nicht erzeugt werden können
(siehe oben). Der bisherige Eintrag `Social Media Tool API-Key` enthält den
**alten**, seit dem Merge ungültigen Key – er liefert nur noch `401` und wird
ersetzt, sobald die neuen Keys existieren.

## Historie

- **12.06.2026** – eigene REST-API v1 gebaut (`0836ab9`), ohne zu bemerken, dass
  der Upstream zwölf Tage zuvor dieselbe Aufgabe offiziell gelöst hatte.
- **24.07.2026** – Upstream nachgezogen (212 Commits), eigene API entfernt
  (`afc51d4`), Umstieg auf die Agent-API.

Der ausführliche Ablauf des Merges steht in
`Social Media Content Tool/docs/UPSTREAM-MERGE-LOG.md`.
