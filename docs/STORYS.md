# Storys auf Instagram und Facebook-Seiten

Stand: 24.09.2026

## Anlass

Jeder Feed-Beitrag und jeder Reel soll rund 26 Stunden nach der Veröffentlichung
zusätzlich als Story auf Instagram und auf der Facebook-Seite erscheinen, damit
das Konto dauerhaft eine Story zeigt. Das Content-Tool plant diese Storys selbst
als eigene Beiträge mit eigenem Termin ein. Der Verteiler kennt dafür den
Beitragstyp „Story“ – mehr nicht: Er erzeugt keine Storys von selbst.

## Gespeichert in `PlatformPost.platform_extra`

| Schlüssel | Wert | Kanäle |
|---|---|---|
| `post_type` | `"story"` | instagram, instagram_login, facebook |

Die Publish-Engine liest den Hinweis in `_resolve_post_type` und übergibt
`PostType.STORY` an den Provider. Regeln an einer Stelle: `providers/story.py`
(Grenzen, Medienprüfung, Hilfsfunktionen für API, Composer und Provider).

## Agent-API

`platform_overrides[].post_type` bei `POST /api/v1/posts/` und
`PATCH /api/v1/posts/{id}`:

| Wert | Bedeutung |
|---|---|
| `"story"` | dieser Kanal wird als Story veröffentlicht |
| `null` | beim Anlegen: normaler Beitrag; bei PATCH: Story wieder ausschalten |
| Feld weglassen | bei PATCH: gespeicherter Wert bleibt |

Andere Werte lehnt schon das Schema ab (422). Die Antwort führt den Wert in
`platform_overrides[].post_type` zurück (`"story"` oder `null`).

PATCH geht bei Entwürfen und bei **eingeplanten** Beiträgen (Termin und Status
bleiben unverändert), nicht bei veröffentlichten (409).

Beispiel – Story für den Reel von gestern, 26 Stunden später eingeplant:

```json
POST /api/v1/posts/
{
  "social_account_id": "<instagram-konto>",
  "caption": "Story zu: Reel 12",
  "media_asset_ids": ["<video-asset>"],
  "action": "schedule",
  "scheduled_at": "2026-09-26T08:00:00Z",
  "platform_overrides": [
    {"social_account_id": "<instagram-konto>", "post_type": "story"}
  ]
}
```

Die `caption` ist Pflicht im Schema, wird bei einer Story aber nicht gesendet
(eine Story hat keinen Text). Sie dient nur als Titel in Kalender und Listen.

### Was die API mit 422 ablehnt

| Fall | Meldung (gekürzt) |
|---|---|
| Kanal ist nicht Instagram oder Facebook | „post_type 'story' gibt es nur für Instagram und Facebook-Seiten …“ |
| kein Medium (Text-Beitrag) | „… braucht genau ein Bild oder ein Video; dieser Beitrag hat kein Medium.“ |
| mehr als ein Medium (Karussell) | „… dieser Beitrag hat N Medien. Für ein Karussell je Folie eine eigene Story anlegen.“ |
| Video länger als 60 Sekunden | „… höchstens 60 Sekunden; dieses Video ist 61.2 Sekunden lang …“ |
| Video kürzer als 3 Sekunden | „… mindestens 3 Sekunden …“ |
| Instagram: Bild kein JPEG oder über 8 MB | „Instagram nimmt als Story-Bild nur JPEG …“ |
| Instagram: Video über 100 MB oder nicht MP4/MOV | „… höchstens 100 MB“ bzw. „nur MP4 oder MOV“ |
| Facebook: Foto über 10 MB oder kein JPEG/PNG/GIF/BMP/TIFF | „Facebook nimmt als Story-Foto …“ |
| Titelbild, Mitwirkende, Instagram-Sound oder Test-Reel am selben Kanal | „Eine Story hat kein(e) … Im selben Aufruf leeren (null, [] bzw. false) oder post_type weglassen.“ |
| PATCH mit neuen Medien, die nicht mehr passen | dieselben Medien-Meldungen |

Gespeicherte Werte zählen mit: Hat ein Entwurf schon ein Titelbild, wird
`post_type: "story"` abgelehnt, solange `cover_asset_id`/`cover_offset_ms`
nicht im selben Aufruf auf `null` gesetzt werden. `POST /posts/{id}/cover`
setzt auf einer Story kein Titelbild (bei ausdrücklichem Kanal 422, sonst
`result: "unsupported"`).

Die Videolänge ist erst bekannt, wenn die Mediathek das Video verarbeitet hat
(`duration > 0`). Ein frisch hochgeladenes Video ohne Länge geht durch; der
Provider prüft die Länge unmittelbar vor dem Veröffentlichen noch einmal und
lässt die Story sonst endgültig scheitern (kein Wiederholen).

## Plattformgrenzen (geprüft am 24.09.2026)

