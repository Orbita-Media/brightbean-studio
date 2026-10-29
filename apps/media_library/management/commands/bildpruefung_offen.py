"""Ungeprüfte Medien an künftigen Beiträgen (docs/BILDPRUEFUNG.md).

Für die Hauptsitzung: Zahl und Liste, nach nächstem Termin sortiert.

    python manage.py bildpruefung_offen
    python manage.py bildpruefung_offen --bis 2026-11-12T00:00:00Z --json
    python manage.py bildpruefung_offen --durchgefallen
    python manage.py bildpruefung_offen --sha-rechnen     # sha256 der offenen Medien nachrechnen
"""

import json

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from apps.media_library.bildpruefung import datei_sha256, gemerkter_sha256, offene_medien, pflicht_an


class Command(BaseCommand):
    help = "Zahl und Liste der ungeprüften Medien an künftigen Beiträgen."

    def add_arguments(self, parser):
        parser.add_argument("--bis", help="ISO-Zeitpunkt: nur Beiträge mit Termin bis dahin.")
        parser.add_argument("--workspace", action="append", help="Workspace-ID (mehrfach möglich).")
        parser.add_argument("--durchgefallen", action="store_true", help="Gültig durchgefallene statt offener.")
        parser.add_argument("--json", action="store_true", help="Ausgabe als JSON.")
        parser.add_argument("--sha-rechnen", action="store_true", help="Fehlende sha256 der Liste nachrechnen.")
        parser.add_argument("--limit", type=int, default=0, help="Höchstens so viele Einträge ausgeben.")

    def handle(self, *args, **opts):
        bis = None
        if opts["bis"]:
            bis = parse_datetime(opts["bis"])
            if bis is None:
                raise CommandError("--bis muss ein ISO-Zeitpunkt sein.")
        ergebnis = offene_medien(workspace_ids=opts["workspace"], bis=bis)
        eintraege = ergebnis["durchgefallen"] if opts["durchgefallen"] else ergebnis["eintraege"]

        if opts["sha_rechnen"]:
            gerechnet = 0
            for e in eintraege:
                if gemerkter_sha256(e["asset"]) is None and datei_sha256(e["asset"]):
                    gerechnet += 1
            self.stderr.write(f"sha256 nachgerechnet: {gerechnet}")

        if opts["limit"]:
            eintraege = eintraege[: opts["limit"]]

        if opts["json"]:
            self.stdout.write(
                json.dumps(
                    {
                        "pflicht_an": pflicht_an(),
                        "anzahl": ergebnis["anzahl"],
                        "durchgefallen_anzahl": len(ergebnis["durchgefallen"]),
                        "items": [_als_dict(e) for e in eintraege],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        self.stdout.write(
            f"Schalter bildpruefung_pflicht: {'AN' if pflicht_an() else 'AUS'} | offen: {ergebnis['anzahl']} | "
            f"gültig durchgefallen: {len(ergebnis['durchgefallen'])}"
        )
        for e in eintraege:
            termin = e["termin"].strftime("%d.%m.%Y %H:%M UTC") if e["termin"] else "ohne Termin"
            stand = e["stand"]
            zusatz = "; ".join(stand.befunde) if stand.durchgefallen else (stand.grund or "")
            self.stdout.write(
                f"{termin:>20}  {e['asset'].id}  {e['asset'].media_type:<5}  {e['asset'].filename}  "
                f"[{', '.join(e['plattformen'])}]  {zusatz}"
            )


def _als_dict(e) -> dict:
    asset = e["asset"]
    return {
        "id": str(asset.id),
        "media_type": asset.media_type,
        "filename": asset.filename,
        "sha256": gemerkter_sha256(asset),
        "termin": e["termin"].isoformat() if e["termin"] else None,
        "plattformen": e["plattformen"],
        "workspace_id": str(e["workspace_id"]),
        "beitragstext": e["beitragstext"],
        "status": e["stand"].status,
        "grund": e["stand"].grund,
        "befunde": e["stand"].befunde,
        "beitraege": [
            {"post_id": str(b["post_id"]), "termin": b["termin"].isoformat() if b["termin"] else None}
            for b in e["beitraege"]
        ],
    }
