"""Instagram collaborators: the one field that puts a post in front of
somebody else's followers.

Tagging does not distribute, collaborating does. Instagram says so itself:
"If someone tags or mentions you in a photo or video, that photo or video won't
be shared with your followers. If you collaborate on a post, that post will be
shared with your followers." (https://help.instagram.com/5861247717337470)

Measured on our own account: one collaboration carried 58.5 % of the reach of
all 38 posts, against a median of 347 for everything else. That is why this
field gets its own test file.

The tests pin the four things that were easy to get wrong:

* it rides on the container as ``collaborators``, a JSON *string* (a native
  list is accepted with a 200 and silently ignored),
* it takes usernames, not numeric IDs, and tolerates a leading ``@``,
* it belongs on the parent CAROUSEL container, not on the children,
* stories do not take it ("For Feed image, Reels and Carousels only"), and a
  story must not be lost over it.
"""

import json
from unittest.mock import MagicMock

from providers.instagram import (
    MAX_COLLABORATORS,
    InstagramProvider,
    build_collaborators,
)
from providers.types import PostType, PublishContent


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _provider(**creds):
    return InstagramProvider({"client_id": "id", "client_secret": "secret", **creds})


# ---------------------------------------------------------------------------
# Building the list
# ---------------------------------------------------------------------------


def test_no_collaborators_is_the_normal_case():
    assert build_collaborators(None) is None
    assert build_collaborators({}) is None
    assert build_collaborators({"ig_user_id": "ig-1"}) is None
    assert build_collaborators({"collaborators": []}) is None
    assert build_collaborators({"collaborators": ""}) is None
    assert build_collaborators({"collaborators": ["", "  ", "@"]}) is None


def test_at_sign_and_whitespace_are_stripped():
    assert build_collaborators({"collaborators": ["@sinascolorcats", " apollo_und_cosmo "]}) == [
        "sinascolorcats",
        "apollo_und_cosmo",
    ]


def test_a_text_field_is_accepted_too():
    """An imported plan hands us one field of text, not a list."""
    assert build_collaborators({"collaborators": "@a, @b c"}) == ["a", "b", "c"]


def test_the_same_name_twice_is_collapsed_case_insensitively():
    assert build_collaborators({"collaborators": ["Autor", "@autor", "AUTOR", "zweiter"]}) == [
        "Autor",
        "zweiter",
    ]


def test_order_is_kept_because_the_first_name_is_read_first():
    assert build_collaborators({"collaborators": ["zebra", "anton"]}) == ["zebra", "anton"]


def test_a_fourth_name_is_dropped_not_sent(caplog):
    """Graph rejects the whole container for a fourth name, which would take a
    fully produced reel down with it. Dropping beats failing."""
    with caplog.at_level("WARNING"):
        names = build_collaborators({"collaborators": ["a", "b", "c", "d", "e"]})

    assert names == ["a", "b", "c"]
    assert len(names) == MAX_COLLABORATORS
    assert "d, e" in caplog.text


def test_junk_does_not_raise():
    assert build_collaborators({"collaborators": 42}) is None
    assert build_collaborators({"collaborators": [None, 0]}) is None


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def _content(post_type, media_urls, **extra):
    return PublishContent(
        text="Caption",
        media_urls=media_urls,
        post_type=post_type,
        extra={"ig_user_id": "ig-1", **extra},
    )


def _publish_flow(provider, steps=3):
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ][:steps]
    )


def test_reel_carries_collaborators_as_a_json_string():
    provider = _provider()
    _publish_flow(provider)

    provider.publish_post(
        "page-token",
        _content(PostType.REEL, ["https://cdn.example.com/reel.mp4"], collaborators=["@sinascolorcats"]),
    )

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "REELS"
    # A native list would be accepted with a 200 and silently ignored.
    assert isinstance(payload["collaborators"], str)
    assert json.loads(payload["collaborators"]) == ["sinascolorcats"]


def test_feed_image_carries_collaborators():
    provider = _provider()
    _publish_flow(provider)

    provider.publish_post(
        "page-token",
        _content(PostType.IMAGE, ["https://cdn.example.com/cover.jpg"], collaborators=["autorin"]),
    )

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert json.loads(payload["collaborators"]) == ["autorin"]


def test_post_without_collaborators_sends_no_such_field():
    provider = _provider()
    _publish_flow(provider)

    provider.publish_post("page-token", _content(PostType.IMAGE, ["https://cdn.example.com/cover.jpg"]))

    assert "collaborators" not in provider._request.call_args_list[0].kwargs["json"]


def test_story_drops_collaborators_instead_of_losing_the_story(caplog):
    provider = _provider()
    _publish_flow(provider)

    with caplog.at_level("WARNING"):
        result = provider.publish_post(
            "page-token",
            _content(PostType.STORY, ["https://cdn.example.com/story.jpg"], collaborators=["autorin"]),
        )

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "STORIES"
    assert "collaborators" not in payload
    assert "autorin" in caplog.text
    # The story itself still goes out.
    assert result.platform_post_id == "media-1"


def test_carousel_puts_collaborators_on_the_parent_not_the_children():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "child-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "child-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "carousel-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post(
        "page-token",
        _content(
            PostType.CAROUSEL,
            ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
            collaborators=["@autorin", "@verlag"],
        ),
    )

    posts = [c for c in provider._request.call_args_list if c.args and c.args[0] == "POST"]
    child_one, child_two, parent = posts[0], posts[1], posts[2]

    assert "collaborators" not in child_one.kwargs["json"]
    assert "collaborators" not in child_two.kwargs["json"]
    assert parent.kwargs["json"]["media_type"] == "CAROUSEL"
    assert json.loads(parent.kwargs["json"]["collaborators"]) == ["autorin", "verlag"]
