"""Composer and account page: the Pinterest default board.

* saving a pin without a chosen board is fine when the account has a
  default board (before, the composer refused it),
* the board lookup tells the composer which board to preselect,
* the account page stores the default board – checked against the real
  board list, name taken from Pinterest,
* the composer's Pinterest panel keeps extras it has no field for.
"""

from unittest.mock import MagicMock, patch

from django.urls import reverse

from apps.composer.models import PlatformPost, Post
from apps.composer.tests.test_instagram_audio import InstagramAudioTestsBase
from apps.social_accounts.models import SocialAccount
from apps.social_accounts.pinterest import set_default_board

BOARD = "1082834372847314271"
BOARDS = [{"id": BOARD, "name": "Soziales", "privacy": "PUBLIC"}]


class PinterestDefaultBoardTests(InstagramAudioTestsBase):
    def setUp(self):
        super().setUp()
        self.pin = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="pinterest",
            account_platform_id="pin-1",
            account_name="Orbita Pinterest",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
            oauth_access_token="tok",
        )
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="pin")
        self.pp = PlatformPost.objects.create(post=self.post, social_account=self.pin, status="draft")
        self.save_url = reverse(
            "composer:save_post_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id}
        )

    def _payload(self, **extra):
        acc = str(self.pin.id)
        payload = {
            "action": "save_draft",
            "title": "Pin",
            "caption": "pin",
            "tags": "",
            "selected_accounts": acc,
            f"pin_board_id_{acc}": "",
        }
        payload.update(extra)
        return payload

    def test_save_without_board_is_refused_without_default(self):
        response = self.client.post(self.save_url, data=self._payload())
        self.assertEqual(response.status_code, 400)
        self.assertIn("Standard-Board", response.json()["errors"]["pinterest_board"])

    def test_save_without_board_passes_with_default(self):
        set_default_board(self.pin, BOARD, "Soziales")
        response = self.client.post(self.save_url, data=self._payload())
        self.assertIn(response.status_code, (200, 204, 302), response.content[:300])

    def test_panel_keeps_extras_it_has_no_field_for(self):
        self.pp.platform_extra = {"board_id": BOARD, "thumb_offset_ms": 2000}
        self.pp.save(update_fields=["platform_extra"])
        acc = str(self.pin.id)
        self.client.post(self.save_url, data=self._payload(**{f"pin_board_id_{acc}": BOARD}))
        self.pp.refresh_from_db()
        self.assertEqual(self.pp.platform_extra["thumb_offset_ms"], 2000)
        self.assertEqual(self.pp.platform_extra["board_id"], BOARD)

    def test_board_lookup_names_the_default(self):
        set_default_board(self.pin, BOARD, "Soziales")
        provider = MagicMock()
        provider.get_boards.return_value = BOARDS
        with patch("providers.get_provider", return_value=provider):
            response = self.client.get(
                reverse(
                    "composer:pinterest_boards",
                    kwargs={"workspace_id": self.workspace.id, "account_id": self.pin.id},
                )
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["default_board_id"], BOARD)

    def _set(self, board_id):
        return self.client.post(
            reverse(
                "social_accounts:pinterest_default_board",
                kwargs={"workspace_id": self.workspace.id, "account_id": self.pin.id},
            ),
            data={"board_id": board_id, "board_name": "vom Formular"},
        )

    def test_account_page_sets_and_removes_the_default(self):
        with patch("apps.social_accounts.pinterest.fetch_boards", return_value=BOARDS):
            response = self._set(BOARD)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.pin.refresh_from_db()
        self.assertEqual(self.pin.platform_settings["pinterest_default_board_id"], BOARD)
        # The name comes from Pinterest, not from the form.
        self.assertEqual(self.pin.platform_settings["pinterest_default_board_name"], "Soziales")

        response = self._set("")
        self.assertEqual(response.status_code, 200)
        self.pin.refresh_from_db()
        self.assertEqual(self.pin.platform_settings, {})

    def test_account_page_refuses_a_foreign_board(self):
        with patch("apps.social_accounts.pinterest.fetch_boards", return_value=BOARDS):
            response = self._set("123")
        self.assertEqual(response.status_code, 400)
        self.pin.refresh_from_db()
        self.assertEqual(self.pin.platform_settings, {})

    def test_account_page_renders_the_default_board_section(self):
        response = self.client.get(reverse("social_accounts:list", kwargs={"workspace_id": self.workspace.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-pinterest-default-board")
        self.assertContains(response, "Standard-Board")
