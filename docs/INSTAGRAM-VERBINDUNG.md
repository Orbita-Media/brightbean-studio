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
4. Kommt dabei **keine** Seite zurück, greift der Ausweichweg über die
   Seitenkennungen im Token (`providers/meta_pages.py`, siehe unten).
5. **Jede Seite ohne `instagram_business_account` fällt raus.** Bleibt nichts
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
nur Kennungen, Seitennamen, Berechtigungsnamen und Fehlerobjekte. Zusätzlich
läuft jede Fehlermeldung durch `redact()`: Der Fehlertext eines Providers trägt
den Anfang der Antwort mit sich, und das ist die einzige Stelle, an der doch
einmal etwas mitkommen könnte, das niemand geprüft hat.

Abrufen auf dem Server:

```bash
# Der Containername enthält den Zeitstempel des letzten Deploys und ändert sich
# damit bei jedem Deploy – deshalb nicht festschreiben, sondern nachschlagen.
ssh -i ~/.ssh/noah_desktop_hetzner root@5.75.158.30 \
  'C=$(docker ps --format "{{.Names}}" | grep "^app-xos84" | head -1); \
   docker logs --since 30m $C 2>&1 | grep "OAuth connect returned no accounts"'
```

## Die fünf Fälle und was jeweils zu tun ist

**`verdict: no_pages`** – weder `/me/accounts` noch der Ausweichweg über die
Seitenkennungen im Token finden eine Seite. Dann fehlt `pages_show_list`, oder im
Anmeldedialog wurde im Schritt „Seiten" nichts ausgewählt. `granular_scopes`
zeigt es: steht dort `pages_show_list` ohne `target_ids`, wurde zugestimmt, aber
nichts ausgewählt. Stehen dort Kennungen und es kommt trotzdem nichts, liess sich
keine der genannten Seiten laden – der Grund steht dann als Warnung im Protokoll.

