# Alternativtexte je Bild bis zum Kanal durchreichen

**31.07.2026** – Behebt, dass veröffentlichte Bilder keinen Alternativtext
trugen, obwohl in der Datenbank für jedes Bild einer hinterlegt ist.

Unsere Beiträge sind Karussells, die fast ausschliesslich aus Text auf farbigem
Grund bestehen. Ohne Alternativtext ist ihr Inhalt für Menschen mit
Sehbehinderung nicht bloss schlechter zugänglich, sondern gar nicht vorhanden.
Auf Bluesky und Instagram ist der Alternativtext zusätzlich ein Rankingfaktor.

## Der Befund

Zum Zeitpunkt der Analyse in der Produktionsdatenbank:

| Kennzahl | Wert |
|---|---|
| `media_library_mediaasset` gesamt | 144 |
| davon mit `alt_text` | **144** |
| `composer_post_media` gesamt | 144 |
| davon mit `alt_text` | **0** |

Die Beschreibungen liegen also am Medium (`MediaAsset.alt_text`, vom Content-Tool
beim Upload über die Agent-API gesetzt), nicht am Anhang. Der Anhang-eigene
`PostMedia.alt_text` wird von keiner Erzeugungsstelle befüllt – weder
`apps/api/routers/posts.py`, noch `apps/composer/services.py`, noch
`apps/composer/views.py`. Nur das Duplizieren eines Beitrags kopiert ihn mit.

## Wo der Text verlorenging

`apps/publisher/engine.py` baute in `_dispatch_to_provider` die Listen
`media_files` und `media_urls` auf und übergab sie an `PublishContent` – die
Alternativtexte der Anhänge kamen dabei nirgends vor. Das `extra`-Wörterbuch
bestand aus den Tags und den kanalspezifischen Zusätzen:

```python
extra = {"tags": platform_post.post.tags or []}
platform_extra = platform_post.platform_extra or {}
extra.update(platform_extra)
```

`providers/bluesky.py` las genau diesen Schlüssel:

```python
alt_text = content.extra.get("alt_text", "")
images.append({"alt": alt_text, "image": blob_ref})
```

Ergebnis: `""` für jedes Bild. Zweiter, ebenso schwerer Mangel: selbst ein
gefüllter Schlüssel hätte **einen** Text für **alle** Bilder eines Beitrags
getragen. Bei einem Karussell mit sechs verschiedenen Folien ist das wertlos.

Denselben Schlüssel las `providers/pinterest.py`; alle übrigen Anbieter
kannten überhaupt kein Alternativtext-Feld.

## Die Lösung

### Transport

`providers/types.py` – `PublishContent` bekommt eine Liste statt eines
Einzelwerts, positionsgleich zu `media_urls` und `media_files`:

```python
media_alt_texts: list[str] = field(default_factory=list)

def alt_text_for(self, index: int, max_length: int | None = None) -> str: ...
```

`alt_text_for` ist die einzige zugelassene Lesart. Sie liefert für einen Index
ausserhalb der Liste (auch für negative) einen leeren String, fällt auf den
alten `extra["alt_text"]` zurück und kürzt auf das Limit des jeweiligen Kanals.

### Einsammeln

`apps/publisher/engine.py:383` – in derselben Schleife, die Datei und Adresse je
Anhang einsammelt, und in derselben Reihenfolge (`order_by("position")`):

```python
media_alt_texts.append((pm.alt_text or asset.alt_text or "").strip())
```

Rangfolge: Anhang-Übersteuerung, sonst der Text am Medium. Fehlt beides, wird
ein leerer String eingetragen – der Platz bleibt also erhalten. Ein Aufrücken
würde die Beschreibung der dritten Folie auf die zweite schieben, und ein
vertauschter Alternativtext ist schlimmer als gar keiner.

### Kanäle

| Kanal | Feld | Je Bild | Limit | Fundstelle |
|---|---|---|---|---|
| Bluesky | `images[].alt` im Embed | ja | kein Limit im Lexicon, wir kürzen bei 2000 | `providers/bluesky.py:394` |
| Bluesky (Video) | `alt` am Video-Embed | – | 1000 Grapheme | `providers/bluesky.py:380` |
| Instagram | `alt_text` am Bild-Container | ja, je Karussell-Kind | 1000 | `providers/instagram.py:317,346` |
| Instagram (Login) | `alt_text` am Bild-Container | ja, je Karussell-Kind | 1000 | `providers/instagram_login.py:321,341` |
| Threads | `alt_text` am Container | ja, je Karussell-Item | 1000 | `providers/threads.py:257,363` |
| Facebook | `alt_text_custom` je Foto-Upload | ja | nicht dokumentiert, wir kürzen bei 1000 | `providers/facebook.py:317,358` |
| LinkedIn | `altText` am Medien-Objekt | Bild und Video (Einzelmedium) | 4086 | `providers/linkedin.py:304,405` |
| Mastodon | `description` je Anhang | ja | je Instanz, siehe unten | `providers/mastodon.py:255,336` |
| Pinterest | `alt_text` am Pin | **nein** | 500 | `providers/pinterest.py:205` |
| Google Business | – | – | – | kein Feld vorhanden |
| X, TikTok, YouTube, dev.to | – | – | – | kein Bild-Upload bzw. reines Video |

