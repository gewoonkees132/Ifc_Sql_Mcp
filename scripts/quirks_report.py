#!/usr/bin/env python3
"""Print the figures that docs/revit-ifc-quirks.md cites, computed by the pipeline itself.

Usage (after ``make samples``)::

    uv run python scripts/quirks_report.py

It runs ``extract`` and ``transform`` on the four pinned Duplex files and prints one
Markdown table per topic, one column per file. Nothing is computed outside the
pipeline's own records, so the document shows what the code does, not what a separate
analysis believes. ``tests/samples/test_duplex.py`` asserts the same figures.
"""

import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from download_samples import DEFAULT_DEST, SAMPLES

from ifc_sql_mcp.ingest.extract import read_model
from ifc_sql_mcp.ingest.raw import RawModel
from ifc_sql_mcp.ingest.records import ModelRecords
from ifc_sql_mcp.ingest.transform import transform

FILES = ("arc", "mep", "ele", "plu")


@dataclass(frozen=True)
class Loaded:
    """One sample file, extracted and transformed."""

    key: str
    raw: RawModel
    records: ModelRecords


def name_shape(name: str | None, element_id: int | None) -> str:
    """Classify a Revit name by its segments, once the ElementId is known."""
    if name is None:
        return "no name"
    if element_id is None:
        return "not a Revit name"
    segments = name.split(":")
    position = segments.index(str(element_id))
    suffix = " + :n" if position < len(segments) - 1 else ""
    body = segments[:position]
    if len(body) == 2:
        return "Family:Type:Id" + suffix
    if len(body) == 3:
        label = "label = type" if body[1] == body[2] else "label differs"
        return f"Family:Type:Label:Id ({label})" + suffix
    return "other"


def element_names(m: Loaded) -> Counter[str]:
    """Count elements by name shape."""
    names = {o.global_id: o.name for o in m.records.objects}
    return Counter(name_shape(names[e.global_id], e.revit_element_id) for e in m.records.elements)


def tag_agreement(m: Loaded) -> Counter[str]:
    """Compare each element's parsed ElementId with its Tag."""
    counts: Counter[str] = Counter()
    for element in m.records.elements:
        if element.tag is None:
            counts["Tag is NULL"] += 1
        elif element.revit_element_id is None:
            counts["Tag set, no ElementId in the name"] += 1
        elif str(element.revit_element_id) == element.tag:
            counts["Tag = parsed ElementId"] += 1
        else:
            counts["Tag differs"] += 1
    return counts


def containment(m: Loaded) -> Counter[str]:
    """Count elements by the class of their direct container, and how storeys resolve."""
    classes = {o.global_id: o.ifc_class_base for o in m.records.objects}
    counts: Counter[str] = Counter()
    for element in m.records.elements:
        container = classes.get(element.container_global_id or "")
        counts[f"contained in {container}" if container else "no container"] += 1
        if container is None and element.storey_global_id is not None:
            counts["no container, storey via host"] += 1
        if element.storey_global_id is None:
            counts["no storey"] += 1
    return counts


def same_named_sets(m: Loaded) -> Counter[str]:
    """Count objects that carry two different property sets with the same name."""
    counts: Counter[str] = Counter()
    for obj in (*m.raw.spatial_elements, *m.raw.elements):
        sets: dict[str | None, set[int]] = {}
        for value in obj.values:
            sets.setdefault(value.set_name, set()).add(value.order[0])
        if any(len(ids) > 1 for ids in sets.values()):
            counts["objects with two sets of one name"] += 1
    return counts


def property_values(m: Loaded) -> Counter[str]:
    """Count property entities, rows, placeholders and type properties."""
    entities = {
        value.order[1]
        for obj in (*m.raw.spatial_elements, *m.raw.elements)
        for value in obj.values
        if not value.from_quantity_set
    }
    rows = m.records.properties
    return Counter(
        {
            "IfcPropertySingleValue entities": len(entities),
            "property rows": len(rows),
            "rows from quantity sets": sum(
                1
                for obj in (*m.raw.spatial_elements, *m.raw.elements)
                for value in obj.values
                if value.from_quantity_set
            ),
            "rows with is_type_property": sum(1 for r in rows if r.is_type_property),
            "rows in PSet_Revit_Type_* sets": sum(
                1 for r in rows if r.pset_name.startswith("PSet_Revit_Type_")
            ),
            "rows from an IfcTypeObject": sum(
                1
                for obj in (*m.raw.spatial_elements, *m.raw.elements)
                for value in obj.values
                if value.from_type
            ),
            "placeholder (value = name)": sum(1 for r in rows if r.value_text == r.prop_name),
            "placeholder (value = 'N/A')": sum(1 for r in rows if r.value_text == "N/A"),
            "empty string": sum(1 for r in rows if r.value_text == ""),
            "rows in radians": sum(1 for r in rows if r.unit == "rad"),
        }
    )


def quantities(m: Loaded) -> Counter[str]:
    """Count quantity rows by name and source."""
    return Counter(f"{q.name} ({q.source})" for q in m.records.quantities)


