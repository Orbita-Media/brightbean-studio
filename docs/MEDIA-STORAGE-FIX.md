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

## Schritt 2 – Speicher angelegt und geprüft (ERLEDIGT)

### Bucket und öffentliche Domain

| Punkt | Wert |
|---|---|
| Bucket | `orbita-social-media`, Region **WEUR** (Westeuropa, nah am Hetzner) |
| Öffentliche Adresse | **`https://social-cdn.orbita-media.de`** (R2 Custom Domain in der Zone `orbita-media.de`) |
| TLS | `ssl: active`, `ownership: active`, `minTLS: 1.2` (per API bestätigt) |
| Zugangsdaten | eigener, **auf genau diesen Bucket beschränkter** R2-Token `orbita-social-media-rw` (Rechte „Bucket Item Read" + „Bucket Item Write") |

Bewusst eine Custom Domain statt der `pub-….r2.dev`-Adresse: die
Entwicklungs-URL ist laut Cloudflare nicht für Produktion gedacht und wird
gedrosselt. Die Custom Domain liegt hinter dem Cloudflare-Cache, was genau dem
Zugriffsmuster von Meta entgegenkommt (mehrere Abrufe derselben Datei je Post).

Die Domain-Ebene ist mit Absicht flach (`social-cdn.orbita-media.de` statt
`media.social.orbita-media.de`): das Universal-Zertifikat von Cloudflare deckt
nur `*.orbita-media.de` ab, eine weitere Ebene hätte ein kostenpflichtiges
Advanced-Zertifikat gebraucht.

### Am Speicher selbst nachgewiesen (vor jeder Code-Änderung)

| Prüfung | Ergebnis |
|---|---|
| `PUT` mit `ACL=private` (so wie django-storages es standardmässig sendet) | **OK** – R2 ignoriert den ACL-Header, kein Fehler, kein Code-Fix nötig |
| `PUT` ohne ACL | OK |
| `GET https://social-cdn.orbita-media.de/…` **ohne Authentifizierung** | **HTTP 200**, `Content-Type: text/plain`, korrekter Inhalt, `Server: cloudflare` |

Der erste Abruf lief noch in ein **403**, weil die Zertifikatsausstellung
(`ssl: pending`) noch lief. Nach dem Wechsel auf `ssl: active` antwortet die
Domain sauber. Wer den Fehler später wiedersieht: zuerst den SSL-Status der
Custom Domain prüfen, nicht die Zugangsdaten.

### Wie die URL entsteht, die Meta später abruft

Im laufenden Container geprüft (`django-storages 1.14.6`,
`storages/backends/s3.py:668-689`): Ist `AWS_S3_CUSTOM_DOMAIN` gesetzt und kein
CloudFront-Signierer konfiguriert, liefert `url()` eine **einfache, absolute,
unsignierte** Adresse:

```
https://social-cdn.orbita-media.de/media_library/2026/07/datei.jpg
```

Das ist wichtig, weil `AWS_QUERYSTRING_AUTH = True` in `base.py:199` steht.
Ohne Custom Domain wären das presignte URLs mit einer Stunde Gültigkeit – für
geplante Beiträge eine Zeitbombe. **Mit** Custom Domain greift dieser Zweig gar
nicht erst. Deshalb bleibt `base.py` unangetastet.

### Der eine Fehler, der ohne Prüfung durchgerutscht wäre: SES

`config/settings/base.py:192-193` setzt im S3-Zweig die Django-Settings
`AWS_ACCESS_KEY_ID` und `AWS_SECRET_ACCESS_KEY` auf die **R2**-Zugangsdaten.
`django_ses/conf.py:14-28` (im Container nachgelesen) löst seine Zugangsdaten so
auf:

```python
return getattr(django_settings, 'AWS_SES_ACCESS_KEY_ID',
               getattr(django_settings, 'AWS_ACCESS_KEY_ID', None))
```

Bisher lieferte das `None`, weshalb boto3 auf die Umgebungsvariablen mit dem
SES-IAM-Schlüssel zurückfiel. Mit `STORAGE_BACKEND=s3` hätte django-ses
stattdessen den **R2-Token** benutzt – jeder Mailversand (Einladungen,
Passwort-Reset) wäre mit `InvalidClientTokenId` gescheitert, und zwar erst
Tage später und ohne erkennbaren Zusammenhang zur Medien-Umstellung.

Behoben in `config/settings/production.py:73-81`: die SES-Schlüssel werden
ausdrücklich aus der Umgebung gesetzt und damit von den Storage-Schlüsseln
entkoppelt.

### Neue Tests

`tests/test_media_storage_settings.py` (6 Tests) sichert die drei Eigenschaften
ab, auf denen die Reparatur beruht – besonders gegen künftige Upstream-Merges:

