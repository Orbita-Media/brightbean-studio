# Restfolien als Antwort-Kette statt stillem Wegfall

**01.08.2026** – Behebt, dass Bluesky von unseren sechs Folien nur vier zeigte
und die Folien 5 und 6 ohne Warnung, ohne Fehler und ohne Protokollzeile
verschwanden.

## Der Befund

`app.bsky.embed.images` fasst höchstens vier Bilder. Der Provider schnitt genau
dort ab:

```python
for index, path in enumerate(media_files[:MAX_EMBED_IMAGES]):
```

Unsere Karussells haben sechs Folien, und die sechste ist die Schluss-Slide,
die zum Buch führt. Der Redaktionsplan sieht zwölf Wochen mit 36
Bluesky-Terminen vor – ohne Lösung hätte jeder einzelne davon seinen Abschluss
verloren. Nichts in der Anwendung hat darauf hingewiesen: kein Fehler, keine
Warnung, kein Eintrag im Veröffentlichungsprotokoll.

## Die Entscheidung

Die Restfolien werden als **Antwort auf den eigenen Beitrag** angehängt.

Auf Bluesky sind Antwort-Ketten übliche Lesart und werden mitgelesen. Die
Alternative „nur vier Folien zeigen" wirft Inhalt weg. Die Alternative „für
Bluesky eine eigene Vier-Folien-Fassung rendern" verdoppelt die Produktion
(sechs Folien werden gebaut, gerendert, mit Alternativtexten versehen und
hochgeladen – das alles ein zweites Mal, für einen Kanal).

## Die Umsetzung

`providers/bluesky.py`

| Schritt | Was passiert |
|---|---|
| 1 | `_build_embeds()` lädt **alle** Blobs hoch und schneidet sie in Gruppen zu vier |
| 2 | Der erste Beitrag trägt Beitragstext und die erste Gruppe |
| 3 | Jede weitere Gruppe wird als Antwort geschrieben, mit `reply.root` **und** `reply.parent` |
| 4 | Zurückgemeldet wird der Wurzelbeitrag; die Kette steht in `extra["thread"]` |

### Warum die Blobs zuerst hochgeladen werden

Der Upload ist der Schritt, der am ehesten scheitert (Grösse, Netz, PDS). Solange
noch kein Beitrag existiert, kostet ein Fehlschlag nichts – der Publisher
wiederholt gefahrlos. Sobald der Wurzelbeitrag steht, würde ein Wiederholen das
komplette Karussell ein zweites Mal posten. Deshalb:

- Fehler **vor** dem ersten Beitrag: Ausnahme, Publisher wiederholt.
- Fehler **nach** dem ersten Beitrag: keine Ausnahme nach aussen. Die Lücke steht
  als `publish_warning` im Ergebnis, landet als `error_message` im
  `PublishLog` und wird protokolliert. Der Beitrag gilt als veröffentlicht,
  weil er es ist.

### Warum die Antwort auf root UND parent zeigen muss

Das Lexicon `app.bsky.feed.post` verlangt in `reply` beide Verweise. Eine
Antwort, die nur den Vorgänger nennt, wird von der AppView nicht in den Faden
einsortiert und hängt im Leeren.

Bei drei Beiträgen heisst das: Beitrag 3 hat `parent` = Beitrag 2 und
`root` = Beitrag 1.

### Alternativtexte

Der Index läuft über die **Original-Anhangsliste** und beginnt je Gruppe NICHT
neu. Ein Neubeginn würde Folie 5 die Beschreibung von Folie 1 geben, und ein
vertauschter Alternativtext ist schlimmer als gar keiner. Lücken bleiben Lücken:
ein Anhang ohne Beschreibung liefert einen leeren String und rückt nichts nach.

### Text der Antwort

Standard: `Fortsetzung – Bild 5 bis 6 von 6` (bei einer einzelnen Restfolie
`Fortsetzung – Bild 5 von 5`). Kurz gehalten, weil das Limit von 300 Zeichen für
jeden Beitrag der Kette gilt, auch für die Antworten. Übersteuerbar je Beitrag
über `extra["thread_continuation_text"]`; die Platzhalter `{start}`, `{end}` und
`{total}` sind optional, das Ergebnis wird auf 300 Zeichen gekürzt.

