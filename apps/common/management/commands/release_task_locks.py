"""Release background-task locks left behind by a killed worker.

Runs right before ``process_tasks`` in the worker container
(docker-compose.coolify.yml). django-background-tasks marks a running task
with ``locked_by`` (the PID) and ``locked_at``. When the previous worker is
killed mid-task – Coolify stops old containers with a hard timeout during a
deploy – that lock stays behind. The new worker sees ``locked_by = 1``, finds
PID 1 alive (itself, every container starts at PID 1) and treats the task as
running until ``BACKGROUND_TASK_MAX_RUN_TIME`` (one hour) has passed.

Seen on 25.09.2026: the inbox cycle, locked at 09:29:25 by the worker that
was removed at 09:29:47, did not run again until an hour later. The same
could hold up the publish cycle.

Only safe while exactly one worker runs and the new one starts after the old
one is gone – which is how Coolify's compose deploy works ("Removing old
containers" comes before "Container worker … Started").
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Release locks of background tasks held by a previous (dead) worker."

    def handle(self, *args, **options):
        from background_task.models import Task

        stale = Task.objects.filter(locked_by__isnull=False)
        names = list(stale.values_list("task_name", "locked_at"))
        count = stale.update(locked_by=None, locked_at=None)
        for task_name, locked_at in names:
            self.stdout.write(f"Released lock of {task_name} (locked at {locked_at})")
        self.stdout.write(f"{count} stale task lock(s) released")
