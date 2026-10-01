"""The labeling rulebook: what each TTB commodity class must carry, and how strictly.

TTB regulates the three classes under separate parts of Title 27: wine under part 4,
distilled spirits under part 5, malt beverages under part 7. The Government Health
Warning in part 16 applies to all three. The parts agree on most of the seven fields in
the brief but differ on alcohol content, net contents, and one wine-only declaration.
Everything class-specific the engine does comes from this module, so a change to a rule
is one edit here, and the applicant's checklist and the specialist's citations come from
the same table the comparisons use.

Standards-of-fill lists follow TTB's January 2025 final rule and are advisory: a size not
on the list produces a note, never a verdict, because the lists change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import BeverageType


class Requirement(StrEnum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    CONDITIONAL = "conditional"  # required in a stated circumstance (imports, sulfites)
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class FieldRule:
    requirement: Requirement
    citation: str
    note: str = ""
    checked_by: str = "engine"  # "engine" or "specialist" (a visual judgment)


@dataclass(frozen=True)
class ClassRules:
    beverage_type: BeverageType
    part: int
    name: str
    fields: dict[str, FieldRule]
    proof_permitted: bool
    metric_required: bool
    standards_of_fill_ml: frozenset[int] | None
    qualifying_phrases: tuple[str, ...]
    table_wine_exemption: bool = False

    def abv_tolerance(self, abv: float) -> float:
        """Labeling tolerance in percentage points for a stated alcohol content."""
        if self.beverage_type is BeverageType.WINE:
            return 1.5 if abv <= 14.0 else 1.0
        if self.beverage_type is BeverageType.MALT_BEVERAGE:
            return 0.3
        return 0.15

    def rule(self, field: str) -> FieldRule:
        return self.fields[field]

    def requirement(self, field: str) -> Requirement:
        rule = self.fields.get(field)
        return rule.requirement if rule else Requirement.NOT_APPLICABLE

    def checklist(self) -> list[dict]:
        """What this class must carry, in the order the comparison table shows it."""
        return [
            {
                "field": key,
                "label": FIELD_LABELS[key],
                "requirement": rule.requirement.value,
                "citation": rule.citation,
                "note": rule.note,
                "checked_by": rule.checked_by,
            }
            for key, rule in self.fields.items()
            if rule.requirement is not Requirement.NOT_APPLICABLE
        ]

    def to_dict(self) -> dict:
        return {
            "beverage_type": self.beverage_type.value,
            "part": self.part,
            "name": self.name,
            "proof_permitted": self.proof_permitted,
            "metric_required": self.metric_required,
            "standards_of_fill_ml": sorted(self.standards_of_fill_ml or []),
            "qualifying_phrases": list(self.qualifying_phrases),
            "checklist": self.checklist(),
        }


FIELD_LABELS = {
    "brand_name": "Brand name",
    "class_type": "Class / type designation",
    "alcohol_content": "Alcohol content",
    "net_contents": "Net contents",
    "producer_name": "Name of bottler, producer, or importer",
    "producer_address": "Address (city and state)",
    "qualifying_phrase": "Qualifying phrase before the name",
    "country_of_origin": "Country of origin",
    "sulfite_declaration": "Sulfite declaration",
    "health_warning": "Government Health Warning Statement",
}

_HEALTH_WARNING = FieldRule(
    Requirement.REQUIRED,
    "27 CFR 16.21",
    "Word for word; 'GOVERNMENT WARNING' in capitals and bold.",
)
_COUNTRY_NOTE = "Required on imported products, in English."

# Standards of fill per the January 2025 final rule (advisory; see module docstring).
SPIRITS_STANDARDS_ML = frozenset(
    {50, 100, 187, 200, 250, 331, 350, 355, 375, 475, 500, 570, 700, 710, 720, 750, 900,
     945, 1000, 1500, 1750, 1800, 2000, 3000, 3750}
)
WINE_STANDARDS_ML = frozenset(
    {50, 100, 180, 187, 300, 330, 360, 375, 473, 500, 550, 568, 600, 620, 700, 720, 750,
     1000, 1500, 1800, 2250, 3000}
)

RULES: dict[BeverageType, ClassRules] = {
    BeverageType.DISTILLED_SPIRITS: ClassRules(
        beverage_type=BeverageType.DISTILLED_SPIRITS,
        part=5,
        name="Distilled spirits",
        proof_permitted=True,
        metric_required=True,
        standards_of_fill_ml=SPIRITS_STANDARDS_ML,
        qualifying_phrases=("Distilled by", "Bottled by", "Produced by", "Imported by"),
        fields={
            "brand_name": FieldRule(Requirement.REQUIRED, "27 CFR 5.63"),
            "class_type": FieldRule(
                Requirement.REQUIRED, "27 CFR 5.63", "A class or type from the standards of identity."
            ),
            "alcohol_content": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 5.65",
                "Percent alcohol by volume; proof may be added and must equal twice the percentage. "
                "Tolerance ±0.15.",
            ),
            "net_contents": FieldRule(
                Requirement.REQUIRED, "27 CFR 5.70", "Metric units; a standard of fill (27 CFR 5.203)."
            ),
            "producer_name": FieldRule(Requirement.REQUIRED, "27 CFR 5.66"),
            "producer_address": FieldRule(Requirement.REQUIRED, "27 CFR 5.66", "City and state."),
            "qualifying_phrase": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 5.66",
                "'Distilled by', 'Bottled by', or similar, before the name.",
                checked_by="specialist",
            ),
            "country_of_origin": FieldRule(Requirement.CONDITIONAL, "27 CFR 5.74", _COUNTRY_NOTE),
            "sulfite_declaration": FieldRule(Requirement.NOT_APPLICABLE, ""),
            "health_warning": _HEALTH_WARNING,
        },
    ),
    BeverageType.WINE: ClassRules(
        beverage_type=BeverageType.WINE,
        part=4,
        name="Wine",
        proof_permitted=False,
        metric_required=True,
        standards_of_fill_ml=WINE_STANDARDS_ML,
        qualifying_phrases=(
            "Produced and bottled by",
            "Vinted and bottled by",
            "Cellared and bottled by",
            "Bottled by",
            "Imported by",
        ),
        table_wine_exemption=True,
        fields={
            "brand_name": FieldRule(Requirement.REQUIRED, "27 CFR 4.33"),
            "class_type": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 4.34",
                "A class or type designation; a grape variety may serve as the type.",
            ),
            "alcohol_content": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 4.36",
                "Percent alcohol by volume. 'Table Wine' or 'Light Wine' may stand in for the "
                "number between 7 and 14 percent. Tolerance ±1.5 up to 14 percent, ±1.0 above.",
            ),
            "net_contents": FieldRule(
                Requirement.REQUIRED, "27 CFR 4.37", "Metric units; a standard of fill (27 CFR 4.72)."
            ),
            "producer_name": FieldRule(Requirement.REQUIRED, "27 CFR 4.35"),
            "producer_address": FieldRule(Requirement.REQUIRED, "27 CFR 4.35", "City and state."),
            "qualifying_phrase": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 4.35",
                "'Produced and bottled by', 'Vinted and bottled by', or similar.",
                checked_by="specialist",
            ),
            "country_of_origin": FieldRule(
                Requirement.CONDITIONAL, "27 CFR 4.32; 19 CFR 134", _COUNTRY_NOTE
            ),
            "sulfite_declaration": FieldRule(
                Requirement.CONDITIONAL,
                "27 CFR 4.32(e)",
                "'Contains sulfites' when sulfur dioxide is 10 parts per million or more.",
            ),
            "health_warning": _HEALTH_WARNING,
        },
    ),
    BeverageType.MALT_BEVERAGE: ClassRules(
        beverage_type=BeverageType.MALT_BEVERAGE,
        part=7,
        name="Malt beverage",
        proof_permitted=False,
        metric_required=False,
        standards_of_fill_ml=None,
        qualifying_phrases=("Brewed by", "Brewed and bottled by", "Brewed and canned by", "Imported by"),
        fields={
            "brand_name": FieldRule(Requirement.REQUIRED, "27 CFR 7.63"),
            "class_type": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 7.64",
                "A class designation such as beer, ale, lager, stout, porter, or malt liquor.",
            ),
            "alcohol_content": FieldRule(
                Requirement.OPTIONAL,
                "27 CFR 7.65",
                "Optional at the federal level; some states require it. When stated, tolerance ±0.3.",
            ),
            "net_contents": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 7.70",
                "U.S. fluid ounces or metric units; no standards of fill.",
            ),
            "producer_name": FieldRule(Requirement.REQUIRED, "27 CFR 7.66"),
            "producer_address": FieldRule(Requirement.REQUIRED, "27 CFR 7.66", "City and state."),
            "qualifying_phrase": FieldRule(
                Requirement.REQUIRED,
                "27 CFR 7.66",
                "'Brewed by', 'Brewed and bottled by', or similar.",
                checked_by="specialist",
            ),
            "country_of_origin": FieldRule(
                Requirement.CONDITIONAL, "27 CFR 7.61; 19 CFR 134", _COUNTRY_NOTE
            ),
            "sulfite_declaration": FieldRule(Requirement.NOT_APPLICABLE, ""),
            "health_warning": _HEALTH_WARNING,
        },
    ),
}


def rules_for(beverage_type: BeverageType | str) -> ClassRules:
    return RULES[BeverageType(beverage_type)]


def all_rules() -> list[dict]:
    """Every class's rulebook as plain data, for the API, the wizard, and the docs."""
    return [RULES[bt].to_dict() for bt in BeverageType]