## Die Bildgrenzen aller Kanäle

Damit dieselbe Falle nicht bei Instagram oder Pinterest wieder zuschlägt, sagt
jetzt jeder Anbieter über `max_media_per_post`, wie viele Anhänge EIN Beitrag
zeigen kann. `chains_overflow_media` markiert die Anbieter, die den Überlauf
verteilen statt ihn fallen zu lassen.

| Kanal | Grenze | Verhalten darüber | Fundstelle |
|---|---|---|---|
| Bluesky | 4 | Kette aus Antworten, nichts geht verloren | `providers/bluesky.py` |
| Facebook | 10 | Abbruch mit Meldung (Album-Weg nicht umgesetzt) | `providers/facebook.py` |
| Instagram | 10 | Abbruch mit Meldung | `providers/instagram.py` |
| Instagram (Login) | 10 | Abbruch mit Meldung | `providers/instagram_login.py` |
| Threads | 20 | Abbruch mit Meldung | `providers/threads.py` |
| Mastodon | 4 (je Instanz) | echte Grenze der Instanz wird abgefragt, dann Abbruch | `providers/mastodon.py` |
| Pinterest | 1 | erstes Bild wird gepinnt, Rest protokolliert | `providers/pinterest.py` |
| LinkedIn | 1 | erstes Bild, Rest protokolliert (Mehrbild nicht umgesetzt) | `providers/linkedin.py` |
| Google Business | 1 | genau ein Medium wird gesendet, Rest protokolliert | `providers/google_business.py` |
| TikTok | 1 | ein Video je Beitrag | `providers/tiktok.py` |
| YouTube | 1 | ein Video je Upload | `providers/youtube.py` |
| dev.to | 1 | erstes Bild als Titelbild | `providers/devto.py` |
| X | 0 | Medien nur im Bezahltarif, Abbruch mit Meldung | `providers/x.py` |

Herkunft der Zahlen: die Herstellerdokumentationen. Bluesky aus dem Lexicon
`app.bsky.embed.images` (`maxLength: 4`), Instagram und Threads aus den
Karussell-Beschreibungen der Graph- und Threads-Schnittstelle, Mastodon aus
`configuration.statuses.max_media_attachments` der Instanz, Google Business aus
dem Hinweis zu `localPosts.media` („only one media item is supported").

Zusätzlich warnt der Publisher **vor** dem Veröffentlichen, wenn ein Kanal nicht
alle Anhänge zeigen kann (`PublishEngine._warn_on_dropped_media`). Anbieter mit
Kette sind ausgenommen – dort geht nichts verloren.

## Tests

| Datei | Umfang |
|---|---|
| `tests/providers/test_bluesky.py` – `TestBuildEmbedsChunking` | Aufteilung 4+2, Alternativtexte über die Gruppengrenze, Lücken ohne Verschiebung, genau vier Bilder, Text ohne Bild, Video |
| `tests/providers/test_bluesky.py` – `TestPublishPostThread` | Zwei Datensätze bei sechs Folien, root/parent bei zwei und drei Beiträgen, Alternativtexte über die Kette, Text der Antwort samt Übersteuerung und Kürzung, Ergebnis mit Wurzelbeitrag und Kette, Reihenfolge Upload vor Beitrag, Fehler in der Antwort, Fehler im Wurzelbeitrag |
| `tests/providers/test_media_limits.py` | Jeder Anbieter deklariert eine Zahl, jede Zahl entspricht der Herstellerangabe, nur Bluesky kettet, Abbrüche bei Instagram/Threads/Mastodon, Protokollzeilen bei Pinterest/Google Business, Warnung des Publishers |

## Nachweis am lebenden Kanal

Siehe Abschnitt „Nachweis" in `docs/NACHWEIS-KETTE-2026-08-01.md`.