| | Instagram (beide Anbindungen) | Facebook-Seite |
|---|---|---|
| Bild | JPEG, höchstens 8 MB, 9:16 empfohlen, sRGB | JPEG, PNG, GIF, BMP, TIFF, höchstens 10 MB (PNG besser unter 1 MB) |
| Video | MP4 oder MOV, **3 bis 60 Sekunden**, höchstens 100 MB, 23–60 fps, höchstens 1920 px breit, H.264/HEVC, AAC | MP4 empfohlen, **3 bis 60 Sekunden**, 9:16, 1080 × 1920 empfohlen (mindestens 540 × 960), 24–60 fps, H.264/H.265, AAC 48 kHz |
| Text, Alternativtext | nicht vorhanden | nicht vorhanden |
| Link-, Umfrage-, Orts-, Beitrags-Sticker | per API nicht möglich | per API nicht möglich |
| Musik | per API nicht möglich (Instagram-Sound nur bei Reels) | per API nicht möglich |
| Mitwirkende, Titelbild, Test-Reel | nicht möglich | nicht möglich |
| Erster Kommentar | nicht möglich – die Engine überspringt ihn bei Storys | nicht möglich |
| Sichtbarkeit | 24 Stunden | 24 Stunden |

Ein Story-Video über 60 Sekunden teilt die Instagram-App selbst in mehrere
Teile; über die API gibt es das nicht. Längere Reels brauchen für die Story
einen eigenen Schnitt bis 60 Sekunden (die aktuellen Reels liegen bei
13,7 bis 55 Sekunden, passen also).

Facebook lehnt ein Foto oder Video ab, das schon in einem veröffentlichten
Beitrag steckt. Der Provider lädt deshalb für jede Story neu hoch; dasselbe
Asset aus der Mediathek kann trotzdem für Feed-Beitrag und Story dienen.

Quellen: Graph-API-Referenz „IG User Media“ (`media_type=STORIES`, Story-Bild-
und Videospezifikation, „Publishing stickers … is not supported“, keine
collaborators bei Storys), Facebook „Page Stories API“ (Photo Stories,
Video Stories, Anforderungen, `GET /{page_id}/stories`).

## Ablauf beim Veröffentlichen

**Instagram** (`providers/instagram.py`, `providers/instagram_login.py`):
Container `POST /{ig-user-id}/media` bzw. `/me/media` mit
`media_type=STORIES` und `image_url` oder `video_url` – sonst nichts. Ob Bild
oder Video, entscheidet der Medientyp aus der Mediathek (`media_types` in
`PublishContent`), nicht die Dateiendung. Danach Warten auf `FINISHED`
(Video-Storys werden wie Reels umgewandelt, bis etwa 2 Minuten),
`media_publish`, dann best-effort der echte `permalink`.

**Facebook** (`providers/facebook.py`):

- Foto: `POST /{page_id}/photos` mit `url` und `published=false`, dann
  `POST /{page_id}/photo_stories` mit `photo_id`. Schlägt der zweite Schritt
  fehl, wird das unveröffentlichte Foto wieder gelöscht.
- Video: `POST /{page_id}/video_stories` mit `upload_phase=start` liefert
  `video_id` und `upload_url`; Upload als gehostete Datei per
  `POST <upload_url>` mit den Kopfzeilen `Authorization: OAuth <token>` und
  `file_url: <öffentliche URL>`; Statusabfrage `GET /{video_id}?fields=status`
  bis `uploading_phase.status = complete`; dann `upload_phase=finish` mit
  `video_id`.
- Ergebnis: `post_id` der Story als `platform_post_id`, URL aus
  `GET /{page_id}/stories` (Rückfall `https://www.facebook.com/stories/{page_id}/`).

Alle Fehler kommen als `PublishError` mit Klartext; Medienanzahl und Länge
werden vor dem ersten Aufruf geprüft (endgültig, kein Wiederholen).

## Composer

Im Instagram- und Facebook-Bereich des Composers gibt es den Schalter
„Als Story veröffentlichen“. Er erscheint bei genau einem angehängten Medium
(und immer, wenn er schon an ist, damit er sich ausschalten lässt). Das
versteckte Feld `story_<konto>` wird immer mitgeschickt; Sound, Mitwirkende,
Test-Reel und Titelbild bleiben beim Speichern erhalten. Solange die Story an
ist, ist der Titelbild-Bereich ausgeblendet (der Wert bleibt gespeichert), und
der Test-Reel-Bereich warnt, falls beides an ist.

In Kalender (Monat, Woche, Tag), Liste, Warteschlange, Entwürfen, Gesendet und
im Organisationskalender trägt eine Story das Badge „Story“.

## Tests

- `apps/api/tests/test_story_api.py` – Anlegen, Einplanen, PATCH setzen/behalten/leeren, alle 422-Fälle, `/cover` auf Storys
- `tests/providers/test_story.py` – Instagram Bild/Video (beide Anbindungen), Facebook Foto-/Video-Story mit Mocks, Fehlerwege, Grenzen
- `apps/publisher/test_story.py` – Engine-Hinweis, Medientypen, kein erster Kommentar
- `apps/composer/tests/test_story.py` – Schalter speichert ohne andere Werte zu verlieren
- `apps/calendar/test_story_badge.py` – Badge in Kalender und Listen