def gsa_versus_revit(m: Loaded) -> str:
    """Compare the GSA BIM Area with the Revit Area of each space."""
    areas: dict[str, dict[str, float]] = {}
    for q in m.records.quantities:
        if q.name == "area_m2":
            areas.setdefault(q.global_id, {})[q.source] = q.value
    both = [a for a in areas.values() if {"ifc_qto", "revit_pset"} <= a.keys()]
    worst = max((abs(a["ifc_qto"] - a["revit_pset"]) for a in both), default=0.0)
    return f"{len(both)} spaces with both, largest difference `{worst:.1e}` m2"


def materials(m: Loaded) -> Counter[str]:
    """Count materials and how elements are made of them."""
    by_element: dict[str, list[float | None]] = {}
    for layer in m.records.material_layers:
        by_element.setdefault(layer.element_global_id, []).append(layer.thickness_m)
    build_ups = {
        tuple(
            (layer.material_name, layer.thickness_m)
            for layer in m.records.material_layers
            if layer.element_global_id == element
        )
        for element, thicknesses in by_element.items()
        if all(t is not None for t in thicknesses)
    }
    return Counter(
        {
            "materials": len(m.records.materials),
            "elements with layers (thickness)": sum(
                1 for t in by_element.values() if all(x is not None for x in t)
            ),
            "distinct layer build-ups": len(build_ups),
            "elements with one material, no thickness": sum(
                1 for t in by_element.values() if t == [None]
            ),
            "elements with a material list": sum(
                1 for t in by_element.values() if len(t) > 1 and all(x is None for x in t)
            ),
            "layer rows": len(m.records.material_layers),
        }
    )


def classifications(m: Loaded) -> Counter[str]:
    """Count classification rows by system."""
    return Counter(c.system for c in m.records.classifications)


def issues(m: Loaded) -> Counter[str]:
    """Count issues by code."""
    return Counter(issue.code.value for issue in m.records.issues)


def objects(m: Loaded) -> Counter[str]:
    """Count objects by base class family, and typed elements."""
    base = Counter(
        o.ifc_class_base for o in m.records.objects if o.object_kind == "spatial_element"
    )
    typed = [e for e in m.records.elements if e.type_name is not None]
    return Counter(
        {
            "elements": sum(1 for o in m.records.objects if o.object_kind == "element"),
            "IfcSpace": base["IfcSpace"],
            "IfcBuildingStorey": base["IfcBuildingStorey"],
            "classes renamed by ifc_class_base": sum(
                1 for o in m.records.objects if o.ifc_class != o.ifc_class_base
            ),
            "typed elements": len(typed),
            "typed, revit_type = type name": sum(1 for e in typed if e.revit_type == e.type_name),
        }
    )


def roof_space_area(m: Loaded) -> str:
    """Show the roof space's Revit Area as written and as stored, to compare units."""
    space = next(s for s in m.raw.spatial_elements if s.name == "R301")
    written = next(v.value for v in space.values if v.name == "Area" and not v.from_quantity_set)
    stored = next(
        q.value
        for q in m.records.quantities
        if q.global_id == space.global_id and q.name == "area_m2" and q.source == "revit_pset"
    )
    return f"Area `{written!r}` {m.records.model.length_unit}2 -> `{stored:.4f}` m2"


def table(title: str, loaded: Iterable[Loaded], metric: Callable[[Loaded], Counter[str]]) -> str:
    """Render one metric as a Markdown table, rows in first-seen order."""
    columns = list(loaded)
    counts = [metric(m) for m in columns]
    rows: list[str] = []
    for count in counts:
        rows.extend(key for key in count if key not in rows)
    lines = [
        f"### {title}",
        "",
        "| | " + " | ".join(m.key.upper() for m in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for row in rows:
        cells = " | ".join(f"{count[row]:,}" for count in counts)
        lines.append(f"| {row} | {cells} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    """Print the report; returns the process exit code."""
    loaded: list[Loaded] = []
    for key in FILES:
        path = Path(DEFAULT_DEST) / SAMPLES[key].file_name
        if not path.exists():
            print(f"missing {path}; run `make samples` first", file=sys.stderr)
            return 1
        raw = read_model(path)
        loaded.append(Loaded(key, raw, transform(raw)))

    for m in loaded:
        model = m.records.model
        storeys = sorted(
            (s for s in m.raw.spatial_elements if s.elevation is not None),
            key=lambda s: s.elevation or 0.0,
        )
        stored = {s.global_id: s.elevation_m for s in m.records.spatial_elements}
        print(
            f"- **{m.key.upper()}** `{model.file_name}`: {model.authoring_app}, "
            f"length unit `{model.length_unit}`, project `{model.project_name}` / "
            f"`{model.project_long_name}`, GlobalId `{model.project_global_id}`"
        )
        for storey in storeys:
            print(f"  - {storey.name}: `{storey.elevation!r}` -> `{stored[storey.global_id]}` m")
        print(f"  - space R301: {roof_space_area(m)}")
        print(f"  - GSA BIM Area versus Revit Area: {gsa_versus_revit(m)}")
    print()
    for title, metric in (
        ("Objects", objects),
        ("Element names", element_names),
        ("Tag versus parsed ElementId", tag_agreement),
        ("Containment and storeys", containment),
        ("Property values", property_values),
        ("Same-named property sets", same_named_sets),
        ("Quantities", quantities),
        ("Materials", materials),
        ("Classifications", classifications),
        ("Issues", issues),
    ):
        print(table(title, loaded, metric))
    return 0


if __name__ == "__main__":
    sys.exit(main())
