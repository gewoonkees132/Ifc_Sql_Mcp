"""Every rule on the synthetic fixture: how many findings, about which objects, saying what.

Each test loads the fixture inside the rolled-back ``conn`` transaction, synchronises the
shipped catalogue and evaluates it, so nothing is committed. ``evaluate`` writes into the
caller's transaction, which is what makes this possible; ``test_dq_run.py`` covers the
committed runs around it.
"""

import dataclasses
from collections import Counter

import make_revit_quirks as fx
import pytest
from rows import add_dq_run, add_element, add_property, add_quantity, add_spatial, new_sha256
from rows import add_model as add_model_row
from sqlalchemy import Connection, RowMapping, insert, text

from ifc_sql_mcp.db import schema
from ifc_sql_mcp.dq.catalogue import Rule, load_catalogue
from ifc_sql_mcp.dq.run import evaluate
from ifc_sql_mcp.dq.sync import sync_rules
from ifc_sql_mcp.ingest.load import load
from ifc_sql_mcp.ingest.records import ModelRecords

RULES = load_catalogue()

# The objects each rule finds in tests/fixtures/revit_quirks.ifc, one GlobalId per finding,
# sorted. make_revit_quirks.py says why each is there.
FIXTURE_FINDINGS: dict[str, list[str]] = {
    # Kitchen CeilingCovering = 'CeilingCovering', wall BarCode = 'BarCode'.
    "value_is_property_name": sorted([fx.KITCHEN, fx.WALL]),
    # Each panel's own Other.TagNumber. The shared Other.Material 'N/A' lost to
    # 'Steel - Painted' (conflicting_property), so it is not stored.
    "value_is_not_applicable": sorted([fx.PANEL_1, fx.PANEL_2]),
    # Kitchen Reference, wall Reference and wall Manufacturer.
    "value_is_empty": sorted([fx.KITCHEN, fx.WALL, fx.WALL]),
    # Every element reaches a storey, the opening and stair parts through their hosts.
    "element_without_storey": [],
    # The store room has no Revit dimensions and no GSA quantity set.
    "space_without_area": [fx.STORE],
    # The fixture's quantities are consistent; test_dq_quantity_rules.py breaks them.
    "wall_area_inconsistent": [],
    "slab_volume_inconsistent": [],
    "space_volume_inconsistent": [],
    "space_perimeter_implausible": [],
    # The fixture's rooms have distinct numbers; test_mcp_tools.py builds a repeat.
    "duplicate_space": [],
    # The fixture has quantities, rooms, named materials and a real project number. Its
    # wall and slab have no FireRating (the door has one).
    "model_without_quantities": [],
    "model_without_spaces": [],
    "missing_fire_rating": sorted([fx.SLAB, fx.WALL]),
    "placeholder_material": [],
    "placeholder_project_info": [],
    "space_area_not_mapped": [],
    # The transform's eight issues (tests/unit/ingest/test_fixture_pipeline.py).
    "multiple_projects": [],
    "invalid_project_global_id": [],
    "unit_assumed_si": [],
    "unit_not_convertible": [],
    "measure_not_converted": [],
    "invalid_global_id": [],
    "duplicate_global_id": [],
    "invalid_elevation": [],
    "revit_name_unparsed": [fx.PROXY],
    "revit_tag_mismatch": [],
    "unnamed_property": [],
    "unsupported_property": [],
    "unsupported_property_set": [],
    "non_finite_value": [],
    "conflicting_property": sorted([fx.PANEL_1, fx.PANEL_2]),
    "quantity_not_a_measure": sorted([fx.PANEL_1, fx.PANEL_2]),
    "negative_quantity": [fx.SLAB],
    "implausible_quantity": [],
    "conflicting_quantity": [],
    "omniclass_unparsed": [fx.STORE],
    "incomplete_classification_reference": [fx.PROXY],
    "unsupported_classification": [],
    "conflicting_classification": [],
    "multiple_material_associations": [],
    "unsupported_material": [],
    "unnamed_material": [],
    "invalid_layer_thickness": [],
}


