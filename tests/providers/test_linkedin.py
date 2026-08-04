"""LinkedIn multi-image posts, document posts and the little-text escaping.

Until 05.08.2026 this provider posted exactly one image and logged the rest of
a carousel away. That was never a LinkedIn limit: the MultiImage Post API takes
2 to 20 images, and the Documents API takes a PDF of up to 300 pages, which is
the strongest carousel format LinkedIn has. Sources: docs/PLATTFORM-GRENZEN.md.
"""

from unittest.mock import MagicMock

import pytest

from providers.exceptions import PublishError
from providers.linkedin import MAX_MULTI_IMAGE, LinkedInProvider, _escape_commentary
from providers.types import PostType, PublishContent


def _provider(*, document_status: str = "AVAILABLE") -> LinkedInProvider:
    """Provider whose HTTP layer answers like LinkedIn and records every call."""
    provider = LinkedInProvider({"client_id": "id", "client_secret": "secret"})
    provider.aufrufe = []
    zaehler = {"bild": 0}

    def fake_request(method, url, **kwargs):
        provider.aufrufe.append((method, url, kwargs))
        antwort = MagicMock()
        antwort.headers = {"x-restli-id": "urn:li:share:42"}
        if url.endswith("/rest/images"):
            zaehler["bild"] += 1
            antwort.json = MagicMock(
                return_value={
                    "value": {
                        "uploadUrl": f"https://upload.test/{zaehler['bild']}",
                        "image": f"urn:li:image:bild{zaehler['bild']}",
                    }
                }
            )
        elif url.endswith("/rest/documents"):
            antwort.json = MagicMock(
                return_value={
                    "value": {
                        "uploadUrl": "https://upload.test/doc",
                        "document": "urn:li:document:doc1",
                    }
                }
            )
        elif "/rest/documents/" in url:
            antwort.json = MagicMock(return_value={"status": document_status})
        else:
            antwort.json = MagicMock(return_value={})
        return antwort

    provider._request = MagicMock(side_effect=fake_request)
    provider._upload_binary = MagicMock()
    return provider


def _posts_body(provider) -> dict:
    for method, url, kwargs in provider.aufrufe:
        if method == "POST" and url.endswith("/rest/posts"):
            return kwargs["json"]
    raise AssertionError("kein Beitrag geschrieben")


class TestMultiImagePost:
    @staticmethod
    def _carousel(anzahl=6) -> PublishContent:
        return PublishContent(
            text="Sechs Folien, ein Beitrag.",
            post_type=PostType.IMAGE,
            media_files=[f"/tmp/{i}.png" for i in range(1, anzahl + 1)],
            media_alt_texts=[f"Folie {i}" for i in range(1, anzahl + 1)],
            extra={"author": "urn:li:organization:1"},
        )

    def test_six_slides_become_one_multi_image_post(self):
        provider = _provider()

        provider.publish_post("token", self._carousel())

        body = _posts_body(provider)
        assert list(body["content"]) == ["multiImage"]
        assert len(body["content"]["multiImage"]["images"]) == 6

    def test_every_slide_keeps_its_own_alt_text_in_order(self):
        provider = _provider()

        provider.publish_post("token", self._carousel())

        images = _posts_body(provider)["content"]["multiImage"]["images"]
        assert [image["altText"] for image in images] == [f"Folie {i}" for i in range(1, 7)]
        # The URN order proves the description stayed on its own slide.
        assert [image["id"] for image in images] == [f"urn:li:image:bild{i}" for i in range(1, 7)]

    def test_a_gap_in_the_alt_texts_does_not_shift_the_rest(self):
        provider = _provider()
        content = self._carousel()
        content.media_alt_texts = ["Folie 1", "", "Folie 3", "Folie 4", "", "Folie 6"]

        provider.publish_post("token", content)

        images = _posts_body(provider)["content"]["multiImage"]["images"]
        # A slide without a description carries no altText at all rather than
        # inheriting its neighbour's.
        assert [image.get("altText") for image in images] == [
            "Folie 1",
            None,
            "Folie 3",
            "Folie 4",
            None,
            "Folie 6",
        ]

    def test_every_image_is_uploaded_before_the_post_is_created(self):
        provider = _provider()

        provider.publish_post("token", self._carousel())

        urls = [url for _method, url, _kwargs in provider.aufrufe]
        assert urls.count("https://api.linkedin.com/rest/images") == 6
        assert urls.index("https://api.linkedin.com/rest/posts") == len(urls) - 1

    def test_a_single_image_stays_a_plain_media_post(self):
        provider = _provider()

        provider.publish_post("token", self._carousel(anzahl=1))

        assert list(_posts_body(provider)["content"]) == ["media"]

    def test_twenty_slides_still_go_out(self):
        provider = _provider()

        provider.publish_post("token", self._carousel(anzahl=MAX_MULTI_IMAGE))

        assert len(_posts_body(provider)["content"]["multiImage"]["images"]) == MAX_MULTI_IMAGE

    def test_twentyone_slides_fail_before_anything_is_posted(self):
        provider = _provider()

        with pytest.raises(PublishError, match="höchstens 20 Bilder"):
            provider.publish_post("token", self._carousel(anzahl=MAX_MULTI_IMAGE + 1))

        assert not any(url.endswith("/rest/posts") for _m, url, _k in provider.aufrufe)

    def test_the_overflow_error_is_permanent(self):
        provider = _provider()

        with pytest.raises(PublishError) as fehler:
            provider.publish_post("token", self._carousel(anzahl=25))

        assert fehler.value.retryable is False

    def test_a_carousel_post_type_takes_the_same_road(self):
        provider = _provider()
        content = self._carousel()
        content.post_type = PostType.CAROUSEL

        provider.publish_post("token", content)

        assert "multiImage" in _posts_body(provider)["content"]


