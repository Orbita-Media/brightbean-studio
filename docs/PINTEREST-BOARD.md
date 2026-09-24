# Pinterest: Board, Link, Standard-Board und Video-Pins

Stand: 24.09.2026

## Anlass

`providers/pinterest.py` verlangt ein `board_id` in `platform_extra`, die
Agent-API konnte aber weder `board_id` noch `link_url` setzen. Die beiden
eingeplanten Pins (28.09. und 21.10.) hatten `platform_extra = {}` und wären
zum Termin gescheitert – Stunden später, ohne dass es jemand sieht. Dazu kam:
Video-Pins gingen nie als Video raus (das Kennzeichen `is_video` setzte
niemand, das MP4 landete als `image_url`), der Upload lief als `PUT` statt
als Multipart-`POST`, und das Titelbild wurde nicht übergeben.

## Gespeichert

| Ort | Schlüssel | Inhalt |
|---|---|---|
| `PlatformPost.platform_extra` | `board_id` | Board des Pins (Ziffern) |
| `PlatformPost.platform_extra` | `link_url` | Ziel-Link des Pins, `https://…` |
| `PlatformPost.platform_extra` | `cover_image_asset_id` | Titelbild eines Video-Pins (Bild-Asset) |
| `PlatformPost.platform_extra` | `thumb_offset_ms` | Frame als Titelbild, wenn kein Bild gesetzt ist |
| `SocialAccount.platform_settings` | `pinterest_default_board_id` / `_name` | Standard-Board des Kontos |

Regeln an einer Stelle: `apps/social_accounts/pinterest.py`.

## Agent-API

`platform_overrides[]` bei `POST /api/v1/posts/` und `PATCH /api/v1/posts/{id}`:

| Feld | Regel |
|---|---|
| `board_id` | nur Pinterest, nur Ziffern; `null` bei PATCH entfernt es (dann gilt das Standard-Board) |
| `link_url` | nur Pinterest, `https://` mit Host, ohne Leerzeichen, höchstens 2048 Zeichen; `null` oder `""` entfernt |
| `cover_asset_id` | bei Pinterest das Titelbild eines Video-Pins (JPEG oder PNG), gespeichert als `cover_image_asset_id` |
| `cover_offset_ms` | bei Pinterest der Frame, in ganze Sekunden abgerundet (`cover_image_key_frame_time`) |

PATCH geht auch bei **eingeplanten** Beiträgen; Termin und Status bleiben.
Die Antwort führt `board_id`, `link_url` und `cover_asset_id` in
`platform_overrides[]` zurück.

### 422 vor dem Termin

Ein Pinterest-Kanal, der eingeplant wird (`action: "schedule"`,
`POST /posts/{id}/schedule`) oder schon eingeplant ist und per PATCH Board,
Link, Titelbild oder Medien ändert, muss sofort veröffentlichbar sein:

- ein Board: das eigene `board_id` oder das Standard-Board des Kontos,
- bei einem Video-Pin ein Titelbild: `cover_asset_id` oder `cover_offset_ms`.

Sonst antwortet die API mit 422 und sagt, was fehlt. Ein Entwurf darf ohne
Board bleiben.

### Boards und Standard-Board

- `GET /api/v1/accounts/{id}/pinterest-boards` – alle Boards des Kontos
  (`id`, `name`, `privacy` = PUBLIC, PROTECTED oder SECRET) plus
  `default_board_id`. Liest direkt bei Pinterest (`GET /v5/boards`, alle
  Seiten über `bookmark`, `page_size` 250). 403 für Konten außerhalb des
  Schlüssels, 422 für Nicht-Pinterest-Konten, 502 wenn Pinterest nicht antwortet.
- `PUT /api/v1/accounts/{id}/pinterest-default-board` mit
  `{"board_id": "1082834372847314271"}` setzt das Standard-Board,
  `{"board_id": null}` entfernt es. Die ID wird gegen die echte Board-Liste
  geprüft, der Name kommt von Pinterest. Braucht `manage_social_accounts`.
- `GET /api/v1/accounts/` zeigt `pinterest_default_board_id` und
  `pinterest_default_board_name` je Konto.

Das Standard-Board wird nicht in die Pins kopiert: Wird es später geändert,
gilt für alle Pins ohne eigenes Board das neue.

## Oberfläche

- Kontenseite (`/social-accounts/<workspace>/`): Pinterest-Konten haben den
  Bereich „Standard-Board“ mit allen Boards als Auswahl; ein zweiter Klick auf
  das gewählte Board entfernt es.
- Composer: Ist kein Board gewählt, ist das Standard-Board vorausgewählt, und
  das Speichern ohne eigenes Board geht, sobald ein Standard-Board existiert.
  Der Pinterest-Bereich behält beim Speichern Werte, für die er kein Feld hat
  (etwa den per API gesetzten Frame).

## Veröffentlichen

- Engine (`apps/publisher/engine.py`): fehlt `board_id`, wird das
  Standard-Board eingesetzt. Das Titelbild (`cover_image_asset_id`) geht als
  öffentliche URL (`cover_image_url`) und als Datei an den Provider.
- Provider (`providers/pinterest.py`), Video-Pin:
  1. `POST /v5/media` mit `media_type=video` → `media_id`, `upload_url`, `upload_parameters`
  2. Multipart-`POST` an `upload_url` mit allen `upload_parameters` als
     Formularfeldern und der Datei als `file` – ohne unser Bearer-Token (S3)
  3. `GET /v5/media/{media_id}` bis `status = succeeded` (`failed` → Fehler)
  4. `POST /v5/pins` mit `media_source = {source_type: "video_id", media_id,
     cover_image_url}` bzw. `cover_image_key_frame_time`
- Video-Grenzen: 4 Sekunden bis 5 Minuten (vor dem ersten Aufruf geprüft,
  endgültiger Fehler), MP4/M4V, H.264/H.265, 9:16 empfohlen.

Quellen (geprüft am 24.09.2026): Pinterest OpenAPI 5.28.0
(`PinMediaSourceVideoID`, `MediaUpload`, `MediaUploadStatus`, `GET /boards`
mit `bookmark`/`page_size` bis 250, `PinCreate.link` maxLength 2048),
[Review Pin specs](https://help.pinterest.com/en/article/review-pin-specs).

## Tests

- `apps/api/tests/test_pinterest_api.py` – Felder, Validierung, 422 beim Einplanen, PATCH, Board-Endpunkte
- `tests/providers/test_pinterest_video.py` – Video-Pin-Ablauf, Titelbild, Grenzen, Board-Paginierung
- `apps/publisher/test_pinterest_board.py` – Rückfall auf das Standard-Board, Titelbild-URL
- `apps/composer/tests/test_pinterest_default_board.py` – Composer und Kontenseite
