"""Tests für apps.common.homoglyphs.

Der Anlass steht in den Tests: „übersprингst" ist der echte Text aus einem
fertigen Beitrag, и/н/г sind kyrillisch. Ebenso wichtig wie das Finden ist das
NICHT-Finden – ein Prüfer, der bei jedem Emoji anschlägt, wird abgeschaltet.
"""

from django.test import SimpleTestCase

from apps.common.homoglyphs import beanstandungen, bereinige, pruefe

ECHTER_FALL = "Wenn du das übersprингst, verlierst du den Anschluss."


class FindetDenEchtenFallTest(SimpleTestCase):
    def test_findet_alle_drei_kyrillischen_zeichen(self):
        funde = pruefe(ECHTER_FALL)

        self.assertEqual([f.zeichen for f in funde], ["и", "н", "г"])
        self.assertEqual([f.codepoint for f in funde], ["U+0438", "U+043D", "U+0433"])

    def test_meldet_die_stelle_konkret_und_nicht_nur_dass_etwas_da_ist(self):
        fund = pruefe(ECHTER_FALL)[0]

        self.assertEqual(fund.art, "gemischt")
        self.assertEqual(fund.schrift, "CYRILLIC")
        self.assertEqual(fund.zeichenname, "CYRILLIC SMALL LETTER I")
        self.assertEqual(fund.wort, "übersprингst")
        self.assertEqual(fund.vorschlag, "i")
        self.assertEqual(ECHTER_FALL[fund.position], "и")

    def test_stellt_das_wort_wieder_her(self):
        neu, ersetzt, offen = bereinige(ECHTER_FALL)

        self.assertEqual(neu, "Wenn du das überspringst, verlierst du den Anschluss.")
        self.assertEqual(len(ersetzt), 3)
        self.assertEqual(offen, [])

    def test_die_meldung_nennt_zeichen_position_und_vorschlag(self):
        text = str(pruefe(ECHTER_FALL)[2])

        self.assertIn("Position", text)
        self.assertIn("U+0433", text)
        self.assertIn("übersprингst", text)
        self.assertIn("g", text)


class LaesstEchtenTextInRuheTest(SimpleTestCase):
    def test_deutscher_text_mit_umlauten_und_satzzeichen(self):
        text = 'Größe, Übung, Straße – „so geht das" … 30 % in 5 Jahren; vgl. S. 12 (Abb. 3)!'

        self.assertEqual(pruefe(text), [])

    def test_emoji_inklusive_zusammengesetzter_familien_und_hautton(self):
        text = "Los geht es 🚀 mit 👍🏽 und 👩‍👩‍👧‍👦 sowie ☕️ ✓ ★ © ™ € °"

        self.assertEqual(pruefe(text), [])

    def test_fremdsprachige_namen_in_lateinischer_schrift(self):
        text = "Café, naïve, Señor, Œuvre, Jokūbas, Håkan, Łukasz"

        self.assertEqual(pruefe(text), [])

    def test_adressen_und_bindestrichwoerter_zerfallen_nicht(self):
        text = "E-Mail an kontakt@orbita-media.de, https://orbita-media.de/buch"

        self.assertEqual(pruefe(text), [])

    def test_leerer_text(self):
        self.assertEqual(pruefe(""), [])


class UnterscheidetMangelVonZitatTest(SimpleTestCase):
    def test_ein_wort_ganz_in_fremder_schrift_ist_nur_ein_hinweis(self):
        funde = pruefe("Ein Zitat: καλημέρα κόσμε")

        self.assertTrue(funde)
        self.assertTrue(all(f.art == "fremdschrift" for f in funde))
        self.assertEqual(beanstandungen("Ein Zitat: καλημέρα κόσμε"), [])

    def test_ein_zitat_wird_nicht_umgeschrieben(self):
        text = "Ein Zitat: καλημέρα"

        neu, ersetzt, offen = bereinige(text)

        self.assertEqual(neu, text)
        self.assertEqual(ersetzt, [])

    def test_am_zitat_steht_kein_vorschlag_der_zum_zerstoeren_einlaedt(self):
        funde = pruefe("日本語 im Zitat")

        self.assertTrue(all(f.vorschlag == "" for f in funde))

    def test_gemischtes_wort_ist_immer_ein_mangel(self):
        funde = beanstandungen("Der Preis ist heiss")  # rein lateinisch
        self.assertEqual(funde, [])

        funde = beanstandungen("Der Prеis ist heiss")  # е ist kyrillisch
        self.assertEqual(len(funde), 1)
        self.assertEqual(funde[0].art, "gemischt")