def _load(conn: Connection, records: ModelRecords) -> int:
    model_id = load(conn, records, "ARC").model_id
    conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    return model_id


def _check(conn: Connection, model_id: int, rules: tuple[Rule, ...] = RULES) -> int:
    """Evaluate ``rules`` against the model in a new run; return the run's id."""
    run_id = add_dq_run(conn, model_id)
    evaluate(conn, run_id, model_id, rules)
    return run_id


def _findings(conn: Connection, run_id: int) -> list[RowMapping]:
    return list(
        conn.execute(
            text(
                "SELECT r.rule_id, r.severity, r.object_id, o.global_id, o.model_id, r.message "
                "FROM ifc.dq_result AS r LEFT JOIN ifc.object AS o USING (object_id) "
                "WHERE r.dq_run_id = :run ORDER BY r.dq_result_id"
            ),
            {"run": run_id},
        ).mappings()
    )


@pytest.fixture
def model_id(conn: Connection, fixture_records: ModelRecords) -> int:
    sync_rules(conn, RULES)
    return _load(conn, fixture_records)


@pytest.fixture
def findings(conn: Connection, model_id: int) -> list[RowMapping]:
    return _findings(conn, _check(conn, model_id))


# --------------------------------------------------------------------------- every rule


def test_every_rule_has_an_expected_result() -> None:
    """A new rule cannot be added without saying what it finds in the fixture."""
    assert list(FIXTURE_FINDINGS) == [rule.rule_id for rule in RULES]


@pytest.mark.parametrize("rule", RULES, ids=lambda rule: rule.rule_id)
def test_each_rule_finds_exactly_the_expected_objects(
    findings: list[RowMapping], rule: Rule
) -> None:
    found = [f for f in findings if f["rule_id"] == rule.rule_id]

    assert sorted(f["global_id"] for f in found) == FIXTURE_FINDINGS[rule.rule_id]
    assert {f["severity"] for f in found} <= {rule.severity}


def test_the_fixture_has_eighteen_findings(findings: list[RowMapping]) -> None:
    assert Counter(f["severity"] for f in findings) == {"warning": 11, "info": 7}


def test_findings_say_what_is_wrong_and_with_which_value(findings: list[RowMapping]) -> None:
    messages = {(f["rule_id"], f["global_id"], f["message"]) for f in findings}

    assert {
        (
            "value_is_property_name",
            fx.WALL,
            "PSet_Revit_Other.BarCode = 'BarCode', the parameter's own name: a placeholder, "
            "not a value",
        ),
        ("value_is_not_applicable", fx.PANEL_1, "Other.TagNumber = 'N/A': no value"),
        ("value_is_empty", fx.KITCHEN, "Pset_SpaceCommon.Reference = '': no value"),
        ("space_without_area", fx.STORE, "space F201 has no area_m2 from any source"),
        # An issue rule keeps the issue's message.
        (
            "negative_quantity",
            fx.SLAB,
            "PSet_Revit_Dimensions.Area = -12.000000000000002 m2 is negative",
        ),
    } <= messages


# --------------------------------------------------------------------------- issues


def test_issue_findings_are_the_stored_issues(
    conn: Connection, model_id: int, findings: list[RowMapping]
) -> None:
    """Rule for rule: the same objects and messages as the ingest_issue rows."""
    issues = conn.execute(
        text("SELECT code, object_id, message FROM ifc.ingest_issue WHERE model_id = :m"),
        {"m": model_id},
    ).all()
    issue_rules = {rule.rule_id for rule in RULES if rule.issue_code is not None}

    from_issues = [(f["rule_id"], f["object_id"], f["message"]) for f in findings]
    assert sorted(f for f in from_issues if f[0] in issue_rules) == sorted(
        (code, object_id, message) for code, object_id, message in issues
    )
    assert len(issues) == 8


