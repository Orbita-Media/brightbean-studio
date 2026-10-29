# Bildprüfung (kurz)

Kein Bild und kein Video geht ohne Bildprüfung raus. Vertrag, Ablauf, API,
Schalter und Oberfläche stehen in `docs/BILDPRUEFUNG.md` – vor jeder Änderung an
Publisher-Engine, Medien-API, `MediaAsset.bildpruefung`/`sha256` oder den
Abzeichen dort lesen.

- Gültig ist eine Prüfung nur mit passendem `sha256` der aktuellen Datei.
- `durchgefallen` sperrt immer; Ungeprüftes hält der Publisher nur bei Schalter
  `bildpruefung_pflicht` AN zurück (Versuch wird nicht verbraucht, 6 h über Termin
  endgültig).
- Schalter nur per `python manage.py bildpruefung_pflicht an|aus|status`, nie
  per Code-Default umstellen.
- Neue Veröffentlichungswege (Kanal, Titelbild, Story) müssen durch
  `_block_on_image_check` in `apps/publisher/engine.py`.
