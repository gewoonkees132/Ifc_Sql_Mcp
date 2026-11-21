"""The audit of modern Revit exports, as tests: what already worked, and each finding's fix.

Deselected by default. Run with::

    uv run python scripts/download_audit_samples.py && uv run pytest -m modern

Every expected figure is the audit's ground truth: an IfcOpenShell reading of the file
that never imports this package (docs/audit/2026-11-modern-revit-exports.md and
docs/audit/evidence/). None is the tool's own output copied back.

The first section pins what the audit found correct, so no fix can break it. The second
holds one acceptance test per finding, taken from its GitHub issue. A finding not yet
fixed is marked ``xfail(strict=True)``. Its fix must make the test pass, and strict
xfail then fails the suite until the marker is removed in the same commit. So the marker
list below is the open part of the backlog, and ``git log -p`` on this file is its
history.
"""

import pytest
from audit_files import Modern

from ifc_sql_mcp.server import tools

pytestmark = pytest.mark.modern


def _open(finding: str, issue: int) -> pytest.MarkDecorator:
    """Mark the acceptance test of a finding that is not fixed yet."""
    return pytest.mark.xfail(strict=True, reason=f"audit finding {finding}, issue #{issue}")


# =========================================================================== what worked


def test_every_file_loads_all_its_objects(modern: Modern) -> None:
    objects = {key: result.row_counts["object"] for key, result in modern.ingested.items()}

    assert objects == {"a": 500, "b": 14_642, "c": 7_124, "d": 500, "e": 5_633, "duplex": 295}


def test_storeys_and_their_elevations_are_exact(modern: Modern) -> None:
    with modern.engine.connect() as connection:
        house = tools.list_storeys(connection, modern.model("a"))
        tower = tools.list_storeys(connection, modern.model("b"))

    assert [(s.name, s.elevation_m, s.elements) for s in house.storeys] == [
        ("Foundation", -0.8, 29),
        ("Level 1 Living Rm.", -0.55, 59),
        ("Level 1", 0.0, 242),
        ("Ceiling", 2.7, 10),
        ("Level 2", 3.0, 146),
        ("Roof Line", 6.0, 3),
    ]
    assert len(tower.storeys) == 28
    assert (tower.storeys[0].name, tower.storeys[0].elevation_m) == ("XX_AR_B02_pf", -8.57)
    assert (tower.storeys[-1].name, tower.storeys[-1].elevation_m) == ("XX_AR_L24_pf", 81.24)
    assert house.elements_without_storey == tower.elements_without_storey == 0


def test_doors_and_windows_per_level_are_exact(modern: Modern) -> None:
    # Audit, Part B step 3 (real Claude session Q6), checked against the ground truth.
    rows = modern.rows(
        "SELECT storey_name, count(*) FILTER (WHERE ifc_class_base = 'IfcDoor'), "
        "count(*) FILTER (WHERE ifc_class_base = 'IfcWindow') FROM ifc.v_element "
        "WHERE model_id = :m GROUP BY storey_name",
        m=modern.model("a"),
    )

    per_level = {name: (doors, windows) for name, doors, windows in rows if doors or windows}
    assert per_level == {
        "Level 1 Living Rm.": (2, 8),
        "Level 1": (4, 0),
        "Level 2": (10, 7),
        "Roof Line": (0, 2),
    }


@pytest.mark.parametrize(("key", "external", "walls"), [("a", 28, 47), ("b", 3_636, 6_095)])
def test_external_walls_are_exact(modern: Modern, key: str, external: int, walls: int) -> None:
    counts = modern.scalar(
        "SELECT ARRAY[count(*) FILTER (WHERE p.value_bool), count(DISTINCT o.object_id)] "
        "FROM ifc.object AS o LEFT JOIN ifc.property AS p ON p.object_id = o.object_id "
        "AND p.pset_name = 'Pset_WallCommon' AND p.prop_name = 'IsExternal' "
        "WHERE o.model_id = :m AND o.ifc_class_base = 'IfcWall'",
        m=modern.model(key),
    )

    assert counts == [external, walls]


