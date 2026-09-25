# Threads – Verbindung des Verlagskontos

Stand: 25.09.2026. Kanal `threads`, Konto `@orbitamedia_verlag`.

## Warum eine eigene Meta-App

Die App „Orbita Social“ (1062167552935661) ist eine Business-App aus dem alten
Produkt-System. Threads lässt sich dort weder als Produkt noch als
Anwendungsfall hinzufügen: Im Anlege-Assistenten schliesst „Auf Threads API
zugreifen“ den Anwendungsfall „Facebook Login“ aus („Manche Anwendungsfälle
können nicht für dieselbe App kombiniert werden“). Deshalb läuft Threads über
eine zweite App:

| Angabe | Wert |
|---|---|
| App-Name | Orbita Threads |
| Meta-App-ID | `2044954146154797` |
| Threads-App-ID | `2275328533214171` (die gehört in den Verteiler, nicht die Meta-App-ID) |
| Business-Portfolio | Orbita Media GmbH (972603227800566, verifiziert) |
| Modus | unveröffentlicht (Entwicklung) – reicht, weil nur das eigene Konto postet |
| Berechtigungen | `threads_basic`, `threads_content_publish`, `threads_manage_insights`, `threads_manage_replies`, `threads_read_replies`, `threads_delete` |
| Redirect-Callback | `https://social.orbita-media.de/social-accounts/callback/threads/` |
| Threads-Tester | `@orbitamedia_verlag`, Einladung am 25.09.2026 angenommen |

Zugangsdaten: Vault-Eintrag **„Meta App Orbita Threads (Threads-API)“**
(Benutzer = Threads-App-ID, Passwort = Threads-Geheimnis).

## Umgebungsvariablen (Coolify-App xos84sccocw488o8kccow88g)

- `PLATFORM_THREADS_APP_ID`
- `PLATFORM_THREADS_APP_SECRET`

Ohne beide bietet der Verteiler Threads gar nicht erst an
(`config/settings/base.py`, kein Rückfall auf die Facebook-Kennung).

## Fallen

- **Das Geheimnis zeigt Meta erst nach zwei Bestätigungen.** „Anzeigen“ öffnet
  „Bitte gib dein Passwort erneut ein“; danach kommt ein zweiter Dialog „Mit
  Passwort bestätigen“, der ebenfalls bestätigt werden muss. Erst dann liefert
  `async/threads-login/app-secret/` den Wert. Wer nach dem ersten Dialog
  nachsieht, sieht weiter Punkte.
- **Tester-Einladung ohne Benachrichtigung.** Annehmen nur auf threads.com unter
  Einstellungen → Website-Berechtigungen → Einladungen. Ohne Annahme scheitert
  die Anmeldung im Entwicklungsmodus.
- **500 Zeichen, Emojis zählen nach UTF-8-Bytes** („Emojis are counted as the
  number of UTF-8 bytes“, developers.facebook.com/docs/threads/posts). Der
  Provider kürzt hart auf 500 Codepunkte; die Kurzfassung baut deshalb das
  Content-Tool (`src/lib/verteiler-fassungen.ts`, `threadsFassung`).
- **Ein Themen-Tag je Beitrag**: „the first valid tag included in a post … is
  treated as the tag for that post“. Mehr als ein Hashtag ist auf Threads
  nutzlos.
