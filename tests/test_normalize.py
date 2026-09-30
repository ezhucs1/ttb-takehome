import pytest

from labelverify.engine.normalize import (
    normalize_address,
    normalize_country,
    normalize_text,
    normalize_words,
    parse_alcohol_content,
    parse_net_contents,
)


class TestNormalizeText:
    def test_case_and_curly_apostrophes_fold_together(self):
        assert normalize_text("STONE’S THROW") == normalize_text("Stone's Throw")

    def test_punctuation_and_whitespace_collapse(self):
        assert normalize_text("  Old   Tom, Distillery!  ") == "old tom distillery"

    def test_ampersand_becomes_and(self):
        assert normalize_text("Smith & Sons") == "smith and sons"

    def test_none_and_empty(self):
        assert normalize_text(None) == ""
        assert normalize_text("") == ""

    def test_words(self):
        assert normalize_words("(1) According to the Surgeon General,") == [
            "1",
            "according",
            "to",
            "the",
            "surgeon",
            "general",
        ]


class TestParseAlcoholContent:
    @pytest.mark.parametrize(
        "text, abv, proof",
        [
            ("45% Alc./Vol. (90 Proof)", 45.0, 90.0),
            ("45% alc/vol", 45.0, None),
            ("ALC. 13.5% BY VOL.", 13.5, None),
            ("Alc. 40% by Vol.", 40.0, None),
            ("12.5% ABV", 12.5, None),
            ("5.0% ALC/VOL", 5.0, None),
            ("45", 45.0, None),
            ("45%", 45.0, None),
            ("13,5 % vol", 13.5, None),
        ],
    )
    def test_percent_forms(self, text, abv, proof):
        parsed = parse_alcohol_content(text)
        assert parsed is not None
        assert parsed.abv == abv
        assert parsed.proof == proof

    def test_proof_only_converts_to_abv(self):
        parsed = parse_alcohol_content("90 Proof")
        assert parsed is not None
        assert parsed.abv == 45.0
        assert parsed.source == "proof"

    def test_proof_symbol(self):
        parsed = parse_alcohol_content("100° proof")
        assert parsed is not None and parsed.abv == 50.0

    @pytest.mark.parametrize("text", [None, "", "not a number", "Bottled in bond"])
    def test_unparseable(self, text):
        assert parse_alcohol_content(text) is None


class TestParseNetContents:
    @pytest.mark.parametrize(
        "text, ml",
        [
            ("750 mL", 750.0),
            ("750ml", 750.0),
            ("750 ML", 750.0),
            ("0.75 L", 750.0),
            ("1 Liter", 1000.0),
            ("1.75 L", 1750.0),
            ("75 cL", 750.0),
            ("375 milliliters", 375.0),
            ("12 FL. OZ.", 354.88),
            ("12 fl oz (355 mL)", 355.0),
            ("NET CONTENTS 750 mL", 750.0),
            ("1 PINT 9.4 FL OZ", 473.18),
        ],
    )
    def test_volume_forms(self, text, ml):
        parsed = parse_net_contents(text)
        assert parsed is not None
        assert parsed.milliliters == pytest.approx(ml, abs=0.01)

    def test_metric_is_preferred_when_both_present(self):
        parsed = parse_net_contents("25.4 fl oz / 750 mL")
        assert parsed is not None
        assert parsed.milliliters == 750.0
        assert parsed.original_unit == "ml"

    @pytest.mark.parametrize("text", [None, "", "seven fifty", "750"])
    def test_unparseable(self, text):
        assert parse_net_contents(text) is None


class TestNormalizeAddress:
    def test_state_names_and_street_words_abbreviate(self):
        assert normalize_address("123 Barrel Lane, Bardstown, Kentucky 40004") == normalize_address(
            "123 BARREL LN., BARDSTOWN, KY 40004"
        )

    def test_multiword_state(self):
        assert normalize_address("Brooklyn, New York") == "brooklyn ny"


class TestNormalizeCountry:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Product of Scotland", "united kingdom"),
            ("PRODUCT OF FRANCE", "france"),
            ("Imported from Mexico", "mexico"),
            ("U.S.A.", "united states"),
            ("USA", "united states"),
            ("United States of America", "united states"),
            (None, ""),
        ],
    )
    def test_aliases(self, text, expected):
        assert normalize_country(text) == expected