def test_the_door_count_per_type_on_one_level_is_exact(modern: Modern) -> None:
    # Audit, Part B step 5 (session Q2): 159 doors in 6 types on XX_AR_L01.
    counts = modern.rows(
        "SELECT count(*) FROM ifc.v_element WHERE model_id = :m AND ifc_class_base = 'IfcDoor' "
        "AND storey_name = 'XX_AR_L01' GROUP BY revit_family, revit_type ORDER BY 1 DESC",
        m=modern.model("b"),
    )

    assert [count for (count,) in counts] == [77, 25, 21, 15, 14, 7]


def test_fire_ratings_added_in_revit_are_loaded(modern: Modern) -> None:
    def fire_ratings(key: str) -> object:
        return modern.scalar(
            "SELECT count(*) FROM ifc.property AS p JOIN ifc.object AS o USING (object_id) "
            "WHERE o.model_id = :m AND p.prop_name = 'FireRating'",
            m=modern.model(key),
        )

    assert fire_ratings("a") == 0
    assert fire_ratings("d") == 42 + 9  # Pset_WallCommon and Pset_CurtainWallCommon


# =========================================================================== the findings


def test_a1_modern_quantity_sets_become_quantities(modern: Modern) -> None:
    wall_area = modern.scalar(
        "SELECT sum(area_m2) FROM ifc.v_element WHERE model_id = :m AND ifc_class_base = 'IfcWall'",
        m=modern.model("a"),
    )
    spaces = modern.scalar(
        "SELECT ARRAY[count(area_m2), count(*)] FROM ifc.v_space_schedule WHERE model_id = :m",
        m=modern.model("c"),
    )

    assert wall_area == pytest.approx(946.04, abs=0.05)
    assert spaces == [163, 163]
    # Summed over every space, rooms exported twice included (A7 separates those).
    total = modern.scalar(
        "SELECT round(CAST(sum(area_m2) AS numeric), 1) FROM ifc.v_space_schedule "
        "WHERE model_id = :m",
        m=modern.model("c"),
    )
    assert float(total) == 8_090.7  # type: ignore[arg-type]
    # File E's rooms: equal to its own BaseQuantities.NetFloorArea, as the audit verified.
    rooms_e = modern.scalar(
        "SELECT round(CAST(sum(area_m2) AS numeric), 1) FROM ifc.v_space_schedule "
        "WHERE model_id = :m",
        m=modern.model("e"),
    )
    assert float(rooms_e) == 30_344.7  # type: ignore[arg-type]


def test_a2_the_stored_wall_areas_add_up_to_the_real_total(modern: Modern) -> None:
    # The query any reader would write; it gave 1,157,891,673.6 m2 before the fix.
    naive = modern.scalar(
        "SELECT sum(p.value_num) FROM ifc.property AS p JOIN ifc.object AS o USING (object_id) "
        "WHERE o.model_id = :m AND p.pset_name = 'Qto_WallBaseQuantities' "
        "AND p.prop_name = 'NetSideArea'",
        m=modern.model("a"),
    )

    assert naive == pytest.approx(946.04, abs=0.05)
    # Each wall kept its plausible area: none is flagged. File A's 67 findings are slabs:
    # 33 NetArea (the solid's surface), 33 GrossVolume (m2 x mm) and one roof's GrossArea.
    by_class = modern.rows(
        "SELECT ifc_class, count(*) FROM ifc.v_dq_finding WHERE model_id = :m "
        "AND rule_id = 'implausible_quantity' GROUP BY 1",
        m=modern.model("a"),
    )
    assert dict(by_class) == {"IfcSlab": 67}
    # File C's spaces: every GrossVolume (1,000 x area x height) and GrossPerimeter (feet
    # labelled millimetres) is kept as written, flagged, and not used as a quantity.
    flagged = modern.rows(
        "SELECT p.prop_name, count(*) FROM ifc.property AS p JOIN ifc.object AS o "
        "USING (object_id) WHERE o.model_id = :m AND p.is_implausible GROUP BY 1 ORDER BY 1",
        m=modern.model("c"),
    )
    # And 6 slabs: Revit 2021 writes NetArea as the whole solid's surface and GrossVolume
    # as GrossArea (m2) x Width (mm).
    assert dict(flagged) == {"GrossPerimeter": 163, "GrossVolume": 163 + 6, "NetArea": 6}