Besonderheiten, die aus den offiziellen Dokumentationen stammen und nicht
geraten sind:

- **Instagram** nimmt `alt_text` ausdrücklich nur für Bilder. Reels, Storys und
  Video-Kinder eines Karussells lehnen es ab, deshalb setzen wir es dort nicht.
- **Threads** dokumentiert `alt_text` für Bild-, Video- und Karussell-Beiträge,
  sagt aber nirgends, wo es beim Karussell hingehört. Wir setzen es am
  Item-Container, weil Metas baugleiche Instagram-Schnittstelle es dort nimmt.
- **Facebook** hält den Text am Foto-Upload fest, auch bei den unveröffentlicht
  gestagten Fotos einer Mehrbild-Meldung. Das spätere `attached_media` trägt
  nur noch IDs und hat gar keinen Platz für eine Beschreibung.
- **Mastodon** kennt kein festes Limit: bis v4.5.x sind es 1500 Zeichen, ab
  v4.6.0 sind es 10000. `get_instance_max_alt_text_length()` liest den Wert aus
  `configuration.media_attachments.description_limit` der Instanz und fällt bei
  jedem Fehler auf 1500 zurück.
- **Pinterest** hat je Pin genau einen Alternativtext; die Multi-Image-Items
  haben schlicht kein Feld dafür. Ein im Composer eingetippter Wert gewinnt,
  sonst steht die Beschreibung des ersten Bildes für den Pin ein.
- **Google Business** unterstützt bei `localPosts` nur `sourceUrl` als
  Datenfeld. Das ist im Code vermerkt, damit die Lücke nicht wie ein
  Versehen aussieht.

### Rückfallebene

Fehlt zu einem Bild der Alternativtext, wird das Feld weggelassen – Bluesky
ausgenommen, wo das Lexicon `alt` als Pflichtfeld führt und deshalb ein leerer
String gesendet wird. In keinem Fall scheitert die Veröffentlichung daran.

## Tests

| Datei | Umfang |
|---|---|
| `apps/publisher/tests.py` – `DispatchAltTextTest` | Reihenfolge, Übersteuerung am Anhang, Lücke ohne Verschiebung, Beitrag ohne Anhänge |
| `tests/providers/test_bluesky.py` – `TestBuildEmbedAltText` | Zuordnung Bild zu Text, Deckelung bei vier Bildern, Kürzung, Video-Embed, alter Einzelschlüssel |
| `tests/providers/test_alt_text.py` | `alt_text_for` sowie Instagram, Threads, Facebook, LinkedIn, Mastodon und Pinterest je mit Positionstreue und Weglassen bei fehlendem Text |

Gesamtlauf nach der Änderung: 1118 Tests grün.

## Nachweis am lebenden Kanal

Veröffentlicht am 31.07.2026 über die Agent-API (Workspace „Orbita Media
Verlag"), Entwurf `48985f76-a129-4ced-b9cc-514ad3c8dd87`:

<https://bsky.app/profile/orbitamedia.bsky.social/post/3mrxyumn7ve2i>

Abruf über die öffentliche Schnittstelle
(`app.bsky.feed.getPostThread`) zeigt `app.bsky.embed.images` mit vier Bildern
und vier **verschiedenen** Alternativtexten in genau der Reihenfolge der
Anhänge. Bild 1 und Bild 4 wurden zusätzlich heruntergeladen und mit ihrem
Alternativtext verglichen: der Text auf der Folie stimmt wörtlich mit der
Beschreibung überein, es gibt keine Verschiebung um eine Position.

## Erledigt (01.08.2026)

Beide hier festgehaltenen offenen Punkte sind behoben:

- **Bluesky zeigt höchstens vier Bilder.** Die Restfolien werden jetzt als
  Antwort auf den eigenen Beitrag angehängt, die Alternativtexte wandern
  positionstreu mit. Ausserdem deklariert jeder Kanal seine Bildgrenze und
  der Publisher warnt vor dem Veröffentlichen, wenn Anhänge nicht sichtbar
  würden. Siehe `docs/BLUESKY-ANTWORT-KETTE.md`.
- **Alternativtexte sind nicht pflegbar.** Der Detailbereich der Mediathek
  hat jetzt ein Feld dafür, direkt über den Schlagwörtern. Der Text hängt am
  Medium, eine Korrektur wirkt deshalb für jeden Beitrag, der das Bild
  benutzt.

Neu hinzugekommen ist eine Prüfung auf Fremdzeichen in Texten und
Alternativtexten (`docs/FREMDZEICHEN-PRUEFUNG.md`) – Anlass war ein
veröffentlichungsreifer Beitragstext mit kyrillischen Buchstaben mitten im
deutschen Wort.
