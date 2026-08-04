# Bluesky-Antwort-Kette – abgelöst am 05.08.2026

> **Diese Lösung gibt es nicht mehr.** Die Restfolien werden nicht als Antwort
> auf den eigenen Beitrag angehängt. Was die Kanäle wirklich können, steht in
> `docs/PLATTFORM-GRENZEN.md`; welcher Kanal welche Fassung bekommt, steht im
> Content-Tool in `docs/KANALFASSUNGEN.md`. Diese Datei bleibt als Chronik
> stehen, damit nachvollziehbar ist, warum die Kette gebaut und warum sie
> wieder ausgebaut wurde.

## Das Problem (galt und gilt)

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

## Was am 01.08.2026 gebaut wurde

Die Restfolien wurden als **Antwort auf den eigenen Beitrag** angehängt:
`_build_embeds()` lud alle Blobs hoch und schnitt sie in Vierergruppen, der
erste Beitrag trug Text und Gruppe eins, jede weitere Gruppe folgte als Antwort
mit `reply.root` und `reply.parent`.

## Warum das wieder raus ist

Zwei Gründe, der erste wiegt schwerer:

1. **Es war die falsche Antwort auf die Frage.** Ein Beitrag muss für sich
   stehen. Eine Fortsetzung in einer Antwort macht aus einer Aussage zwei
   Datensätze, und der zweite wird ausserhalb des Zusammenhangs gelesen oder
   gar nicht. Wenn ein Kanal weniger Folien zeigt, gehört der Inhalt
   **verdichtet**, nicht aufgeteilt – eine eigene, in sich geschlossene Fassung
   je Kanal.
2. **Die Grenze stimmte nicht mehr.** Bluesky hat am 03.06.2026 das Embed
   `app.bsky.embed.gallery` eingeführt (bis 10 Bilder, Schema-Decke 20); die
   offizielle App zeigt sie seit Version 1.123 als Wisch-Karussell. Ein
   Sechs-Folien-Karussell passt damit in **einen** Beitrag. Die Kette löste ein
   Problem, das die Plattform selbst schon gelöst hatte.

Dazu kam ein Erhebungsfehler, der mit derselben Änderung behoben wurde: die
Grenzen der anderen Kanäle waren aus unserem eigenen Code abgeleitet statt aus
den Herstellerdokumentationen. „LinkedIn: 1 Bild" und „Pinterest: 1 Bild" waren
nie Plattform-Grenzen – LinkedIn nimmt 20 Bilder plus Dokument-Beiträge,
Pinterest 5. Die belegte Übersicht steht jetzt in
`docs/PLATTFORM-GRENZEN.md`.

## Was an ihre Stelle getreten ist

| Damals | Heute |
|---|---|
| Über vier Folien: Antwort-Kette | Über vier Folien: `app.bsky.embed.gallery`, ein Beitrag |
| Über der Kanalgrenze: Warnung, Beitrag geht gekürzt raus | Über der Kanalgrenze: Beitrag scheitert mit klarer Meldung |
| `chains_overflow_media` markierte kettende Anbieter | Feld entfernt, kein Anbieter kettet mehr |
| `extra["thread_continuation_text"]` steuerte den Antworttext | ersatzlos entfallen |
| `PublishEngine._warn_on_dropped_media` | `PublishEngine._block_on_dropped_media` |

Der Nachweis am lebenden Kanal vom 01.08.2026
(`docs/NACHWEIS-KETTE-2026-08-01.md`) beschreibt entsprechend einen Zustand,
den es nicht mehr gibt.
