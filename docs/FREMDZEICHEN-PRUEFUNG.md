# Fremdzeichen in erzeugten Texten

**01.08.2026** – Im Beitragstext eines fertigen Entwurfs stand „übersprингst".
Die Zeichen и, н und г sind kyrillisch. Im Fliesstext ist das nicht zu sehen.

## Woher das kommt

Die Texte entstehen im Content-Tool über ein Sprachmodell. Das Modell setzt
mitten im deutschen Wort den kyrillischen Buchstaben ein, der für denselben
**Laut** steht – nicht den, der gleich aussieht:

| gemeint | geliefert | Codepoint |
|---|---|---|
| i | и | U+0438 |
| n | н | U+043D |
| g | г | U+0433 |

Das ist der Grund, warum die Reparatur unten die Umschrift benutzt und nicht die
optische Ähnlichkeit. Wer nach optischer Ähnlichkeit ersetzt, macht aus и ein
„n" (die Form ähnelt einem gespiegelten N) und verschlimmert den Fehler.

Aufgefallen war der Fall rein zufällig – es gab keine Prüfung, die so etwas
hätte finden können.

## Was geprüft wird

Der erwartete Zeichenvorrat: lateinische Schrift samt deutscher Umlaute,
Ziffern, Satzzeichen, Symbole, Emoji, Leerraum. Beanstandet wird gezielt, in
drei Stufen:

| Art | Kriterium | Wirkung |
|---|---|---|
| `gemischt` | Ein Wort enthält lateinische UND fremde Buchstaben, oder ein Zeichen normalisiert sich zu lateinischer Schrift (Breitform ｆ, mathematisches 𝐟) | Mangel, sperrt |
| `unsichtbar` | Steuer- oder Formatzeichen ohne Darstellung (U+200B, U+00AD, U+202E) | Mangel, sperrt |
| `fremdschrift` | Ein Wort besteht vollständig aus einer anderen Schrift | nur Hinweis |

Die dritte Stufe ist der Grund, warum die Prüfung keine Fehlalarme produziert:
ein griechisches Zitat oder ein japanischer Titel ist legitim und geht durch.
Ein deutsches Wort mit einem kyrillischen Buchstaben darin ist es nie.

Ausdrücklich **kein** Fund sind: Emoji samt zusammengesetzter Familien
(👩‍👩‍👧‍👦 – der Zero Width Joiner darin ist erlaubt), Hautton-Modifikatoren,
Umlaute, ß, französische, spanische, polnische und skandinavische Namen (Café,
Señor, Łukasz, Håkan), Anführungszeichen, Gedankenstriche, Auslassungspunkte,
Zeilenumbrüche und Tabulatoren, E-Mail-Adressen und Netzadressen.

## Was gemeldet wird

Nicht „enthält Fremdzeichen", sondern je Stelle:

```
Position 19: „и" (U+0438 CYRILLIC SMALL LETTER I) in „übersprингst", gemeint ist vermutlich „i"
```

Also: Zeichen, Codepoint, Unicode-Name, Position im Text, das betroffene Wort
und ein Vorschlag. An einem `fremdschrift`-Hinweis steht **kein** Vorschlag –
er wäre eine Aufforderung, ein Zitat kaputtzumachen.

## Wo die Prüfung sitzt

### An der Erzeugung: Content-Tool

`src/lib/homoglyphen.ts` mit `pruefe()`, `beanstandungen()` und `bereinige()`.
`src/lib/campaign-gen.ts` reinigt jedes erzeugte Feld direkt nach der Antwort
des Modells: Hook, Bildunterschrift, Zitat, Slide-Überschriften, Slide-Texte,
Kicker, Quellen und die Bildschirmtexte der Videos. Jede Ersetzung wird
protokolliert; was nicht eindeutig ist, bleibt stehen und wird gemeldet.

`scripts/verteiler-homoglyphen.ts` prüft die im Verteiler liegenden Entwürfe
und Alternativtexte (Vorbild: `scripts/verteiler-faktenpruefung.ts`).

### Als letzte Sperre: Verteiler

`apps/common/homoglyphs.py`, aufgerufen aus
`PublishEngine._block_on_foreign_characters`. Geprüft werden Beitragstext,
Titel, erster Kommentar und **jeder** Alternativtext. Ein Fund lässt den Beitrag
endgültig scheitern (`retryable=False`, kein Wiederholen) mit den konkreten
Stellen im Fehlertext.

Hier wird bewusst **nicht** repariert. Zwei Gründe: der Text in der Datenbank
bliebe sonst falsch und der Fehler käme beim nächsten Beitrag wieder; und die
richtige Ersetzung ist eine redaktionelle Entscheidung, keine technische.

### Am Eingabefeld: Mediathek

Der Alternativtext-Editor im Detailbereich zeigt dieselben Funde direkt unter
dem Feld. Das ist der billigste Moment, sie zu bemerken.

## Reparatur

`bereinige()` ersetzt ausschliesslich:

- Buchstaben in `gemischt`-Wörtern, für die es eine Ein-Zeichen-Umschrift gibt
- unsichtbare Zeichen (werden entfernt)

Buchstaben ohne eindeutige Ein-Zeichen-Entsprechung (ж, ч, ш, щ, ю, я, ъ, ь)
bleiben stehen und werden gemeldet. Lieber eine Stelle melden als sie falsch
reparieren.

Aufgerufen wird die Reparatur nur dort, wo ein Mensch das Ergebnis noch sieht:
bei der Erzeugung im Content-Tool und im ausdrücklichen Suchlauf. Nie beim
Veröffentlichen.

## Suchlauf über den Bestand

```
python manage.py pruefe_fremdzeichen                 # nur melden
python manage.py pruefe_fremdzeichen --reparieren    # Eindeutiges beheben
python manage.py pruefe_fremdzeichen --workspace <uuid>
```

Geprüft werden `Post` (Titel, Text, erster Kommentar, interne Notiz),
`PlatformPost` (kanalspezifische Fassungen), `PostMedia` (Übersteuerung des
Alternativtextes) und `MediaAsset` (Alternativtext, Titel).

## Tests

| Datei | Umfang |
|---|---|
| `apps/common/tests/test_homoglyphs.py` | Der echte Fall mit allen drei Zeichen, konkrete Meldung, Wiederherstellung, was NICHT anschlägt (Emoji, Umlaute, fremdsprachige Namen, Adressen), Zitat gegen Mangel, unsichtbare Zeichen, Breitformen und mathematische Alphabete, Nicht-Reparatur des Uneindeutigen |
| `apps/publisher/tests.py` – `DispatchFremdzeichenSperreTest` | Sauberer Text geht durch, kyrillische Zeichen im Beitragstext und im Alternativtext halten den Beitrag auf, unsichtbares Zeichen hält auf, fremdsprachiges Zitat geht durch |
| `apps/media_library/tests/test_alt_text_endpoint.py` | Warnung am Eingabefeld, keine Warnung bei sauberem Text |
| Content-Tool: `src/lib/homoglyphen.test.ts` | dieselbe Matrix in TypeScript |
