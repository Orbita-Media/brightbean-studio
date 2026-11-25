# Meta-Drossel und Threads-Verlängerung

Stand 25.11.2026. Anlass: Meldungen 12858/12962 (Threads 401, Token abgelaufen) und
13033/13034 (Instagram/Facebook 403 „(#4) Application request limit reached“) am
24.11.2026; dieselben Fehlerbilder kamen dreimal in 30 Tagen, die Reparaturen hielten nicht.

## 1. Threads wurde nie verlängert

- Threads (und Instagram Login) verlängern mit dem Access-Token selbst
  (`th_refresh_token`, `ig_refresh_token`), ein Refresh-Token gibt es nicht;
  `oauth_refresh_token` blieb leer.
- Jede Verlängerungsstelle (Zustandsprüfung, `tokens.py`, Publisher, Titelbild,
  Pinterest-Boards) prüfte dieses leere Feld und verlängerte nie. Das Verlagskonto
  (verbunden 25.09.) lief am 24.11. 01:44 UTC ab, IYC wäre am 14.12. gefolgt.
- Behebung: `SocialAccount.refresh_credential` liefert für diese Plattformen das
  Access-Token; alle Stellen nutzen es. Die Zustandsprüfung (alle 6 h) verlängert
  7 Tage vor Ablauf.
- Die Zustandsprüfung lief nur für `connected`/`token_expiring`; ein einziger
  Fehlschlag (`error`) beendete jede weitere Prüfung. Jetzt werden auch
  `error`-Konten geprüft und heilen von selbst.
- Frühwarnung im Social-Agent (claudy-agenten `social_agent.py`): lange Token
  (Threads, Pinterest, LinkedIn, Instagram Login), die 5 Tage vor Ablauf noch nicht
  verlängert sind, melden „Token läuft ab“.

## 2. Meta-Drossel

Befund 24.11.: Die #4-Fehler häuften sich alle 6 h (01, 07, 13, 19/20 UTC). Welcher
Aufrufer das App-Budget dann erschöpft, ist nicht abschließend gemessen
(`x-app-usage` lag nachts bei 8–14 %). Sicher vergeudet wurde: Die stündliche
Auswertung fragte alle ~250 Instagram-Storys der letzten 90 Tage jede Stunde neu ab – Storys verschwinden nach 24 h, jeder Abruf
scheiterte, ohne Schnappschuss galt der Beitrag in der nächsten Stunde wieder als
fällig (~6000 vergebliche Graph-Aufrufe am Tag). Dazu ein Aufruf je Metrik (7 je
Instagram-Beitrag). Lief eine Veröffentlichung in das erschöpfte Stundenfenster,
verbrauchte sie ihre drei Versuche in 36 Minuten (1 + 5 + 30) und scheiterte.
Meta-Code 4 galt außerdem als Anmeldefehler: Die Zustandsprüfung machte aus jedem
403 „Account connection expired“ (18.11., am selben Tag gab es #4-Fehler beim
Veröffentlichen), der Social-Agent meldete vom 20. bis 24.11. täglich „Meta-Zugang
ungültig … Fehler 4“ und riet zum Neu-Einspielen der Token.

Behebung:

- `providers/base.py`: Meta-Antworten mit Code 4, 17, 32, 613 oder 80001 ff.
  werden zu `RateLimitError` und setzen eine **harte** Drossel je API-Host
  (`apps/publisher/drossel.py`, Tabelle `publisher_drossel`). Melden die Header
  `x-app-usage`/`x-business-use-case-usage` 85 % oder mehr, gibt es eine
  **weiche** Drossel.
- Auswertung, Postfach und Zustandsprüfung pausieren bei jeder Drossel für die
  Plattform; die Veröffentlichung läuft bei weicher Drossel weiter.
- Publisher: `RateLimitError` oder harte Drossel hält den Beitrag zurück
  (`_hold_for_rate_limit`), ohne einen Versuch zu verbrauchen, 10–60 min je
  Schritt, endgültig gescheitert erst 6 h nach dem Termin.
- Auswertung: Storys nach 24 h nicht mehr abfragen; fehlgeschlagene Abrufe setzen
  `analytics_fehler_am`/`analytics_fehler_zahl` und warten den doppelten Takt je
  Fehlschlag (höchstens 30 Tage); Instagram-Insights in einem Aufruf
  (`fetch_insights_safe(..., gemeinsam_zuerst=True)`, Rückfall einzeln).
- Zustandsprüfung: Drossel ist kein „getrennt“, der Status bleibt.
- claudy-agenten `meta_token.py pruefen`: Code 4/17/32/613 bei `debug_token` heißt
  `gedrosselt`, nicht ungültig; `social_agent.py` meldet dann keinen Meta-Ausfall.

Tests: `apps/social_accounts/tests/test_threads_refresh_und_drossel.py`,
`tests/providers/test_meta_insights.py`, `tests/providers/test_instagram.py`.

## 3. Restverbrauch nach der Reparatur (25.11.2026)

Gegenprobe 25.11. 02:30 UTC: seit Deploy 23:47 ein `#4` um 01:48, 273
Instagram-Auswertungsfehler, 354 Pinterest-Fehler. `x-app-usage` lag bis 01:42
bei 8–16 %, stieg bis 01:47 auf 72 % und um 01:48 auf 100 %; das App-Budget
entspricht damit rund 500 Aufrufen je Stunde.

Ursache: Die Zustandsprüfung (alle 6 h, 23 Konten) speichert jedes Konto mit
`oauth_access_token` in `update_fields`, `refresh_oauth_token` ebenso (YouTube,
Google Business stündlich). `apps/analytics/signals.py` hielt jede solche
Speicherung für einen Reconnect und startete `backfill_account_analytics`, das
alle Beiträge der letzten 90 Tage ohne Takt, Story-Fenster und Fehler-Backoff
abfragte: 23 Backfills um 01:3x, 253 Instagram-Aufrufe auf tote Storys und 226
Facebook-Insights-Aufrufe in zehn Minuten. Das ist das 6-h-Muster aus Abschnitt 2
(01, 07, 13, 19 UTC). Die weiche Drossel griff nicht, weil die Header nur bei
erfolgreichen Antworten gelesen wurden; die Welle bestand aus 400ern.

Behebung:

- Backfill nur bei neuem Konto, OAuth-Reconnect (`_create_or_update_account`
  schreibt `analytics_needs_reconnect` mit) oder neuem Token für ein nicht
  verbundenes Konto, nie bei bloßer Token-Erneuerung.
- `backfill_account_analytics` nutzt `_post_cadence_due` wie der Stundenlauf und
  pausiert bei jeder Drossel; Kontowerte nur, wenn für heute noch keine Zeile da ist.
- `providers/base.py`: Auslastung aus jeder Meta-Antwort (auch 4xx), weiche Drossel
  ab 60 % statt 85 %.
- Pinterest: `start_date` heute minus 89 Tage, `end_date` heute (vorher
  2020-01-01 bis 2099-12-31, jeder Pin scheiterte stündlich); Summen aus
  `summary_metrics`.

Andere Nutzer derselben Meta-App (Tokens aus dem Verteiler): `iyc-highlight-takt`
(4× täglich), `iyc-kommentar-agent lauf` (6× täglich), `claudy-agenten
meta_token.py`; zusammen wenige Prozent. Tests:
`apps/analytics/tests/test_meta_restverbrauch.py`.
