# Nachweis am lebenden Kanal: sechs Folien auf Bluesky

**01.08.2026** – Beleg dafür, dass die Antwort-Kette aus
`docs/BLUESKY-ANTWORT-KETTE.md` in Produktion tut, was sie soll.

## Was veröffentlicht wurde

Genau **ein** Entwurf aus dem Arbeitsbereich „Orbita Media Verlag"
(`42f5b8c3-d7cc-4be3-8289-ad8c2407def7`). Alle übrigen 53 Entwürfe blieben
unberührt – die Freigabe liegt bei Noah.

| | |
|---|---|
| Titel | Wenn es ernst wird, bleib ich Komiker (Kalle Pohl) |
| Entwurf | `8762604b-4f51-4a48-a35c-10f20b3e942c` |
| Format | value-carousel, **sechs** Folien |
| Kanal | Bluesky, `orbitamedia.bsky.social` |
| Veröffentlicht | 2026-07-31 23:27:36 UTC |

Warum dieser: sein Redaktionsplan-Termin (12.10.2026) liegt am weitesten in der
Zukunft, der Inhalt ist unkritisch, und der Entwurf, an dem der Mangel
ursprünglich auffiel (Häkeln, `48985f76`), war bereits am 31.07. mit vier
Folien veröffentlicht und stand deshalb nicht mehr zur Verfügung.

## Der Faden

Wurzelbeitrag:
<https://bsky.app/profile/orbitamedia.bsky.social/post/3mry46ceniq2d>

Abgerufen über die öffentliche Schnittstelle
(`app.bsky.feed.getPostThread`, ohne Anmeldung):

```
--- 3mry46ceniq2d ---                     (Wurzel)
Text: Kalle Pohl hat Krebs, Gewalt und Rückschläge erlebt – …
Bilder: 4 (app.bsky.embed.images#view)
  1. Krebsdiagnose mit 50 – und er ging trotzdem auf Tour …
  2. Die Ärzte sagten schonen – er spielte weiter …
  3. Seine Kindheit war alles andere als lustig …
  4. 9 Jahre Millionenpublikum in der grössten Comedy-Show Europas …

  --- 3mry46ddbzd2m ---                   (Antwort)
  Text: Fortsetzung – Bild 5 bis 6 von 6
  reply.root  : 3mry46ceniq2d
  reply.parent: 3mry46ceniq2d
  Bilder: 2 (app.bsky.embed.images#view)
    5. Humor ist keine Flucht – er ist eine Entscheidung …
    6. Die ganze Geschichte steht im Buch. Wenn es ernst wird, bleib ich
       Komiker – von Kalle Pohl. Link in Bio.
```

Damit ist belegt:

- **Alle sechs Folien sind erreichbar.** Vor der Änderung wären 5 und 6
  weggefallen, darunter die Schluss-Slide mit Buchcover und
  Handlungsaufforderung.
- **Die Reihenfolge stimmt.** Bild 1 bis 4 im Wurzelbeitrag, 5 und 6 in der
  Antwort, jeweils in Anhangsreihenfolge.
- **Die Antwort hängt korrekt im Faden.** `reply.root` und `reply.parent`
  zeigen beide auf den Wurzelbeitrag, weil die Kette hier nur zwei Beiträge
  lang ist.

## Alternativtexte, Zeichen für Zeichen

Gegenprobe: die sechs Alternativtexte aus der Mediathek gegen die sechs
Alternativtexte, die die öffentliche Schnittstelle ausliefert – in
Anhangsreihenfolge, ohne Kürzung.

| Folie | Datei | Vergleich | Länge |
|---|---|---|---|
| 1 | 9783989353237-karussell-01.jpg | identisch | 127 |
| 2 | 9783989353237-karussell-02.jpg | identisch | 126 |
| 3 | 9783989353237-karussell-03.jpg | identisch | 125 |
| 4 | 9783989353237-karussell-04.jpg | identisch | 143 |
| 5 | 9783989353237-karussell-05.jpg | identisch | 136 |
| 6 | 9783989353237-karussell-06.jpg | identisch | 104 |

Keine Verschiebung an der Gruppengrenze: Folie 5 trägt die Beschreibung von
Folie 5, nicht die von Folie 1.

Zusätzlich wurden die Bilddateien der Folien 5 und 6 aus dem Faden geladen und
angesehen. Folie 5 zeigt „Prinzip 04 – Humor ist keine Flucht", Folie 6 das
Buchcover mit „Link in Bio". Es sind also wirklich die Restfolien und keine
Wiederholung der ersten vier.

## Wie es nachvollzogen werden kann

```bash
curl -s "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread\
?uri=at%3A%2F%2Fdid%3Aplc%3Afixrfy26qytuogcdpzkokbgu%2Fapp.bsky.feed.post%2F3mry46ceniq2d&depth=10"
```

## Deploy

Commit `4d2148f` auf `main`, Coolify-Anwendung `xos84sccocw488o8kccow88g`,
Deploy nach 91 Sekunden `finished`. Erst danach wurde veröffentlicht.
