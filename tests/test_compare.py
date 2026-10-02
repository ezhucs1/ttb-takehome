import pytest

from labelverify.engine.compare import (
    compare_alcohol_content,
    compare_brand_name,
    compare_class_type,
    compare_country_of_origin,
    compare_net_contents,
    compare_producer_address,
    compare_producer_name,
    verify,
)
from labelverify.engine.models import BeverageType, Recommendation, Verdict
from tests.conftest import make_field


class TestClassTypeWrapped:
    """The filed class appears inside a longer designation on the label."""

    def test_descriptive_words_around_the_class_need_review(self, application, extraction):
        application.class_type = "Gin"
        extraction.class_type = make_field("Blood Orange Forward Gin")
        result = compare_class_type(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW
        assert "one is the other with words added" in result.reason

    def test_malt_designation_with_qualifiers(self, application, extraction):
        application.beverage_type = BeverageType.MALT_BEVERAGE
        application.class_type = "Beer"
        extraction.class_type = make_field("100% Malt Premium Beer")
        assert compare_class_type(application, extraction).verdict is Verdict.NEEDS_REVIEW

    def test_a_different_class_is_still_a_mismatch(self, application, extraction):
        application.class_type = "Stout"
        extraction.class_type = make_field("Belgian-Style Dark Strong Ale")
        assert compare_class_type(application, extraction).verdict is Verdict.MISMATCH

    def test_partial_word_does_not_count(self, application, extraction):
        application.class_type = "Ale"
        extraction.class_type = make_field("Pale Lager")  # "ale" inside "pale" is not the class
        assert compare_class_type(application, extraction).verdict is Verdict.MISMATCH

    def test_brand_with_a_word_dropped_needs_review(self, application, extraction):
        application.brand_name = "Sounds Vineyard"
        extraction.brand_name = make_field("SOUNDS")
        result = compare_brand_name(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "words added" in result.reason

    def test_producer_with_a_suffix_needs_review(self, application, extraction):
        application.producer_name = "SVP Winery"
        extraction.producer_name = make_field("SVP Winery, LLC")
        assert compare_producer_name(application, extraction).verdict is not Verdict.MISMATCH

    def test_unrelated_brand_is_still_a_mismatch(self, application, extraction):
        application.brand_name = "Kilchoman"
        extraction.brand_name = make_field("NC'NEAN")
        assert compare_brand_name(application, extraction).verdict is Verdict.MISMATCH

    def test_filed_brand_printed_as_the_second_name_matches(self, application, extraction):
        """A product name under a brewery name: the reader called the brewery the brand,
        but the filed brand is on the label, and the applicant designates the brand."""
        application.brand_name = "Groovitory"
        extraction.brand_name = make_field("twelvenote BREW CO.")
        extraction.fanciful_name = make_field("Groovitory")
        result = compare_brand_name(application, extraction)
        assert result.verdict is Verdict.MATCH
        assert "the name the applicant designates" in result.reason

    def test_filed_brand_close_to_the_second_name_needs_review(self, application, extraction):
        application.brand_name = "Groovitory"
        extraction.brand_name = make_field("twelvenote BREW CO.")
        extraction.fanciful_name = make_field("Groovitorie")
        assert compare_brand_name(application, extraction).verdict is Verdict.NEEDS_REVIEW

    def test_the_producer_name_alone_does_not_make_a_brand(self, application, extraction):
        """The bottler's name is on every label; printing it does not make it the brand."""
        application.brand_name = "Saugatuck Brewing Co."
        extraction.brand_name = make_field("SPARTAN SELECT")
        extraction.producer_name = make_field("Saugatuck Brewing Co")
        assert compare_brand_name(application, extraction).verdict is Verdict.MISMATCH


class TestBrandName:
    def test_exact(self, application, extraction):
        assert compare_brand_name(application, extraction).verdict is Verdict.MATCH

    def test_case_and_apostrophe_differences_match(self, application, extraction):
        application.brand_name = "Stone's Throw"
        extraction.brand_name = make_field("STONE’S THROW")
        result = compare_brand_name(application, extraction)
        assert result.verdict is Verdict.MATCH
        assert "capitalization" in result.reason

    def test_small_typo_needs_review(self, application, extraction):
        extraction.brand_name = make_field("OLD TOM DISTILERY")
        result = compare_brand_name(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW
        assert result.similarity is not None and result.similarity >= 75
        assert "Similar but not identical" in result.reason

    def test_different_brand_is_mismatch(self, application, extraction):
        extraction.brand_name = make_field("STONE RIDGE RESERVE")
        assert compare_brand_name(application, extraction).verdict is Verdict.MISMATCH

    def test_missing_on_label_is_mismatch(self, application, extraction):
        extraction.brand_name = make_field(None)
        result = compare_brand_name(application, extraction)
        assert result.verdict is Verdict.MISMATCH
        assert "Not found" in result.reason

    def test_reordered_words_go_to_a_human(self, application, extraction):
        application.brand_name = "Distillery Old Tom"
        assert compare_brand_name(application, extraction).verdict is Verdict.NEEDS_REVIEW

    def test_exact_text_has_plain_reason(self, application, extraction):
        assert compare_brand_name(application, extraction).reason == "Matches the application."


class TestBeverageType:
    """The product type the applicant filed against what the label's class/type implies."""

    def test_bourbon_filed_as_wine_is_a_mismatch(self, application, extraction):
        from labelverify.engine.compare import compare_beverage_type

        application.beverage_type = "wine"
        extraction.class_type = make_field("Straight Bourbon Whiskey")
        result = compare_beverage_type(application, extraction)
        assert result.verdict is Verdict.MISMATCH
        assert "reads as distilled spirits" in result.reason and "filed as wine" in result.reason
        # Both columns speak in the three commodity classes, not in the designation text.
        assert (result.application_value, result.label_value) == ("Wine", "Distilled spirits")
        assert verify(application, extraction).recommendation is Recommendation.REQUEST_CORRECTION

    @pytest.mark.parametrize(
        ("class_type", "filed"),
        [
            ("Kentucky Straight Bourbon Whiskey", "distilled_spirits"),
            ("Single Malt Scotch Whisky", "distilled_spirits"),  # malt, but a whisky
            ("Straight Rye Whiskey", "distilled_spirits"),
            ("Cabernet Sauvignon", "wine"),
            ("Rosé Wine", "wine"),
            ("Hard Cider", "wine"),
            ("India Pale Ale", "malt_beverage"),
            ("Rye Ale", "malt_beverage"),  # rye alone is not a spirit
            ("Malt Beverage with Natural Flavors", "malt_beverage"),
        ],
    )
    def test_each_category_is_recognised(self, application, extraction, class_type, filed):
        from labelverify.engine.compare import compare_beverage_type

        application.beverage_type = filed
        application.class_type = class_type
        extraction.class_type = make_field(class_type)
        assert compare_beverage_type(application, extraction).verdict is Verdict.MATCH

    def test_a_designation_naming_no_category_is_left_to_the_specialist(
        self, application, extraction
    ):
        from labelverify.engine.compare import compare_beverage_type

        application.class_type = "Special Reserve"
        extraction.class_type = make_field("Special Reserve")
        result = compare_beverage_type(application, extraction)
        assert result.verdict is Verdict.NOT_APPLICABLE
        assert result.label_value == "Not stated on the label"


class TestNetContentsByClass:
    def test_malt_beverages_may_use_fluid_ounces_and_have_no_standards_of_fill(
        self, application, extraction
    ):
        application.beverage_type = BeverageType.MALT_BEVERAGE
        application.class_type = "Lager"
        extraction.class_type = make_field("Lager")
        application.net_contents = "16 fl oz"
        extraction.net_contents = make_field("16 FL OZ")
        result = compare_net_contents(application, extraction)
        assert result.verdict is Verdict.MATCH and result.notes == []

    def test_spirits_need_a_metric_statement(self, application, extraction):
        """27 CFR 5.70: fluid ounces alone are not enough on a spirits label."""
        application.net_contents = "750 mL"
        extraction.net_contents = make_field("25.4 FL OZ")
        result = compare_net_contents(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW
        assert any("metric" in n for n in result.notes) and "5.70" in " ".join(result.notes)
        extraction.net_contents = make_field("25.4 FL OZ (750 mL)")
        assert compare_net_contents(application, extraction).verdict is Verdict.MATCH

    def test_standards_of_fill_are_advisory_and_per_class(self, application, extraction):
        application.net_contents = "600 mL"
        extraction.net_contents = make_field("600 mL")
        spirits = compare_net_contents(application, extraction)
        assert spirits.verdict is Verdict.MATCH and any(
            "standard of fill" in n for n in spirits.notes
        )
        application.beverage_type = BeverageType.WINE
        application.class_type = "Red Wine"
        extraction.class_type = make_field("Red Wine")
        wine = compare_net_contents(application, extraction)  # 600 mL is a wine size since 2025
        assert wine.verdict is Verdict.MATCH and wine.notes == []


class TestSulfiteDeclaration:
    def test_only_wine_gets_the_row_and_a_missing_statement_is_a_review_item(
        self, application, extraction
    ):
        from labelverify.engine.compare import compare_sulfite_declaration

        assert "sulfite_declaration" not in [
            f.field for f in verify(application, extraction).fields
        ]
        application.beverage_type = BeverageType.WINE
        application.class_type = "Cabernet Sauvignon"
        extraction.class_type = make_field("Cabernet Sauvignon")
        application.alcohol_content = "13.5"
        extraction.alcohol_content = make_field("13.5% Alc. by Vol.")
        fields = {f.field: f for f in verify(application, extraction).fields}
        assert fields["sulfite_declaration"].verdict is Verdict.NEEDS_REVIEW
        assert fields["sulfite_declaration"].citation == "27 CFR 4.32(e)"
        extraction.sulfite_declaration = make_field("Contains Sulfites")
        assert compare_sulfite_declaration(application, extraction).verdict is Verdict.MATCH


class TestClassResolution:
    def test_filed_type_wins_then_designation_then_reader(self, application, extraction):
        from labelverify.engine.compare import resolve_beverage_type

        assert resolve_beverage_type(application, extraction) == (
            BeverageType.DISTILLED_SPIRITS,
            "filed",
        )
        application.beverage_type = None
        assert resolve_beverage_type(application, extraction) == (
            BeverageType.DISTILLED_SPIRITS,
            "class_type",
        )
        extraction.class_type = make_field("Special Reserve")  # no category words
        extraction.product_category = make_field("wine", 0.9)
        assert resolve_beverage_type(application, extraction) == (BeverageType.WINE, "reader")
        extraction.product_category = make_field("wine", 0.3)  # too unsure to trust
        assert resolve_beverage_type(application, extraction) == (None, "unknown")

    def test_unfiled_type_is_taken_from_the_label_and_its_rules_applied(
        self, application, extraction
    ):
        application.beverage_type = None
        application.class_type = "India Pale Ale"
        extraction.class_type = make_field("India Pale Ale")
        application.alcohol_content = ""
        extraction.alcohol_content = make_field(None)
        application.net_contents = "12 fl oz"
        extraction.net_contents = make_field("12 FL OZ")
        result = verify(application, extraction)
        assert result.beverage_type is BeverageType.MALT_BEVERAGE and result.rules_part == 7
        assert result.beverage_type_inferred
        assert result.field("beverage_type").verdict is Verdict.NOT_APPLICABLE
        assert result.field("alcohol_content").verdict is Verdict.NOT_APPLICABLE  # malt rule
        assert result.field("net_contents").verdict is Verdict.MATCH  # fluid ounces allowed
        assert any("taken from the label: malt beverage" in line for line in result.summary)

    def test_unresolvable_type_is_checked_strictly_and_flagged(self, application, extraction):
        application.beverage_type = None
        application.class_type = "Special Reserve"
        extraction.class_type = make_field("Special Reserve")
        result = verify(application, extraction)
        assert result.beverage_type is None and result.rules_part == 5
        assert result.field("beverage_type").verdict is Verdict.NEEDS_REVIEW
        assert result.recommendation is Recommendation.NEEDS_REVIEW

    def test_every_row_carries_its_citation(self, application, extraction):
        result = verify(application, extraction)
        assert all(f.citation for f in result.fields)
        assert result.field("health_warning").citation == "27 CFR 16.21"
        assert result.field("alcohol_content").citation == "27 CFR 5.65"


class TestSpiritsRules:
    """Part 5 checks beyond the seven fields: all review items except bottled-in-bond proof."""

    def test_qualifying_phrase_is_required_and_imports_name_the_importer(
        self, application, extraction
    ):
        from labelverify.engine.compare import compare_qualifying_phrase

        assert compare_qualifying_phrase(application, extraction).verdict is Verdict.MATCH
        extraction.qualifying_phrase = make_field(None)
        result = compare_qualifying_phrase(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "5.66" in result.reason
        extraction.qualifying_phrase = make_field("Distilled and Bottled by")
        application.is_import = True
        application.country_of_origin = "Scotland"
        result = compare_qualifying_phrase(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "importer" in result.reason
        extraction.qualifying_phrase = make_field("Imported by")
        assert compare_qualifying_phrase(application, extraction).verdict is Verdict.MATCH

    def test_whisky_without_an_age_statement_goes_to_review(self, application, extraction):
        from labelverify.engine.compare import compare_age_statement

        assert compare_age_statement(application, extraction).verdict is Verdict.MATCH
        extraction.age_statement = make_field(None)
        result = compare_age_statement(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "5.141" in result.reason
        extraction.class_type = make_field("Vodka")  # not a whisky: no row at all
        assert compare_age_statement(application, extraction) is None
        application.beverage_type = BeverageType.WINE
        extraction.class_type = make_field("Cabernet Sauvignon")
        assert compare_age_statement(application, extraction) is None

    def test_bottled_in_bond_must_be_100_proof(self, application, extraction):
        from labelverify.engine.compare import compare_bottled_in_bond

        assert compare_bottled_in_bond(application, extraction) is None  # no claim, no row
        extraction.bottled_in_bond_claim = make_field("BOTTLED IN BOND")
        result = compare_bottled_in_bond(application, extraction)  # 45%: a hard finding
        assert result.verdict is Verdict.MISMATCH and "100 proof" in result.reason
        extraction.alcohol_content = make_field("50% Alc./Vol. (100 Proof)")
        application.alcohol_content = "50% (100 proof)"
        assert compare_bottled_in_bond(application, extraction).verdict is Verdict.MATCH
        assert verify(application, extraction).recommendation is Recommendation.APPROVE

    def test_blended_whisky_needs_a_percentage_statement(self, application, extraction):
        from labelverify.engine.compare import compare_blend_percentage

        assert compare_blend_percentage(application, extraction) is None
        application.class_type = "Blended Bourbon Whiskey"
        extraction.class_type = make_field("Blended Bourbon Whiskey")
        result = compare_blend_percentage(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "5.143" in result.reason
        extraction.blend_percentage = make_field("51% Straight Bourbon Whiskey")
        assert compare_blend_percentage(application, extraction).verdict is Verdict.MATCH


class TestClassType:
    def test_whisky_spelling_variant_matches(self, application, extraction):
        extraction.class_type = make_field("Kentucky Straight Bourbon Whisky")
        assert compare_class_type(application, extraction).verdict is Verdict.MATCH

    def test_different_class_is_mismatch(self, application, extraction):
        extraction.class_type = make_field("Blended Canadian Whisky")
        assert compare_class_type(application, extraction).verdict is Verdict.MISMATCH


class TestAlcoholContent:
    def test_percent_and_proof_forms_agree(self, application, extraction):
        application.alcohol_content = "45"
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MATCH

    def test_proof_only_on_application(self, application, extraction):
        application.alcohol_content = "90 proof"
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MATCH

    def test_different_value_is_mismatch(self, application, extraction):
        extraction.alcohol_content = make_field("40% Alc./Vol. (80 Proof)")
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.MISMATCH
        assert "45%" in result.reason and "40%" in result.reason

    def test_inconsistent_proof_on_label_is_flagged(self, application, extraction):
        extraction.alcohol_content = make_field("45% Alc./Vol. (80 Proof)")
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.MISMATCH
        assert any("proof" in n.lower() for n in result.notes)

    def test_missing_on_spirits_label_is_mismatch(self, application, extraction):
        extraction.alcohol_content = make_field(None)
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MISMATCH

    def test_wine_must_state_it_unless_it_is_a_table_wine(self, application, extraction):
        """27 CFR 4.36: required, but 'Table Wine' may replace the number at 7 to 14 percent."""
        application.beverage_type = BeverageType.WINE
        application.alcohol_content = ""
        extraction.alcohol_content = make_field(None)
        extraction.class_type = make_field("Red Wine")
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.MISMATCH and "4.36" in result.reason
        extraction.class_type = make_field("California Table Wine")
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.MATCH and "Table Wine" in result.reason
        application.alcohol_content = "12.5"
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MATCH
        application.alcohol_content = "15"  # outside the 7 to 14 band: the number is required
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MISMATCH

    def test_malt_beverages_may_omit_it(self, application, extraction):
        """27 CFR 7.65: optional federally, even when the application states a figure."""
        application.beverage_type = BeverageType.MALT_BEVERAGE
        application.class_type = "India Pale Ale"
        extraction.class_type = make_field("India Pale Ale")
        extraction.alcohol_content = make_field(None)
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.NOT_APPLICABLE and "7.65" in result.reason
        assert any("45%" in n for n in result.notes)  # the application's figure is noted
        application.alcohol_content = ""
        assert compare_alcohol_content(application, extraction).verdict is Verdict.NOT_APPLICABLE

    def test_differences_inside_the_class_tolerance_go_to_review(self, application, extraction):
        """Identical is a match; inside the labeling tolerance is a review item; beyond it is
        a mismatch. The tolerance is the class's own (27 CFR 5.65, 4.36, 7.65)."""
        extraction.alcohol_content = make_field("45.1% Alc./Vol.")  # spirits: ±0.15
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "0.15" in result.reason
        extraction.alcohol_content = make_field("45.5% Alc./Vol.")
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MISMATCH

        application.beverage_type = BeverageType.WINE
        application.class_type = "Cabernet Sauvignon"
        extraction.class_type = make_field("Cabernet Sauvignon")
        application.alcohol_content = "13"
        extraction.alcohol_content = make_field("14% Alc. by Vol.")  # wine at or under 14: ±1.5
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "1.5" in result.reason
        extraction.alcohol_content = make_field("15.5% Alc. by Vol.")  # above 14: ±1.0
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MISMATCH

    def test_proof_on_a_wine_label_is_flagged(self, application, extraction):
        application.beverage_type = BeverageType.WINE
        application.class_type = "Cabernet Sauvignon"
        extraction.class_type = make_field("Cabernet Sauvignon")
        application.alcohol_content = "13.5"
        extraction.alcohol_content = make_field("13.5% Alc./Vol. (27 Proof)")
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW
        assert any("spirits convention" in n for n in result.notes)

    def test_blank_everywhere_is_mismatch_for_spirits(self, application, extraction):
        application.alcohol_content = ""
        extraction.alcohol_content = make_field(None)
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MISMATCH

    def test_unreadable_text_needs_review(self, application, extraction):
        extraction.alcohol_content = make_field("forty five percent")
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW
        assert "forty five percent" in result.reason

    def test_label_value_with_blank_application_needs_review(self, application, extraction):
        application.alcohol_content = ""
        assert compare_alcohol_content(application, extraction).verdict is Verdict.NEEDS_REVIEW


class TestNetContents:
    def test_unit_variants_match(self, application, extraction):
        for text in ("750ml", "0.75 L", "75 cL", "750 ML"):
            extraction.net_contents = make_field(text)
            assert compare_net_contents(application, extraction).verdict is Verdict.MATCH, text

    def test_different_volume_is_mismatch(self, application, extraction):
        extraction.net_contents = make_field("1 L")
        result = compare_net_contents(application, extraction)
        assert result.verdict is Verdict.MISMATCH
        assert "750 mL" in result.reason and "1000 mL" in result.reason

    def test_nonstandard_fill_gets_a_note(self, application, extraction):
        application.net_contents = "730 mL"
        extraction.net_contents = make_field("730 mL")
        result = compare_net_contents(application, extraction)
        assert result.verdict is Verdict.MATCH
        assert any("standard of fill" in n for n in result.notes)

    def test_malt_beverages_have_no_standard_of_fill_note(self, application, extraction):
        application.beverage_type = BeverageType.MALT_BEVERAGE
        application.net_contents = "12 fl oz"
        extraction.net_contents = make_field("12 FL OZ")
        result = compare_net_contents(application, extraction)
        assert result.verdict is Verdict.MATCH
        assert result.notes == []

    def test_missing_is_mismatch(self, application, extraction):
        extraction.net_contents = make_field(None)
        assert compare_net_contents(application, extraction).verdict is Verdict.MISMATCH


class TestProducer:
    def test_name_matches(self, application, extraction):
        assert compare_producer_name(application, extraction).verdict is Verdict.MATCH

    def test_address_abbreviations_match(self, application, extraction):
        assert compare_producer_address(application, extraction).verdict is Verdict.MATCH

    def test_missing_address_needs_review_not_mismatch(self, application, extraction):
        extraction.producer_address = make_field(None)
        assert compare_producer_address(application, extraction).verdict is Verdict.NEEDS_REVIEW

    def test_different_city_is_mismatch(self, application, extraction):
        extraction.producer_address = make_field("9 Harbor Way, Portland, OR 97201")
        assert compare_producer_address(application, extraction).verdict is Verdict.MISMATCH

    def test_city_and_state_on_the_label_match_a_street_address_on_the_application(
        self, application, extraction
    ):
        """The label need only carry the city and state; the filing may give the street."""
        application.producer_address = "249 W SHORT ST STE 200, Lexington KY 40507"
        extraction.producer_address = make_field("Lexington, KY")
        result = compare_producer_address(application, extraction)
        assert result.verdict is Verdict.MATCH and "city and state" in result.reason
        extraction.producer_address = make_field("Kenwood, California")
        application.producer_address = "10200 SONOMA HWY, KENWOOD CA 95452"
        assert compare_producer_address(application, extraction).verdict is Verdict.MATCH

    def test_the_label_may_carry_more_address_than_the_application(self, application, extraction):
        application.producer_address = "Shandon, CA"
        extraction.producer_address = make_field(
            "Shandon, CA; 8901 State Hwy YY, New Haven, MO 63056"
        )
        assert compare_producer_address(application, extraction).verdict is Verdict.MATCH
        application.producer_address = "21481 E 8TH ST STE 25 & 30, Sonoma CA 95476"
        extraction.producer_address = make_field("SONOMA, CALIFORNIA USA")
        assert compare_producer_address(application, extraction).verdict is Verdict.MATCH

    def test_bottled_for_names_the_bottler_not_the_customer(self, application, extraction):
        application.producer_name = "SVP Winery"
        extraction.producer_name = make_field("SVP Winery for McKelvey Vineyards")
        result = compare_producer_name(application, extraction)
        assert result.verdict is Verdict.MATCH
        assert result.label_value == "SVP Winery for McKelvey Vineyards"
        assert any("bottled for" in n for n in result.notes)
        application.producer_name = "Wines for Change"
        extraction.producer_name = make_field("WINES FOR CHANGE")  # "for" inside one name
        whole = compare_producer_name(application, extraction)
        assert whole.verdict is Verdict.MATCH and not whole.notes

    def test_a_different_city_in_a_street_address_still_mismatches(self, application, extraction):
        application.producer_address = "249 W SHORT ST STE 200, Lexington KY 40507"
        extraction.producer_address = make_field("Louisville, KY")
        assert compare_producer_address(application, extraction).verdict is Verdict.MISMATCH

    def test_entity_suffixes_do_not_count(self, application, extraction):
        application.producer_name = "VINOVAE, INC."
        extraction.producer_name = make_field("Vinovae")
        assert compare_producer_name(application, extraction).verdict is Verdict.MATCH
        application.producer_name = "Founders Brewing Company"
        extraction.producer_name = make_field("Founders Brewing Co")
        assert compare_producer_name(application, extraction).verdict is Verdict.MATCH

    def test_a_filing_with_trade_and_legal_names_matches_either(self, application, extraction):
        application.producer_name = "Go Brewing, Go Brewing Opco, LLC"
        extraction.producer_name = make_field("GO BREWING")
        result = compare_producer_name(application, extraction)
        assert result.verdict is Verdict.MATCH
        assert "one of the names the application lists" in result.reason
        assert result.application_value == "Go Brewing, Go Brewing Opco, LLC"
        extraction.producer_name = make_field("Some Other Brewery")
        assert compare_producer_name(application, extraction).verdict is Verdict.MISMATCH


class TestCountryOfOrigin:
    def test_domestic_product_is_not_applicable(self, application, extraction):
        assert compare_country_of_origin(application, extraction).verdict is Verdict.NOT_APPLICABLE

    def test_import_with_matching_country(self, application, extraction):
        application.is_import = True
        application.country_of_origin = "Scotland"
        extraction.country_of_origin = make_field("Product of Scotland")
        assert compare_country_of_origin(application, extraction).verdict is Verdict.MATCH

    def test_import_missing_on_label_is_mismatch(self, application, extraction):
        application.is_import = True
        application.country_of_origin = "France"
        assert compare_country_of_origin(application, extraction).verdict is Verdict.MISMATCH

    def test_import_with_different_country_is_mismatch(self, application, extraction):
        application.is_import = True
        application.country_of_origin = "France"
        extraction.country_of_origin = make_field("Product of Italy")
        assert compare_country_of_origin(application, extraction).verdict is Verdict.MISMATCH

    def test_import_with_blank_application_country_needs_review(self, application, extraction):
        application.is_import = True
        assert compare_country_of_origin(application, extraction).verdict is Verdict.NEEDS_REVIEW


class TestVerify:
    def test_clean_label_is_approved(self, application, extraction):
        result = verify(application, extraction)
        assert result.recommendation is Recommendation.APPROVE
        # seven brief fields, the warning, the product type, the qualifying phrase, and the
        # age statement a whisky label carries
        assert len(result.fields) == 11
        assert result.field("health_warning").verdict is Verdict.MATCH

    def test_any_mismatch_requests_correction(self, application, extraction):
        extraction.alcohol_content = make_field("40% Alc./Vol.")
        result = verify(application, extraction)
        assert result.recommendation is Recommendation.REQUEST_CORRECTION
        assert any(line.startswith("Alcohol Content:") for line in result.summary)

    def test_review_without_mismatch_needs_review(self, application, extraction):
        extraction.brand_name = make_field("OLD TOM DISTILERY")  # a one-letter near miss
        result = verify(application, extraction)
        assert result.recommendation is Recommendation.NEEDS_REVIEW

    def test_unknown_bold_alone_does_not_hold_an_application(self, application, extraction):
        extraction.health_warning.heading_bold = None
        result = verify(application, extraction)
        assert result.recommendation is Recommendation.APPROVE
        warning = next(f for f in result.fields if f.field == "health_warning")
        assert any("could not tell" in n for n in warning.notes)

    def test_low_confidence_match_stays_a_match_but_is_flagged(self, application, extraction):
        """The values agree, so the row says match; the read was poor, so it is marked
        unverified and the application still goes to review."""
        extraction.brand_name = make_field("OLD TOM DISTILLERY", confidence=0.4)
        result = verify(application, extraction)
        brand = result.field("brand_name")
        assert brand.verdict is Verdict.MATCH and brand.uncertain
        assert any("Low read confidence" in n for n in brand.notes)
        assert result.recommendation is Recommendation.NEEDS_REVIEW
        assert any("low-confidence read" in line for line in result.summary)
        # A difference on a poor read asks for a clearer image; the same difference read
        # confidently is a finding.
        extraction.brand_name = make_field("SOMETHING ELSE", confidence=0.4)
        poor = verify(application, extraction).field("brand_name")
        assert poor.verdict is Verdict.NEEDS_REVIEW and poor.uncertain
        extraction.brand_name = make_field("SOMETHING ELSE", confidence=0.95)
        assert verify(application, extraction).field("brand_name").verdict is Verdict.MISMATCH

    def test_unreadable_image_never_approves(self, application, extraction):
        extraction.image_quality.readable = False
        extraction.image_quality.issues = ["glare across the bottom third"]
        result = verify(application, extraction)
        assert result.recommendation is Recommendation.NEEDS_REVIEW
        assert any("clearer photo" in s for s in result.summary)
        assert any("glare" in s for s in result.summary)

    def test_result_serializes_to_json(self, application, extraction):
        payload = verify(application, extraction).model_dump(mode="json")
        assert payload["recommendation"] == "approve"
        assert payload["fields"][0]["verdict"] == "match"


class TestWineRules:
    """Part 4 checks beyond the seven fields: appellation, vintage, estate bottling, and
    the tax class line the alcohol tolerance may not cross."""

    @pytest.fixture
    def wine(self, application, extraction):
        application.beverage_type = BeverageType.WINE
        application.class_type = "Cabernet Sauvignon"
        application.alcohol_content = "14.5"
        extraction.class_type = make_field("Cabernet Sauvignon")
        extraction.alcohol_content = make_field("14.5% Alc. by Vol.")
        extraction.qualifying_phrase = make_field("Produced and Bottled by")
        extraction.age_statement = make_field(None)
        extraction.sulfite_declaration = make_field("Contains Sulfites")
        extraction.appellation = make_field("Napa Valley")
        extraction.vintage_year = make_field("2021")
        return application, extraction

    def test_vintage_needs_an_appellation_beside_it(self, wine):
        from labelverify.engine.compare import compare_vintage_year

        application, extraction = wine
        result = compare_vintage_year(application, extraction)
        assert result.verdict is Verdict.MATCH and "4.27" in result.reason
        extraction.appellation = make_field(None)
        result = compare_vintage_year(application, extraction)
        assert result.verdict is Verdict.MISMATCH and "no appellation" in result.reason
        assert verify(application, extraction).recommendation is Recommendation.REQUEST_CORRECTION
        extraction.vintage_year = make_field(None)  # no vintage: nothing to check, no row
        assert compare_vintage_year(application, extraction) is None

    def test_a_future_or_unreadable_vintage(self, wine):
        from datetime import date

        from labelverify.engine.compare import compare_vintage_year

        application, extraction = wine
        extraction.vintage_year = make_field(str(date.today().year + 1))
        assert compare_vintage_year(application, extraction).verdict is Verdict.MISMATCH
        extraction.vintage_year = make_field("MMXXI")
        assert compare_vintage_year(application, extraction).verdict is Verdict.NEEDS_REVIEW

    def test_appellation_row_informs_and_cites(self, wine):
        from labelverify.engine.compare import compare_appellation

        application, extraction = wine
        row = compare_appellation(application, extraction)
        assert row.verdict is Verdict.MATCH and row.label_value == "Napa Valley"
        assert any("records" in n for n in row.notes)
        result = verify(application, extraction)
        by_field = {f.field: f for f in result.fields}
        assert by_field["appellation"].citation == "27 CFR 4.25"
        assert by_field["vintage_year"].citation == "27 CFR 4.27"
        assert result.recommendation is Recommendation.APPROVE

    def test_estate_bottled_needs_an_appellation(self, wine):
        from labelverify.engine.compare import compare_estate_bottled

        application, extraction = wine
        assert compare_estate_bottled(application, extraction) is None  # no claim, no row
        extraction.estate_bottled_claim = make_field("ESTATE BOTTLED")
        row = compare_estate_bottled(application, extraction)
        assert row.verdict is Verdict.MATCH and "4.26" in row.notes[0]
        extraction.appellation = make_field(None)
        row = compare_estate_bottled(application, extraction)
        assert row.verdict is Verdict.MISMATCH and "4.26" in row.reason

    def test_wine_rows_never_appear_for_other_classes(self, application, extraction):
        from labelverify.engine.compare import (
            compare_appellation,
            compare_estate_bottled,
            compare_vintage_year,
        )

        extraction.appellation = make_field("Kentucky")
        extraction.vintage_year = make_field("2019")
        extraction.estate_bottled_claim = make_field("Estate Bottled")
        for fn in (compare_appellation, compare_vintage_year, compare_estate_bottled):
            assert fn(application, extraction) is None  # a bourbon
        fields = {f.field for f in verify(application, extraction).fields}
        assert not fields & {"appellation", "vintage_year", "estate_bottled"}

    def test_alcohol_tolerance_does_not_cross_the_tax_class_line(self, wine):
        application, extraction = wine
        application.alcohol_content = "13.8"
        extraction.alcohol_content = make_field("14.2% Alc. by Vol.")  # 0.4 apart, within ±1.5
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.MISMATCH and "14 percent tax class" in result.reason
        application.alcohol_content = "13.0"
        extraction.alcohol_content = make_field("13.8% Alc. by Vol.")  # same class: review
        assert compare_alcohol_content(application, extraction).verdict is Verdict.NEEDS_REVIEW


class TestMaltRules:
    """Part 7 checks: a recognized class designation, the alcohol statement's wording, a
    statement of strength, and net contents that may be blown into the glass."""

    @pytest.fixture
    def malt(self, application, extraction):
        application.beverage_type = BeverageType.MALT_BEVERAGE
        application.class_type = "Lager"
        application.alcohol_content = "5.2"
        application.net_contents = "12 fl oz"
        extraction.class_type = make_field("Lager")
        extraction.alcohol_content = make_field("5.2% ALC/VOL")
        extraction.net_contents = make_field("12 FL OZ")
        extraction.qualifying_phrase = make_field("Brewed by")
        extraction.age_statement = make_field(None)
        return application, extraction

    def test_strength_claims_go_to_review_and_only_on_malt(self, malt):
        from labelverify.engine.compare import compare_strength_claim

        application, extraction = malt
        assert compare_strength_claim(application, extraction) is None  # nothing claimed
        extraction.strength_claim = make_field("EXTRA STRENGTH")
        row = compare_strength_claim(application, extraction)
        assert row.verdict is Verdict.NEEDS_REVIEW and "7.65" in row.reason
        assert verify(application, extraction).recommendation is Recommendation.NEEDS_REVIEW
        # The brand, class, and alcohol statement are scanned when the reader reported none.
        extraction.strength_claim = make_field(None)
        extraction.class_type = make_field("High Test Lager")
        application.class_type = "High Test Lager"
        assert compare_strength_claim(application, extraction).label_value == "High Test"
        application.beverage_type = BeverageType.WINE  # no such rule for wine
        extraction.class_type = make_field("Strong Red Wine")
        application.class_type = "Strong Red Wine"
        assert compare_strength_claim(application, extraction) is None

    def test_class_designation_must_contain_a_recognized_term(self, malt):
        application, extraction = malt
        assert compare_class_type(application, extraction).verdict is Verdict.MATCH
        application.class_type = "Imperial Reserve"
        extraction.class_type = make_field("Imperial Reserve")
        result = compare_class_type(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "7.64" in result.notes[0]

    def test_alcohol_statement_needs_the_words_alcohol_by_volume(self, malt):
        application, extraction = malt
        assert compare_alcohol_content(application, extraction).verdict is Verdict.MATCH
        extraction.alcohol_content = make_field("5.2%")
        result = compare_alcohol_content(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "Alc./Vol." in result.notes[0]

    def test_net_contents_may_be_blown_into_the_glass(self, malt, application, extraction):
        application, extraction = malt
        extraction.net_contents = make_field(None)
        result = compare_net_contents(application, extraction)
        assert result.verdict is Verdict.NEEDS_REVIEW and "blown into the glass" in result.reason
        application.beverage_type = BeverageType.DISTILLED_SPIRITS  # printed or nothing
        application.class_type = "Vodka"
        extraction.class_type = make_field("Vodka")
        assert compare_net_contents(application, extraction).verdict is Verdict.MISMATCH


class TestUncertainRead:
    """An OCR-grade read: nothing it did not find counts as missing."""

    @staticmethod
    def _ocr_grade(extraction):
        for name in (
            "brand_name",
            "class_type",
            "alcohol_content",
            "net_contents",
            "producer_name",
            "producer_address",
            "qualifying_phrase",
            "age_statement",
        ):
            setattr(extraction, name, make_field(getattr(extraction, name).value, confidence=0.45))
        extraction.health_warning.confidence = 0.45

    def test_absences_become_review_but_differences_stay_findings(self, application, extraction):
        from labelverify.engine.compare import read_is_uncertain

        self._ocr_grade(extraction)
        assert read_is_uncertain(extraction)
        extraction.net_contents = make_field(None)  # OCR missed the volume line
        extraction.alcohol_content = make_field("40% Alc./Vol.", confidence=0.45)  # read unsurely
        result = verify(application, extraction)
        net = result.field("net_contents")
        assert net.verdict is Verdict.NEEDS_REVIEW and net.uncertain
        assert "confirm on the image" in net.reason
        abv = result.field("alcohol_content")  # a difference the reader is unsure of: review
        assert abv.verdict is Verdict.NEEDS_REVIEW and abv.uncertain
        assert any("clearer photo" in n for n in abv.notes)
        assert result.recommendation is Recommendation.NEEDS_REVIEW
        assert any("did not find: Net Contents" in line for line in result.summary)
        # The same difference read confidently is a finding.
        extraction.alcohol_content = make_field("40% Alc./Vol.", confidence=0.95)
        assert verify(application, extraction).field("alcohol_content").verdict is Verdict.MISMATCH

    def test_a_confident_read_keeps_absences_as_findings(self, application, extraction):
        from labelverify.engine.compare import read_is_uncertain

        assert not read_is_uncertain(extraction), [
            (n, getattr(extraction, n).confidence) for n in ("brand_name", "class_type")
        ]
        extraction.net_contents = make_field(None)
        assert verify(application, extraction).field("net_contents").verdict is Verdict.MISMATCH

    def test_nothing_read_at_all_is_an_uncertain_read(self, application):
        from labelverify.engine.compare import read_is_uncertain
        from labelverify.engine.models import HealthWarningExtraction, ImageQuality, LabelExtraction

        blank = LabelExtraction(
            health_warning=HealthWarningExtraction(present=False),
            image_quality=ImageQuality(readable=False, issues=["No text could be read."]),
        )
        assert read_is_uncertain(blank)
        result = verify(application, blank)
        assert result.recommendation is Recommendation.NEEDS_REVIEW
        assert Verdict.MISMATCH not in {f.verdict for f in result.fields}

    def test_a_small_warning_slip_on_an_uncertain_read_is_review(self, application, extraction):
        from labelverify.engine.warning import STATUTORY_TEXT

        self._ocr_grade(extraction)
        extraction.health_warning.text = STATUTORY_TEXT.replace("birth defects", "birth defect5")
        assert (
            verify(application, extraction).field("health_warning").verdict is Verdict.NEEDS_REVIEW
        )
        extraction.health_warning.text = STATUTORY_TEXT.replace(
            "GOVERNMENT WARNING", "Government Warning"
        )
        heading = verify(application, extraction).field("health_warning")
        assert heading.verdict is Verdict.NEEDS_REVIEW and heading.uncertain  # unsure read
        extraction.health_warning.confidence = 0.95
        assert verify(application, extraction).field("health_warning").verdict is Verdict.MISMATCH


class TestFiledStatements:
    """Statements the applicant files as printed are compared with what the read found;
    a filing that does not provide them (None) leaves the rule's own verdict alone."""

    def test_not_provided_keeps_the_rule_row(self, application, extraction):
        row = next(f for f in verify(application, extraction).fields if f.field == "age_statement")
        assert (
            row.verdict is Verdict.MATCH
            and row.application_value == "Required if aged under 4 years"
        )

    def test_filed_as_printed_matches(self, application, extraction):
        application.qualifying_phrase = "Distilled and Bottled by"
        application.age_statement = "Aged Six Years"
        result = verify(application, extraction)
        rows = {f.field: f for f in result.fields}
        assert rows["qualifying_phrase"].verdict is Verdict.MATCH
        assert rows["qualifying_phrase"].application_value == "Distilled and Bottled by"
        assert "Filed as printed" in rows["qualifying_phrase"].notes[0]
        assert rows["age_statement"].verdict is Verdict.MATCH
        assert result.recommendation is Recommendation.APPROVE

    def test_filed_differently_is_a_mismatch(self, application, extraction):
        application.age_statement = "Aged 4 Years"
        row = next(f for f in verify(application, extraction).fields if f.field == "age_statement")
        assert row.verdict is Verdict.MISMATCH
        assert "Filed as 'Aged 4 Years', but the label prints 'Aged Six Years'" in row.reason

    def test_filed_blank_while_printed_needs_review(self, application, extraction):
        application.qualifying_phrase = ""
        row = next(
            f for f in verify(application, extraction).fields if f.field == "qualifying_phrase"
        )
        assert row.verdict is Verdict.NEEDS_REVIEW and "left this blank" in row.reason

    def test_filed_but_not_on_the_label_is_a_mismatch(self, application, extraction):
        application.beverage_type = BeverageType.WINE
        application.class_type = "Cabernet Sauvignon"
        extraction.class_type = make_field("Cabernet Sauvignon")
        extraction.age_statement = make_field(None)
        application.appellation = "Napa Valley"  # the label names no appellation
        row = next(f for f in verify(application, extraction).fields if f.field == "appellation")
        assert row.verdict is Verdict.MISMATCH and row.label_value is None
        assert "nothing of the kind was read" in row.reason

    def test_filed_warning_is_compared_with_the_printed_one(self, application, extraction):
        from labelverify.engine.warning import STATUTORY_TEXT

        application.health_warning = STATUTORY_TEXT
        row = next(f for f in verify(application, extraction).fields if f.field == "health_warning")
        assert row.verdict is Verdict.MATCH and row.application_value == STATUTORY_TEXT
        application.health_warning = STATUTORY_TEXT.replace("women", "woman")
        row = next(f for f in verify(application, extraction).fields if f.field == "health_warning")
        assert row.verdict is Verdict.MISMATCH and "as filed differs" in row.reason

    def test_a_statement_for_another_class_is_ignored(self, application, extraction):
        application.sulfite_declaration = "Contains Sulfites"  # spirits carry no such rule
        fields = {f.field for f in verify(application, extraction).fields}
        assert "sulfite_declaration" not in fields