def test_an_issue_about_an_object_that_was_never_loaded_names_its_global_id(
    conn: Connection, model_id: int
) -> None:
    """dq_result has no GlobalId column, so the message carries it (ADR 5)."""
    conn.execute(
        insert(schema.ingest_issue).values(
            [
                {
                    "model_id": model_id,
                    "issue_index": 9,
                    "code": "invalid_global_id",
                    "object_id": None,
                    "global_id": "not-a-global-id",
                    "message": "IfcWall 'Basic Wall:Generic:1' has a malformed GlobalId; skipped",
                },
                {
                    "model_id": model_id,
                    "issue_index": 10,
                    "code": "unit_assumed_si",
                    "object_id": None,
                    "global_id": None,
                    "message": "the file declares no AREAUNIT; assuming m2",
                },
            ]
        )
    )

    findings = _findings(conn, _check(conn, model_id))

    by_rule = {f["rule_id"]: f for f in findings}
    assert (by_rule["invalid_global_id"]["object_id"], by_rule["invalid_global_id"]["message"]) == (
        None,
        "IfcWall 'Basic Wall:Generic:1' has a malformed GlobalId; skipped "
        "(GlobalId 'not-a-global-id': no object with it was loaded)",
    )
    # A model-level issue has no GlobalId to add.
    assert (by_rule["unit_assumed_si"]["object_id"], by_rule["unit_assumed_si"]["message"]) == (
        None,
        "the file declares no AREAUNIT; assuming m2",
    )


# --------------------------------------------------------------------------- one model only


def test_a_run_reports_only_the_objects_of_its_own_model(
    conn: Connection, model_id: int, fixture_records: ModelRecords
) -> None:
    """The same file loaded twice: each model's run finds its own eighteen, never the other's."""
    second = dataclasses.replace(
        fixture_records,
        model=dataclasses.replace(fixture_records.model, file_sha256=new_sha256()),
    )
    other_id = _load(conn, second)

    first_findings = _findings(conn, _check(conn, model_id))
    other_findings = _findings(conn, _check(conn, other_id))

    assert {f["model_id"] for f in first_findings} == {model_id}
    assert {f["model_id"] for f in other_findings} == {other_id}
    assert len(first_findings) == len(other_findings) == 18


# --------------------------------------------------------------------------- SQL rules, edge cases


def _sql_rule(rule_id: str) -> tuple[Rule, ...]:
    return tuple(rule for rule in RULES if rule.rule_id == rule_id)


def test_element_without_storey_finds_an_element_no_storey_reaches(
    conn: Connection, model_id: int
) -> None:
    storey = conn.execute(
        text("SELECT object_id FROM ifc.object WHERE global_id = :g AND model_id = :m"),
        {"g": fx.LEVEL_1, "m": model_id},
    ).scalar_one()
    lost = add_element(conn, model_id, "IfcFurnishingElement", "Chair:Lost:1")
    add_element(conn, model_id, "IfcFurnishingElement", "Chair:Found:2", storey_id=storey)

    findings = _findings(conn, _check(conn, model_id, _sql_rule("element_without_storey")))

    assert [(f["object_id"], f["message"]) for f in findings] == [
        (lost, "no storey: neither its container nor a host element leads to one")
    ]


def test_value_is_empty_counts_whitespace_but_not_text_or_numbers(
    conn: Connection, model_id: int
) -> None:
    element = add_element(conn, model_id, "IfcWall", "Basic Wall:Generic:3")
    add_property(conn, element, "Other", "Spaces", value_type="IfcLabel", value_text="  \t")
    add_property(conn, element, "Other", "Text", value_type="IfcLabel", value_text="x")
    add_property(conn, element, "Other", "Number", value_type="IfcReal", value_num=0.0)

    findings = _findings(conn, _check(conn, model_id, _sql_rule("value_is_empty")))

    assert [f["message"] for f in findings if f["object_id"] == element] == [
        "Other.Spaces = '  \t': no value"
    ]


def test_space_without_area_accepts_an_area_from_any_source(
    conn: Connection, model_id: int
) -> None:
    measured = add_spatial(conn, model_id, "IfcSpace", "F301")
    add_quantity(conn, measured, "area_m2", 9.5, source="geometry", source_ref="footprint")
    unmeasured = add_spatial(conn, model_id, "IfcSpace", None)

    findings = _findings(conn, _check(conn, model_id, _sql_rule("space_without_area")))

    # The fixture's store room is still found; the space with a computed area is not.
    assert [(f["object_id"], f["message"]) for f in findings if f["global_id"] != fx.STORE] == [
        (unmeasured, "space (unnamed) has no area_m2 from any source")
    ]


