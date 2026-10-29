# Bildprüfung vor dem Veröffentlichen

Stand 30.10.2026. Kein Bild und kein Video verlässt Orbita Social mehr, ohne
dass es geprüft wurde.

## Anlass

Am 29.10.2026 ging das KI-Bild „Buch in Szene“ zu *Simply Bowls* als
Kollaboration mit @marias_foodstyle auf Instagram und Facebook raus: das Buch
steht mit der Unterkante auf dem Schüsselrand, ein Dressing-Strahl läuft
senkrecht durchs Bild, das Essen ist teils nicht zu benennen. Die Autorin hat
sich beschwert. Seitdem prüft die **Bildwache** (Social-Agent im Dirigent) jedes
Medium, und der Publisher lässt nur Geprüftes durch. Prüfmaßstab: Grenzfall =
durchgefallen (Buch physikalisch unmöglich, Durchdringung, Linien/Strahlen,
Undefinierbares, Schrift, KI-Menschen, KI-Gesamteindruck, Cover).

## Datenmodell

`MediaAsset.bildpruefung` (JSON, `null` = ungeprüft):

```json
{
  "status": "bestanden | durchgefallen | von_hand_freigegeben",
  "sha256": "<hex der geprüften Datei>",
  "pruefer": "bildwache/dirigent",
  "modelle": ["claude-opus-5-5", "gemini-3-pro"],
  "befunde": ["Buch steht auf dem Schüsselrand", "…"],
  "geprueft_am": "2026-10-29T22:15:00+00:00",
  "notiz": "…"
}
```

- **Gültig ist eine Prüfung nur, wenn `sha256` zur aktuellen Datei passt.**
  Neue Datei, Bearbeitung (Zuschnitt, Trim) oder wiederhergestellte Version
  machen sie damit von selbst ungültig; das Medium ist wieder offen.
- `MediaAsset.sha256` / `sha256_datei`: Zwischenspeicher des Datei-Hashes für
  genau den Dateinamen in `sha256_datei`. Dateinamen sind im Speicher eindeutig
  (`AWS_S3_FILE_OVERWRITE = False`), ein neuer Name heißt neu rechnen. Gerechnet
  wird beim Upload (Task `process_media_asset`), beim Speichern einer Prüfung
  (immer frisch) und bei Bedarf.
- Regeln für das Objekt: `durchgefallen` braucht mindestens einen Befund,
  `von_hand_freigegeben` eine Notiz; `pruefer` ist Pflicht. `geprueft_am`
  fehlt: jetzt.
- Code: `apps/media_library/bildpruefung.py` (Auswertung `pruefstand`, Liste
  `offene_medien`, Speichern `pruefung_speichern`).

## Publisher-Sperre

`PublishEngine._block_on_image_check` (`apps/publisher/engine.py`) läuft in
`_dispatch_to_provider` vor jedem Download, vor dem Nachsehen nach unklaren
Versuchen und vor der Übergabe an die Plattform. Geprüft wird jedes Medium,
das wirklich veröffentlicht wird: alle Anhänge (bei YouTube/TikTok nur die
Videos, die Bilder lassen diese Kanäle weg) und Titelbilder aus
`platform_extra` (`thumbnail_asset_id`, `cover_image_asset_id`). Das gilt für
alle Kanäle und Formate (Feed, Karussell, Story, Reel, Pin, Short …), weil
alle durch diese eine Stelle gehen.

| Stand des Mediums | Schalter AUS (Standard) | Schalter AN |
|---|---|---|
| `bestanden` / `von_hand_freigegeben`, sha256 passt | veröffentlichen | veröffentlichen |
| `durchgefallen`, sha256 passt | **endgültig FAILED**, Befunde im Fehlertext | **endgültig FAILED** |
| ungeprüft oder sha256 einer älteren Datei | veröffentlichen | **zurückhalten** |

- **Durchgefallen sperrt immer**, auch bei ausgeschaltetem Schalter
  (Hauptsitzung 29.10.: die Bestandsprüfung fand 23 durchgefallene Bilder, die
  vor dem Einschalten schon gesperrt sein müssen). `PublishError` mit
  `retryable=False`, Fehlertext beginnt mit „Bildprüfung durchgefallen“.
- **Zurückhalten** (`_hold_for_image_check`): Status bleibt `scheduled`,
  `retry_count` bleibt unverändert (kein Versuch verbraucht), `next_retry_at`
  = jetzt + 10 min, `publish_error` beginnt mit „Bildprüfung fehlt“ und nennt
  die offenen Medien. Beide Fälligkeitspfade (Erstversuch und Retry) warten
  auf `next_retry_at`. Ein PublishLog-Eintrag je Blick.
