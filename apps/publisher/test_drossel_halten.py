"""Beitrag unter aktiver Meta-Drossel (25.11.2026, docs/META-DROSSEL.md).

Fällt der Termin in eine harte Drossel (#4 Application request limit), wird der
Beitrag ohne Anfrage an Meta zurückgehalten: kein Versuch verbraucht, kein
Fehlschlag, keine Benachrichtigung. Nach Drosselende geht er genau einmal raus.
"""

from datetime import timedelta

from django.utils import timezone

from apps.notifications.models import Notification
from apps.publisher import drossel
from apps.publisher.models import PublishLog
from apps.publisher.test_outcome_unknown import _FakeProvider, _OutcomeBase
from providers.exceptions import RateLimitError
from providers.types import PublishResult


def _ok():
    return PublishResult(platform_post_id="ig-9", url="https://www.instagram.com/p/ig-9/", extra={})


class DrosselHaltenTest(_OutcomeBase):
    def test_harte_drossel_hält_ohne_anfrage_und_ohne_fehlschlag(self):
        from apps.composer.models import PlatformPost

        drossel.setzen("graph.facebook.com", 1800, hart=True, grund="(#4) Application request limit reached")
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 0)
        self.assertGreater(pp.next_retry_at, timezone.now() + timedelta(minutes=25))
        self.assertIn("zählt nicht als Fehlversuch", pp.publish_error)
        self.assertEqual(provider.publish_calls, [])
        self.assertEqual(PublishLog.objects.filter(platform_post=pp).count(), 0)
        self.assertEqual(Notification.objects.count(), 0)

        # Nächster Takt während der Drossel: weiter nichts.
        self._run(provider)
        self.assertEqual(provider.publish_calls, [])

    def test_nach_drosselende_genau_einmal_veröffentlicht(self):
        from apps.composer.models import PlatformPost
        from apps.publisher.models import Drossel

        drossel.setzen("graph.facebook.com", 1800, hart=True, grund="(#4)")
        pp = self._pp()
        provider = _FakeProvider(publish=[_ok()])
        self._run(provider)

        Drossel.objects.update(bis=timezone.now() - timedelta(seconds=1))
        self._age(pp, next_retry_at=timezone.now() - timedelta(seconds=1))
        self._run(provider)
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(len(provider.publish_calls), 1)
        self.assertEqual(pp.retry_count, 0)

    def test_code_4_beim_veröffentlichen_hält_statt_zu_scheitern(self):
        from apps.composer.models import PlatformPost

        pp = self._pp()
        provider = _FakeProvider(
            publish=[RateLimitError("(#4) Application request limit reached", retry_after=3600), _ok()]
        )
        self._run(provider)

        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(pp.retry_count, 0)
        self.assertEqual(Notification.objects.count(), 0)

        self._age(pp, next_retry_at=timezone.now() - timedelta(seconds=1))
        self._run(provider)
        pp.refresh_from_db()
        self.assertEqual(pp.status, PlatformPost.Status.PUBLISHED)
        self.assertEqual(len(provider.publish_calls), 2)