def test_a3_a_wall_type_counts_its_walls_not_their_openings(modern: Modern) -> None:
    with modern.engine.connect() as connection:
        takeoff = tools.quantity_takeoff(connection, modern.model("b"), "revit_type", "count")

    [group] = [
        g
        for g in takeoff.groups
        if g.group == "Basic Wall:1AR_MUR_CV003 _Interno_Esterno Poroton Ventilata"
    ]
    assert group.elements == 1_165


def test_a4_doors_and_windows_have_their_sizes(modern: Modern) -> None:
    sized = modern.scalar(
        "SELECT ARRAY[count(*) FILTER (WHERE ifc_class_base = 'IfcDoor'), "
        "count(*) FILTER (WHERE ifc_class_base = 'IfcWindow')] FROM ifc.v_element "
        "WHERE model_id = :m AND width_m IS NOT NULL AND height_m IS NOT NULL",
        m=modern.model("b"),
    )
    sliding = modern.scalar(
        "SELECT DISTINCT round(width_m::numeric, 3) FROM ifc.v_element WHERE model_id = :m "
        "AND revit_type = '0AR_POR_PI005- 80x210'",
        m=modern.model("b"),
    )

    assert sized == [1_458, 1_199]
    assert float(sliding) == 1.697  # type: ignore[arg-type]
    assert modern.findings("b", "unsupported_property_set") == 0


def test_a6_ifc4_doors_and_windows_have_their_materials(modern: Modern) -> None:
    windows = modern.scalar(
        "SELECT count(*) FROM ifc.v_element WHERE model_id = :m "
        "AND ifc_class_base = 'IfcWindow' AND materials IS NOT NULL",
        m=modern.model("a"),
    )

    assert windows == 17
    assert modern.findings("a", "unsupported_material") == 0
    assert modern.findings("c", "unsupported_material") == 0


def test_a7_rooms_exported_twice_are_found(modern: Modern) -> None:
    with modern.engine.connect() as connection:
        schedule = tools.space_schedule(connection, modern.model("c"))

    assert modern.findings("c", "duplicate_space") == 47
    assert (schedule.space_count, schedule.duplicate_spaces) == (163, 47)
    assert round(schedule.total_area_m2 or 0, 1) == 8_090.7
    assert round(schedule.total_area_without_duplicates_m2 or 0, 1) == 6_124.8
    # The other files hold no room twice.
    assert [modern.findings(k, "duplicate_space") for k in ("a", "b", "d", "e", "duplex")] == [
        0,
        0,
        0,
        0,
        0,
    ]


def test_a8_an_area_nobody_measured_is_not_zero(modern: Modern) -> None:
    with modern.engine.connect() as connection:
        takeoff = tools.quantity_takeoff(connection, modern.model("b"), "revit_type", "area_m2")

    assert takeoff.total is None
    assert takeoff.coverage.elements_with_value == 0
    assert takeoff.note.startswith("WARNING: no element in this takeoff has area_m2")
    assert "Export base quantities" in takeoff.note


