# Orbita Social – Externe Content-API (v1)

REST-API für externe Tools, um Posts programmatisch anzulegen und zu schedulen.
Basis-URL: `https://social.orbita-media.de/api/v1/`

## Authentifizierung

Jeder Request braucht einen Workspace-API-Key als Bearer-Token:

```
Authorization: Bearer <64-hex-key>
```

Der Key bindet alle Requests an genau einen Workspace – fremde Accounts,
Posts und Media Assets sind nicht erreichbar. Ohne gültigen Key: `401`.

Rate-Limit: 120 Requests pro Minute und Key (danach `429`).

### Key erzeugen (auf dem Server, im app-Container)

```bash
python manage.py create_api_key --workspace "Mein Workspace" --name "Content Tool"
```

`--workspace` akzeptiert den Workspace-Namen oder die UUID. Keys lassen sich
im Django-Admin (Workspace API Keys) deaktivieren.

## Endpoints

### GET /api/v1/accounts/

Listet die verbundenen Social Accounts des Workspace.

```bash
curl -H "Authorization: Bearer $API_KEY" \
  https://social.orbita-media.de/api/v1/accounts/
```

Antwort `200`:

```json
{
  "accounts": [
    {
      "id": "b9c1…",
      "platform": "instagram",
      "account_name": "Orbita Media",
      "account_handle": "orbita.media",
      "connection_status": "connected"
    }
  ]
}
```

### POST /api/v1/media/

Multipart-Upload eines Media-Assets (Bild, Video, GIF, PDF).
Felder: `file` (Pflicht), `alt_text` (optional), `title` (optional).
Limits: Bilder 20 MB, Videos 1 GB.

```bash
curl -H "Authorization: Bearer $API_KEY" \
  -F "file=@cover.jpg" \
  -F "alt_text=Buchcover Beispiel" \
  https://social.orbita-media.de/api/v1/media/
```

Antwort `201`:

```json
{
  "id": "3f2a…",
  "url": "https://social.orbita-media.de/media/media_library/2026/06/cover.jpg",
  "media_type": "image",
  "width": 1080,
  "height": 1350
}
```

Hinweis: Bei Bildern stehen `width`/`height` sofort in der Response, bei
Videos werden die Metadaten asynchron vom Worker nachgetragen. Die `url` ist
bei lokalem Storage informativ – relevant für Posts ist die `id`.

### POST /api/v1/posts/

Erstellt einen Post inkl. PlatformPosts (Ziel-Accounts) und Media-Anhängen.
Content-Type: `application/json`.

Request-Body:

```json
{
  "caption": "Basistext des Posts",
  "title": "Optionaler Titel (z. B. YouTube)",
  "first_comment": "Optionaler erster Kommentar",
  "scheduled_at": "2026-07-01T09:00:00+02:00",
  "tags": ["buchrelease", "fantasy"],
  "platform_posts": [
    {
      "social_account_id": "<uuid aus /accounts/>",
      "status": "scheduled",
      "platform_specific_caption": "Optionaler Override pro Plattform",
      "platform_specific_title": null,
      "platform_extra": {},
      "scheduled_at": "2026-07-01T10:30:00+02:00"
    }
  ],
  "media": [
    {
      "media_asset_id": "<uuid aus /media/>",
      "position": 0,
      "alt_text": "Optionaler Alt-Text"
    }
  ]
}
```

Regeln:

- `caption` ist Pflicht (darf leer sein), alles andere optional.
- `status` pro PlatformPost: nur `draft` oder `scheduled` (Default
  `scheduled`). `published` kann über die API nicht gesetzt werden –
  das Publishing übernimmt die Publisher-Engine.
- `status: "scheduled"` braucht ein `scheduled_at` – entweder am
  PlatformPost oder auf Post-Ebene (PlatformPost-Zeit hat Vorrang).
  Zeiten in ISO 8601, idealerweise mit Zeitzonen-Offset.
- `social_account_id` und `media_asset_id` müssen zum Workspace des
  API-Keys gehören (Media: auch geteilte Org-Assets erlaubt), sonst `400`.
- Pro Social Account ist nur ein PlatformPost erlaubt, jedes Media-Asset
  nur einmal.
- Ein Post ohne `platform_posts` ist erlaubt (reiner Entwurf).
- Liegt `scheduled_at` in der Vergangenheit, published die Engine beim
  nächsten Poll (alle ~15 Sekunden) sofort.

```bash
curl -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "caption": "Neues Buch ist da!",
    "scheduled_at": "2026-07-01T09:00:00+02:00",
    "platform_posts": [
      {"social_account_id": "<account-uuid>", "status": "scheduled"}
    ],
    "media": [
      {"media_asset_id": "<asset-uuid>", "position": 0}
    ]
  }' \
  https://social.orbita-media.de/api/v1/posts/
```

Antwort `201`: das Post-Detail (gleiches Format wie GET unten).

### GET /api/v1/posts/&lt;uuid&gt;/

Status-Abfrage eines Posts.

```bash
curl -H "Authorization: Bearer $API_KEY" \
  https://social.orbita-media.de/api/v1/posts/<post-uuid>/
```

Antwort `200`:

```json
{
  "id": "…",
  "status": "scheduled",
  "title": "",
  "caption": "Neues Buch ist da!",
  "first_comment": "",
  "tags": [],
  "scheduled_at": "2026-07-01T07:00:00+00:00",
  "published_at": null,
  "created_at": "2026-06-12T18:00:00+00:00",
  "platform_posts": [
    {
      "id": "…",
      "social_account_id": "…",
      "platform": "instagram",
      "account_name": "Orbita Media",
      "status": "scheduled",
      "scheduled_at": "2026-07-01T07:00:00+00:00",
      "published_at": null,
      "platform_post_id": "",
      "publish_error": ""
    }
  ],
  "media": [
    {
      "media_asset_id": "…",
      "position": 0,
      "alt_text": "",
      "media_type": "image",
      "filename": "cover.jpg"
    }
  ]
}
```

`status` auf Post-Ebene ist aggregiert über alle PlatformPosts
(`draft`, `scheduled`, `publishing`, `published`, `partially_published`,
`failed`, …). Pro PlatformPost stehen `publish_error` und
`platform_post_id` (die Post-ID auf der Plattform nach dem Publishing).

## Fehlerformate

- `400` – Validierungsfehler, Body enthält Feld-Fehler als JSON
- `401` – fehlender/ungültiger API-Key
- `404` – Post nicht gefunden (oder gehört zu anderem Workspace)
- `429` – Rate-Limit überschritten (120/min pro Key)
