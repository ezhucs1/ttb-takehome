"""The rulebook: what each commodity class must carry, as data the UI and docs reuse."""

from labelverify.engine.models import BeverageType
from labelverify.engine.rules import RULES, Requirement, all_rules, rules_for


def test_three_classes_three_parts():
    assert {r.part for r in RULES.values()} == {4, 5, 7}
    assert rules_for("wine").part == 4 and rules_for(BeverageType.MALT_BEVERAGE).part == 7


def test_class_differences_the_engine_relies_on():
    spirits, wine, malt = (rules_for(t) for t in BeverageType)
    assert spirits.requirement("alcohol_content") is Requirement.REQUIRED
    assert wine.requirement("alcohol_content") is Requirement.REQUIRED and wine.table_wine_exemption
    assert malt.requirement("alcohol_content") is Requirement.OPTIONAL
    assert (
        spirits.abv_tolerance(45),
        wine.abv_tolerance(13),
        wine.abv_tolerance(15),
        malt.abv_tolerance(5),
    ) == (
        0.15,
        1.5,
        1.0,
        0.3,
    )
    assert spirits.proof_permitted and not wine.proof_permitted and not malt.proof_permitted
    assert spirits.metric_required and wine.metric_required and not malt.metric_required
    assert malt.standards_of_fill_ml is None and 750 in spirits.standards_of_fill_ml
    assert wine.requirement("sulfite_declaration") is Requirement.CONDITIONAL
    assert spirits.requirement("sulfite_declaration") is Requirement.NOT_APPLICABLE
    for rules in (spirits, wine, malt):
        assert rules.requirement("health_warning") is Requirement.REQUIRED
        assert rules.rule("health_warning").citation == "27 CFR 16.21"
        assert rules.requirement("country_of_origin") is Requirement.CONDITIONAL


def test_checklists_are_plain_data_with_citations():
    for entry in all_rules():
        assert entry["part"] in (4, 5, 7) and entry["checklist"]
        for item in entry["checklist"]:
            assert item["citation"].startswith("27 CFR")
            assert item["requirement"] in ("required", "optional", "conditional")
            assert item["checked_by"] in ("engine", "specialist")
    wine_fields = {i["field"] for i in rules_for("wine").checklist()}
    assert "sulfite_declaration" in wine_fields
    assert "sulfite_declaration" not in {i["field"] for i in rules_for("malt_beverage").checklist()}


def test_wine_carries_the_part_4_statements_the_other_classes_do_not():
    wine = {i["field"]: i for i in rules_for("wine").checklist()}
    for field, section in (
        ("appellation", "4.25"),
        ("vintage_year", "4.27"),
        ("estate_bottled", "4.26"),
    ):
        assert wine[field]["requirement"] == "conditional" and section in wine[field]["citation"]
    for other in ("distilled_spirits", "malt_beverage"):
        assert not {"appellation", "vintage_year", "estate_bottled"} & {
            i["field"] for i in rules_for(other).checklist()
        }


def test_malt_beverages_carry_the_strength_rule_and_nothing_wine_or_spirits_specific():
    malt = {i["field"]: i for i in rules_for("malt_beverage").checklist()}
    assert (
        malt["strength_claim"]["requirement"] == "conditional"
        and "7.65" in malt["strength_claim"]["citation"]
    )
    assert "blown into the glass" in malt["net_contents"]["note"]
    assert "strength_claim" not in {i["field"] for i in rules_for("wine").checklist()}
    assert "strength_claim" not in {i["field"] for i in rules_for("distilled_spirits").checklist()}
