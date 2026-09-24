# Titelbild für Reels und Videos

Stand: 24.09.2026

## Anlass

Der erste Reel über den Verteiler ging mit dem Standard-Titelbild von Instagram
raus: dem ersten Frame, einem verschwommenen Hintergrund ohne Text. Im Feed und
im Profilraster ist genau dieses Bild das, was vor dem Antippen zu sehen ist.
Jeder Reel bekommt deshalb sein stärkstes Bild mit Hook als Titelbild.

## Gespeichert in `PlatformPost.platform_extra`

| Schlüssel | Inhalt | Kanäle |
|---|---|---|
| `thumbnail_asset_id` | UUID eines Bild-MediaAssets im Workspace | youtube, instagram, instagram_login, facebook |
| `thumb_offset_ms` | Frame des Videos in Millisekunden (0 = erster Frame) | instagram, instagram_login, tiktok |
| `video_cover_timestamp_ms` | derselbe Frame, TikTok-eigener Schlüssel (wird bei TikTok immer mitgeschrieben) | tiktok |

Regeln an einer Stelle: `providers/video_cover.py`.

## Was jede Plattform kann

| Plattform | Vor dem Veröffentlichen | Nach dem Veröffentlichen |
|---|---|---|
| Instagram (beide Anbindungen) | `cover_url` (Bild) oder `thumb_offset` (Frame), nur bei Reels | nicht änderbar – ein veröffentlichtes IG-Media kennt per API nur `comment_enabled` |
| Facebook | nach dem Upload `POST /{video_id}/thumbnails` (`source`, `is_preferred=true`) | ja, derselbe Aufruf |
| YouTube | `thumbnails.set` nach dem Upload | ja, `thumbnails.set` |
| TikTok | `post_info.video_cover_timestamp_ms` (nur Frame, kein Bild) | nicht änderbar |
| alle anderen | – | – |

Quellen (geprüft am 24.09.2026): Graph-API-Referenz „IG User Media“
(`cover_url`: „For Reels only … must be on a public server“, JPEG, höchstens
8 MB, sRGB, 9:16 empfohlen; `thumb_offset`: „Location, in milliseconds … default
0“; „If you specify both cover_url and thumb_offset, we use cover_url and ignore
thumb_offset“), „IG Media – Updating“ (nur `comment_enabled`), „Video Thumbnails“
(`source` höchstens 10 MB, `is_preferred`), TikTok „Direct Post API Reference“
(`video_cover_timestamp_ms`).

## Profilraster 3:4

Instagram (und TikTok) zeigen im Profilraster aus dem 9:16-Titelbild nur den
mittleren 3:4-Ausschnitt: oben und unten fällt je ein Achtel weg (bei 1080 × 1920
bleiben die Zeilen 240 bis 1680). Hook, Gesicht und Buchtitel gehören in diesen
Bereich. Der Composer zeigt ihn als gestrichelten Rahmen in der Vorschau.

## Verhalten beim Veröffentlichen

- Instagram: Die Engine macht aus `thumbnail_asset_id` eine öffentliche absolute
  URL (`thumbnail_url`, gleiche APP_URL-Regel wie die Medien). Mit Bild geht nur
  `cover_url` raus, sonst `thumb_offset`. Bei Story, Bild oder Karussell wird das
  Titelbild mit Log-Warnung ignoriert, der Beitrag geht trotzdem.
- Lehnt Instagram das Bild ab (kein JPEG, zu groß, nicht erreichbar), wird der
  Container ohne Bild neu angelegt – mit dem Frame aus `thumb_offset_ms`, falls
  gesetzt, sonst mit dem Standard. Der Plattform-Ton bleibt dabei erhalten.
  Das Ergebnis trägt dann `cover_dropped: true`.
- Facebook: Das Titelbild wird nach dem Video-Upload gesetzt. Ein Fehler dort
  lässt den Beitrag nie scheitern (sonst Doppelpost durch den Retry); er landet
  als `thumbnail_set: false` und `thumbnail_error` in `platform_extra`.
- YouTube: wie bisher `thumbnails.set`, jetzt mit `thumbnail_set` im Ergebnis.

## Agent-API

