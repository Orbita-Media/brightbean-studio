# Instagram-Personenmarkierungen (`user_tags`)

**02.11.2026** – Der Verteiler markiert Personen auf Instagram-Bildern. Anlass:
Team-Karussell von @inyourcorner_book, auf dem Stefan Schott (@stefanschott_)
und Tobias Grimm (@tobinstar) auf ihren Fotos markiert sein sollen.

## Was eine Markierung bringt (und was nicht)

Die markierte Person wird benachrichtigt, der Beitrag erscheint in ihrem Reiter
„Markiert“. An ihre Follower wird er **nicht** ausgespielt – das kann nur
`collaborators` (siehe `MITWIRKENDE-NACHTRAEGLICH-SETZEN.md`).

## Agent-API

`POST /api/v1/posts/` und `PATCH /api/v1/posts/{id}`:

```json
"platform_overrides": [{
  "social_account_id": "<instagram-konto>",
  "user_tags": [
    {"slide": 1, "username": "stefanschott_", "x": 0.76, "y": 0.38},
    {"slide": 3, "username": "stefanschott_", "x": 0.62, "y": 0.26}
  ]
}]
```

- `slide` zählt ab 1 in der Reihenfolge von `media_asset_ids`, `x`/`y` von 0 bis 1
  (0/0 = links oben). Führendes `@` wird abgeschnitten, derselbe Name auf derselben
  Folie zählt einmal.
- Nur `instagram` und `instagram_login`. 422 bei anderem Kanal, unbekannter Folie,
  Video-Folie, Story, ungültigem Namen und mehr als 20 Markierungen je Bild.
- PATCH: Feld weggelassen = bleibt, `[]` = entfernen, Liste = ersetzen; geprüft
  gegen die Medien nach dem Aufruf.
- `GET /posts/{id}` zeigt sie unter `platform_overrides[].user_tags`.
- Gespeichert in `PlatformPost.platform_extra["user_tags"]` (flache Liste wie oben).

## Veröffentlichen

`providers/instagram_user_tags.py` liefert je Folie die Graph-Form
`[{username, x, y}]`; sie geht als JSON-**String** in `user_tags` (wie
`collaborators`, eine native Liste wird von Graph still verworfen).

| Beitrag | Wo | Fundstelle |
|---|---|---|
| Einzelbild | am Bild-Container | `providers/instagram.py` `_publish_single`, `providers/instagram_login.py` |
| Karussell | am **Kind**-Container der Folie, nie am Eltern-Container | `_publish_carousel` beider Provider |
| Reel, Video-Folie, Story | entfällt mit Logzeile | `warn_unsupported` |

Rückfall: Lehnt Graph einen Container mit Markierung ab (privates, umbenanntes oder
gelöschtes Konto), wird er einmal ohne `user_tags` erzeugt; das Ergebnis trägt
`user_tags_dropped: true`. Scheitert auch die Wiederholung, kommt der erste Fehler.

Beiträge ohne Markierung senden das Feld gar nicht und laufen wie vorher.

## Tests

- `tests/providers/test_instagram_user_tags.py` – Normalisieren, Einzelbild,
  Karussell je Kind, Video-Folie, Reel/Story, Rückfall, Instagram-Login-Weg.
- `apps/api/tests/test_instagram_user_tags_api.py` – Anlegen, Lesen, PATCH-Semantik,
  422-Fälle, Weitergabe durch die Engine.

Gesamtlauf 02.11.2026 (Wegwerf-Postgres auf dem Hetzner, Image des Verteilers):
1991 Tests grün.

## Nachweis am lebenden Konto (02.11.2026, nichts veröffentlicht)

Nach dem Deploy (Commit 257d25c) in der Django-Shell des Verteilers: deployter
`InstagramProvider._publish_carousel` mit Konto @inyourcorner_book (Facebook-Weg),
zwei vorhandenen Bildern der IYC-Mediathek und `_publish_container` durch eine
Attrappe ersetzt – Container werden erzeugt, aber nie veröffentlicht (Container
verfallen nach 24 h).

- Kind 1 mit `user_tags` `[stefanschott_ (0.76/0.38), tobinstar (0.76/0.69)]` →
  Container `18154325320519771`, Status FINISHED.
- Kind 2 mit `[stefanschott_ (0.62/0.26)]` → `18154325356519771`, FINISHED.
- Eltern-Container CAROUSEL ohne `user_tags` → `18154325395519771`, FINISHED;
  kein `user_tags_dropped`.
- Gegenprobe: Kind mit unbekanntem Namen → HTTP 400, Code 110, Subcode 2207018
  („Nutzer mit privatem Profil oder ungültigem Benutzernamen kann nicht geladen
  werden“). Graph wertet das Feld also aus und verwirft es nicht still.
- Privates Konto (`jantraupe`) → dieselbe Ablehnung 2207018: private Konten lassen
  sich per API nicht markieren. Beim Veröffentlichen griffe der Rückfall (Bild ohne
  Markierung, `user_tags_dropped`).
