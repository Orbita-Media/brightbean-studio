# Token-Erneuerung und Postfach-Abruf

Stand 25.09.2026. Anlass: Der Worker warf alle fünf Minuten zwei Tracebacks
im Postfach-Abruf (`apps/inbox/tasks.py`).

## 1. YouTube: `401 Invalid Credentials`

**Befund.** Das YouTube-Konto „Orbita Media Verlag“ hatte `token_expires_at`
08:38:35 UTC. Ab 08:39 scheiterte `get_messages` mit 401. Der Deploy um 08:38
war Zufall: Das Token war um 07:38 von der Zustandsprüfung erneuert worden
und lief eine Stunde später regulär ab.

**Ursache.** Google-Zugangstoken (YouTube, Google Business Profile) gelten
eine Stunde. Erneuert wurden sie nur

- vor jeder Veröffentlichung (`apps/publisher/engine.py`, `_dispatch_to_provider`,
  Fenster sieben Tage – ein Stundentoken wird also *immer* erneuert),
- in einigen Composer-Ansichten und beim Titelbild-Upload,
- in der Zustandsprüfung alle sechs Stunden (`schedule_all_health_checks`).

Postfach (alle 5 Minuten), Auswertung (stündlich) und Antworten aus dem
Postfach nahmen `account.oauth_access_token` ungeprüft. Damit liefen sie etwa
fünf von sechs Stunden mit einem abgelaufenen Token. Einen 401 behandelte
keiner dieser Wege.

**Veröffentlichen war nicht betroffen.** Belegt mit dem Test
`DispatchRefreshesExpiredTokenTest` in `apps/publisher/tests.py`, der auch
gegen den damaligen Live-Stand grün lief: abgelaufenes Token → `refresh_token`
wird aufgerufen, `publish_post` bekommt das neue Token, `token_expires_at`
liegt danach in der Zukunft. Das Refresh-Token selbst war gültig (manuelle
Erneuerung um 09:08 UTC, Profilabruf danach erfolgreich) – kein Neuverbinden
nötig.

**Behebung.** `apps/social_accounts/tokens.py`:

- `call_with_fresh_token(account, provider, call)` erneuert zehn Minuten vor
  Ablauf (`ACCESS_TOKEN_REFRESH_MARGIN`) und bei einem Anmeldefehler
  (HTTP 401, `TokenExpiredError`, Meta-Code 190) genau einmal und wiederholt
  den Aufruf. Ein zweiter 401 geht nach oben, keine Schleife.
- Eingehängt in Postfach-Abruf, Postfach-Antwort und alle drei
  Auswertungsabrufe (`get_account_metrics`, `get_post_analytics`,
  `get_post_metrics`).
- `SocialAccount.refresh_oauth_token` sperrt die Kontozeile
  (`select_for_update`). Hat ein anderer Prozess inzwischen erneuert, wird
  dessen Token übernommen statt das Refresh-Token ein zweites Mal zu
  verbrauchen. Wichtig für Anbieter, die das Refresh-Token bei jeder Nutzung
  tauschen (Bluesky, TikTok).

**Was der 401 verdeckt hat.** Mit gültigem Token erreicht die stündliche
Auswertung YouTube jetzt wirklich und bekommt `403 YouTube Analytics API has
not been used in project 1002261931374 before or it is disabled`. Das ist
kein Token-Problem, sondern eine Einstellung im Google-Cloud-Projekt des
OAuth-Clients „Orbita Verteiler (YouTube)“: Dort muss die *YouTube Analytics
API* aktiviert werden
(`https://console.developers.google.com/apis/api/youtubeanalytics.googleapis.com/overview?project=1002261931374`).
Bis dahin schreibt der Worker stündlich eine Warnung; Veröffentlichen
(YouTube Data API v3) ist davon nicht betroffen.

**Andere Konten.** Kurzlebige Token haben YouTube und Google Business (1 h),
Bluesky (2 h, kein Postfach, keine Auswertung) und TikTok (24 h, wird von der
Zustandsprüfung alle 6 h erneuert). Pinterest, Threads und LinkedIn laufen
30 bis 60 Tage und werden von der Zustandsprüfung im Sieben-Tage-Fenster
erneuert. Facebook und Instagram nutzen Seiten-Token ohne Ablaufdatum.