def test_a9_data_quality_reports_what_the_model_lacks(modern: Modern) -> None:
    assert modern.findings("b", "model_without_quantities") == 1
    assert modern.findings("b", "model_without_spaces") == 1
    assert modern.findings("b", "placeholder_project_info") == 1
    assert modern.findings("c", "space_without_area") == 0
    # Fire ratings: none on B's 8,079 walls, doors and slabs; D is A with 51 of its 107
    # given one. IfcOpenShell's get_psets counts the same.
    assert modern.findings("b", "missing_fire_rating") == 8_079
    assert (
        modern.findings("a", "missing_fire_rating"),
        modern.findings("d", "missing_fire_rating"),
    ) == (107, 56)
    placeholders = dict(
        modern.rows(
            "SELECT substring(message FROM '^material ''(.*)'', used'), "
            "substring(message FROM 'used by ([0-9]+) elements')::int "
            "FROM ifc.v_dq_finding WHERE model_id = :m AND rule_id = 'placeholder_material'",
            m=modern.model("b"),
        )
    )
    assert placeholders["Default Wall"] == 6_024
    assert placeholders["Materiale per il rendering 0-127-255"] == 1_199


def test_a9_the_loaders_gaps_are_reported_apart_from_the_models(modern: Modern) -> None:
    by_scope = dict(
        modern.rows(
            "SELECT scope, array_agg(DISTINCT rule_id ORDER BY rule_id) FROM ifc.v_dq_finding "
            "WHERE model_id = :m GROUP BY scope",
            m=modern.model("b"),
        )
    )

    # What the architect can fix in Revit...
    assert "model_without_quantities" in by_scope["model_content"]
    # ...and what this tool does not read yet, which no longer counts against the model.
    assert set(by_scope["loader_coverage"]) <= {
        "measure_not_converted",
        "revit_name_unparsed",
        "unsupported_property",
        "unsupported_property_set",
    }
    with modern.engine.connect() as connection:
        report = tools.data_quality_report(
            connection,
            modern.model("b"),
            severity=None,
            rule_id=None,
            samples_per_rule=0,
            scope="loader_coverage",
        )
    assert report.findings_by_scope is not None
    assert report.findings_by_scope["model_content"].error == 0
    assert {rule.scope for rule in report.rules} == {"loader_coverage"}


@_open("A12", 14)
def test_a12_a_placeholder_project_number_joins_no_project(modern: Modern) -> None:
    tower = modern.scalar(
        "SELECT count(*) FROM ifc.v_storey_alignment "
        "WHERE model_id = :m AND project_model_count > 1",
        m=modern.model("b"),
    )

    assert tower == 0


@_open("A13", 15)
def test_a13_normal_revit_behaviour_is_not_reported(modern: Modern) -> None:
    unconverted_u_values = modern.scalar(
        "SELECT count(*) FROM ifc.v_dq_finding WHERE model_id = :m "
        "AND rule_id = 'measure_not_converted' AND message LIKE '%ThermalTransmittance%'",
        m=modern.model("b"),
    )
    opening_tags = modern.scalar(
        "SELECT count(*) FROM ifc.v_dq_finding WHERE model_id = :m "
        "AND rule_id = 'revit_tag_mismatch' AND ifc_class LIKE 'IfcOpening%'",
        m=modern.model("b"),
    )

    assert unconverted_u_values == 0
    assert opening_tags == 0


@_open("A14", 16)
def test_a14_every_stair_flight_has_its_revit_type(modern: Modern) -> None:
    typed = modern.scalar(
        "SELECT count(revit_type) FROM ifc.v_element "
        "WHERE model_id = :m AND ifc_class_base = 'IfcStairFlight'",
        m=modern.model("b"),
    )

    assert typed == 182


@_open("A16", 18)
def test_a16_revit_types_hold_no_control_characters(modern: Modern) -> None:
    with_control = modern.scalar(
        "SELECT count(*) FROM ifc.element AS e JOIN ifc.object AS o USING (object_id) "
        r"WHERE o.model_id = :m AND e.revit_type ~ '[\x01-\x1f]'",
        m=modern.model("b"),
    )

    assert with_control == 0
