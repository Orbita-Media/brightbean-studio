"""Management-Command: API-Key für einen Workspace erzeugen.

Aufruf:
    python manage.py create_api_key --workspace "<Name oder UUID>" --name "Content Tool"
"""

import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.api.models import WorkspaceAPIKey
from apps.workspaces.models import Workspace


class Command(BaseCommand):
    help = "Erzeugt einen neuen API-Key für einen Workspace und gibt ihn aus."

    def add_arguments(self, parser):
        parser.add_argument("--workspace", required=True, help="Workspace-Name oder UUID")
        parser.add_argument("--name", required=True, help="Anzeigename des Keys, z. B. 'Content Tool'")

    def handle(self, *args, **options):
        ident = options["workspace"]
        name = options["name"]

        workspace = None
        try:
            workspace_uuid = uuid.UUID(ident)
        except ValueError:
            workspace_uuid = None
        if workspace_uuid is not None:
            workspace = Workspace.objects.filter(pk=workspace_uuid).first()

        if workspace is None:
            matches = list(Workspace.objects.filter(name__iexact=ident).select_related("organization"))
            if len(matches) > 1:
                listing = "\n".join(f"  {w.id}  {w.name} ({w.organization.name})" for w in matches)
                raise CommandError(f"Mehrere Workspaces mit diesem Namen gefunden, bitte UUID verwenden:\n{listing}")
            workspace = matches[0] if matches else None

        if workspace is None:
            available = "\n".join(f"  {w.id}  {w.name}" for w in Workspace.objects.all()[:50])
            raise CommandError(f"Workspace '{ident}' nicht gefunden. Verfügbare Workspaces:\n{available}")

        key = WorkspaceAPIKey.generate_key()
        api_key = WorkspaceAPIKey.objects.create(workspace=workspace, key=key, name=name)

        self.stdout.write(self.style.SUCCESS("API-Key erstellt."))
        self.stdout.write(f"Workspace: {workspace.name} ({workspace.id})")
        self.stdout.write(f"Key-Name:  {api_key.name}")
        self.stdout.write(f"Key:       {key}")
