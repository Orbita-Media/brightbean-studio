# Doppelpost-Schutz bei unklarer Antwort der Plattform

**24.10.2026** – Zwei Beiträge sind doppelt erschienen, beide nach einem
automatischen neuen Versuch:

| Beitrag | Plattform | Versuch 1 | Ergebnis |
|---|---|---|---|
| PlatformPost 128a96a9 „Dein Wecker heißt Katzenverhalten“ (22.10.) | Instagram | `media_publish` antwortete 500 „An unexpected error has occurred“ | Reel war trotzdem live (12:01:15), der Retry postete um 12:03:14 erneut |
| PlatformPost f3d0b1be „In Your Corner“ (15.10.) | Facebook | Foto-Upload: „The read operation timed out“ | Foto war trotzdem live (10:00:01), der Retry postete um 10:01:04 erneut |

Ein Fehler, der **nach** dem Absenden des Veröffentlichungsaufrufs kommt, sagt
nichts darüber, ob der Beitrag erschienen ist. Der alte Ablauf behandelte ihn
wie jeden anderen Fehler und versuchte es einfach noch einmal.

## Welche Fehler als „Ausgang unklar“ gelten

Jeder Provider markiert genau den Aufruf, der einen Beitrag öffentlich macht,
mit `SocialProvider.publish_commit()` (`providers/base.py`). Ein Fehler darin
wird zu `PublishOutcomeUnknownError`, wenn er nach dem Senden liegen kann:

- Lesen oder Schreiben läuft in einen Timeout, die Verbindung bricht mitten in
  der Antwort ab,
- die Plattform antwortet 5xx oder (Meta) mit Fehlercode 1 „unknown error“
  bzw. 2 „unexpected error“, auch unter HTTP 400,
- die Erfolgsantwort ist nicht lesbar oder enthält keine Beitrags-ID.

Unverändert und wiederholbar bleiben Fehler, die belegen, dass nichts
angekommen ist oder die Plattform abgelehnt hat: keine Verbindung, DNS,
Verbindungs-Timeout, 4xx, 429.

Vorbereitende Schritte liegen bewusst außerhalb: Instagram- und
Threads-Container, Medien-Uploads (Bluesky, LinkedIn, Pinterest, Facebook-
Mehrfachfotos), die YouTube-Upload-Sitzung. Ein Container wird nie von selbst
veröffentlicht, ein Fehler dort ist weiterhin gefahrlos wiederholbar.

| Provider | Commit-Aufruf | Nachsehen möglich |
|---|---|---|
| Instagram (Facebook-Login und Instagram-Login) | `media_publish` | ja, `/media` nach Bildunterschrift (Stories nein) |
| Facebook | `/photos` (veröffentlicht), `/feed`, `/videos`, `photo_stories`, `video_stories` finish | ja, `published_posts` nach Text (Stories nein) |
| Threads | `threads_publish` | ja, `/threads` nach Text |
| Bluesky | `createRecord` | ja, `listRecords` nach Text und `createdAt` |
| YouTube | Abschluss des Video-Uploads | ja, Upload-Playlist nach Titel |
| LinkedIn, Pinterest, TikTok, X, Mastodon, DEV.to, Google Business | der jeweilige Anlage-Aufruf | nein |

## Was die Engine damit macht (`apps/publisher/engine.py`)

1. Nach `PublishOutcomeUnknownError` wartet sie kurz
   (`PUBLISHER_OUTCOME_LOOKUP_DELAY`, Standard 15 s) und fragt
   `find_published_post`: gibt es auf dem Konto seit Beginn des Versuchs
   (minus 2 min Uhrentoleranz) einen Beitrag mit genau diesem Text? IDs, die
   schon anderen Zeilen gehören, zählen nicht.
2. **Gefunden:** Der Beitrag wird mit dieser ID als veröffentlicht übernommen,
   nichts wird neu gepostet. Das Veröffentlichungsprotokoll vermerkt es.
3. **Nicht gefunden:** normaler Retry mit Backoff, aber die Zeile merkt sich in
   `outcome_unknown_since` den Beginn des ersten unklaren Versuchs. **Vor jedem
   weiteren Versuch** wird erneut nachgesehen (Fenster ab diesem Zeitpunkt);
   erst wenn auch dann nichts da ist, wird gepostet.
4. **Nicht prüfbar** (Plattform ohne Abgleich, Story, Beitrag ohne Text, zu
   viele Beiträge im Zeitfenster) oder letzter Versuch: Status FAILED mit
   „Möglicherweise schon veröffentlicht – bitte auf … prüfen“, **kein**
   automatischer neuer Versuch. Ein Mensch schaut nach und plant nur neu ein,
   wenn der Beitrag fehlt.
5. Scheitert nur das Nachsehen selbst (Netz), wird nichts gepostet; der
   nächste Versuch sieht wieder zuerst nach.

Endet eine Versuchsreihe mit einem anderen Fehler, obwohl vorher ein Versuch
unklar war, beginnt die Fehlermeldung ebenfalls mit „Möglicherweise schon
veröffentlicht“. Der Sweep für hängende Zeilen (`_settle_interrupted`) behält
`outcome_unknown_since` bei einem Wiedereinreihen, sodass auch dann zuerst
nachgesehen wird.

## Tests

- `apps/publisher/test_outcome_unknown.py` – Engine: Übernahme, Retry mit
  Nachsehen, nicht prüfbar, letzter Versuch, Sweep.
- `tests/providers/test_publish_outcome.py` – Einordnung der Fehler, Commit je
  Provider (Container-Fehler bleiben wiederholbar), Abgleich je Plattform.