### Beim Anlegen und per PATCH

`platform_overrides[]` von `POST /api/v1/posts` und `PATCH /api/v1/posts/{id}`:

```json
{"social_account_id": "…", "cover_asset_id": "<UUID eines Bildes>", "cover_offset_ms": 2400}
```

- `cover_asset_id`: Bild-Asset des Workspace; für Instagram nur JPEG bis 8 MB,
  Facebook bis 10 MB, YouTube bis 2 MB. Sonst 422.
- `cover_offset_ms`: ≥ 0 und innerhalb der Videolänge, wenn sie bekannt ist.
- Ein Feld, das der Kanal nicht kann (Frame bei YouTube/Facebook, Bild bei
  TikTok, alles bei Bluesky …), antwortet 422. Ein Beitrag nur mit Bildern
  bekommt kein Titelbild (422).
- PATCH: weggelassen = bleibt, `null` = entfernen. Ton, Mitwirkende und
  Test-Reel bleiben unberührt.
- `GET /api/v1/posts/{id}` zeigt je Kanal `platform_overrides[].cover_asset_id`
  und `cover_offset_ms`.

### Für geplante und veröffentlichte Beiträge: `POST /api/v1/posts/{id}/cover`

PATCH antwortet bei `scheduled` mit 409. Dieser Endpunkt arbeitet in jedem Status:

```json
{"cover_asset_id": "<UUID>" , "cover_offset_ms": 2400, "social_account_id": null}
```

- Ohne `social_account_id`: alle Kanäle des Beitrags; was ein Kanal nicht kann,
  wird übersprungen und gemeldet. Mit `social_account_id`: nur dieser Kanal, ein
  unpassendes Feld ist dann 422.
- Noch nicht veröffentlicht: Wert in `platform_extra`, Termin und Status bleiben.
- Veröffentlicht: YouTube und Facebook ändern das Live-Video (braucht die
  Berechtigung `publish_directly`); Instagram und TikTok melden „nach dem
  Veröffentlichen nicht änderbar“. Entfernen geht dort nicht, nur ersetzen.
- Gerade in `publishing`: 409, nichts wird geändert.

Antwort:

```json
{"post_id": "…", "results": [
  {"social_account_id": "…", "platform": "instagram", "status": "scheduled",
   "result": "saved", "message": "Instagram: Titelbild gespeichert, …"}
]}
```

`result` ist einer von `saved` (gespeichert für später), `updated` (am
veröffentlichten Video geändert), `unchanged` (identischer Aufruf), `unsupported`
(Plattform kann es nicht), `error` (versucht, gescheitert, nichts gespeichert).
Der Aufruf ist idempotent.

## Composer

Bereich „Titelbild“ je Instagram- und Facebook-Kanal eines Video-Beitrags:
Bild aus der Mediathek, Bild hochladen, „Frame als Bild“ (speichert den Frame als
JPEG-Asset) oder – nur Instagram – „Frame per Regler“ (Video springt mit, setzt
`thumb_offset_ms`). Vorschau im 9:16-Format mit gestricheltem 3:4-Rahmen, rote
Warnung, wenn Instagram ein Nicht-JPEG bekommen würde. TikTok behält seine
Cover-Auswahl in den TikTok-Einstellungen (jetzt „Titelbild (Cover)“), YouTube
den Block „Custom Thumbnail“.

## Dateien

| Bereich | Datei |
|---|---|
| Regeln | `providers/video_cover.py` |
| Provider | `providers/instagram.py`, `providers/instagram_login.py`, `providers/facebook.py`, `providers/youtube.py`, `providers/tiktok.py`, `providers/base.py` (`set_video_thumbnail`) |
| Engine | `apps/publisher/engine.py` |
| Nachträglich | `apps/publisher/cover.py` |
| Agent-API | `apps/api/schemas.py`, `apps/api/routers/posts.py` |
| Composer | `apps/composer/views.py` (`_cover_extra`), `templates/composer/compose.html` |
| Tests | `tests/providers/test_video_cover.py`, `apps/publisher/test_video_cover.py`, `apps/api/tests/test_video_cover_api.py`, `apps/composer/tests/test_video_cover.py` |
