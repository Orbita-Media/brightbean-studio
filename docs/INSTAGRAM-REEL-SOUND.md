# Plattform-Sounds für Instagram-Reels

**05.08.2026** – Instagram ist der einzige unserer Kanäle, bei dem sich ein
Titel aus der Plattform-Bibliothek über die Schnittstelle zuweisen lässt. Diese
Datei hält fest, wie das im Verteiler umgesetzt ist und warum es an genau
diesen Stellen sitzt.

Belege zur Schnittstelle selbst stehen im Content-Tool
(`docs/research/TRENDING-SOUNDS-API.md`, zweifach unabhängig geprüft) und in
Kurzform in `docs/MUSIK-UND-TON.md` Abschnitt 2.

## Die Ausgangslage in einem Absatz

Wer nur die Media-Referenz von `POST /{ig-user-id}/media` liest, kommt zum
falschen Schluss: dort stehen 21 Parameter und kein einziger wählt Musik aus.
Das einzige tonbezogene Feld heisst `audio_name` und **benennt** nur den
Originalton, der ohnehin im hochgeladenen Video steckt („You can only rename
once"). Der Weg, der funktioniert, steht auf einer eigenen Doku-Seite: Meta hat
am 01.06.2026 die *Instagram Audio API* für Apps mit Facebook-Login geöffnet.

- `GET /ig_audio?audio_type=music` – **ohne** `search_query` liefert Meta
  ausdrücklich die angesagten Titel („if no search query is provided, trending
  audio is returned"), mit Suchbegriff das Suchergebnis.
- Beim Anlegen des Reel-Containers:
  `audio_configuration={"audio_id": …, "audio_volume": …, "video_volume": …}`.

`video_volume` ist der praktisch wichtigste Punkt: Der Originalton des Videos
bleibt mischbar, die eigene Sprecherstimme also hörbar. Es ist keine
Entweder-oder-Entscheidung.

## Wo es im Code sitzt

| Schicht | Datei | Was dort passiert |
|---|---|---|
| Provider | `providers/instagram.py` | `list_audio()`, `get_audio()`, `build_audio_configuration()`; der REELS-Container trägt `audio_configuration` als JSON-String |
| Composer | `apps/composer/views.py`, `templates/composer/compose.html` | Panel „Reel Sound", schreibt nach `PlatformPost.platform_extra` |
| Agent-API | `apps/api/schemas.py`, `apps/api/routers/posts.py`, `apps/api/routers/accounts.py` | `platform_overrides[].instagram_audio` und `GET /accounts/{id}/instagram-audio` |
| Publisher | unverändert | `platform_extra` fliesst wie bisher in `PublishContent.extra` |

Der letzte Punkt ist Absicht: Der Sound bekommt **keinen** eigenen Mechanismus.
Er nimmt exakt den Weg, den TikToks `privacy_level`, Pinterests `board_id` und
YouTubes `privacy_status` schon nehmen – `PlatformPost.platform_extra` →
`extra` → Provider. Ein zweiter Kanal daneben wäre die Stelle gewesen, an der
später etwas auseinanderläuft.

## Die Voreinstellungen der Mischung, und warum

Meta setzt beide Regler ohne Angabe auf 100. Für uns wäre das falsch: Unsere
Reels haben eine Sprecherin, und ein Musikbett unter Sprache gehört rund 12 LU
darunter (`docs/MUSIK-UND-TON.md` Abschnitt 5). Die Werte verhalten sich wie
ein linearer Prozentwert der Amplitude, und 10^(−12/20) = 0,251.

| Voreinstellung | `audio_volume` | `video_volume` | Wofür |
|---|---|---|---|
| **Stimme führt** (Standard) | 25 | 100 | Reel mit Sprecherin. Die Datei muss musikfrei sein. |
| **Sound trägt** | 100 | 0 | Reel ohne Sprache. Der Ton der Datei wird stummgeschaltet. |

Beide Regler bleiben frei einstellbar; die Voreinstellungen sind nur der
schnelle Weg zu den zwei Fällen, die es bei uns wirklich gibt.

## Doppelte Musik: die eine Falle

Unsere Reels tragen ein CC0-Musikbett **in der Datei**, weil nur das auf alle
Kanäle mitreist. Legt Instagram zusätzlich einen Plattform-Sound darüber,
laufen zwei Stücke gleichzeitig. Der Verteiler kann den Ton einer hochgeladenen
Datei nicht ändern, also wird nach Fall getrennt:

1. **Reel mit Sprecherin** – die Musik muss aus der Datei heraus. Das
   Content-Tool baut dafür die musikfreie Fassung
   (`src/lib/video-post.ts`, Variante ohne Musikbett): Stimme und Raumton
   bleiben, das Bett entfällt. Der Plattform-Sound ersetzt es.
   `video_volume` bleibt auf 100, sonst verschwindet die Stimme mit.
2. **Reel ohne Sprache** – hier genügt `video_volume: 0`. Das schaltet den
   kompletten Originalton der Datei stumm, also auch ein enthaltenes Musikbett,
   und der Plattform-Sound trägt allein. Kein neuer Rendervorgang nötig.

Der Hinweistext im Composer sagt genau das. Automatisch erkennen kann der
Verteiler es nicht: Ob eine Datei ein Musikbett enthält, weiss nur die Stelle,
die sie gebaut hat.

## Was der Rückfallweg tut

Der Katalog, den Meta Dritten öffnet, ist eine Teilmenge des App-Katalogs
(„the available selection may vary from what appears in the native app") und
er bewegt sich. Fällt ein Titel weg, würde der Container-Aufruf scheitern und
ein fertig produziertes Reel mitnehmen. Deshalb legt
`_create_container_with_audio()` den Container ein zweites Mal ohne
`audio_configuration` an, protokolliert das und vermerkt `audio_dropped` im
Ergebnis. Ein verwaister erster Container wird nie veröffentlicht, der Retry
ist also gefahrlos.

Dieselbe Haltung gilt für die Suche: Ein Ausfall bei Meta ergibt eine leere
Liste mit Begründung, keinen Fehler. Der Sound ist ein Extra, kein Bestandteil
des Beitrags.

## Grenzen, die bleiben

- **Nur mit Facebook-Login.** Eine Anbindung über „Instagram Login" darf
  veröffentlichen, aber keinen Ton wählen. Das entscheidet sich beim Einrichten
  des Kanals, nicht beim Posten. Seit dem 06.08.2026 hat die Orbita-Installation
  Zugangsdaten für **beide** Wege hinterlegt, der Kanal „Instagram (Direct)"
  steht also auf „Connect". Verbunden werden soll trotzdem nur „Instagram" –
  Begründung und Belegstellen im Content-Tool unter
  `docs/KANAL-SETUP-ANLEITUNG.md` Abschnitt 1.9. Der Code setzt das hart um:
  `platform == "instagram"` in `apps/composer/views.py:231`,
  `apps/api/routers/accounts.py:63` und `apps/api/routers/posts.py:189`.
- **Keine Vorschau.** Die Doku sagt es ausdrücklich („Previewing a Reel with
  attached audio is not supported"). Die Mischung muss vorher sitzen.
- **Nur beim Anlegen.** Ton nachträglich zuweisen sieht die Schnittstelle nicht
  vor.
- **Nur Reels.** Auf einem Bild- oder Karussellbeitrag wird eine gesetzte
  `audio_id` verworfen und protokolliert, statt den Beitrag scheitern zu lassen.
- **`should_loop_audio` wird nicht unterstützt.** Das Feld steht nur im
  Code-Beispiel der Doku, nicht in der Feldtabelle, ist damit undokumentiert
  und wird nicht als zugesichert behandelt.
- **Rechtlicher Vorbehalt.** Dass ein Titel „für Drittanbieter-Nutzung
  freigegeben" ist, heisst nicht, dass ein Verlag damit für eigene Bücher werben
  darf. Deshalb ist der Plattform-Sound bewusst optional und je Beitrag zu
  wählen, nicht Regelfall der Automatisierung.
- **MCP bleibt aussen vor.** Der MCP-Kanal kennt `platform_overrides`
  insgesamt nicht; der Sound wäre dort das einzige Sonderfeld.

## Stand der Prüfung

| Was | Ergebnis |
|---|---|
| Einheitstests | 25 neue Tests (Provider, Composer, Agent-API), Suite 1281 → 1317 grün |
| Endpunkt existiert | belegt: `GET /v25.0/ig_audio` ohne Token antwortet `OAuthException 190` (Authentifizierung fehlt), ein erfundener Pfad dagegen `GraphMethodException 100/33` |
| Abruf mit App-Token | schlägt fehl (`OAuthException 190`) – die Audio API verlangt ein Nutzer-Token, ein App-Token genügt nicht |
| Composer im Browser | durchgespielt auf einer lokalen Instanz mit angelegtem Instagram-Kanal: Panel erscheint erst mit angehängtem Video, Titel wählbar, Voreinstellungen schalten 25/100 gegen 100/0, `Save Draft` **und** Autosave schreiben `platform_extra` korrekt, Neuladen zeigt den Titel wieder an; Console ohne Fehler |
| Fehlerpfad im Browser | mit ungültigem Token liefert der Endpunkt `available: false` und das Panel zeigt „Instagram did not return any audio" – kein Abbruch, keine Ausnahme |
| Darstellung | 1280×900, 390×844 und 360×780 geprüft, gemessener horizontaler Overflow jeweils keiner (`scrollWidth == clientWidth`); überlange Titel werden abgeschnitten statt zu sprengen |
| Echter Katalog-Abruf | **offen**, weil kein Instagram-Konto verbunden ist (Stand 05.08.2026 hat der Verteiler genau zwei Kanäle: Bluesky und YouTube) |

Der echte Abruf ist vorbereitet und nicht erfunden: sobald ein Konto verbunden
ist, liefert

```
python manage.py instagram_audio_check
python manage.py instagram_audio_check --query "walking shoes"
```

entweder die angesagten Titel mit ihren Kennungen oder den genauen Grund, aus
dem Meta sie verweigert.
