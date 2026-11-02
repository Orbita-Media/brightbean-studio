"""Instagram-Personenmarkierungen (``user_tags``) auf Bildern und Karussell-Folien.

Festgehalten wird, was leicht falsch geht:

* das Feld fährt als JSON-*String* am Container mit (wie ``collaborators``),
* im Karussell sitzt es am KIND der jeweiligen Folie, nie am Eltern-Container,
  und jede Folie trägt nur ihre eigenen Markierungen,
* Reel, Video-Folie und Story nehmen keine Markierungen – sie entfallen mit
  einer Logzeile, der Beitrag geht trotzdem raus,
* lehnt Instagram eine Markierung ab (privates oder unbekanntes Konto), geht
  das Bild ohne sie raus und ``user_tags_dropped`` steht im Ergebnis,
* Beiträge ohne Markierungen senden das Feld gar nicht (unverändert wie vorher).
"""

import json
from unittest.mock import MagicMock

from providers.exceptions import APIError
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.instagram_user_tags import (
    MAX_USER_TAGS_PER_IMAGE,
    normalize_user_tags,
    user_tags_for_slide,
)
from providers.types import PostType, PublishContent

TEAM_TAGS = [
    {"slide": 1, "username": "stefanschott_", "x": 0.76, "y": 0.38},
    {"slide": 1, "username": "tobinstar", "x": 0.76, "y": 0.69},
    {"slide": 3, "username": "stefanschott_", "x": 0.62, "y": 0.26},
]


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _provider():
    return InstagramProvider({"client_id": "id", "client_secret": "secret"})


def _login_provider():
    return InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})


def _content(post_type, media_urls, **extra):
    return PublishContent(
        text="Caption",
        media_urls=media_urls,
        post_type=post_type,
        extra={"ig_user_id": "ig-1", **extra},
    )


def _single_flow(provider):
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )


def _carousel_flow(provider, children):
    steps = []
    for n in range(children):
        steps += [_resp({"id": f"child-{n + 1}"}), _resp({"status_code": "FINISHED"})]
    steps += [_resp({"id": "carousel-1"}), _resp({"status_code": "FINISHED"}), _resp({"id": "media-1"})]
    provider._request = MagicMock(side_effect=steps)


def _posts(provider):
    return [c for c in provider._request.call_args_list if c.args and c.args[0] == "POST"]


# ---------------------------------------------------------------------------
# Normalisieren
# ---------------------------------------------------------------------------


def test_keine_markierungen_ist_der_normalfall():
    assert normalize_user_tags(None) == []
    assert normalize_user_tags([]) == []
    assert normalize_user_tags("stefanschott_") == []
    assert user_tags_for_slide(None, 0) is None
    assert user_tags_for_slide({}, 0) is None
    assert user_tags_for_slide({"user_tags": TEAM_TAGS}, 1) is None


def test_at_zeichen_wird_abgeschnitten_und_koordinaten_geklemmt():
    tags = normalize_user_tags([{"slide": "2", "username": " @Name.x ", "x": 1.7, "y": -0.2}])
    assert tags == [{"slide": 2, "username": "Name.x", "x": 1.0, "y": 0.0}]


def test_unbrauchbare_eintraege_fallen_weg_ohne_fehler():
    tags = normalize_user_tags(
        [
            {"slide": 0, "username": "a", "x": 0.5, "y": 0.5},
            {"slide": 1, "username": "mit leerzeichen", "x": 0.5, "y": 0.5},
            {"slide": 1, "username": "ok", "x": "nix", "y": 0.5},
            {"slide": 1, "username": "OK", "x": 0.5, "y": 0.5},
            {"slide": 1, "username": "ok", "x": 0.1, "y": 0.1},
            "kaputt",
        ]
    )
    # Derselbe Name auf derselben Folie zählt einmal, der erste gewinnt.
    assert tags == [{"slide": 1, "username": "OK", "x": 0.5, "y": 0.5}]


def test_folie_zaehlt_ab_eins_index_ab_null():
    extra = {"user_tags": TEAM_TAGS}
    assert user_tags_for_slide(extra, 0) == [
        {"username": "stefanschott_", "x": 0.76, "y": 0.38},
        {"username": "tobinstar", "x": 0.76, "y": 0.69},
    ]
    assert user_tags_for_slide(extra, 2) == [{"username": "stefanschott_", "x": 0.62, "y": 0.26}]


