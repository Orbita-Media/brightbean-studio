# Mitwirkende nachträglich setzen – Lücke geschlossen am 08.08.2026

Eine Instagram-Kollaboration ist der einzige belegte Verteilungshebel, den ein
Verlagskonto ohne fremde Zustimmung nicht bekommt: Ein **markierter** Beitrag
wird nicht an die Follower des Markierten ausgespielt, ein
**Kollaborations**-Beitrag schon. Instagram sagt das selbst.

Die API konnte Mitwirkende bis zum 08.08.2026 nur beim **Anlegen** entgegen-
nehmen. Wer einen Entwurf ohne sie erzeugt hatte, kam nicht mehr heran.

## Was das konkret gekostet hätte

Das Content-Tool hatte acht Erscheinungs-Pakete eingespielt – 64 Entwürfe,
davon 24 auf Instagram. Keiner trug einen Mitwirkenden, weil das
Einspiel-Skript das Feld nicht sendete. Nachrüsten hiess:

- sieben Beiträge **neu anlegen**,
- sieben alte **von Hand löschen** – ein `DELETE` gibt es in der API nicht,
- und beim nächsten Launch stünde dasselbe Problem wieder da.

Das ist ein Umweg um einen Fehler im eigenen Fork. Also wurde der Fehler
behoben.

## Was geändert wurde

### 1. `UpdatePostRequest` nimmt `platform_overrides` entgegen

Mit derselben Prüfung wie beim Anlegen: höchstens drei Nutzernamen (Schema),
nur für Instagram-Konten, führendes `@` und Leerzeichen werden abgeschnitten,
derselbe Name zweimal wird zusammengefasst. Zusätzlich muss das genannte Konto
ein **Kind dieses Beitrags** sein – sonst schriebe der Aufruf eine Einstellung,
die beim Veröffentlichen nichts tut.

### 2. Die Semantik ist die des Composer-Fixes vom Vortag

| Fall | Wirkung |
|---|---|
| Feld **nicht** dabei | gespeicherter Wert bleibt |
| Feld dabei, gefüllt | wird gesetzt |
| Feld dabei, leer (`""` / `[]`) | bewusst entfernt |

Das gilt zweistufig: für `platform_overrides` als Ganzes und innerhalb eines
Eintrags noch einmal Feld für Feld. Ohne diese Unterscheidung würde ein
Aufruf, der nur den Wunschtermin verschiebt, nebenbei die kanalspezifische
Fassung und die Mitwirkenden löschen – **genau der Fehler, der einen Tag vorher
im Composer behoben wurde** (siehe `KANALFASSUNG-BEIM-SPEICHERN.md`).

Technisch entscheidet `model_fields_set` von Pydantic, welche Felder der
Aufrufer wirklich geschickt hat. „Weggelassen" und „`null`" sehen im JSON
gleich aus, meinen aber das Gegenteil voneinander.

### 3. `PostResponse` führt `platform_overrides` – der wichtigere Teil

Die Antwort enthielt die gespeicherten Overrides bisher **überhaupt nicht**.
Das war kein Schönheitsfehler, sondern machte jede Prüfung wertlos:

> Ein Werkzeug las alle 24 Instagram-Entwürfe zurück und meldete für jeden
> „keine Mitwirkenden". Dieselbe Antwort hätte es auch bei perfektem Zustand
> gegeben. Es konnte nicht zwischen **„fehlt"** und **„kann ich nicht sehen"**
> unterscheiden.

Eine Prüfung, deren Antwort feststeht, ist keine Prüfung. Genau daran ist im
selben Zeitraum ein anderer Wächter gescheitert, der wochenlang „nichts Neues"
meldete, obwohl er gar nicht anschlagen konnte.

Seit der Änderung gibt `GET /api/v1/posts/{id}` je Kanal zurück, was wirklich
gespeichert ist:

```json
"platform_overrides": [
  {
    "social_account_id": "9735eaee-…",
    "platform": "instagram",
    "title": null,
    "caption": null,
    "first_comment": null,
    "collaborators": ["kyocreepy"]
  }
]
```

`null` heisst durchgängig: kein Override, der Kanal nimmt den Wert des
Beitrags. Eine **leere Liste** bei `collaborators` heisst: ausdrücklich keine.

## Was die Tests abdecken

`apps/api/tests/test_instagram_collaborators_api.py`, Klasse
`TestCollaboratorsOnUpdate`:

- Mitwirkende lassen sich nachträglich setzen
- die Antwort zeigt sie – ohne das ist keine Prüfung möglich
- **ein Aufruf ohne das Feld lässt sie stehen** (der wichtigste Test)
- eine leere Liste entfernt sie bewusst
- eine kanalspezifische Fassung überlebt das Setzen der Mitwirkenden
- ein fremdes Konto wird mit 422 abgewiesen
- Mitwirkende bleiben Instagram vorbehalten

## Was Aufrufer wissen müssen

**Die Einladung erreicht den Partner als Direktnachricht ohne
Benachrichtigung.** Folgen sich beide Konten nicht gegenseitig, landet sie im
Anfragen-Ordner. Nimmt der Partner sie nicht an, erscheint der Beitrag ohne ihn
– und ohne die Reichweite, für die die Kollaboration gemacht ist. Wer einlädt,
muss auf anderem Weg Bescheid sagen, und zwar bevor der Termin da ist.
