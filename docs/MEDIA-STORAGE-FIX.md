# Medien-Auslieferung reparieren (Upstream-Issue #130)

**Start: 27.07.2026** – Behebung des blockierenden Medien-Bugs in Orbita Social
(`social.orbita-media.de`), bevor die Social-Accounts verbunden werden.
Grundlage: `Social Media Content Tool/docs/POSTIZ-VS-BRIGHTBEAN-RUNDE2.md`,
Abschnitt C8, sowie `docs/UPSTREAM-MERGE-LOG.md`.

Dieses Log wird fortlaufend nach jedem Teilschritt geschrieben (cux-Survival).

## Das Problem in einem Satz

Instagram und Facebook holen sich Bilder und Videos **selbst** von einer
öffentlich erreichbaren Adresse. Unsere Produktionsinstanz hatte für `/media/…`
**keine HTTP-Route** – jede IG-/FB-Veröffentlichung wäre mit einer Meta-Meldung
gescheitert, die nach einem Berechtigungsproblem aussieht.

## Schritt 0 – Befund bestätigt (ERLEDIGT, nichts geändert)

Nachgeprüft im lokalen Repo `C:\Users\nmdar\Nextcloud\TOOLS\BrightBean Studio`
(Stand `861aa3d`, identisch mit dem deployten Stand):

| Fundstelle | Inhalt | Folge |
|---|---|---|
| `config/settings/production.py:4` | `DEBUG = False` | der `static()`-Helfer greift nie |
| `config/urls.py:102-103` | `if settings.DEBUG: urlpatterns += static(MEDIA_URL, …)` | einzige Media-Route hängt an DEBUG |
| `config/settings/base.py:12` | `STORAGE_BACKEND` Standardwert `"local"`, im Compose nicht überschrieben | Dateien landen im Container-Volume |
| `config/settings/base.py:180-182` | WhiteNoise nur als `staticfiles`-Backend | bedient `STATIC_ROOT`, nicht `MEDIA_ROOT` |

**Die Kette bis zum Fehlschlag ist im Code vollständig nachvollziehbar:**

`apps/publisher/engine.py:369-373` baut die URL, die später an Meta geht:

```python
url = asset.file.url
if url.startswith("/"):
    # Local storage: make absolute using APP_URL
    url = f"{app_url}{url}"
media_urls.append(url)
```

Bei lokalem Storage liefert `asset.file.url` also `/media/…`, wird zu
`https://social.orbita-media.de/media/…` aufgeblasen – und genau diese Adresse
gibt es nicht. `providers/instagram.py:296-332` und `providers/facebook.py:280-327`
setzen daraus `image_url` / `video_url` bzw. `url` / `file_url`.
Betroffen sind ausserdem `threads.py`, `google_business.py`, `pinterest.py`,
`devto.py` und der URL-Zweig von `linkedin.py` – alle Provider, die Meta-artig
per URL abholen lassen.

### Bestandsaufnahme auf dem Server (vor der Änderung)

| Punkt | Wert |
|---|---|
| Coolify-App | ID 24, UUID `xos84sccocw488o8kccow88g` |
| Container | `app-`, `worker-`, `maintenance-`, `postgres-…` (alle laufen) |
| `media_library_media_asset` | **0 Zeilen** |
| Volume `/app/media` | nur leere Ordnerhüllen (`media_library/2026/06`, `thumbs/…`), 28 KB |

**Damit ist keine Datenmigration nötig** – es gibt nichts umzuziehen. Der
Umstieg auf ein anderes Storage-Backend ist an dieser Stelle risikofrei.

## Schritt 1 – Lösungsweg gewählt: Cloudflare R2 (ERLEDIGT)

### Unterstützt der Fork S3 sauber? Ja.

| Prüfpunkt | Ergebnis |
|---|---|
| Abhängigkeit | `requirements.txt:19` → `django-storages[s3]>=1.14,<2.0` (zieht `boto3` mit) |
| Settings-Zweig | `config/settings/base.py:186-203` – vollständiger `s3`-Zweig vorhanden |
| Erwartete Variablen | `STORAGE_BACKEND`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_CUSTOM_DOMAIN`, `S3_REGION_NAME` |
| CSP | `base.py:310-318` trägt die Storage-Domain automatisch in `CSP_IMG_SRC`/`CSP_MEDIA_SRC` nach |

Der Weg ist im Fork also vorgesehen und nicht selbst gebaut.

### Warum R2 und nicht „Media-Route unter DEBUG=False"

| Kriterium | R2 (gewählt) | eigene Media-Route |
|---|---|---|
| Auslieferung | Cloudflare-CDN, unabhängig vom App-Container | jede Bildabholung durch Meta läuft durch gunicorn |
| WhiteNoise für Media | – | laut WhiteNoise-Doku ausdrücklich **nicht** vorgesehen (kein Range-Support für Video-Streams, Manifest-Logik) |
| Video an Meta | Range-Requests von Meta werden von R2 nativ bedient | `django.views.static.serve` ist für Produktion ausdrücklich nicht empfohlen |
| Skalierung | mehrere App-Container möglich | Volume muss von allen Containern geteilt werden |
| Upstream-Pflege | vorgesehener Pfad, überlebt jeden Merge | Sonderweg, kollidiert bei jedem Upstream-Merge in `config/urls.py` |
| Aufwand | ENV setzen + drei Korrekturen (siehe Schritt 2) | eine Zeile Code, aber dauerhaft eigener Pfad |

Entscheidung: **R2**. Die Media-Route bliebe ein Sonderweg an genau der Datei,
die der Upstream aktiv pflegt, und würde Metas Video-Abholung über den
Python-Prozess leiten.

### Bereits vorhandene Bausteine

- R2 ist im Haus (Content-Tool nutzt `book-production-covers`, dazu `cap-videos`).
- Cloudflare-Zugang inklusive Account-ID, Zone-ID `orbita-media.de` und Global API
  Key liegt im Vault unter **`Cloudflare API`**.
- **Bewusst ein eigener Bucket** statt Mitbenutzung von `book-production-covers`:
  dort liegen Buchcover und Produktionsdaten; ein Bucket, der für Meta öffentlich
  lesbar sein muss, darf diese Daten nicht enthalten.

Nächster Schritt: Bucket und öffentliche Domain anlegen, Code-Korrekturen, ENV
über die Coolify-API setzen.
