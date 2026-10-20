# Papierkorb für Posts und Medien (seit 20.10.2026)

Auftrag Noah: Claude und die Agenten sollen im Verteiler alles selbst können,
auch löschen – wiederherstellbar.

## Verhalten

- Löschen setzt nur `deleted_at` (`Post`, `MediaAsset`). Der Standard-Manager
  (`objects`) blendet Gelöschtes aus, `all_objects` sieht alles. Bei Posts
  blendet auch der Standard-Manager von `PlatformPost` die Kinder aus
  (Kalender, Publisher, Analytics sehen sie nicht mehr).
- Post in den Papierkorb (`trash_post`): eingeplante Kinder gehen zurück auf
  Entwurf, damit nichts aus dem Papierkorb veröffentlicht wird. Läuft gerade
  eine Veröffentlichung (`publishing`), wird abgelehnt (409). Veröffentlichte
  Beiträge bleiben auf der Plattform stehen; ihre Quote zählt weiter
  (`apps/api/limits.py` zählt über `all_objects`).
- Medium in den Papierkorb (`trash_asset`): abgelehnt, solange ein eingeplanter
  oder laufender Post es nutzt. Die Datei bleibt bis zum endgültigen Löschen.
- Endgültig löschen geht nur aus dem Papierkorb (zweiter Schritt).
- Geteilte Organisations-Medien löscht die Organisations-Mediathek wie bisher
  direkt; über einen Workspace-Schlüssel nicht (403).

## API (gleicher Schlüssel wie beim Anlegen)

| Aufruf | Wirkung | Recht |
|---|---|---|
| `DELETE /api/v1/posts/{id}` | in den Papierkorb, Antwort mit `deleted_at` | `create_posts` |
| `POST /api/v1/posts/{id}/restore` | zurückholen, Kinder bleiben Entwurf | `create_posts` |
| `GET /api/v1/posts/trash?limit=50` | Papierkorb, neueste Löschung zuerst | – |
| `DELETE /api/v1/posts/{id}?permanent=true` | endgültig, nur aus dem Papierkorb (sonst 409) | `create_posts` |
| `DELETE /api/v1/media/{id}` | in den Papierkorb | `delete_media` |
| `POST /api/v1/media/{id}/restore` | zurückholen | `delete_media` |
| `GET /api/v1/media/?trashed=true&processing_status=any` | Papierkorb | – |
| `DELETE /api/v1/media/{id}?permanent=true` | endgültig, nur aus dem Papierkorb (sonst 409) | `delete_media` |

## Oberfläche

Workspace-Einstellungen → **Trash** (`/workspace/<id>/trash/`): Posts und
Medien mit „Restore“ und „Delete forever“ (Bestätigungsdialog). Der
Lösch-Knopf der Mediathek heißt „Move to trash“, Post-Löschen im Kalender und
im Composer wandert ebenfalls in den Papierkorb. Einen Kanal aus einem Post
entfernen (`?account=`) bleibt ein direktes Löschen dieser Kanalzeile.

## Mediendetailseite (Handy)

`/workspace/<id>/media/<asset>/` stand fest dreispaltig; am Handy wurde das
Seitenpanel auf rund 160 px gequetscht. Jetzt untereinander bis `lg`. Der
Lösch-Knopf dort zielte auf `#detail-panel`, das es nur in der Mediathek gab:
htmx brach mit targetError ab und schickte nichts. Die Seite hat jetzt ein
`#detail-panel`, nach dem Löschen leitet der Server per `HX-Redirect` zurück
zur Mediathek.

Tests: `apps/api/tests/test_trash_api.py`.
