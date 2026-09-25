"""A worker killed mid-task must not block that task for an hour (25.09.2026)."""

from io import StringIO

import pytest
from background_task.models import Task
from django.core.management import call_command
from django.utils import timezone


@pytest.mark.django_db
def test_releases_locks_of_a_dead_worker_and_leaves_the_rest_alone():
    now = timezone.now()
    stuck = Task.objects.create(
        task_name="apps.inbox.tasks.run_inbox_sync_cycle",
        task_params="[[], {}]",
        task_hash="stuck",
        run_at=now,
        locked_by="1",
        locked_at=now,
    )
    idle = Task.objects.create(
        task_name="apps.publisher.tasks.run_publish_cycle",
        task_params="[[], {}]",
        task_hash="idle",
        run_at=now,
    )
    # With the lock in place the task is not runnable – that is the bug.
    assert not Task.objects.unlocked(now).filter(pk=stuck.pk).exists()

    out = StringIO()
    call_command("release_task_locks", stdout=out)

    stuck.refresh_from_db()
    idle.refresh_from_db()
    assert stuck.locked_by is None and stuck.locked_at is None
    assert Task.objects.unlocked(now).filter(pk=stuck.pk).exists()
    assert idle.run_at == now
    assert "1 stale task lock(s) released" in out.getvalue()
