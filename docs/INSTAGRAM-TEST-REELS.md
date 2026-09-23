# Instagram Test-Reels (Trial Reels) über den Verteiler

Stand 23.09.2026. Ein Test-Reel geht zuerst nur an Menschen, die dem Konto
**nicht** folgen. Follower sehen es weder im Feed noch unter Reels, und es steht
nicht im Profil, bis es für alle freigegeben („graduiert") ist. Noah hat in der
App bestätigt: @orbitamedia_verlag (465 Follower, Profi-Konto) bekommt beim
Teilen den Schalter „Test-Reel" angeboten – Instagram verlangt für Profi-Konten
200 Follower.

## Was Meta dazu sagt

IG User Media Reference, `POST /{ig-user-id}/media` (Facebook-Login) und
Content-Publishing-Anleitung, Abschnitt „Trial Reels posts", Beispiel mit
`POST graph.instagram.com/…/media` (Instagram-Login):

> `trial_params` – An optional parameter for publishing trial reels. **The
> `media_type` must be `REELS` if this parameter is included in the request.**
> `graduation_strategy` – **Required.** The value should be either `MANUAL` or
> `SS_PERFORMANCE`. When `MANUAL`, the trial reel can be manually graduated in
> the native app. When `SS_PERFORMANCE`, the trial reel will be automatically
> graduated if the trial reel performs well.

- https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
- https://developers.facebook.com/docs/instagram-platform/content-publishing/

Einschränkungen, die daraus und aus der Hilfeseite
(https://help.instagram.com/835643311711702) folgen:

| Punkt | Regel |
|---|---|
| Medientyp | Nur `REELS` – also genau ein Video. Kein Bild, keine Story, kein Karussell |
| Freigabe | `SS_PERFORMANCE` (automatisch bei guter Leistung) oder `MANUAL` (von Hand in der App). Pflichtfeld |
| Konto | Öffentlich, Profi-Konto ab 200 Follower (privat: 1.000). Ein Konto, das nicht in Empfehlungen erscheinen darf, verliert die Funktion |
| Inhalt | Neues Material: bereits geteilte Inhalte bekommen als Test-Reel weniger Reichweite |
| Sichtbarkeit | Follower können es trotzdem sehen, wenn es per DM geteilt wird, auf Audio-Seiten oder in Suchmaschinen |
| Auswertung | Insights innerhalb von 24 Stunden; ein Test-Reel läuft langsamer an und beeinflusst das Ranking der normalen Reels nicht |
| Automatik in der App | Wer in der App „automatisch teilen" einschaltet, setzt das für alle künftigen Test-Reels. Über die Schnittstelle gilt der Wert je Beitrag |
| Kombination | Mit `collaborators`, `audio_configuration` oder `share_to_feed` dokumentiert Meta **keine** Einschränkung. Der Verteiler setzt `share_to_feed` nicht |

## Beleg gegen die echte Schnittstelle (23.09.2026)

Im laufenden App-Container, mit dem echten Token von @orbitamedia_verlag,
einem 8-Sekunden-Video (720×1280, H.264/AAC) als Probedatei unter
`social-cdn.orbita-media.de/e2e-trial-check/…` (danach gelöscht). Nur
Container angelegt, **nie** `media_publish` aufgerufen – die Container
verfallen nach 24 Stunden.

| Versuch | Antwort |
|---|---|
| `trial_params` als JSON-String, Strategie `BOGUS` | 400, Subcode 2207075 „Unbekannte Abschlussstrategie für Test-Reels" – Meta liest den String |
| `trial_params` als verschachteltes Objekt, Strategie `BOGUS` | 400, derselbe Subcode – auch das Objekt wird gelesen |
| JSON-String `{"graduation_strategy": "SS_PERFORMANCE"}` | 200, Container `17956926075211601`, `status_code` **FINISHED** |
| JSON-String `{"graduation_strategy": "MANUAL"}` | 200, Container `17956926081211601`, `status_code` **FINISHED** |

Die Fehlversuche sind der eigentliche Nachweis: Ein Feld, das still ignoriert
würde (so wie `collaborators` als natives JSON-Array), hätte bei `BOGUS` mit
200 geantwortet. Der Verteiler schickt den JSON-String wie bei
`audio_configuration` und `collaborators`.

## So setzt man es

### Agent-API (Content Tool)

Beim **Anlegen** (`POST /api/v1/posts/`), weil Entwürfe nie per PATCH auf
einen neuen Stand gebracht werden:

```json
{
  "social_account_id": "<instagram-konto>",
  "caption": "…",
  "media_asset_ids": ["<genau ein Video>"],
  "platform_overrides": [
    {
      "social_account_id": "<instagram-konto>",
      "trial": true,
      "trial_graduation": "SS_PERFORMANCE"
    }
  ],
  "action": "draft"
}
```

- `trial` (bool): `true` = Test-Reel, `false` entfernt es.
- `trial_graduation`: `"SS_PERFORMANCE"` (Voreinstellung) oder `"MANUAL"`.
  Andere Werte → 422.
- Nur für `instagram` und `instagram_login`, sonst 422.
- Mit Medien muss es genau ein Video sein, sonst 422. Ohne Medien geht es
  durch; der Verteiler prüft beim Veröffentlichen erneut.
- `GET /api/v1/posts/{id}` zeigt je Kanal `platform_overrides[].trial` und
  `trial_graduation` (`null` = normales Reel).
- `PATCH` kann es auch; ein weggelassenes Feld lässt den Wert stehen.

### Composer

Karte „Test-Reel (zuerst nur Nicht-Follower)" unter den Instagram-Einstellungen,
sichtbar bei genau einem Video – und immer, solange der Schalter an ist, damit
er sich nach einem Medientausch wieder ausschalten lässt (dann mit roter
Warnung). Darunter die Freigabe: „Automatisch" oder „Von Hand".

## Verhalten beim Veröffentlichen

`providers/instagram_trial.py` gilt für beide Anbindungen:

- Test-Reel auf Reel (auch `PostType.VIDEO`, der Rückfall für ein einzelnes
  Video): `trial_params` als JSON-String am REELS-Container.
- Test-Reel auf Bild, Story oder Karussell: `PublishError`, **bevor** etwas
  hochgeladen wird, nicht wiederholbar. Anders als beim Plattform-Ton wird
  hier nicht still verworfen: ohne Test ginge der Beitrag an alle Follower –
  das Gegenteil dessen, was bestellt war.
- Fällt beim Ton-Rückfall der Sound weg, bleibt `trial_params` erhalten.
- Das Ergebnis trägt `trial_graduation` in `PublishResult.extra`.

Nebenbei behoben: Die Instagram-Login-Anbindung schickte ein einzelnes Video
(`PostType.VIDEO`) als `image_url` und scheiterte. Es nimmt jetzt wie bei der
Facebook-Login-Anbindung den REELS-Weg.

## Dateien

| Bereich | Datei |
|---|---|
| Regeln | `providers/instagram_trial.py` |
| Provider | `providers/instagram.py`, `providers/instagram_login.py` |
| Agent-API | `apps/api/schemas.py` (`PlatformOverride`, `PlatformOverrideOut`), `apps/api/routers/posts.py` |
| Composer | `apps/composer/views.py` (`_instagram_trial_extra`), `templates/composer/compose.html` |
| Tests | `tests/providers/test_instagram_trial.py`, `apps/api/tests/test_instagram_trial_api.py`, `apps/composer/tests/test_instagram_trial.py` |