class TestDocumentPost:
    @staticmethod
    def _document(name="/tmp/karussell.pdf", titel=None) -> PublishContent:
        return PublishContent(
            text="Die Kurzfassung als Dokument.",
            title=titel,
            post_type=PostType.DOCUMENT,
            media_files=[name],
            extra={"author": "urn:li:organization:1"},
        )

    def test_a_pdf_becomes_a_document_post(self):
        provider = _provider()

        provider.publish_post("token", self._document())

        body = _posts_body(provider)
        assert body["content"]["media"]["id"] == "urn:li:document:doc1"

    def test_the_file_name_stands_in_when_no_title_is_given(self):
        # title is a required field for documents; a post without one is
        # rejected, and the reader shows it above the pages.
        provider = _provider()

        provider.publish_post("token", self._document())

        assert _posts_body(provider)["content"]["media"]["title"] == "karussell.pdf"

    def test_an_explicit_title_wins(self):
        provider = _provider()

        provider.publish_post("token", self._document(titel="Sechs Wege aus dem Grübeln"))

        assert _posts_body(provider)["content"]["media"]["title"] == "Sechs Wege aus dem Grübeln"

    def test_a_pdf_attachment_is_recognised_without_an_explicit_post_type(self):
        provider = _provider()
        content = self._document()
        content.post_type = PostType.IMAGE

        provider.publish_post("token", content)

        assert "media" in _posts_body(provider)["content"]
        assert _posts_body(provider)["content"]["media"]["id"].startswith("urn:li:document:")

    def test_the_post_waits_until_the_document_is_processed(self):
        provider = _provider()

        provider.publish_post("token", self._document())

        urls = [url for _method, url, _kwargs in provider.aufrufe]
        status_index = next(i for i, url in enumerate(urls) if "/rest/documents/" in url)
        assert status_index < urls.index("https://api.linkedin.com/rest/posts")

    def test_a_rejected_document_fails_instead_of_posting(self):
        provider = _provider(document_status="PROCESSING_FAILED")

        with pytest.raises(PublishError, match="abgelehnt"):
            provider.publish_post("token", self._document())

        assert not any(url.endswith("/rest/posts") for _m, url, _k in provider.aufrufe)

    def test_a_png_is_refused_as_a_document(self):
        provider = _provider()
        content = self._document(name="/tmp/folie.png")
        content.post_type = PostType.DOCUMENT

        with pytest.raises(PublishError, match="nur .pdf"):
            provider.publish_post("token", content)

    def test_an_oversized_document_fails_before_the_upload(self, tmp_path, monkeypatch):
        # LinkedIn answers an oversized file with a bare 400, which reads like
        # a broken token. Better to say what is actually wrong.
        monkeypatch.setattr("providers.linkedin.MAX_DOCUMENT_BYTES", 10)
        datei = tmp_path / "zu-gross.pdf"
        datei.write_bytes(b"x" * 64)
        provider = _provider()

        with pytest.raises(PublishError, match="bis 100 MB"):
            provider.publish_post("token", self._document(name=str(datei)))

        provider._upload_binary.assert_not_called()

    def test_a_document_of_normal_size_passes_the_check(self, tmp_path):
        datei = tmp_path / "klein.pdf"
        datei.write_bytes(b"%PDF-1.4 kurz")
        provider = _provider()

        provider.publish_post("token", self._document(name=str(datei)))

        assert _posts_body(provider)["content"]["media"]["title"] == "klein.pdf"


class TestLittleTextEscaping:
    """``commentary`` is markup, not plain text."""

    def test_reserved_characters_are_escaped(self):
        assert _escape_commentary("Kapitel 3 (Teil 1)") == "Kapitel 3 \\(Teil 1\\)"

    def test_underscores_and_asterisks_survive_as_text(self):
        # Without escaping LinkedIn reads these as italics and bold and eats
        # the characters out of ordinary prose.
        assert _escape_commentary("wie_das_hier und *so*") == "wie\\_das\\_hier und \\*so\\*"

    def test_the_backslash_is_escaped_first(self):
        # Escaping it last would double-escape everything added before.
        assert _escape_commentary("a\\b(c)") == "a\\\\b\\(c\\)"

    def test_ordinary_text_is_untouched(self):
        assert _escape_commentary("Sechs Wege aus dem Grübeln.") == "Sechs Wege aus dem Grübeln."

    def test_empty_text_stays_empty(self):
        assert _escape_commentary("") == ""

    def test_the_escaping_reaches_the_post_body(self):
        provider = _provider()
        content = PublishContent(
            text="Drei Fragen (die wirklich helfen)",
            post_type=PostType.TEXT,
            extra={"author": "urn:li:organization:1"},
        )

        provider.publish_post("token", content)

        assert _posts_body(provider)["commentary"] == "Drei Fragen \\(die wirklich helfen\\)"
