# Plattform-Grenzen: was die Kanäle wirklich können

**05.08.2026** – Erhoben aus den Herstellerdokumentationen, nicht aus unserem Code.

## Warum es diese Datei gibt

Die vorherige Übersicht (`docs/BLUESKY-ANTWORT-KETTE.md`, 01.08.2026) hat die
Grenzen aus dem abgeleitet, was unser Fork gerade tut. Dabei sind zwei Zahlen
entstanden, die es so nie gab:

- „LinkedIn: 1 Bild" – LinkedIn nimmt **2 bis 20** Bilder je Beitrag und
  ausserdem **Dokumente** (PDF-Karussell, bis 300 Seiten).
- „Pinterest: 1 Bild" – Pinterest kennt **Karussell-Pins mit 2 bis 5** Bildern.

Dazu kam eine Zahl mit einem erfundenen Beleg: der Kommentar an
`providers/google_business.py` zitierte „only one media item is supported" aus
der localPosts-Referenz. Dieser Satz steht dort nicht.

Deshalb trennt diese Datei zwei Dinge, die vorher vermischt waren:

| Spalte | Bedeutung |
|---|---|
| **Plattform** | Was die Schnittstelle des Anbieters laut seiner eigenen Doku hergibt |
| **Fork** | Was unser Code daraus tatsächlich nutzt |

Wo beide auseinanderfallen, steht in der letzten Spalte, ob es nachgezogen
wurde. „Nicht in der Doku gefunden" heisst genau das – nicht „gibt es nicht"
und erst recht nicht „ist unbegrenzt".

## Die Kurzfassung

| Kanal | Bilder je Beitrag (Plattform) | Fork nutzt | Stand |
|---|---|---|---|
| Bluesky | **10** (`app.bsky.embed.gallery`, Soft-Limit; Schema 20). Das alte `app.bsky.embed.images` bleibt bei 4 | 10 | nachgezogen 05.08.2026 |
| Threads | 20 | 20 | deckungsgleich |
| Instagram | 10 | 10 | deckungsgleich |
| LinkedIn | **20** (`content.multiImage`), zusätzlich Dokument-Beitrag | 20 | nachgezogen 05.08.2026 |
| TikTok | **35** (Photo Post) | 35 | nachgezogen 05.08.2026 |
| Facebook | nicht dokumentiert (`attached_media` ist undokumentiert) | 10 | siehe unten, bewusst konservativ |
| Pinterest | **5** (Karussell-Pin, min. 2) | 5 | nachgezogen 05.08.2026 |
| Mastodon | 4 als Default, je Instanz abfragbar | 4 + Abfrage | deckungsgleich |
| Google Business | keine Zahl dokumentiert (`media` ist ein Array) | 1 | bewusst konservativ, siehe unten |
| YouTube | Bild-Beiträge über die API **nicht möglich** | 1 Video | Plattform-Grenze |
| dev.to | 1 Titelbild | 1 | deckungsgleich |
| X | Medien nur im Bezahltarif | 0 | bewusst aus |

---