# ------------------------------------------------------------- quantities that disagree


def _quantified(conn: Connection, ifc_class: str, **quantities: float) -> tuple[int, int]:
    """A model with one element or space with the given canonical quantities."""
    sync_rules(conn, RULES)
    model_id = add_model_row(conn)
    if ifc_class == "IfcSpace":
        object_id = add_spatial(conn, model_id, "IfcSpace", "101")
    else:
        object_id = add_element(conn, model_id, ifc_class, "Basic Wall:Generic:1")
    for name, number in quantities.items():
        add_quantity(conn, object_id, name, number)
    return model_id, object_id


def _rule(rule_id: str) -> tuple[Rule, ...]:
    return tuple(rule for rule in RULES if rule.rule_id == rule_id)


@pytest.mark.parametrize(
    ("rule_id", "ifc_class", "quantities", "message"),
    [
        (
            "wall_area_inconsistent",
            "IfcWall",
            {"area_m2": 11.0, "length_m": 2.0, "height_m": 5.0},
            "area_m2 = 11.000 m2 is more than 1.05 x length_m x height_m = 10.500 m2",
        ),
        (
            "slab_volume_inconsistent",
            "IfcSlab",
            {"volume_m3": 18.5, "area_m2": 123.4, "width_m": 25.4},
            "volume_m3 = 18.500 m3 is not between 0.75 and 1.1 x area_m2 x width_m = 3134.360 m3",
        ),
        (
            "space_volume_inconsistent",
            "IfcSpace",
            {"volume_m3": 30.0, "area_m2": 10.0, "height_m": 2.6},
            "volume_m3 = 30.000 m3 is more than 1.1 x area_m2 x height_m = 28.600 m3",
        ),
        (
            "space_perimeter_implausible",
            "IfcSpace",
            {"perimeter_m": 5.0, "area_m2": 10.0},
            "perimeter_m = 5.000 m is shorter than 2 x sqrt(pi x area_m2) = 11.210 m",
        ),
    ],
)
def test_a_quantity_that_contradicts_the_others_is_found(
    conn: Connection,
    rule_id: str,
    ifc_class: str,
    quantities: dict[str, float],
    message: str,
) -> None:
    model_id, object_id = _quantified(conn, ifc_class, **quantities)

    [finding] = _findings(conn, _check(conn, model_id, _rule(rule_id)))

    assert (finding["object_id"], finding["message"]) == (object_id, message)


@pytest.mark.parametrize(
    ("rule_id", "ifc_class", "quantities"),
    [
        # Just inside the allowance, and a wall without a height: nothing to compare.
        ("wall_area_inconsistent", "IfcWall", {"area_m2": 10.5, "length_m": 2.0, "height_m": 5.0}),
        ("wall_area_inconsistent", "IfcWall", {"area_m2": 99.0, "length_m": 2.0}),
        # A slab with openings has less volume than its gross area x thickness.
        ("slab_volume_inconsistent", "IfcSlab",
         {"volume_m3": 16.0, "area_m2": 105.9, "width_m": 0.18}),
        # A room under a ceiling lower than its unbounded height is smaller, never larger.
        ("space_volume_inconsistent", "IfcSpace",
         {"volume_m3": 19.0, "area_m2": 10.0, "height_m": 2.6}),
        # A circle of the area has the shortest perimeter there is.
        ("space_perimeter_implausible", "IfcSpace", {"perimeter_m": 11.22, "area_m2": 10.0}),
    ],
)  # fmt: skip
def test_quantities_that_agree_are_not_found(
    conn: Connection, rule_id: str, ifc_class: str, quantities: dict[str, float]
) -> None:
    model_id, _ = _quantified(conn, ifc_class, **quantities)

    assert _findings(conn, _check(conn, model_id, _rule(rule_id))) == []
