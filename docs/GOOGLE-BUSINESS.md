# Google-Unternehmensprofil (Google Business Profile)

Stand 25.09.2026. Kanal `google_business`, Provider `providers/google_business.py`.

## Freigabe

Google hat den Zugriff für das Projekt 1002261931374 am 25.09.2026 freigegeben
(Fall 9-3378000040906, 300 Anfragen je Minute). Am selben Tag aktiviert und
gemessen (Service Usage API): Account Management API 300/min, Business
Information API 300/min, `mybusiness.googleapis.com` (v4, Beiträge) aktiviert.
Der Freigabe-Wächter auf dem Hetzner (`/root/gbp-freigabe-watch.py`) hat sich
danach selbst abgeschaltet.

## Was vor dem ersten Beitrag behoben wurde

Der Provider war nie gegen die echte Schnittstelle gelaufen. Drei Fehler und
zwei Lücken, alle mit Tests (`tests/providers/test_google_business.py`):

| # | Fehler | Folge vorher | Jetzt |
|---|---|---|---|
| F1 | `accounts.locations.list` ohne `readMask` (Pflichtparameter) | 400 schon beim Verbinden | `readMask=name,title,storefrontAddress,phoneNumbers`, alle Seiten, alle Konten (das erste Konto ist oft das persönliche ohne Standort) |
| F2 | Beitrag an `v4/locations/{id}/localPosts` | 404 | v4-Pfad `accounts/{a}/locations/{l}/localPosts`; das verbundene Konto speichert genau diesen Pfad als `account_platform_id`, die Engine reicht ihn als `extra["location_path"]` durch |
| F3 | Kennzahlen aus dem Beitrag selbst gelesen | immer 0 | `localPosts:reportInsights`: `LOCAL_POST_VIEWS_SEARCH` → `impressions`, `LOCAL_POST_ACTIONS_CALL_TO_ACTION` → `clicks` |
| L1 | `link_url` verworfen | kein Button | `callToAction {actionType, url}` |
| L2 | `languageCode` Vorgabe `en` | deutsche Beiträge als englisch markiert | Vorgabe `de` |

Ein Beitrag im Zustand `REJECTED` gilt als Fehler, nicht als veröffentlicht.

## Agent-API

`platform_overrides[]` für ein `google_business`-Konto:

| Feld | Bedeutung |
|---|---|
| `link_url` | Ziel des Buttons, `https://`, höchstens 2048 Zeichen |
| `gbp_cta` | Button: `SHOP` („Kaufen“), `ORDER` („Online bestellen“), `LEARN_MORE` („Weitere Informationen“, Vorgabe bei Link ohne `gbp_cta`), `BOOK` („Reservieren“), `SIGN_UP` („Anmelden“), `CALL` („Anrufen“, ohne Link) |

Einen `BUY` gibt es in der Schnittstelle nicht; „Kaufen“ im Dialog ist `SHOP`
(Discovery-Dokument `mybusiness_google_rest_v4p9.json`, `CallToAction.actionType`).
Jeder Button außer `CALL` braucht `link_url`: Einplanen ohne antwortet 422
(beim Anlegen, bei `/schedule` und wenn PATCH den Link eines eingeplanten
Beitrags entfernt). `gbp_cta` auf einem anderen Kanal antwortet 422.
Rücklesen: `GET /api/v1/posts/{id}` liefert beide Felder in `platform_overrides`.

## Was die Schnittstelle nicht kann

- **Kein Planen bei Google.** `LocalPost` hat kein Terminfeld; der Verteiler
  plant und veröffentlicht zum Termin. Im Profil erscheint der Beitrag erst dann.
- Ein Bild je Beitrag, nur über eine öffentliche Adresse (`sourceUrl`).
- Keine Kommentare, keine Hashtag-Auswertung (Inhaltsregeln im Content-Tool,
  `docs/GOOGLE-PROFIL-POSTINGPLAN.md`).
