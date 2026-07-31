"""Durchsucht den ganzen Bestand nach Fremdzeichen und repariert auf Wunsch.

Aufruf:
    python manage.py pruefe_fremdzeichen                 (nur melden)
    python manage.py pruefe_fremdzeichen --reparieren    (Eindeutiges beheben)
    python manage.py pruefe_fremdzeichen --workspace <uuid>

Geprüft werden Beitragstexte, Titel, erste Kommentare, kanalspezifische
Fassungen sowie die Alternativtexte an Mediathek und Anhang. Repariert wird nur,
was ``homoglyphs.bereinige`` als eindeutig einstuft; alles andere wird gemeldet
und bleibt stehen.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.common.homoglyphs import bereinige, pruefe
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset

# (Modell, Feldname) je Stelle, an der Text liegt, der veröffentlicht wird.
FELDER = [
    (Post, ("title", "caption", "first_comment", "internal_notes")),
    (
        PlatformPost,
        ("platform_specific_title", "platform_specific_caption", "platform_specific_first_comment"),
    ),
    (PostMedia, ("alt_text",)),
    (MediaAsset, ("alt_text", "title")),
]


class Command(BaseCommand):
    help = "Findet kyrillische/griechische Homoglyphen und unsichtbare Zeichen im Bestand."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reparieren",
            action="store_true",
            help="Eindeutige Funde ersetzen und speichern (Standard: nur melden).",
        )
        parser.add_argument(
            "--workspace",
            help="Nur einen Arbeitsbereich prüfen (UUID).",
        )

    def handle(self, *args, **opts):
        reparieren = opts["reparieren"]
        workspace = opts.get("workspace")

        geprueft = 0
        betroffen = 0
        ersetzungen = 0
        offene = 0

        for modell, felder in FELDER:
            queryset = modell.objects.all()
            if workspace:
                queryset = self._auf_workspace(modell, queryset, workspace)

            for objekt in queryset.iterator():
                treffer_am_objekt = False
                aenderungen: list[str] = []

                for feld in felder:
                    text = getattr(objekt, feld, "") or ""
                    geprueft += 1
                    funde = pruefe(text)
                    beanstandet = [f for f in funde if f.beanstandet]
                    if not beanstandet:
                        continue

                    treffer_am_objekt = True
                    self.stdout.write(f"\n{modell.__name__} {objekt.pk} . {feld}")
                    for fund in beanstandet:
                        self.stdout.write(f"   {fund}")

                    if not reparieren:
                        offene += len(beanstandet)
                        continue

                    neu, ersetzt, offen = bereinige(text)
                    if ersetzt:
                        setattr(objekt, feld, neu)
                        aenderungen.append(feld)
                        ersetzungen += len(ersetzt)
                        self.stdout.write(self.style.SUCCESS(f"   -> {neu[:120]}"))
                    for fund in offen:
                        if fund.beanstandet:
                            offene += 1
                            self.stdout.write(self.style.WARNING(f"   offen: {fund}"))

                if treffer_am_objekt:
                    betroffen += 1
                if aenderungen:
                    with transaction.atomic():
                        objekt.save(update_fields=aenderungen)

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{geprueft} Textfelder geprüft, {betroffen} Objekte betroffen, "
                f"{ersetzungen} Zeichen ersetzt, {offene} Stellen offen."
            )
        )
        if offene and not reparieren:
            self.stdout.write("Mit --reparieren werden die eindeutigen Stellen behoben.")

    @staticmethod
    def _auf_workspace(modell, queryset, workspace_id):
        """Der Weg zum Arbeitsbereich ist je Modell ein anderer."""
        pfade = {
            "Post": "workspace_id",
            "PlatformPost": "post__workspace_id",
            "PostMedia": "post__workspace_id",
            "MediaAsset": "workspace_id",
        }
        return queryset.filter(**{pfade[modell.__name__]: workspace_id})