- **6 h über dem Termin** (Termin = `PlatformPost.scheduled_at` oder
  `Post.scheduled_at`) scheitert ein zurückgehaltener Beitrag endgültig
  („Bildprüfung fehlt auch 6 Stunden nach dem Termin …“). Neu einplanen nach
  der Prüfung.
- Doppelpost-Schutz (docs/DOPPELPOST-SCHUTZ.md) bleibt unberührt: die Sperre
  liegt vor `on_dispatch`, `dispatched_at` bleibt leer, `outcome_unknown_since`
  bleibt beim Zurückhalten erhalten.
- Wird ein Medium nach dem Zurückhalten geprüft, geht der Beitrag beim nächsten
  Blick (höchstens 10 min) als ganz normaler Versuch raus.

## Schalter `bildpruefung_pflicht`

In der DB (`publisher_schalter`), wirkt ohne Redeploy ab dem nächsten
Publisher-Takt. **Startet AUS**; die Hauptsitzung schaltet ihn ein, wenn der
Bestand geprüft ist.

```bash
docker exec -it app-xos84sccocw488o8kccow88g-<…> python manage.py bildpruefung_pflicht status
docker exec -it app-xos84sccocw488o8kccow88g-<…> python manage.py bildpruefung_pflicht an --von "Hauptsitzung"
docker exec -it app-xos84sccocw488o8kccow88g-<…> python manage.py bildpruefung_pflicht aus --von "…"
```

`status` zeigt Zustand, wer zuletzt umgeschaltet hat, und die Zahl der offenen
und durchgefallenen Medien. `an` warnt, wenn noch Ungeprüftes an Beiträgen hängt.

## Liste der offenen Medien

```bash
python manage.py bildpruefung_offen                       # Zahl + Liste nach Termin
python manage.py bildpruefung_offen --bis 2026-11-12T00:00:00Z --json
python manage.py bildpruefung_offen --durchgefallen       # gültig durchgefallene
python manage.py bildpruefung_offen --sha-rechnen         # fehlende sha256 nachrechnen
```

„Künftige Beiträge“ heißt: PlatformPosts mit Status `draft`, `pending_review`,
`pending_client`, `approved`, `changes_requested`, `on_hold` oder `scheduled`
(Beitrag nicht im Papierkorb). Das ist weiter als der ursprüngliche Vertrag
(scheduled/draft), damit nichts aus einer Freigabeschleife ungeprüft
nachrutscht.

## API (Agent-API, API-Key je Workspace)

Authentifizierung wie alle `/api/v1`-Endpunkte (`Authorization: Bearer bb_studio_…`).

### `GET /api/v1/media/bildpruefung/offen/`

Parameter: `bis` (ISO, nur Beiträge mit Termin bis dahin; Entwürfe ohne Termin
fallen dann heraus), `workspace` (optional, muss der Workspace des Keys sein,
sonst 403), `status` (`offen` Standard oder `durchgefallen`), `limit` (Standard
1000, höchstens 5000).

```json
{
  "anzahl": 1,
  "durchgefallen_anzahl": 23,
  "pflicht_an": false,
  "status": "offen",
  "bis": null,
  "workspace_id": "…",
  "items": [
    {
      "id": "8c1e…",
      "media_type": "image",
      "filename": "buch-in-szene.png",
      "mime_type": "image/png",
      "url": "https://…/media_library/2026/10/buch-in-szene.png?X-Amz-…",
      "sha256": "9f2b…" ,
      "termin": "2026-10-30T11:30:00Z",
      "plattformen": ["facebook", "instagram"],
      "workspace_id": "…",
      "beitragstext": "Cosmos – …",
      "bildpruefung_status": "offen",
      "grund": "fehlt",
      "bildpruefung": null,
      "beitraege": [{"post_id": "…", "termin": "2026-10-30T11:30:00Z", "plattformen": ["instagram"]}]
    }
  ]
}
```

- `url` ist der Download (vorsigniert, kurzlebig).
- `sha256` ist `null`, solange der Wert noch nicht gerechnet ist (die Liste lädt
  keine Dateien). Die Bildwache rechnet ihn aus der heruntergeladenen Datei;
  beim POST wird er gegen die Datei abgeglichen.
- `grund`: `fehlt` (nie geprüft), `veraltet` (Prüfung zu einer älteren Datei),
  `datei_fehlt` (Datei nicht lesbar).

### `POST /api/v1/media/{id}/bildpruefung/`

Body = Prüfobjekt (siehe oben). Braucht die Key-Berechtigung `edit_media`;
`von_hand_freigegeben` zusätzlich einen Key-Aussteller, der (noch) Owner/Admin
ist.

