"""Fremdzeichen finden, die lateinisch aussehen, aber keine sind.

Anlass: Im Text eines fertigen Beitrags stand „übersprингst". Die Zeichen и, н
und г sind kyrillisch, das Wort ist im Fliesstext nicht als falsch zu erkennen
und stammt aus dem Sprachmodell, das den Text erzeugt hat. Aufgefallen ist es
nur zufällig – es gab keine Prüfung, die so etwas hätte fangen können.

Der erwartete Zeichenvorrat ist: lateinische Schrift samt deutscher Umlaute,
Ziffern, Satzzeichen, Symbole, Emoji und Leerraum. Beanstandet wird deshalb
gezielt:

``gemischt``
    Ein Wort enthält lateinische UND nicht-lateinische Buchstaben. Das ist der
    Homoglyphen-Fall und praktisch nie Absicht.
``unsichtbar``
    Steuer- oder Formatzeichen ohne Darstellung (Zero-Width-Space, weiches
    Trennzeichen, Richtungsmarken). Sie überleben jedes Lektorat.
``fremdschrift``
    Ein Wort besteht vollständig aus einer anderen Schrift. Das kann ein
    legitimes Zitat sein und wird nur gemeldet, nicht beanstandet.

Die Reparatur ersetzt ausschliesslich Buchstaben in ``gemischt``-Wörtern und
entfernt unsichtbare Zeichen. Dort steht ausser Frage, dass lateinischer Text
gemeint war. Grundlage ist die Umschrift (и→i, н→n, г→g) und nicht die optische
Ähnlichkeit, weil genau so der beobachtete Fehler aussah: das Modell setzt den
Buchstaben ein, der für denselben LAUT steht, nicht den, der gleich aussieht.
Jede Ersetzung wird einzeln zurückgemeldet und ist damit nachprüfbar.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Umschrift kyrillisch -> lateinisch, auf Einzelbuchstaben beschränkt.
# Buchstaben ohne Ein-Zeichen-Entsprechung (ж, ч, ш, щ, ю, я, ъ, ь) fehlen
# absichtlich: dort ist die Ersetzung nicht eindeutig, also wird gemeldet
# statt geraten.
KYRILLISCH_LATEIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "з": "z",
    "и": "i",
    "й": "j",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ы": "y",
    "э": "e",
    "і": "i",
    "ј": "j",
    "ѕ": "s",
    "һ": "h",
    "ԛ": "q",
    "ԝ": "w",
}

# Griechisch -> lateinisch, gleiche Regel.
GRIECHISCH_LATEIN = {
    "α": "a",
    "β": "b",
    "γ": "g",
    "δ": "d",
    "ε": "e",
    "ζ": "z",
    "η": "e",
    "ι": "i",
    "κ": "k",
    "λ": "l",
    "μ": "m",
    "ν": "n",
    "ο": "o",
    "π": "p",
    "ρ": "r",
    "σ": "s",
    "ς": "s",
    "τ": "t",
    "υ": "y",
    "φ": "f",
    "χ": "h",
    "ω": "o",
}

_UMSCHRIFT: dict[str, str] = {}
for _quelle in (KYRILLISCH_LATEIN, GRIECHISCH_LATEIN):
    for _klein, _latein in _quelle.items():
        _UMSCHRIFT[_klein] = _latein
        _gross = _klein.upper()
        if _gross != _klein:
            _UMSCHRIFT[_gross] = _latein.upper()

# Format- und Steuerzeichen, die zur Emoji-Darstellung gehören und deshalb
# erlaubt sind: Zero Width Joiner und die Variantenselektoren.
ERLAUBTE_UNSICHTBARE = {"‍", "︎", "️"}

# Zeichen, die ein Wort zusammenhalten (Bindestrich, Apostroph, Punkt in
# Abkürzungen). Ohne sie zerfiele „E-Mail" in zwei Wörter und der
# Mischschrift-Test würde stumpf.
_WORTZEICHEN = re.compile(r"[^\W_]|['’‐-―\-.]", re.UNICODE)


@dataclass(frozen=True)
class Fund:
    """Eine beanstandete Stelle, so konkret wie möglich."""

    art: str  # gemischt | unsichtbar | fremdschrift
    position: int  # Zeichenindex im geprüften Text, ab 0
    zeichen: str
    codepoint: str  # "U+0438"
    zeichenname: str  # "CYRILLIC SMALL LETTER I"
    schrift: str  # CYRILLIC, GREEK, ...
    wort: str  # das Wort, in dem die Stelle steckt
    vorschlag: str  # lateinische Entsprechung, leer wenn unbekannt

    @property
    def beanstandet(self) -> bool:
        """``fremdschrift`` ist ein Hinweis, alles andere ein Mangel."""
        return self.art in ("gemischt", "unsichtbar")

    def __str__(self) -> str:
        vorschlag = f", gemeint ist vermutlich „{self.vorschlag}“" if self.vorschlag else ""
        return (
            f"Position {self.position}: „{self.zeichen}“ ({self.codepoint} {self.zeichenname}) "
            f"in „{self.wort}“{vorschlag}"
        )


def _schrift(zeichen: str) -> str:
    """Schriftname eines Buchstabens, abgeleitet aus dem Unicode-Namen."""
    try:
        name = unicodedata.name(zeichen)
    except ValueError:
        return "UNBEKANNT"
    return name.split(" ")[0]


def _ist_buchstabe(zeichen: str) -> bool:
    return unicodedata.category(zeichen).startswith("L")


def _ist_unsichtbar(zeichen: str) -> bool:
    if zeichen in ERLAUBTE_UNSICHTBARE or zeichen in "\n\r\t":
        return False
    return unicodedata.category(zeichen) in ("Cc", "Cf", "Co", "Cn")


def _zeichenname(zeichen: str) -> str:
    try:
        return unicodedata.name(zeichen)
    except ValueError:
        return "OHNE NAMEN"


def _ist_lateinisch_normalisiert(zeichen: str) -> bool:
    """Wird das Zeichen durch Unicode-Normalisierung zu lateinischer Schrift?

    Trifft auf Breitformen (ｆ) und die mathematischen Alphabete (𝐟) zu, nicht
    auf Kyrillisch oder Griechisch – die haben keine lateinische Normalform.
    """
    normalisiert = unicodedata.normalize("NFKC", zeichen)
    if normalisiert == zeichen:
        return False
    buchstaben = [z for z in normalisiert if _ist_buchstabe(z)]
    return bool(buchstaben) and all(_schrift(z) == "LATIN" for z in buchstaben)


def _vorschlag(zeichen: str) -> str:
    """Lateinische Entsprechung: erst Umschrift, dann Unicode-Normalisierung.

    Die Normalisierung fängt die Breitformen (ａ) und die mathematischen
    Alphabete (𝐚), die ein Modell ebenfalls ausspuckt.
    """
    if zeichen in _UMSCHRIFT:
        return _UMSCHRIFT[zeichen]
    normalisiert = unicodedata.normalize("NFKC", zeichen)
    if normalisiert != zeichen and all(_schrift(z) == "LATIN" for z in normalisiert if _ist_buchstabe(z)):
        return normalisiert
    return ""


def _woerter(text: str) -> list[tuple[int, str]]:
    """Wörter mit ihrer Startposition. Ein Wort ist eine Folge von Wortzeichen."""
    treffer: list[tuple[int, str]] = []
    start: int | None = None
    for i, zeichen in enumerate(text):
        if _WORTZEICHEN.fullmatch(zeichen):
            if start is None:
                start = i
        elif start is not None:
            treffer.append((start, text[start:i]))
            start = None
    if start is not None:
        treffer.append((start, text[start:]))
    return treffer


def pruefe(text: str) -> list[Fund]:
    """Alle Funde eines Textes, in Lesereihenfolge."""
    if not text:
        return []

    funde: list[Fund] = []

    for position, zeichen in enumerate(text):
        if _ist_unsichtbar(zeichen):
            funde.append(
                Fund(
                    art="unsichtbar",
                    position=position,
                    zeichen=zeichen,
                    codepoint=f"U+{ord(zeichen):04X}",
                    zeichenname=_zeichenname(zeichen),
                    schrift="STEUERZEICHEN",
                    wort=text[max(0, position - 15) : position + 15],
                    vorschlag="",
                )
            )

    for start, wort in _woerter(text):
        buchstaben = [(i, z) for i, z in enumerate(wort) if _ist_buchstabe(z)]
        if not buchstaben:
            continue
        fremde = [(i, z) for i, z in buchstaben if _schrift(z) != "LATIN"]
        if not fremde:
            continue
        hat_lateinische = len(fremde) < len(buchstaben)
        for i, zeichen in fremde:
            # Ein Zeichen, das sich zu einem lateinischen Buchstaben
            # normalisieren lässt (Breitform ｆ, mathematisches 𝐟), IST
            # lateinischer Text in Verkleidung – auch in einem Wort, das
            # sonst kein lateinisches Zeichen enthält.
            verkleidet = _ist_lateinisch_normalisiert(zeichen)
            gemischt = hat_lateinische or verkleidet
            funde.append(
                Fund(
                    art="gemischt" if gemischt else "fremdschrift",
                    position=start + i,
                    zeichen=zeichen,
                    codepoint=f"U+{ord(zeichen):04X}",
                    zeichenname=_zeichenname(zeichen),
                    schrift=_schrift(zeichen),
                    wort=wort,
                    # Ein Vorschlag steht nur an einem echten Mangel. An einem
                    # fremdsprachigen Zitat wäre er eine Aufforderung, es
                    # kaputtzumachen.
                    vorschlag=_vorschlag(zeichen) if gemischt else "",
                )
            )

    return sorted(funde, key=lambda f: f.position)


def beanstandungen(text: str) -> list[Fund]:
    """Nur die Funde, die einen Text unbrauchbar machen."""
    return [f for f in pruefe(text) if f.beanstandet]


def bereinige(text: str) -> tuple[str, list[Fund], list[Fund]]:
    """(bereinigter Text, ersetzte Funde, offene Funde).

    Ersetzt wird nur, was eindeutig ist: Buchstaben in Wörtern, die auch
    lateinische Buchstaben enthalten, und unsichtbare Zeichen. Alles andere
    bleibt stehen und steht in der Liste der offenen Funde – lieber eine Stelle
    melden als sie falsch reparieren.
    """
    funde = pruefe(text)
    if not funde:
        return text, [], []

    zeichen = list(text)
    ersetzt: list[Fund] = []
    offen: list[Fund] = []

    for fund in funde:
        if fund.art == "unsichtbar":
            zeichen[fund.position] = ""
            ersetzt.append(fund)
        elif fund.art == "gemischt" and fund.vorschlag:
            zeichen[fund.position] = fund.vorschlag
            ersetzt.append(fund)
        else:
            offen.append(fund)

    return "".join(zeichen), ersetzt, offen