## 2. Instagram: `(#3) Application does not have the capability`

**Befund.** `InstagramProvider.get_messages` rief ausschliesslich
`/{ig_user_id}/conversations` ab, also Direktnachrichten. Das braucht
`instagram_manage_messages` und die Instagram-Messaging-Fähigkeit der
Meta-App (App-Review). Beides fehlt; Meta antwortet mit Code 3. Kommentare
wurden gar nicht abgerufen, obwohl `instagram_manage_comments` erteilt ist.

**Behebung.**

- Der Abruf besteht aus zwei Teilen: `comments` (Kommentare und Antworten auf
  die letzten 25 Beiträge, eigene Antworten ausgenommen) und `dm`.
- Verweigert Meta einen Teil mit Code 3, 10, 200 oder 230, landet er in
  `provider.inbox_unavailable_parts`; die übrigen Teile laufen weiter.
- Die Engine merkt sich das in `platform_settings["inbox_unavailable"]`
  (Grund und Zeitpunkt), überspringt den Teil 24 Stunden lang und versucht es
  danach erneut. Klappt es wieder, verschwindet der Eintrag von selbst.
- Kontokarte und Postfach zeigen den Hinweis „Direktnachrichten nicht
  verfügbar“ mit dem Grund von Meta.
- Antworten auf Kommentare gehen an `/{comment_id}/replies`, Antworten auf
  Direktnachrichten an die Unterhaltung.

Veröffentlichen berührt nichts davon.

**Direktnachrichten einschalten** (nur falls gewünscht): In der Meta-App das
Produkt für Instagram-Messaging hinzufügen und `instagram_manage_messages`
durch den App-Review bringen, dann `META_REQUEST_MESSAGES_SCOPE=true` setzen
und den Instagram-Kanal einmal neu verbinden. Der Abruf nimmt die
Direktnachrichten spätestens 24 Stunden später automatisch wieder auf.

## 3. Beim Deploy abgebrochene Aufgaben blieben eine Stunde gesperrt

**Befund.** Nach dem Deploy um 09:29 lief der Postfach-Abruf nicht mehr:
`background_task` zeigte `locked_by = 1`, `locked_at = 09:29:25`. Der alte
Worker hatte die Aufgabe um 09:29:25 begonnen und wurde um 09:29:47 entfernt.

**Ursache.** `process_tasks` fängt unter Linux nur SIGTSTP ab, nicht SIGTERM.
Als PID 1 ignorierte der Worker Coolifys Stoppsignal und wurde nach der
Frist hart beendet, mitten in der laufenden Aufgabe. Der neue Worker ist
ebenfalls PID 1, hält den Sperrvermerk deshalb für lebendig und wartet
`BACKGROUND_TASK_MAX_RUN_TIME` (eine Stunde) ab. Dasselbe kann den
Veröffentlichungslauf treffen.

**Behebung** (`docker-compose.coolify.yml`):

- `stop_signal: SIGTSTP` – der Worker bringt die laufende Aufgabe zu Ende und
  beendet sich selbst.
- Vor `process_tasks` läuft `manage.py release_task_locks`
  (`apps/common/management/commands/release_task_locks.py`) und löst die
  Sperren des Vorgängers. Sicher, weil Coolify die alten Container entfernt,
  bevor der neue Worker startet.

## Tests

- `apps/social_accounts/tests/test_tokens.py` – Erneuern vor Ablauf, einmal
  bei 401, keine Schleife, Übernahme bei gleichzeitiger Erneuerung.
- `apps/inbox/tests/test_sync_tokens.py` – YouTube mit abgelaufenem Token und
  mit 401, Instagram-Kommentare trotz verweigerter Direktnachrichten,
  Überspringen und erneuter Versuch nach 24 Stunden, Antwortwege.
- `apps/publisher/tests.py::DispatchRefreshesExpiredTokenTest` – Veröffentlichen
  erneuert ein abgelaufenes Token.
- `apps/common/tests/test_release_task_locks.py` – Sperre eines toten Workers
  wird gelöst.
