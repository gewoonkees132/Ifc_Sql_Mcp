"""The pipeline on the four real Duplex files: every figure docs/revit-ifc-quirks.md cites.

Deselected by default. Run with::

    make samples && uv run pytest -m sample

The figures come from the metric functions in ``scripts/quirks_report.py``, the script
that prints the tables in the document, so the two cannot drift apart. A few facts are
also checked directly on the records.
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import quirks_report as report
from download_samples import DEFAULT_DEST, SAMPLES, ensure_sample
from record_contract import violations

from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.transform import transform

pytestmark = pytest.mark.sample

EXPECTED: dict[str, dict[str, dict[str, int]]] = {
    "arc": {
        "objects": {
            "elements": 268,
            "IfcSpace": 21,
            "IfcBuildingStorey": 4,
            "classes renamed by ifc_class_base": 56,
            "typed elements": 99,
            "typed, revit_type = type name": 99,
        },
        "element_names": {
            "Family:Type:Id": 105,
            "Family:Type:Id + :n": 8,
            "Family:Type:Label:Id (label = type)": 107,
            "Family:Type:Label:Id (label = type) + :n": 46,
            "no name": 2,
        },
        "tag_agreement": {
            "Tag = parsed ElementId": 230,
            "Tag is NULL": 36,
            "Tag set, no ElementId in the name": 2,
        },
        "containment": {
            "contained in IfcBuildingStorey": 146,
            "contained in IfcSpace": 61,
            "no container": 61,
            "no container, storey via host": 61,
        },
        "property_values": {
            "IfcPropertySingleValue entities": 5213,
            "property rows": 13455,
            "rows from quantity sets": 21,
            "rows with is_type_property": 8594,
            "rows in PSet_Revit_Type_* sets": 8594,
            "placeholder (value = name)": 7650,
            "empty string": 473,
            "rows in radians": 16,
        },
        "same_named_sets": {},
        "quantities": {
            "area_m2 (ifc_qto)": 21,
            "area_m2 (revit_pset)": 115,
            "height_m (revit_pset)": 21,
            "perimeter_m (revit_pset)": 54,
            "volume_m3 (revit_pset)": 131,
            "length_m (revit_pset)": 84,
        },
        "materials": {
            "materials": 18,
            "elements with layers (thickness)": 91,
            "distinct layer build-ups": 15,
            "elements with one material, no thickness": 8,
            "layer rows": 271,
        },
        "classifications": {"OmniClass Table 13": 21, "OmniClass Table 23": 117},
        "issues": {},
    },
    "mep": {
        "objects": {
            "elements": 926,
            "IfcSpace": 42,
            "IfcBuildingStorey": 3,
            "typed elements": 926,
            "typed, revit_type = type name": 507,
        },
        "element_names": {
            "Family:Type:Id": 419,
            "Family:Type:Label:Id (label = type)": 503,
            "Family:Type:Label:Id (label differs)": 4,
        },
        "tag_agreement": {"Tag = parsed ElementId": 926},
        "containment": {"contained in IfcBuildingStorey": 759, "contained in IfcSpace": 167},
        "property_values": {
            "IfcPropertySingleValue entities": 28129,
            "property rows": 63683,
            "rows from quantity sets": 42,
            "rows with is_type_property": 35268,
            "rows in PSet_Revit_Type_* sets": 35268,
            "placeholder (value = name)": 29967,
            "empty string": 1961,
            "rows in radians": 420,
        },
        "same_named_sets": {},
        "quantities": {
            "length_m (revit_pset)": 433,
            "area_m2 (ifc_qto)": 42,
            "area_m2 (revit_pset)": 42,
            "height_m (revit_pset)": 42,
            "perimeter_m (revit_pset)": 42,
            "volume_m3 (revit_pset)": 42,
        },
        "materials": {},
        "classifications": {"OmniClass Table 13": 42, "OmniClass Table 23": 507},
        "issues": {"quantity_not_a_measure": 44, "conflicting_property": 2},
    },
    "ele": {
        "objects": {
            "elements": 103,
            "IfcSpace": 1,
            "IfcBuildingStorey": 3,
            "typed elements": 99,
            "typed, revit_type = type name": 99,
        },
        "element_names": {
            "Family:Type:Label:Id (label = type)": 99,
            "Family:Type:Label:Id (label differs)": 4,
        },
        "tag_agreement": {"Tag = parsed ElementId": 103},
        "containment": {"contained in IfcBuildingStorey": 101, "contained in IfcSpace": 2},
        "property_values": {
            "IfcPropertySingleValue entities": 2366,
            "property rows": 6862,
            "rows from quantity sets": 1,
            "placeholder (value = name)": 1,
            "placeholder (value = 'N/A')": 3018,
            "empty string": 273,
        },
        "same_named_sets": {"objects with two sets of one name": 106},
        "quantities": {
            "area_m2 (ifc_qto)": 1,
            "area_m2 (revit_pset)": 1,
            "height_m (revit_pset)": 1,
            "perimeter_m (revit_pset)": 1,
            "volume_m3 (revit_pset)": 1,
            "length_m (revit_pset)": 2,
        },
        "materials": {
            "materials": 6,
            "elements with one material, no thickness": 6,
            "elements with a material list": 14,
            "layer rows": 36,
        },
        "classifications": {"OmniClass Table 23": 103, "UniFormat": 14},
        "issues": {
            "omniclass_unparsed": 1,
            "incomplete_classification_reference": 85,
            "conflicting_property": 2,
        },
    },
    "plu": {
        "objects": {
            "elements": 498,
            "IfcSpace": 1,
            "IfcBuildingStorey": 3,
            "typed elements": 498,
            "typed, revit_type = type name": 267,
        },
        "element_names": {"Family:Type:Id": 231, "Family:Type:Label:Id (label = type)": 267},
        "tag_agreement": {"Tag = parsed ElementId": 498},
        "containment": {"contained in IfcBuildingStorey": 496, "contained in IfcSpace": 2},
        "property_values": {
            "IfcPropertySingleValue entities": 16049,
            "property rows": 41649,
            "rows from quantity sets": 1,
            "placeholder (value = name)": 921,
            "placeholder (value = 'N/A')": 11464,
            "empty string": 1759,
            "rows in radians": 228,
        },
        "same_named_sets": {"objects with two sets of one name": 501},
        "quantities": {
            "area_m2 (ifc_qto)": 1,
            "area_m2 (revit_pset)": 1,
            "height_m (revit_pset)": 1,
            "perimeter_m (revit_pset)": 1,
            "volume_m3 (revit_pset)": 1,
            "length_m (revit_pset)": 231,
        },
        "materials": {
            "materials": 3,
            "elements with one material, no thickness": 2,
            "elements with a material list": 2,
            "layer rows": 6,
        },
        "classifications": {"OmniClass Table 23": 257, "UniFormat": 4},
        "issues": {
            "omniclass_unparsed": 1,
            "incomplete_classification_reference": 263,
            "conflicting_property": 225,
            "quantity_not_a_measure": 61,
        },
    },
}

LENGTH_UNITS = {"arc": "m", "mep": "m", "ele": "ft", "plu": "mm"}


@pytest.fixture(scope="module", params=report.FILES)
def loaded(request: pytest.FixtureRequest) -> report.Loaded:
    key: str = request.param
    path, _ = ensure_sample(SAMPLES[key], Path(DEFAULT_DEST))
    raw = read_model(path)
    return report.Loaded(key, raw, transform(raw))


@pytest.mark.parametrize("metric", sorted(EXPECTED["arc"]))
def test_reported_figures(loaded: report.Loaded, metric: str) -> None:
    measured: Any = getattr(report, metric)(loaded)
    expected = EXPECTED[loaded.key][metric]
    # Metrics that are zero in a file are left out of EXPECTED; Counter returns 0 for them.
    assert {k: v for k, v in measured.items() if v} == expected


def test_records_satisfy_the_schema(loaded: report.Loaded) -> None:
    assert violations(loaded.records) == []


def test_model_facts(loaded: report.Loaded) -> None:
    model = loaded.records.model
    assert model.file_sha256 == SAMPLES[loaded.key].sha256
    assert (model.ifc_schema, model.mvd) == ("IFC2X3", "CoordinationView")
    assert (model.project_name, model.project_long_name) == ("0001", "Duplex Apartment")
    assert model.length_unit == LENGTH_UNITS[loaded.key]
    # The architectural model was exported from a different Revit project.
    expected_project = "1xS3BCk291UvhgP2a6eflL" if loaded.key == "arc" else "0o8nNAbaT5OQLV8CAFemQP"
    assert model.project_global_id == expected_project


def test_storeys_agree_across_units_after_rounding(loaded: report.Loaded) -> None:
    names = {o.global_id: o.name for o in loaded.records.objects}
    elevations = {
        names[s.global_id]: s.elevation_m
        for s in loaded.records.spatial_elements
        if s.elevation_m is not None
    }
    expected = {"Level 1": Decimal("0.000"), "Level 2": Decimal("3.100"), "Roof": Decimal("6.000")}
    if loaded.key == "arc":
        expected["T/FDN"] = Decimal("-1.250")
    assert elevations == expected


def test_the_roof_space_has_the_same_area_in_every_unit(loaded: report.Loaded) -> None:
    space = next(o for o in loaded.records.objects if o.name == "R301")
    areas = {
        q.source: q.value
        for q in loaded.records.quantities
        if q.global_id == space.global_id and q.name == "area_m2"
    }
    assert areas == {
        "ifc_qto": pytest.approx(145.7216889999995),
        "revit_pset": pytest.approx(145.7216889999995),
    }


def test_every_quantity_points_at_a_property_row(loaded: report.Loaded) -> None:
    rows = {(p.global_id, f"{p.pset_name}.{p.prop_name}") for p in loaded.records.properties}
    assert {(q.global_id, q.source_ref) for q in loaded.records.quantities} <= rows