def test_mehr_als_zwanzig_werden_gekuerzt(caplog):
    viele = [{"slide": 1, "username": f"n{i}", "x": 0.5, "y": 0.5} for i in range(25)]
    with caplog.at_level("WARNING"):
        tags = user_tags_for_slide({"user_tags": viele}, 0)
    assert len(tags) == MAX_USER_TAGS_PER_IMAGE
    assert "n24" in caplog.text


# ---------------------------------------------------------------------------
# Facebook-Login-Weg (InstagramProvider)
# ---------------------------------------------------------------------------


def test_einzelbild_traegt_markierungen_als_json_string():
    provider = _provider()
    _single_flow(provider)

    provider.publish_post(
        "page-token",
        _content(PostType.IMAGE, ["https://cdn.example.com/1.jpg"], user_tags=TEAM_TAGS),
    )

    payload = _posts(provider)[0].kwargs["json"]
    assert isinstance(payload["user_tags"], str)
    assert json.loads(payload["user_tags"]) == [
        {"username": "stefanschott_", "x": 0.76, "y": 0.38},
        {"username": "tobinstar", "x": 0.76, "y": 0.69},
    ]


def test_beitrag_ohne_markierungen_sendet_das_feld_nicht():
    provider = _provider()
    _single_flow(provider)
    provider.publish_post("page-token", _content(PostType.IMAGE, ["https://cdn.example.com/1.jpg"]))
    assert "user_tags" not in _posts(provider)[0].kwargs["json"]

    provider = _provider()
    _carousel_flow(provider, 2)
    provider.publish_post(
        "page-token",
        _content(PostType.CAROUSEL, ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"]),
    )
    assert all("user_tags" not in c.kwargs["json"] for c in _posts(provider))


def test_karussell_markiert_jede_folie_am_kind_und_nie_am_eltern_container():
    provider = _provider()
    _carousel_flow(provider, 5)

    result = provider.publish_post(
        "page-token",
        _content(
            PostType.CAROUSEL,
            [f"https://cdn.example.com/team-0{n}.jpg" for n in range(1, 6)],
            user_tags=TEAM_TAGS + [{"slide": 5, "username": "tobinstar", "x": 0.63, "y": 0.3}],
            collaborators=["akajav"],
        ),
    )

    posts = _posts(provider)
    kinder, eltern = posts[:5], posts[5]
    assert [c.kwargs["json"]["is_carousel_item"] for c in kinder] == [True] * 5
    assert json.loads(kinder[0].kwargs["json"]["user_tags"]) == [
        {"username": "stefanschott_", "x": 0.76, "y": 0.38},
        {"username": "tobinstar", "x": 0.76, "y": 0.69},
    ]
    assert "user_tags" not in kinder[1].kwargs["json"]
    assert json.loads(kinder[2].kwargs["json"]["user_tags"]) == [{"username": "stefanschott_", "x": 0.62, "y": 0.26}]
    assert "user_tags" not in kinder[3].kwargs["json"]
    assert json.loads(kinder[4].kwargs["json"]["user_tags"]) == [{"username": "tobinstar", "x": 0.63, "y": 0.3}]
    assert eltern.kwargs["json"]["media_type"] == "CAROUSEL"
    assert "user_tags" not in eltern.kwargs["json"]
    assert json.loads(eltern.kwargs["json"]["collaborators"]) == ["akajav"]
    assert not (result.extra or {}).get("user_tags_dropped")


def test_video_folie_bekommt_keine_markierung(caplog):
    provider = _provider()
    _carousel_flow(provider, 2)

    with caplog.at_level("WARNING"):
        provider.publish_post(
            "page-token",
            _content(
                PostType.CAROUSEL,
                ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.mp4"],
                user_tags=[
                    {"slide": 1, "username": "bild", "x": 0.5, "y": 0.5},
                    {"slide": 2, "username": "video", "x": 0.5, "y": 0.5},
                ],
            ),
        )

    kinder = _posts(provider)[:2]
    assert json.loads(kinder[0].kwargs["json"]["user_tags"]) == [{"username": "bild", "x": 0.5, "y": 0.5}]
    assert "user_tags" not in kinder[1].kwargs["json"]
    assert "video" in caplog.text


def test_reel_und_story_lassen_markierungen_weg_und_gehen_raus(caplog):
    for post_type, url in (
        (PostType.REEL, "https://cdn.example.com/r.mp4"),
        (PostType.STORY, "https://cdn.example.com/s.jpg"),
    ):
        provider = _provider()
        _single_flow(provider)
        with caplog.at_level("WARNING"):
            result = provider.publish_post(
                "page-token",
                _content(post_type, [url], user_tags=[{"slide": 1, "username": "jemand", "x": 0.5, "y": 0.5}]),
            )
        assert "user_tags" not in _posts(provider)[0].kwargs["json"]
        assert result.platform_post_id == "media-1"
    assert "jemand" in caplog.text


def test_abgelehnte_markierung_kostet_die_folie_nicht(caplog):
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            APIError("Instagram API error 400: user not found", platform="Instagram"),
            _resp({"id": "child-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "child-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "carousel-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    with caplog.at_level("WARNING"):
        result = provider.publish_post(
            "page-token",
            _content(
                PostType.CAROUSEL,
                ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
                user_tags=[{"slide": 1, "username": "privat", "x": 0.5, "y": 0.5}],
            ),
        )

    erster, wiederholung = _posts(provider)[0], _posts(provider)[1]
    assert "user_tags" in erster.kwargs["json"]
    assert "user_tags" not in wiederholung.kwargs["json"]
    assert wiederholung.kwargs["json"]["image_url"] == "https://cdn.example.com/1.jpg"
    assert result.platform_post_id == "media-1"
    assert result.extra.get("user_tags_dropped") is True
    assert "privat" in caplog.text


def test_scheitert_auch_die_wiederholung_kommt_der_erste_fehler():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            APIError("Instagram API error 400: erster", platform="Instagram"),
            APIError("Instagram API error 400: zweiter", platform="Instagram"),
        ]
    )
    try:
        provider.publish_post(
            "page-token",
            _content(
                PostType.IMAGE,
                ["https://cdn.example.com/1.jpg"],
                user_tags=[{"slide": 1, "username": "x", "x": 0.5, "y": 0.5}],
            ),
        )
    except APIError as exc:
        assert "erster" in str(exc)
    else:
        raise AssertionError("APIError erwartet")


def test_einzelbild_abgelehnte_markierung_wird_im_ergebnis_vermerkt():
    provider = _provider()
    provider._request = MagicMock(
        side_effect=[
            APIError("Instagram API error 400: user not found", platform="Instagram"),
            _resp({"id": "container-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )
    result = provider.publish_post(
        "page-token",
        _content(
            PostType.IMAGE,
            ["https://cdn.example.com/1.jpg"],
            user_tags=[{"slide": 1, "username": "privat", "x": 0.5, "y": 0.5}],
        ),
    )
    assert result.extra.get("user_tags_dropped") is True


# ---------------------------------------------------------------------------
# Instagram-Login-Weg (InstagramLoginProvider) – gleiche Regeln
# ---------------------------------------------------------------------------


def test_login_weg_einzelbild_und_karussell():
    provider = _login_provider()
    _single_flow(provider)
    provider.publish_post(
        "ig-token",
        _content(PostType.IMAGE, ["https://cdn.example.com/1.jpg"], user_tags=TEAM_TAGS),
    )
    assert len(json.loads(_posts(provider)[0].kwargs["json"]["user_tags"])) == 2

    provider = _login_provider()
    _carousel_flow(provider, 3)
    provider.publish_post(
        "ig-token",
        _content(
            PostType.CAROUSEL,
            [f"https://cdn.example.com/{n}.jpg" for n in range(1, 4)],
            user_tags=TEAM_TAGS,
        ),
    )
    posts = _posts(provider)
    assert len(json.loads(posts[0].kwargs["json"]["user_tags"])) == 2
    assert "user_tags" not in posts[1].kwargs["json"]
    assert json.loads(posts[2].kwargs["json"]["user_tags"]) == [{"username": "stefanschott_", "x": 0.62, "y": 0.26}]
    assert "user_tags" not in posts[3].kwargs["json"]


def test_login_weg_abgelehnte_markierung():
    provider = _login_provider()
    provider._request = MagicMock(
        side_effect=[
            APIError("Instagram API error 400: user not found", platform="Instagram"),
            _resp({"id": "container-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )
    result = provider.publish_post(
        "ig-token",
        _content(
            PostType.IMAGE,
            ["https://cdn.example.com/1.jpg"],
            user_tags=[{"slide": 1, "username": "privat", "x": 0.5, "y": 0.5}],
        ),
    )
    assert result.extra.get("user_tags_dropped") is True