- `200`: das Medium wie bei `GET /media/{id}` (mit `bildpruefung`).
- `409`: `sha256` passt nicht zur aktuellen Datei:
  `{"error": "conflict", "detail": "…", "sha256_gemeldet": "…", "sha256_aktuell": "…"}`.
- `422`: Vertrag verletzt (Status unbekannt, `durchgefallen` ohne Befund,
  `von_hand_freigegeben` ohne Notiz, sha256 kein 64-stelliges Hex …).

```bash
curl -X POST https://social.orbita-media.de/api/v1/media/<id>/bildpruefung/ \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"status":"durchgefallen","sha256":"<hex>","pruefer":"bildwache/dirigent",
       "modelle":["claude-opus-5-5"],"befunde":["Buch steht auf dem Schüsselrand"],
       "geprueft_am":"2026-10-29T22:15:00Z","notiz":""}'
```

### `GET /api/v1/media/{id}` und die Liste `GET /api/v1/media/`

Liefern zusätzlich `bildpruefung` (Objekt oder `null`), `bildpruefung_status`
(`bestanden | durchgefallen | von_hand_freigegeben | offen`),
`bildpruefung_gueltig` und `sha256` (zwischengespeichert oder `null`).

## Oberfläche

Abzeichen „Bildprüfung: bestanden / durchgefallen / offen / von Hand
freigegeben“ an jedem Bild und Video (Dokumente nicht):

| Ort | Template | Form |
|---|---|---|
| Composer, angehängte Medien (96-px-Vorschau) | `composer/partials/media_list.html` | `thumb`: Leiste unten, nur Statuswort |
| Mediathek, Raster | `media_library/_asset_card.html`, `_shared_asset_card.html` | `karte`: Pille unten links (ab `sm` mit „Bildprüfung:“) |
| Mediathek, Liste | `media_library/_asset_list_row.html` | `zeile` |
| Mediathek, Detail (Panel und eigene Seite) | `media_library/_asset_detail_panel.html` | Block mit Grund, Befunden, Prüfer, Zeit (Berlin), Notiz |

Farben: bestanden grün, von Hand freigegeben blau, durchgefallen rot (weiße
Schrift), offen gelb. Ein Klick auf ein Abzeichen öffnet den Dialog
(`media_library/_bildpruefung_dialog.html`, einmal in `base.html`, Event
`bildpruefung-zeigen`) mit Befunden, Prüfer, Modellen und Notiz.

**Von Hand freigeben** (eigener Dialog, keine Browser-Popups): sichtbar nur für
Owner/Admin der Organisation und Owner des Workspace, und nur solange das
Medium nicht schon freigegeben ist. Org-geteilte Medien darf nur Owner/Admin
der Organisation freigeben. Die Begründung ist Pflicht (höchstens 2000
Zeichen). `POST /workspace/<ws>/media/<asset>/bildpruefung/freigeben/`
(`media_library:asset_bildpruefung_freigeben`, Feld `notiz`) rechnet den
sha256 der Datei frisch, schreibt `von_hand_freigegeben` mit dem Benutzer als
`pruefer` („Name <E-Mail>“) und antwortet mit JSON (`bildpruefung`,
`abzeichen` je Form, `block`), damit die Seite ohne Neuladen den neuen Stand
zeigt. 403 ohne Recht, 422 ohne Begründung oder bei unlesbarer Datei. Befunde
einer vorherigen Prüfung derselben Datei bleiben stehen.

Der Stand wird je Seite und Medium einmal ausgewertet (Templatetag
`apps/media_library/templatetags/bildpruefung_tags.py`); der sha256 wird nur
gerechnet, wenn eine Prüfung vorliegt und kein gemerkter Wert zur Datei passt.
Tests: `apps/media_library/tests/test_bildpruefung_ui.py`.

## Abweichungen vom ursprünglichen Vertrag

- Durchgefallen sperrt auch bei Schalter AUS (Änderung der Hauptsitzung vom 29.10.).
- `GET …/offen/` umfasst alle noch veröffentlichbaren Status (siehe oben),
  nicht nur scheduled/draft; Titelbilder zählen mit.
- Feldname `termin` statt „nächster Termin“; zusätzlich `grund`,
  `bildpruefung_status`, `beitraege`, `durchgefallen_anzahl`, `pflicht_an`.
- `workspace` ist optional und kann nur der Workspace des Keys sein (Keys sind
  je Workspace ausgestellt); für mehrere Workspaces je Workspace einen Key.
- `sha256` in der Liste kann `null` sein (siehe oben).
- Einzelabruf heißt `GET /api/v1/media/{id}` **ohne** Schrägstrich am Ende (so
  sind alle bestehenden Einzelrouten der Medien-API gebaut; mit Schrägstrich
  kommt 404). Die beiden neuen Bildprüfungs-Routen haben den Schrägstrich.
