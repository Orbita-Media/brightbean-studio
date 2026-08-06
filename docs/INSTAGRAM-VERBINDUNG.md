# Instagram verbinden – was der Graph wirklich sagt

Warum dieser Text existiert: Am 06.08.2026 lief die Anbindung von
`@orbitamedia_verlag` mehrfach vollständig durch – Facebook-Anmeldung erfolgreich,
„Noah Malik wurde mit Orbita Social verknüpft" – und endete trotzdem in der
Kanalliste mit „No Instagram Business accounts were found for your account".
Die Meldung nannte eine mögliche Ursache; welche es tatsächlich war, stand
nirgends. Genau diese Lücke schliesst dieser Text.

## Der Ablauf, Stelle für Stelle

1. `apps/social_accounts/views.py` → `connect_platform` schickt zu Facebook.
   Angefragte Berechtigungen: `providers/instagram.py` → `required_scopes`
   (`instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`,
   `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement`).
2. Zurück im `oauth_callback` wird der Code gegen ein Nutzertoken getauscht.
3. `providers/instagram.py` → `get_user_pages` fragt
   `GET /me/accounts?fields=…,instagram_business_account{…}` ab.
4. **Jede Seite ohne `instagram_business_account` fällt raus.** Bleibt nichts
   übrig, sieht der Nutzer die Warnung und landet wieder in der Kanalliste.

Der Weg über die Seite ist keine Eigenheit dieses Werkzeugs, sondern der
dokumentierte: „GET /{page-id}?fields=instagram_business_account" liefert die
Instagram-Kennung, und dafür braucht es „An Instagram Business Account or
Instagram Creator Account" plus „A Facebook Page connected to that account"
([Instagram API with Facebook Login, Get
Started](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/get-started)).

## Die Diagnose im Fehlerfall

Seit dem 06.08.2026 erhebt der Rückweg bei leerem Ergebnis selbst, woran es
liegt: `providers/meta_diagnostics.py`, aufgerufen über
`InstagramProvider.diagnose_pages` bzw. `FacebookProvider.diagnose_pages` aus
`apps/social_accounts/views.py` → `_run_page_diagnostics`.

Erhoben wird:

| Abfrage | Was sie beantwortet |
|---|---|
| `GET /me/permissions` | Welche Berechtigungen wurden **erteilt**, welche abgelehnt? |
| `GET /debug_token` | Welche Ziele hat der Nutzer im Dialog **ausgewählt** (`granular_scopes`)? Eine erteilte Berechtigung ohne Ziel-ID ist der Unterschied zwischen „zugestimmt" und „auch etwas ausgewählt". |
| `GET /me/accounts` | Wie viele Seiten kommen zurück, und trägt eine davon ein Instagram-Konto? |

Kein Zugangstoken landet im Protokoll – weder ganz noch gekürzt. Enthalten sind
nur Kennungen, Seitennamen, Berechtigungsnamen und Fehlerobjekte.

Abrufen auf dem Server:

```bash
ssh -i ~/.ssh/noah_desktop_hetzner root@5.75.158.30 \
  "docker logs --since 30m app-xos84sccocw488o8kccow88g-024611249478 2>&1 \
   | grep 'OAuth connect returned no accounts'"
```

## Die drei Fälle und was jeweils zu tun ist

**`verdict: no_pages`** – `/me/accounts` liefert keine einzige Seite. Dann fehlt
`pages_show_list`, oder im Anmeldedialog wurde im Schritt „Seiten" nichts
ausgewählt, oder der Nutzer hat auf der Seite keine Rolle. `granular_scopes`
zeigt es: steht dort `pages_show_list` ohne `target_ids`, wurde zugestimmt, aber
nichts ausgewählt.

**`verdict: pages_without_instagram`** – Seiten kommen, keine trägt ein
Instagram-Konto. Dann ist die Verknüpfung im Sinne des Graph nicht gesetzt,
**egal was das Business-Portfolio unter „Verknüpfte Assets" anzeigt**. Die
Verknüpfung, die zählt, wird an der Seite selbst gesetzt: Meta Business Suite →
Einstellungen → **Verknüpfte Konten** → Instagram → **Konto verbinden**
([Meta Business Help
Center](https://www.facebook.com/business/help/connect-instagram-to-page)).
Voraussetzung: Das Instagram-Konto ist professionell (Business oder Creator) und
man ist Administrator der Seite.

**Fehlerobjekt** – dann steht der Code direkt im Protokoll (`errors[].error.code`),
zum Beispiel 190 für ein abgelaufenes Token oder 200 für eine fehlende
Berechtigung.

## Ausweichweg: `connected_instagram_account`

Der Graph führt zwei Felder für dieselbe Sache. `instagram_business_account` ist
das dokumentierte; `connected_instagram_account` ist älter, undokumentiert und
trägt in manchen Konten die Verknüpfung, die aus der Instagram-App heraus
entstanden ist. Findet der erste Weg nichts, prüft
`_accounts_via_connected_instagram` das zweite Feld – als eigener, abgesicherter
Aufruf, damit ein nicht mehr existierendes Feld nicht die ganze Anbindung
mitreisst.

Ein Treffer wird **nicht ungeprüft** angeboten: Das Feld kann auch auf ein
privates Konto zeigen, mit dem sich nichts veröffentlichen liesse. Deshalb
werden die Profilfelder des Kontos nachgeladen; antwortet es nicht mit
`username`, ist es kein professionelles Konto und wird verworfen.

## Entwicklungsmodus und erweiterter Zugriff

Die Meta-App steht auf **Entwicklung**. Das ist für die eigenen Konten kein
Hindernis: Im Entwicklungsmodus wirken alle Berechtigungen für Personen mit
einer Rolle in der App (Administrator, Entwickler, Tester) ohne App-Überprüfung.
Der erweiterte Zugriff („Advanced Access") wird erst gebraucht, wenn **fremde**
Nutzer ihre Konten anbinden sollen. Die Warnung „public_profile –
Verifizierung erforderlich" im Entwicklerbereich betrifft genau diesen Schritt
und ist für die interne Nutzung nicht zu klicken – die Unternehmensverifizierung
würde sonst unnötig angestossen.

## Threads: eigene App-Kennung

Threads gehört zwar in dieselbe Meta-App, hat aber eine eigene Kennung. Meta
wörtlich: „When creating your app there will be 2 app IDs and app secrets. For
Threads API implementation purposes, use the Threads app ID and its
corresponding app secret."
([Threads API, Get Started](https://developers.facebook.com/docs/threads/get-started))

Mit der Facebook-Kennung antwortet das Anmeldefenster von threads.com mit
`No app ID was provided in the request` (error_code 4476002) – genau der Fehler
vom 06.08.2026. Seither gilt: Ohne `PLATFORM_THREADS_APP_ID` steht Threads im
Verteiler auf „Not Configured" und nennt beim Überfahren den Grund, statt den
Nutzer auf die Fehlerseite von Meta zu schicken.

Die Kennung steht im Entwicklerbereich unter **Use cases → Access the Threads
API → API setup**. Danach `PLATFORM_THREADS_APP_ID` und
`PLATFORM_THREADS_APP_SECRET` setzen; die Rückleitadresse
`{APP_URL}/social-accounts/callback/threads/` muss dort ebenfalls eingetragen
sein.
