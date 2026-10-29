"""Schalter ``bildpruefung_pflicht`` (docs/BILDPRUEFUNG.md).

An: ungeprüfte Bilder und Videos halten ihren Beitrag zurück (alle 10 Minuten
neuer Blick, 6 h nach dem Termin endgültig gescheitert). Aus (Standard):
ungeprüfte gehen raus. Ein gültig durchgefallenes Medium sperrt in beiden
Fällen. Wirkt ohne Redeploy ab dem nächsten Publisher-Takt.

    python manage.py bildpruefung_pflicht status
    python manage.py bildpruefung_pflicht an
    python manage.py bildpruefung_pflicht aus
"""

import getpass

from django.core.management.base import BaseCommand

from apps.media_library.bildpruefung import offene_medien, pflicht_an, pflicht_setzen


class Command(BaseCommand):
    help = "Bildprüfung als Pflicht vor dem Veröffentlichen ein- oder ausschalten (an | aus | status)."

    def add_arguments(self, parser):
        parser.add_argument("aktion", choices=["an", "aus", "status"])
        parser.add_argument("--von", default="", help="Wer schaltet (für die Nachvollziehbarkeit).")

    def handle(self, *args, aktion, von, **options):
        from apps.publisher.models import Schalter

        if aktion in ("an", "aus"):
            pflicht_setzen(aktion == "an", von=von or f"manage.py ({_benutzer()})")
        zeile = Schalter.objects.filter(name=Schalter.BILDPRUEFUNG_PFLICHT).first()
        self.stdout.write(f"bildpruefung_pflicht: {'AN' if pflicht_an() else 'AUS'}")
        if zeile is not None:
            self.stdout.write(
                f"  zuletzt umgeschaltet {zeile.umgeschaltet_am:%d.%m.%Y %H:%M} UTC von {zeile.umgeschaltet_von or '?'}"
            )
        else:
            self.stdout.write("  noch nie gesetzt (Standard AUS)")
        ergebnis = offene_medien()
        self.stdout.write(
            f"  offen an künftigen Beiträgen: {ergebnis['anzahl']}, "
            f"gültig durchgefallen: {len(ergebnis['durchgefallen'])}"
        )
        if aktion == "an" and ergebnis["anzahl"]:
            self.stdout.write(
                self.style.WARNING(
                    f"  Achtung: {ergebnis['anzahl']} ungeprüfte Medien halten ihre Beiträge ab sofort zurück "
                    "(manage.py bildpruefung_offen zeigt sie)."
                )
            )


def _benutzer() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "?"
