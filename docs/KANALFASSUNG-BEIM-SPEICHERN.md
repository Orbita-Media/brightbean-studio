# Die kanalspezifische Fassung überlebt das Speichern – behoben am 07.08.2026

Ein Beitrag kann je Kanal eine eigene Fassung tragen
(`PlatformPost.platform_specific_caption`). Beim Veröffentlichen gewinnt sie
gegen den gemeinsamen Text:

```python
# apps/composer/models.py
@property
def effective_caption(self):
    if self.platform_specific_caption is not None:
        return self.platform_specific_caption
    return self.post.caption
```

Genau diese Fassung ging beim Speichern im Composer verloren – ohne Meldung,
ohne Rückfrage.

## Was passiert ist

Gemessen an einem eigens angelegten Wegwerf-Entwurf:

| | Basistext | Fassung für Bluesky | was auf Bluesky rausgegangen wäre |
|---|---|---|---|
| vor dem Speichern | 505 | **71** | 71 |
| nach einem Klick auf „Save Draft" | 504 | **entfernt** | **504** |

Bluesky macht bei 300 Zeichen dicht. Der Beitrag wäre dort aufgelaufen. Im
Verteiler hingen daran 47 Kanalzeilen mit je eigener Kurzfassung, weil der
Basistext für Instagram gedacht ist und zwischen 400 und 550 Zeichen liegt.

## Warum

Zwei Bauteile, die einzeln harmlos sind und zusammen den Schaden machen.

**1. Das Feld wurde leer gerendert.** Das Textfeld steht in einem
`x-for`-Template. Alpine rendert es in das DOM, `x-show` blendet es nur aus –
**abgeschickt wird es trotzdem**. Gefüllt wurde es nie: der Zustand startete
mit `overrides: {}`, und der gespeicherte Wert wurde nirgends hineingeschrieben.

**2. Die Sicht konnte „nicht dabei" nicht von „leer" unterscheiden.**

```python
# vorher
override_caption = request.POST.get(f"override_caption_{acc_id}", "").strip()
pp.platform_specific_caption = override_caption if override_caption else None
```

Ein leerer String hiess dort „Fassung entfernen". Dasselbe galt für
`platform_specific_title` und `platform_specific_first_comment` – für den
ersten Kommentar rendert der Composer bis heute überhaupt kein Feld, dessen
kanalspezifische Fassung wurde also bei **jedem** Speichern geleert.

## Die Vorschau hat zusätzlich falsch gemeldet

Sie baute ihren Text aus dem Formular statt aus dem gespeicherten Stand und
meldete deshalb „479/300", obwohl die gespeicherte Fassung 274 Zeichen hatte.
Derselbe Fehler steckte im Zähler unter dem gemeinsamen Text: er nahm das
kleinste Limit **aller** gewählten Kanäle, auch derer, die den Basistext gar
nicht verwenden.

**Das ist die gefährlichere Hälfte.** Eine rote Zahl unter dem Textfeld lädt
dazu ein, den **Basistext** zu kürzen – und der ist der Text, der auf
Instagram und Facebook rausgeht. Der Schaden wäre also nicht auf den Kanal
beschränkt geblieben, dessen Grenze gemeldet wurde.

## Was jetzt gilt

**Anwesenheit statt Inhalt** (`_apply_platform_overrides`):

| Feld im Formular | Bedeutung |
|---|---|
| dabei und gefüllt | Fassung speichern |
| dabei und leer | Fassung bewusst entfernt |
| **nicht dabei** | **gespeicherte Fassung bleibt stehen** |

Damit ist ein Speichern aus einem anderen Bereich der Oberfläche folgenlos.
Dieselbe Absicherung tragen die Instagram- und TikTok-Extras schon länger
(„Only rebuild extras when the panel was part of the form").

**Der Composer zeigt, was rausgeht.** Die gespeicherten Fassungen wandern über
`json_script` in den Alpine-Zustand, die Felder hängen per `x-model` daran, und
ein Kanal mit eigener Fassung öffnet seinen Kasten von selbst. Verborgener Text
war die Ursache dafür, dass der Verlust niemandem auffiel.

**„Remove override" leert den Wert.** Vorher setzte der Knopf nur
`overrides[accId] = false`, klappte den Kasten also zu und liess den Text im
verborgenen Feld stehen – beim nächsten Speichern wäre er wieder mitgegangen.

**Beide Zähler rechnen richtig.** Die Vorschau fällt für ein Feld, das das
Formular nicht mitschickt, auf den gespeicherten Stand zurück.
`sharedCaptionLimit()` nimmt für den gemeinsamen Text nur noch Kanäle **ohne**
eigene Fassung; tragen alle eine eigene, steht dort nur die Zeichenzahl ohne
Grenze.

## Nachgeprüft

`apps/composer/tests/test_platform_overrides.py` deckt beide Hälften ab, samt
der Gegenprobe, dass ein bewusstes Entfernen weiter funktioniert.

Dazu ein echter Speichervorgang über die Oberfläche (headless, eigene
Datenbank): Feld trägt 71 Zeichen, Kasten sichtbar, Vorschau meldet „71/300"
statt einer Überschreitung, das Formular schickt die 71 Zeichen mit, und in der
Datenbank steht die Fassung danach unverändert. Kein Fehler in der Browser-Konsole,
kein waagerechter Überlauf auf 1440, 390 und 360 Pixel Breite.

## Was das für bestehende Beiträge heisst

Nichts – es war nichts zu reparieren. Zum Zeitpunkt des Fixes trugen 47 von 48
Bluesky-Kanalzeilen ihre Fassung, genau der Sollstand aus dem Protokoll des
Content-Tools. Die eine Zeile ohne Fassung ist ein am 27.07. veröffentlichter
Beitrag, der nie eine hatte. `composer_post_version` war leer, es hatte also
niemand einen dieser Beiträge im Composer gespeichert.