1. `STORAGE_BACKEND=s3` aktiviert den S3-Zweig und trägt die Storage-Domain in
   `CSP_IMG_SRC`/`CSP_MEDIA_SRC` nach (ohne die blockiert der Browser die
   Vorschaubilder), `local` bleibt unverändert Dateisystem.
2. Die SES-Schlüssel unterscheiden sich nachweislich von den Storage-Schlüsseln.
3. `S3Storage.url()` liefert mit Custom Domain eine absolute Adresse **ohne**
   Signatur-Parameter.

| Prüfung | Ergebnis |
|---|---|
| Neue Tests | 6 von 6 grün |
| Komplette Testsuite | **1070 Tests, alle grün** (1064 vorher + 6 neue) |

Gelaufen in einem Wegwerf-Container aus dem aktuell deployten Image
(`861aa3d…`) gegen eine eigene Postgres-Instanz – die Produktionsdatenbank
wurde dafür nicht angefasst.

## Schritt 3 – Umgebungsvariablen gesetzt (ERLEDIGT)

Gesetzt über die **Coolify-REST-API** (`POST`/`PATCH
/api/v1/applications/xos84sccocw488o8kccow88g/envs`), nicht in der Datenbank –
Coolify verschlüsselt die Werte, direkte Schreibzugriffe führen beim Build zu
`DecryptException`.

| Variable | Wert |
|---|---|
| `STORAGE_BACKEND` | `local` → **`s3`** (aktualisiert) |
| `S3_ENDPOINT_URL` | `https://<account>.r2.cloudflarestorage.com` |
| `S3_ACCESS_KEY_ID` | Token-ID (32 Zeichen) |
| `S3_SECRET_ACCESS_KEY` | SHA-256 des Token-Werts (64 Zeichen) |
| `S3_BUCKET_NAME` | `orbita-social-media` |
| `S3_CUSTOM_DOMAIN` | `social-cdn.orbita-media.de` |
| `S3_REGION_NAME` | `auto` |

Danach über die API gegengelesen: alle sieben Variablen stehen mit
`is_preview: false` an der Anwendung, `STORAGE_BACKEND` steht auf `s3`.

Die Zugangsdaten liegen im Vault unter
**`Orbita Social R2 (orbita-social-media)`** – im Repo steht kein Geheimnis.

## Schritt 4 – Deploy (ERLEDIGT)

| Punkt | Wert |
|---|---|
| Auslöser | `git push origin main` (`861aa3d..57ab862`), Webhook wie bei allen anderen Apps |
| Commits | `91f3063` (Doku), `d569899` (SES-Entkopplung + Tests), `57ab862` (Doku) |
| Status | **finished** nach 151 s – mit dem Deploy-Watcher verfolgt, nicht geraten |
| Container | neu gestartet, `SOURCE_COMMIT=57ab862f…` |
| Variablen im Container | `STORAGE_BACKEND=s3`, `S3_BUCKET_NAME=orbita-social-media`, `S3_CUSTOM_DOMAIN=social-cdn.orbita-media.de`, `S3_REGION_NAME=auto`, Schlüssel mit 32 bzw. 64 Zeichen |

## Schritt 5 – Verifikation (ERLEDIGT)

### 5a – Im laufenden Produktions-Container

```
STORAGE_BACKEND      : s3
default_storage      : django.core.files.storage.DefaultStorage
AWS_STORAGE_BUCKET   : orbita-social-media
AWS_S3_CUSTOM_DOMAIN : social-cdn.orbita-media.de
EMAIL_BACKEND        : django_ses.SESBackend
SES-Key != S3-Key    : True
GESPEICHERT          : E2E-MEDIA-CHECK/probe.jpg
URL                  : https://social-cdn.orbita-media.de/E2E-MEDIA-CHECK/probe.jpg
```

Die Zeile `SES-Key != S3-Key: True` belegt, dass die Entkopplung aus Schritt 2
in der Produktion greift – der Mailversand läuft weiter über den SES-Schlüssel.

### 5b – Abruf von aussen, ohne Anmeldung (das, was Meta tut)

```
$ curl -I https://social-cdn.orbita-media.de/E2E-MEDIA-CHECK/probe.jpg
HTTP/1.1 200 OK
Content-Type: image/jpeg
Content-Length: 160
Cache-Control: max-age=86400
Accept-Ranges: bytes
```

| Kriterium | Ergebnis |
|---|---|
| HTTP-Status | **200** |
| Content-Type | **`image/jpeg`** – aus der Dateiendung abgeleitet, Meta lehnt falsche Typen ab |
| Authentifizierung | keine nötig, kein Signatur-Parameter in der URL |
| `Accept-Ranges: bytes` | Meta holt Videos in Teilstücken – R2 beantwortet Range-Anfragen nativ |
| URL absolut | ja, `https://…` mit Domain, kein `/media/…` |
