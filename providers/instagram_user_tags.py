"""Instagram-Personenmarkierungen auf Bildern (``user_tags``).

Graph API, ``POST /{ig-user-id}/media`` (Facebook-Login) bzw. ``POST /me/media``
(Instagram-Login), Feldtabelle der IG-User-Media-Referenz:

    user_tags – "An array of public usernames and x/y coordinates for any
    public Instagram users who you want to tag in the image. Each object in
    the array should have the following information: ``username`` […],
    ``x`` – a float that indicates the x-axis position in the image, 0.0 to
    1.0 […], ``y`` – […] 0.0 to 1.0."

https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media

Wo die Markierung hingehört:

* Einzelbild: am Bild-Container.
* Karussell: am **Kind**-Container der jeweiligen Folie (``is_carousel_item``),
  nicht am Eltern-Container – genau wie ``alt_text``. Jede Folie trägt ihre
  eigenen Markierungen.
* Reel, Video-Folie und Story: nicht unterstützt. Dort wird die Markierung mit
  einer Logzeile weggelassen, statt den Beitrag zu verlieren; die Agent-API
  weist sie schon beim Anlegen ab.

Gespeichert wird in ``PlatformPost.platform_extra["user_tags"]`` als flache
Liste ``[{"slide": 1, "username": "name", "x": 0.5, "y": 0.4}, …]``. ``slide``
zählt ab 1 in der Reihenfolge der Anhänge (``PostMedia.position``), so wie ein
Mensch die Folien zählt.

Wie ``collaborators`` geht das Feld als JSON-*String* über die Leitung: Graph
packt verschachtelte Werte aus einem JSON-Körper nicht zuverlässig aus, eine
native Liste bei ``collaborators`` wurde mit 200 angenommen und still
verworfen.

Markieren verteilt den Beitrag NICHT an die Follower der Markierten, das tut
nur ``collaborators`` (https://help.instagram.com/5861247717337470). Die
Markierung sorgt dafür, dass die Person benachrichtigt wird und der Beitrag in
ihrem Reiter „Markiert“ erscheint.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

#: Schlüssel in ``PlatformPost.platform_extra``.
EXTRA_USER_TAGS = "user_tags"

#: Instagram erlaubt bis zu 20 Personenmarkierungen je Foto.
MAX_USER_TAGS_PER_IMAGE = 20

#: Instagram-Nutzernamen: Buchstaben, Ziffern, Punkt, Unterstrich, höchstens 30.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalize_username(raw) -> str:
    """``" @Name "`` → ``"Name"``; leerer String, wenn nichts übrig bleibt."""
    return str(raw or "").strip().lstrip("@").strip()


def _coordinate(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return round(max(0.0, min(number, 1.0)), 4)


def normalize_user_tags(raw) -> list[dict]:
    """Bereinigt eine gespeicherte oder übergebene Markierungsliste.

    Lässt Einträge ohne gültige Folie, ohne gültigen Namen oder ohne
    Koordinaten weg, schneidet ``@`` und Leerzeichen ab, klemmt x/y auf 0–1,
    fasst denselben Namen auf derselben Folie zusammen (Groß-/Kleinschreibung
    egal, der erste gewinnt) und behält sonst die Reihenfolge. Wirft nie.
    """
    if not raw or not isinstance(raw, list | tuple):
        return []
    tags: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            slide = int(item.get("slide"))
        except (TypeError, ValueError):
            continue
        if slide < 1:
            continue
        username = normalize_username(item.get("username"))
        if not USERNAME_RE.match(username):
            continue
        x = _coordinate(item.get("x"))
        y = _coordinate(item.get("y"))
        if x is None or y is None:
            continue
        key = (slide, username.lower())
        if key in seen:
            continue
        seen.add(key)
        tags.append({"slide": slide, "username": username, "x": x, "y": y})
    return tags


def user_tags_for_slide(extra: dict | None, index: int) -> list[dict] | None:
    """Die Markierungen der Folie ``index`` (0-basiert, wie ``media_urls``).

    Liefert die Graph-Form ``[{"username", "x", "y"}, …]`` oder ``None``, wenn
    die Folie keine trägt – der Normalfall. Mehr als 20 Markierungen werden
    gekürzt statt gesendet: Graph lehnt sonst den ganzen Container ab.
    """
    if not extra:
        return None
    slide = index + 1
    tags = [
        {"username": t["username"], "x": t["x"], "y": t["y"]}
        for t in normalize_user_tags(extra.get(EXTRA_USER_TAGS))
        if t["slide"] == slide
    ]
    if not tags:
        return None
    if len(tags) > MAX_USER_TAGS_PER_IMAGE:
        logger.warning(
            "Instagram: %d Markierungen auf Folie %d, erlaubt sind %d, %s entfallen",
            len(tags),
            slide,
            MAX_USER_TAGS_PER_IMAGE,
            ", ".join(t["username"] for t in tags[MAX_USER_TAGS_PER_IMAGE:]),
        )
        tags = tags[:MAX_USER_TAGS_PER_IMAGE]
    return tags


def user_tags_field(tags: list[dict]) -> str:
    """Das Feld so, wie es über die Leitung geht: ein JSON-String."""
    return json.dumps(tags)


def warn_unsupported(extra: dict | None, what: str, index: int = 0) -> None:
    """Logzeile für Markierungen, die dieses Medium nicht tragen kann."""
    tags = user_tags_for_slide(extra, index)
    if tags:
        logger.warning(
            "Instagram: Markierungen %s auf Folie %d weggelassen, %s nimmt keine user_tags",
            ", ".join(t["username"] for t in tags),
            index + 1,
            what,
        )


def has_user_tags(extra: dict | None) -> bool:
    return bool(normalize_user_tags((extra or {}).get(EXTRA_USER_TAGS)))