class FindetUnsichtbareZeichenTest(SimpleTestCase):
    def test_zero_width_space(self):
        funde = beanstandungen("Zero​Width")

        self.assertEqual(len(funde), 1)
        self.assertEqual(funde[0].art, "unsichtbar")
        self.assertEqual(funde[0].codepoint, "U+200B")

    def test_weiches_trennzeichen_und_richtungsmarke(self):
        funde = beanstandungen("Soft­Hyphen‮")

        self.assertEqual([f.codepoint for f in funde], ["U+00AD", "U+202E"])

    def test_werden_ersatzlos_entfernt(self):
        neu, ersetzt, offen = bereinige("Zero​Width­")

        self.assertEqual(neu, "ZeroWidth")
        self.assertEqual(len(ersetzt), 2)

    def test_zeilenumbruch_und_tabulator_sind_kein_fund(self):
        self.assertEqual(pruefe("Zeile eins\nZeile zwei\tmit Tabulator\r\n"), [])

    def test_emoji_verbinder_bleibt_unangetastet(self):
        # Der Zero Width Joiner haelt zusammengesetzte Emoji zusammen. Wer ihn
        # entfernt, macht aus einer Familie vier Einzelfiguren.
        text = "Familie 👩‍👩‍👧‍👦"

        neu, ersetzt, offen = bereinige(text)

        self.assertEqual(neu, text)
        self.assertEqual(ersetzt, [])


class FindetVerkleideteLateinischeZeichenTest(SimpleTestCase):
    def test_breitformen(self):
        neu, ersetzt, offen = bereinige("ｆｕｌｌｗｉｄｔｈ")

        self.assertEqual(neu, "fullwidth")
        self.assertEqual(len(ersetzt), 9)

    def test_mathematische_alphabete(self):
        neu, _, _ = bereinige("𝐁𝐨𝐥𝐝 im Text")

        self.assertEqual(neu, "Bold im Text")


class RepariertNurEindeutigesTest(SimpleTestCase):
    def test_kyrillischer_buchstabe_ohne_ein_zeichen_umschrift_bleibt_stehen(self):
        # ж hat keine eindeutige lateinische Entsprechung (zh). Lieber melden
        # als raten.
        text = "Wir maжchen das"

        neu, ersetzt, offen = bereinige(text)

        self.assertEqual(neu, text)
        self.assertEqual(ersetzt, [])
        self.assertEqual(len(offen), 1)
        self.assertEqual(offen[0].zeichen, "ж")
        self.assertEqual(offen[0].vorschlag, "")

    def test_grossbuchstaben_werden_gross_ersetzt(self):
        neu, _, _ = bereinige("Der РReis")  # Р ist kyrillisch

        self.assertEqual(neu, "Der RReis")

    def test_mehrere_woerter_im_selben_text(self):
        # „ersте" traegt zwei kyrillische Zeichen (т, е), „zweitе" eines.
        text = "Das ersте Wort und das zweitе Wort"

        neu, ersetzt, offen = bereinige(text)

        self.assertEqual(neu, "Das erste Wort und das zweite Wort")
        self.assertEqual([f.zeichen for f in ersetzt], ["т", "е", "е"])
        self.assertEqual(offen, [])

    def test_positionen_bleiben_stabil_wenn_nichts_entfernt_wird(self):
        neu, _, _ = bereinige(ECHTER_FALL)

        self.assertEqual(len(neu), len(ECHTER_FALL))