**`verdict: pages_without_instagram`** – Seiten kommen, keine trägt ein
Instagram-Konto, **in keinem der beiden Felder**. Dann ist die Verknüpfung im
Sinne des Graph nicht gesetzt, egal was das Business-Portfolio unter
„Verknüpfte Assets" anzeigt. Gesetzt wird sie an der Seite selbst: Meta Business
Suite → Einstellungen → **Verknüpfte Konten** → Instagram → **Konto verbinden**
([Meta Business Help
Center](https://www.facebook.com/business/help/898752960195806)).
Voraussetzung: Das Instagram-Konto ist professionell (Business oder Creator) und
man ist Administrator der Seite.

**`verdict: pages_without_instagram`, aber `tasks` ohne `MANAGE`/`CREATE_CONTENT`**
– dann fehlt nicht die Verknüpfung, sondern die Rolle. Die Referenz sagt zum
Feld `instagram_business_account`: „requires a User access token from a User who
is able to perform appropriate tasks on the Page". Ohne solche Rolle blendet Meta
das verknüpfte Konto aus, und die Antwort sieht exakt so aus, als wäre nichts
verknüpft. Abhilfe: Vollzugriff (mindestens „Inhalte") in Meta Business Suite →
Einstellungen → Personen. Die Meldung nennt diesen Fall inzwischen getrennt.

**`verdict: pages_with_instagram`, aber trotzdem nichts anzubieten** – die
Verknüpfung ist da, das Konto liess sich aber nicht bestätigen. Dann liegt es
nicht an der Verknüpfung, sondern am Konto: Ein privates Konto beantwortet keine
Profilfelder und kann nichts veröffentlichen. Umstellen in der Instagram-App →
Einstellungen und Privatsphäre → **Kontotyp und Tools** → **Zu professionellem
Konto wechseln**.

**Fehlerobjekt** – dann steht der Code direkt im Protokoll (`errors[].error.code`),
zum Beispiel 190 für ein abgelaufenes Token oder 200 für eine fehlende
Berechtigung.

## Derselbe Befund im Verbindungslink für Kunden

`apps/onboarding/views.py` ist der zweite Weg in dieselbe Anbindung: Ein Kunde
öffnet einen Verbindungslink und meldet sich selbst an. Dieser Weg fiel bei
leerer Seitenliste bisher stumm in den Standardweg – bei Facebook wurde dann das
**persönliche Profil** verbunden, mit dem sich nichts veröffentlichen lässt, bei
Instagram endete es in „Failed to connect account. Please try again." Seit dem
06.08.2026 zeigt er dieselbe konkrete Meldung wie der Verteiler und legt nichts
an. Nebeneffekt: Das Profil wird für Facebook und Instagram gar nicht mehr
abgefragt, es wurde dort ohnehin verworfen.

## Seiten aus einem Business-Portfolio – warum `/me/accounts` schwieg

Das war die tatsächliche Ursache bei `@orbitamedia_verlag`. Das Protokoll des
dritten Versuchs (06.08.2026, 19:03:01) sagte es unmissverständlich:

```
"pages":  {"count": 0, "items": []},
"token":  {"granular_scopes": {
    "instagram_basic":       ["17841466348000992"],
    "pages_show_list":       ["708768612318133", "254978271039996"],
    "pages_read_engagement": ["708768612318133", "254978271039996"]}}
```

Der Nutzer hatte im Dialog **zwei Seiten und das Instagram-Konto freigegeben** –
`granular_scopes` belegt es –, und trotzdem lieferte `/me/accounts` null Seiten.
Kein Fehlerobjekt, kein Hinweis, einfach `{"data": []}`.

**Der Grund steht in Metas Doku, nur an anderer Stelle.** Die Referenz
beschreibt die Verbindung als „Pages the User has a role **on**"
([/user/accounts](https://developers.facebook.com/docs/graph-api/reference/user/accounts/)),
und die Pages-API wird deutlicher: „This returns a list of Pages you have a role
on, including the Page category, your permissions on each Page, and the Page
access token."
([Pages, Access Tokens](https://developers.facebook.com/docs/pages/access-tokens)).
Gemeint ist die klassische **Seitenrolle**. Noahs Seiten gehören einem
Business-Portfolio, dort ist ihm „uneingeschränkter Zugriff" zugewiesen – eine
Seitenrolle im alten Sinn hat er nicht.

Dass Meta zwischen beidem trennt, ist dokumentiert – wenn auch für eine
Nachbaredge. Zur Rollen-Edge steht unter Limitations wörtlich: „This edge only
returns people who **do not belong to a business**. To find business users, query
the Page Assigned Users edge."
([Page/roles](https://developers.facebook.com/docs/graph-api/reference/page/roles/)).
Für `/me/accounts` fehlt dieser Satz. Das ist eine Lücke in der Doku, kein
anderes Verhalten.

### Der Ausweichweg

`providers/meta_pages.py`, eingehängt in `InstagramProvider.get_user_pages`,
`FacebookProvider.get_user_pages` und die Diagnose:

1. `GET /debug_token` liefert die im Dialog freigegebenen Kennungen. Genommen
   werden `pages_show_list` und `pages_read_engagement`, vereinigt und
   entdoppelt. Die `instagram_*`-Berechtigungen bleiben aussen vor: Sie tragen
   an derselben Stelle die Kennung des **Kontos**, nicht die einer Seite.
2. Jede Seite wird **einzeln** geholt: `GET /{page-id}?fields=id,name,category,
   access_token,picture,tasks,instagram_business_account{…},connected_instagram_account{…}`.
   Das ist der dokumentierte Weg für eine einzelne Seite, und `pages_read_engagement`
   reicht dafür: „For apps that have been granted the `pages_read_engagement` …
   permissions, only data owned by the Page is accessible."
   ([Graph API Reference, Page](https://developers.facebook.com/docs/graph-api/reference/page/)).
3. Scheitert eine Seite, wird sie übersprungen und protokolliert. Eine Seite, auf
   die der Zugang nicht reicht, darf die andere nicht mitnehmen.
4. Eine Feldleiter von breit nach schmal fängt ab, dass ein einzelner Feldname
   den ganzen Aufruf scheitern lässt (Fehler 100, „nonexisting field").

**Warum ausgerechnet dieser Weg und nicht `business_management`.** Die naheliegende
Alternative wären `GET /{business-id}/owned_pages` bzw. `client_pages`. Beide
verlangen die Berechtigung `business_management`, und die braucht eine
App-Überprüfung. Der Weg über die einzelne Seite kommt mit genau den
Berechtigungen aus, die bereits erteilt sind.

**Warum es für Instagram besonders gut passt.** Das Feld
`instagram_business_account` ist in der Referenz **aufgabenbasiert** formuliert
(„requires a User access token from a User who is able to perform appropriate
tasks on the Page"), nicht rollenbasiert. Wer über ein Portfolio Aufgaben auf der
Seite ausführen darf, erfüllt das.

### Der Seitenschlüssel – der eine Punkt, den die Doku offenlässt

Zum Feld `access_token` widerspricht sich Meta selbst:

| Quelle | Bedingung |
|---|---|
| [Graph API Reference, Page](https://developers.facebook.com/docs/graph-api/reference/page/) | „Only returned if the User making the request has a **role** (other than Live Contributor) on the Page." |
| [Pages, Overview](https://developers.facebook.com/docs/pages/overview) | „the app User must own or be able to perform a **Task** on the Page." |

Ob eine Portfolio-Zuweisung als „role" zählt, ist nirgends definiert. Deshalb
fordert der Code den Schlüssel notfalls einzeln nach
(`GET /{page-id}?fields=access_token`) und schreibt ins Protokoll, wenn auch das
nichts liefert. Für **Instagram** ist das kein Ausschlusskriterium: Kommt kein
Seitenschlüssel, verbindet `apps/social_accounts/views.py` → `select_account`
das Konto mit dem Nutzertoken. Für **Facebook** ist ein Seitenschlüssel dagegen
zwingend; dort erscheint dann die Meldung „the platform did not provide an
account token".

### Was im Protokoll steht

Jeder Verbindungsversuch schreibt jetzt eine Zeile, die den benutzten Weg nennt:

```
Instagram: 1 Seite(n) über granular_scopes gefunden
Instagram: 2 von 2 im Token genannten Seite(n) über den Ausweichweg granular_scopes geladen (/me/accounts war leer)
```

Steht dort `me_accounts`, lief alles über die Sammelabfrage. Steht dort
`granular_scopes`, hat der Ausweichweg gegriffen – dann ist der Zugang über ein
Business-Portfolio zugewiesen. Der Befund der Diagnose führt dasselbe unter
`pages.source`. Zugangstoken stehen in keiner dieser Zeilen.

### Wenn auch der Ausweichweg nichts findet

Dann bleibt ein Fall übrig, der von aussen wie „keine Seite" aussieht, aber
etwas anderes ist: Das Token nennt Seitenkennungen, der Graph gibt die Seiten
aber weder als Liste noch einzeln heraus. Dem Nutzer zu raten, er solle im
Dialog eine Seite anhaken, wäre dann die falsche Fährte – er hat sie ja
angehakt. Die Meldung nennt in diesem Fall die freigegebenen Kennungen und
verweist auf den Zugriff im Business-Portfolio
(`selected_page_ids` in `providers/meta_diagnostics.py`). Welche Seite genau
scheiterte und warum, steht als Warnung im Protokoll.

## Die zwei Seitenfelder – der eigentliche Stolperstein

Der Graph führt für dieselbe Verknüpfung **zwei** Felder, und die Referenz
unterscheidet sie danach, WIE die Verknüpfung entstanden ist
([Graph API Reference,
Page](https://developers.facebook.com/docs/graph-api/reference/page/)):

| Feld | Beschreibung laut Referenz |
|---|---|
| `instagram_business_account` | „Instagram account linked to page during Instagram business conversion flow" |
| `connected_instagram_account` | „Instagram account connected to page via page settings" |

Der Verbinden-Weg las bis zum 06.08.2026 nur das erste. Wer sein Konto also
nicht über den Umwandlungsablauf, sondern in den Seiteneinstellungen oder im
Business-Portfolio verbunden hat, fiel lautlos durch – die Verknüpfung war da,
nur im anderen Feld.

Seither prüft `_accounts_via_connected_instagram` das zweite Feld, wenn das
erste nichts hergibt. Der Aufruf bleibt eigenständig und abgesichert, damit ein
Feldname, den eine spätere Graph-Version nicht mehr kennt, nicht die ganze
Anbindung mitreisst.

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