## Bluesky

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Beitrag | 4 über `app.bsky.embed.images`, **10 über `app.bsky.embed.gallery`** (Schema-Decke 20, Soft-Limit 10) | [lexicons/app/bsky/embed/images.json](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/images.json) · [gallery.json](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/gallery.json) |
| Mehrbild-Format | `embed` im Record `app.bsky.feed.post`; kein Container-/Publish-Zweischritt. Blobs über `com.atproto.repo.uploadBlob`, dann `com.atproto.repo.createRecord` | [docs.bsky.app/docs/advanced-guides/posts](https://docs.bsky.app/docs/advanced-guides/posts) |
| Wird `gallery` ausgeliefert? | Ja. `app.bsky.feed.defs#postView` führt `app.bsky.embed.gallery#view` in seiner embed-Union | [lexicons/app/bsky/feed/defs.json](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/feed/defs.json) |
| Seit wann? | Lexicon-Commit `41a561e` vom 03.06.2026, „[APP-1983] New gallery embed type (#4827)"; im veröffentlichten SDK `@atproto/api` 0.20.37 enthalten (`dist/client/types/app/bsky/embed/gallery.js`); die offizielle App zeigt ab fünf Bildern ein Wisch-Karussell seit Version 1.123 (09.06.2026) | GitHub-Commit-API · npm-Paket · [gigazine.net zur App 1.123](https://gigazine.net/gsc_news/en/20260610-bluesky-photo-carousel/) |
| Dokument-Beiträge | **Nein.** Die embed-Union kennt nur images, video, gallery, external, record, recordWithMedia | post.json |
| Bildgrösse | max. **2.000.000 Bytes**, `accept: ["image/*"]`. Die Prosa-Doku nennt noch 1 MB, das Lexicon ist massgeblich | images.json: `"May be up to 2 MB, formerly limited to 1 MB"` |
| Bildmasse | nicht in der Doku gefunden. `aspectRatio` ist ein Render-Hinweis, keine Vorgabe | – |
| Videos | 1 je Beitrag, nur MP4, max. 100.000.000 Bytes. Video und Bilder schliessen sich aus | video.json |
| Text | **300 Graphemes** und gleichzeitig 3000 Bytes | post.json: `"maxLength": 3000, "maxGraphemes": 300` |

**Wichtig für die Umsetzung:** `app.bsky.embed.gallery#image` verlangt
`aspectRatio` als **Pflichtfeld** (bei `images#image` ist es optional). Ohne die
echten Pixelmasse lässt sich kein Galerie-Beitrag bauen; der Provider misst sie
deshalb vor dem ersten Upload mit Pillow und bricht ab, wenn eine Datei nicht
lesbar ist.

**Fork-Stand:** Bis 05.08.2026 kannte der Provider nur das Vier-Bilder-Embed und
hängte den Rest als Antwort an den eigenen Beitrag. Beides ist weg. Bis vier
Folien bleibt `app.bsky.embed.images` (jeder Client versteht es seit Jahren), ab
fünf wird `app.bsky.embed.gallery` geschrieben. Ein Sechs-Folien-Karussell passt
damit in **einen** Beitrag.

---

## Threads

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Beitrag | **20** (Bilder, Videos oder gemischt), Minimum 2 Kinder | [developers.facebook.com/docs/threads/posts](https://developers.facebook.com/docs/threads/posts): *"Carousels are limited to 20 images, videos, or a mix of the two"* |
| Mehrbild-Format | `media_type=CAROUSEL`, Dreischritt: Item-Container mit `is_carousel_item=true` → Eltern-Container mit `children` → `POST /{threads-user-id}/threads_publish` | [Threads Publishing Reference](https://developers.facebook.com/docs/threads/reference/publishing/) |
| Dokument-Beiträge | Nein. Nur TEXT, IMAGE, VIDEO, CAROUSEL | Publishing Reference |
| Bild | nur **JPEG und PNG**, max. 8 MB, Breite 320–1440 px, Seitenverhältnis bis 10:1. Übergabe per öffentlicher `image_url`, kein Binär-Upload | docs/threads/posts |
| Text | 500 Zeichen, Emojis zählen nach UTF-8-Bytes | *"For the post character limit, emojis are counted as the number of UTF-8 bytes."* |
| Alternativtext | max. 1.000 Zeichen | *"The maximum length of `alt_text` is 1,000 characters"* |

**Fork-Stand:** deckungsgleich (20).

---

## Instagram

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Beitrag | **10** | [Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing): *"Carousels are limited to 10 images, videos, or a mix of the two."* |
| Mehrbild-Format | `media_type=CAROUSEL`, Dreischritt wie Threads (`is_carousel_item=true` → `children` → `media_publish`) | Content Publishing |
| Dokument-Beiträge | Nein | [IG User Media](https://developers.facebook.com/docs/instagram-platform/reference/instagram-user/media/) |
| Bild | **nur JPEG** – PNG und WebP ausdrücklich nicht. Max. 8 MB, Seitenverhältnis 4:5 bis 1.91:1, Breite 320–1440 px, sRGB | *"JPEG is the only image format supported."* · *"Must be within a 4:5 to 1.91:1 range"* |
| Text | 2.200 Zeichen | IG User Media |
| Kontingent | 100 API-Beiträge je 24 h, ein Karussell zählt als ein Beitrag | *"Carousels count as a single post."* |

**Layout-Falle:** Instagram beschneidet alle Karussell-Bilder auf das
Seitenverhältnis des **ersten**. Die erste Folie bestimmt das Format der ganzen
Strecke.

**Fork-Stand:** deckungsgleich (10).

---

## LinkedIn

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Beitrag | **2 bis 20** | [MultiImage Post API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/multiimage-post-api): *"A MultiImage post is a post containing multiple images (minimum of 2 images and maximum of 20 images)."* |
| Mehrbild-Format | `content.multiImage.images` auf `POST /rest/posts`; je Bild `{"id": "urn:li:image:...", "altText": "..."}`. Upload je Bild über `POST /rest/images?action=initializeUpload` → PUT auf `uploadUrl` → URN | MultiImage Post API |
| Einschränkung | *"API partners can only create non-sponsored multiImage posts."* Organisch ja, gesponsert nein. Umgekehrt ist das echte „Carousel"-Format nur gesponsert: *"Organic carousel is currently not supported"* – für uns also **multiImage**, nicht carousel | [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api) |
| Dokument-Beiträge | **Ja.** `POST /rest/documents?action=initializeUpload` → PUT → `content.media` mit Dokument-URN. *"The file size can't exceed 100MB and 300 pages. The following file types are supported: PPT, PPTX, DOC, DOCX, and PDF."* | [Documents API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/documents-api) |
| Dokument-Besonderheiten | `title` ist Pflicht (*"Required field for Documents API"*). `SYNCHRONOUS_UPLOAD` gibt es nicht – vor dem Posten `GET /rest/documents/{urn}` bis `status: AVAILABLE` (Zustände WAITING_UPLOAD → PROCESSING → AVAILABLE / PROCESSING_FAILED) | Documents API |
| Profil und Seite | Beides über dieselbe API, `author` ist `urn:li:person:{id}` oder `urn:li:organization:{id}`. Für Dokumente ausdrücklich auch persönlich: *"For documents with person URN owners, the caller must match the document owner."* Ein Funktionsunterschied bei multiImage oder Documents ist nicht dokumentiert | Posts API · Documents API |
| Scopes | `w_member_social` (Person), `w_organization_social` (Organisation, dazu Seitenrolle ADMINISTRATOR / DIRECT_SPONSORED_CONTENT_POSTER / CONTENT_ADMIN) | Posts API |
| Bild | *"Images with less than 36,152,320 pixels. JPG, GIF, and PNG formats. GIF format supports up to 250 frames"*. Dateigrösse in MB und Seitenverhältnis: nicht in der Doku gefunden | [Images API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api) |
| Alternativtext | max. 4.086 Zeichen, empfohlen unter 120 | MultiImage Post API |
| Text | **nicht in der Doku gefunden.** Belegt ist nur der Fehler `400 FIELD_LENGTH_TOO_LONG` | – |
| `ugcPosts` vs. `/rest/posts` | *"The Posts API replaces the ugcPosts API."* multiImage und Documents sind ausschliesslich unter `/rest/posts` dokumentiert | Posts API |

**Eigene Falle, unabhängig von der Bilderzahl:** `commentary` ist kein Klartext,
sondern LinkedIns „little"-Format. *"All reserved characters need to be escaped
with a backslash, even if those characters are not used in one of the supported
elements or templates."* Zu maskieren sind `| { } @ [ ] ( ) < > # \ * _ ~`.
Ohne Maskierung zerlegt LinkedIn Klammern, Sternchen und Unterstriche aus
normalem Fliesstext.

**Fork-Stand:** Der Provider konnte bis 05.08.2026 genau ein Bild und
protokollierte den Rest weg. Nachgezogen wurden Mehrbild-Beiträge (bis 20) und
Dokument-Beiträge, dazu die Maskierung des Beitragstextes.

---

## TikTok

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Beitrag | **35** | [Photo Post Reference](https://developers.tiktok.com/doc/content-posting-api-reference-photo-post): *"An array containing up to 35 photo content URLs. The URLs must be publicly accessible."* |
| Format | `POST /v2/post/publish/content/init/` mit `media_type: PHOTO`, `post_mode: DIRECT_POST` oder `MEDIA_UPLOAD`, `photo_images` als URL-Liste, `photo_cover_index` für die Titelfolie | Photo Post Reference |
| Einschränkung | `source` nur `PULL_FROM_URL` – für Fotos gibt es **keinen** Datei-Upload. Die Bilder müssen öffentlich erreichbar sein | Photo Post Reference |
| Dokument-Beiträge | Nein | – |
| Bild | nur **WebP und JPEG**, max. 20 MB je Bild, max. 1080p. Seitenverhältnis nicht gefunden | [Media Transfer Guide](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide) |
| Text | Titel 90, Beschreibung 4.000 UTF-16-Einheiten | Photo Post Reference |
| Kontingent | 6 Anfragen je Minute und Zugriffstoken; bei `MEDIA_UPLOAD` zusätzlich höchstens 5 offene Freigaben je 24 h. Ungeprüfte Apps posten nur auf private Konten | Photo Post Reference |

**Fork-Stand:** Der Provider kannte nur Videos. Foto-Beiträge sind seit
05.08.2026 eingebaut; die Bilder gehen als öffentliche URLs raus, weil die API
für Fotos nichts anderes annimmt.

**Was vor dem ersten Foto-Beitrag zu tun ist:** Videos umgeht der Provider die
Domain-Verifikation, indem er `FILE_UPLOAD` nutzt. Für Fotos gibt es diesen Weg
nicht – `source` darf nur `PULL_FROM_URL` sein. Die Domain, die unsere Bilder
ausliefert, muss deshalb im TikTok-Entwicklerportal als *URL prefix* verifiziert
sein, sonst antwortet der Init-Aufruf mit `url_ownership_unverified`. Dieser
Code steht bereits in `PERMANENT_PUBLISH_ERROR_CODES`, der Beitrag scheitert
also sofort und läuft nicht in die Wiederholungsschleife.

---

## Facebook (Seiten)

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Beitrag | **nicht in der Doku gefunden.** Die POST-Parameterliste von `/{page-id}/feed` (actions, link, backdated_time, child_attachments, feed_targeting, message, multi_share_end_card, multi_share_optimized, object_attachment, place, published, scheduled_publish_time, tags, targeting, call_to_action, thumbnail) enthält `attached_media` überhaupt nicht | [Page Feed](https://developers.facebook.com/docs/graph-api/reference/page/feed) |
| Mehrbild-Weg | `attached_media[n]={"media_fbid":"..."}` nach `POST /{page-id}/photos` mit `published=false`. Auf Metas Domain nur im Entwicklerforum belegt, nicht in der Referenz | [Community-Thread](https://developers.facebook.com/community/threads/704907595313769/) |
| Bekannter Defekt | Im selben Thread melden mehrere Entwickler seit Oktober 2025 `OAuthException code 1` – aber nur bei **geplanten** Beiträgen; sofort veröffentlichte Mehrbild-Beiträge laufen. Keine Antwort von Meta | Community-Thread |
| Karussell | Kein Bild-Karussell. Nur ein Link-Karussell über `child_attachments`: *"Minimum 2 and maximum of 5 objects. If you set `multi_share_optimized` to true, you can upload a maximum of 10 objects but Facebook will display the top 5."* | Page Feed |
| Album-Weg | scheidet aus: auf `page/albums` sind Create/Read/Update/Delete nicht ausführbar | [Page Albums](https://developers.facebook.com/docs/graph-api/reference/page/albums/) |
| Bild | jpeg, bmp, png, gif, tiff; *"Files can not exceed 10MB. For .png files, we recommend not exceeding 1MB"* | [Page Photos](https://developers.facebook.com/docs/graph-api/reference/page/photos/) |
| Text | nicht in der Doku gefunden | – |

**Bewertung:** Die im Fork stehende 10 ist **nicht belegbar** – sie stammt aus
Drittquellen. Sie bleibt trotzdem stehen, weil sie den Fehler in die sichere
Richtung macht: unsere Karussells haben sechs Folien, liegen also darunter, und
eine erfundene höhere Zahl würde Beiträge durchlassen, die die API dann
ablehnt. Die Zahl ist im Code als unbelegt gekennzeichnet.

---

## Pinterest

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Karussell-Pin | **2 bis 5** | [Pinterest OpenAPI 5.28.0](https://github.com/pinterest/api-description/blob/main/v5/openapi.yaml), `PinMediaSourceImagesURL`: `maxItems: 5`, `minItems: 2` |
| Format | `POST /v5/pins` mit `media_source.source_type = "multiple_image_urls"` (oder `multiple_image_base64`), Feld `items` mit je `url` (Pflicht) sowie optional `title`, `description`, `link`; `index` bestimmt die Titelkarte | openapi.yaml |
| Alle `source_type`-Werte | `image_url`, `image_base64`, `multiple_image_urls`, `multiple_image_base64`, `video_id`, `pin_url` (letzteres *"only available to a list of beta users"*) | openapi.yaml |
| Bildformate | Enum `ContentType` kennt genau **image/jpeg und image/png**. Kein WebP, kein GIF | openapi.yaml |
| Bildmasse/Dateigrösse | nicht in der Doku gefunden. Die Pixelgrenzen in der Spec (max. 89.478.485 px, > 75 px Kantenlänge) sind ausdrücklich als *"Ad images"* gekennzeichnet und gelten nicht für organische Pins | openapi.yaml |
| Text | `title` 100, `description` 800, `alt_text` 500, `link` 2048 | `PinCreate`-Schema |
| Zugriffsstufe | Der Knackpunkt ist nicht der Kontotyp: *"all Pins and Boards created with Trial access are only visible to their creator as Sandbox entities."* Für sichtbare Pins ist **Standard access** nötig | Pinterest-Doku |
| Widerspruch | Die Anleitungsseite schreibt *"We've recently simplified our organic Pin formats to image or video Pins"* und erwähnt Karussell nicht. Die aktuelle OpenAPI definiert `multiple_image_urls` vollständig und **ohne** `deprecated`-Vermerk | Anleitungsseite vs. openapi.yaml |

**Fork-Stand:** Karussell-Pins sind seit 05.08.2026 eingebaut. Wegen des
Widerspruchs mit einem Rückfall: lehnt die API den Karussell-Pin mit HTTP 400
ab, wird derselbe Pin als Einzelbild-Pin mit der ersten Folie erneut versucht
und das im Ergebnis vermerkt.

---

## Mastodon

| Frage | Antwort | Quelle |
|---|---|---|
| Bilder je Beitrag | `configuration.statuses.max_media_attachments`, Doku-Beispiel **4**. Instanzabhängig, zur Laufzeit über `GET /api/v2/instance` abfragbar | [Instance-Entity](https://docs.joinmastodon.org/entities/Instance/): *"The maximum number of media attachments that can be added to a status"* |
| Format | Kein Karussell-Objekt. Zweischritt: `POST /api/v2/media` je Datei → ID, dann `POST /api/v1/statuses` mit `media_ids[]` | [Media](https://docs.joinmastodon.org/methods/media/) · [Statuses](https://docs.joinmastodon.org/methods/statuses/) |
| Dokument-Beiträge | Nein. Typ-Enum: unknown, image, gifv, video, audio | [MediaAttachment](https://docs.joinmastodon.org/entities/MediaAttachment/) |
| Bild | `image_size_limit` 16.777.216 Bytes, `image_matrix_limit` 33.177.600 px (entspricht 7680x4320). JPEG, PNG, GIF, HEIC, HEIF, WebP, AVIF. Kein Mindestmass, kein Seitenverhältnis vorgegeben | Instance-Entity |
| Text | `configuration.statuses.max_characters`, Default 500 | Instance-Entity |

**Fork-Stand:** deckungsgleich – der Provider fragt die echte Instanz-Grenze ab,
bevor er hochlädt, und bricht darüber ab.

---

## Google Business Profile

| Frage | Antwort | Quelle |
|---|---|---|
| Medien je `localPost` | **keine Zahl dokumentiert.** `media` ist ein Array (`"media": [ { object (MediaItem) } ]`) | [localPosts](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts) |
| Der frühere Beleg | Der Satz *"only one media item is supported"* steht **nicht** in der Referenz. Dokumentiert ist nur: *"The media associated with the post. sourceUrl is the only supported data field for a LocalPost MediaItem."* Die „nur eins"-Regeln der Doku betreffen Standortmedien (Titel- und Profilbild), nicht Beiträge | localPosts |
| Upload-Weg | *"To include media in a local post, you must upload it from a URL"* – nur `sourceUrl`, kein Byte-Upload | [Create Posts](https://developers.google.com/my-business/content/posts-data) |
| Bild | *"all photos must measure a minimum of 250px on the short edge, with a file size of at least 10240 bytes."* Maximale Grösse, maximale Auflösung und erlaubte Formate: nicht gefunden | [Upload media](https://developers.google.com/my-business/content/upload-photos) |
| Zugang | Antrag nötig; Freigabe erkennt man am Kontingent: 0 QPM = nicht freigegeben, 300 QPM = freigegeben | [Prerequisites](https://developers.google.com/my-business/content/prereqs) |
| Text | Zeichenobergrenze von `summary` nicht gefunden | localPosts |

**Bewertung:** Strukturell wäre Mehrbild möglich, aber es gibt keine
dokumentierte Zusage – weder eine Obergrenze noch eine Bestätigung, dass mehr
als ein Eintrag angezeigt wird. Der Fork bleibt deshalb bei **1** und sagt das
jetzt ehrlich („nicht dokumentiert, konservativ gewählt") statt ein Zitat zu
erfinden. Wer das ändern will, braucht einen echten Testlauf gegen ein
freigegebenes Konto, nicht eine weitere Vermutung.

---

## YouTube

| Frage | Antwort | Quelle |
|---|---|---|
| Bild-Beiträge | **Nicht möglich.** Die Ressourcenliste der Data API v3 enthält keine Ressource für Community- oder Kanal-Beiträge | [API Reference](https://developers.google.com/youtube/v3/docs) |
| Der frühere Weg | abgeschaltet: *"The channel bulletin feature has now been fully deprecated."* (04.06.2020, angekündigt 17.04.2020). `activities` kann heute nur noch `list` | [Revision History](https://developers.google.com/youtube/v3/revision_history) |
| Was mit Bildern geht | `thumbnails.set` (2 MB), `channelBanners.insert` (6 MB, empfohlen 2560x1440), `playlistImages.insert` (2 MB), `watermarks.set` (10 MB) – alles jpeg/png | API Reference |
| Text | Titel 100 Zeichen, Beschreibung 5.000 **Bytes** (bei Umlauten also weniger Zeichen), Tags zusammen 500 Zeichen | [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert) |

**Fork-Stand:** 1 Video je Upload – das ist die Plattform-Grenze, nicht unsere.

---

## dev.to und X

| Kanal | Grenze | Quelle |
|---|---|---|
| dev.to | 1 Titelbild je Artikel (`cover_image`) | [Forem API](https://developers.forem.com/api/v1) |
| X | Medien-Upload gehört zum Bezahltarif. Der Provider hat ihn bewusst aus und lässt einen Beitrag mit Anhang scheitern statt ihn ohne Bild zu senden | `providers/x.py`, Fork-Entscheidung |

---

## Was daraus für die Beitragserzeugung folgt

Der gemeinsame Nenner, wenn dasselbe Bild überall durchgehen soll:

- **Format JPEG.** PNG scheidet aus, sobald Instagram im Spiel ist; WebP schon
  bei Threads.
- **Höchstens 2 MB** je Bild – Bluesky ist hier der Engpass.
- **Breite 320 bis 1440 px**, Seitenverhältnis zwischen 4:5 und 1.91:1
  (Instagram ist der Engpass), Farbraum sRGB.
- **Text: 300 Graphemes**, wenn derselbe Text auf allen Kanälen laufen soll –
  Bluesky ist mit Abstand am engsten, Instagram mit 2.200 am weitesten.

Und für die Folienzahl: mit 4 Folien läuft ein Beitrag überall ausser auf den
Ein-Bild-Kanälen. Sechs Folien gehen auf Bluesky, Threads, Instagram, LinkedIn,
TikTok und Facebook. Pinterest braucht eine Fassung mit höchstens fünf,
Google Business eine mit einer einzigen Folie.

Welcher Kanal welche Fassung bekommt, steht im Content-Tool in
`docs/KANALFASSUNGEN.md`.
