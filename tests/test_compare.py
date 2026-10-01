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
        assert compare_beverage_type(application, extraction).verdict is Verdict.NOT_APPLICABLE


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

    def test_blank_everywhere_is_not_applicable_for_wine(self, application, extraction):
        application.beverage_type = BeverageType.WINE
        application.alcohol_content = ""
        extraction.alcohol_content = make_field(None)
        assert compare_alcohol_content(application, extraction).verdict is Verdict.NOT_APPLICABLE

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
        assert len(result.fields) == 9  # seven brief fields, the warning, and the product type
        assert result.field("health_warning").verdict is Verdict.MATCH

    def test_any_mismatch_requests_correction(self, application, extraction):
        extraction.alcohol_content = make_field("40% Alc./Vol.")
        result = verify(application, extraction)
        assert result.recommendation is Recommendation.REQUEST_CORRECTION
        assert any(line.startswith("Alcohol Content:") for line in result.summary)

    def test_review_without_mismatch_needs_review(self, application, extraction):
        extraction.health_warning.heading_bold = None
        result = verify(application, extraction)
        assert result.recommendation is Recommendation.NEEDS_REVIEW

    def test_low_confidence_match_is_downgraded(self, application, extraction):
        extraction.brand_name = make_field("OLD TOM DISTILLERY", confidence=0.4)
        result = verify(application, extraction)
        brand = result.field("brand_name")
        assert brand.verdict is Verdict.NEEDS_REVIEW
        assert any("Low read confidence" in n for n in brand.notes)
        assert result.recommendation is Recommendation.NEEDS_REVIEW

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
