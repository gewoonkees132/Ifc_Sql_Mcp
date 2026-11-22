-- ifc.v_space_schedule, version 3. Created by migration 0019; it replaces version 2.
-- duplicate_of now comes from the spaces' plans (spatial_element.repeats_id): a space whose
-- plan lies at least 90 % inside larger spaces on its storey covers their floor again, a
-- room exported twice even when its number or name differs (re-check finding R1). A space
-- without a plan keeps version 2's key: the same number, name, storey and floor area.
-- It also adds category (Revit's Rooms, Spaces or Areas) and footprint_m2.
-- Released view files are never edited: a change goes into v_space_schedule.v4.sql and a
-- new migration, so that migration 0019 keeps creating exactly this view.

CREATE VIEW ifc.v_space_schedule AS
SELECT
    o.object_id,
    o.model_id,
    m.discipline,
    o.global_id,
    o.name AS number,
    s.long_name AS name,
    storey.object_id AS storey_id,
    storey.name AS storey_name,
    q.area_m2,
    q.perimeter_m,
    q.volume_m3,
    q.height_m,
    omniclass.code AS omniclass_code,
    omniclass.title AS omniclass_title,
    s.revit_category AS category,
    s.footprint_m2,
    CASE
        -- Measured at ingest: the larger space whose floor this one covers again.
        WHEN s.footprint_m2 IS NOT NULL THEN s.repeats_id
        -- No plan: the first space (lowest object_id) with the same number, name, storey
        -- and floor area (to the square centimetre), when it is another one. Spaces that
        -- only share a number (two shafts both numbered 02055) are not marked.
        ELSE nullif(
            first_value(o.object_id) OVER (
                PARTITION BY
                    o.model_id,
                    o.name,
                    s.long_name,
                    s.parent_id,
                    round(CAST(q.area_m2 AS numeric), 2)
                ORDER BY o.object_id
            ),
            o.object_id
        )
    END AS duplicate_of
FROM ifc.spatial_element AS s
INNER JOIN ifc.object AS o ON s.object_id = o.object_id
INNER JOIN ifc.model AS m ON o.model_id = m.model_id
LEFT JOIN ifc.object AS storey
    ON s.parent_id = storey.object_id AND storey.ifc_class_base = 'IfcBuildingStorey'
LEFT JOIN LATERAL (
    -- v_quantity has at most one row per name, so max() only picks that row's value.
    SELECT
        max(vq.value) FILTER (WHERE vq.name = 'area_m2') AS area_m2,
        max(vq.value) FILTER (WHERE vq.name = 'perimeter_m') AS perimeter_m,
        max(vq.value) FILTER (WHERE vq.name = 'volume_m3') AS volume_m3,
        max(vq.value) FILTER (WHERE vq.name = 'height_m') AS height_m
    FROM ifc.v_quantity AS vq
    WHERE vq.object_id = o.object_id
) AS q ON true
LEFT JOIN LATERAL (
    -- A space normally has one OmniClass Table 13 code; if it has more, show the lowest
    -- code so the schedule stays one row per space and the choice is repeatable.
    SELECT
        c.code,
        c.title
    FROM ifc.classification AS c
    WHERE c.object_id = o.object_id AND c.system = 'OmniClass Table 13'
    ORDER BY c.code
    LIMIT 1
) AS omniclass ON true
WHERE o.ifc_class_base = 'IfcSpace';

COMMENT ON VIEW ifc.v_space_schedule IS
    'Room schedule: one row per space with its number, name, storey, canonical '
    'quantities and OmniClass Table 13 classification. A room exported twice (Revit Room '
    'and MEP Space) has two rows; filter duplicate_of IS NULL to count each room once. '
    'Spaces of category Areas are Revit area plans, not rooms: leave them out of room totals.';
COMMENT ON COLUMN ifc.v_space_schedule.object_id IS 'Space key (ifc.object).';
COMMENT ON COLUMN ifc.v_space_schedule.model_id IS 'Model the space was loaded from.';
COMMENT ON COLUMN ifc.v_space_schedule.discipline IS 'Discipline of the model.';
COMMENT ON COLUMN ifc.v_space_schedule.global_id IS 'IFC GlobalId.';
COMMENT ON COLUMN ifc.v_space_schedule.number IS 'Space number (IFC Name), e.g. A101.';
COMMENT ON COLUMN ifc.v_space_schedule.name IS 'Space name (IFC LongName), e.g. Kitchen.';
COMMENT ON COLUMN ifc.v_space_schedule.storey_id IS 'Storey the space belongs to.';
COMMENT ON COLUMN ifc.v_space_schedule.storey_name IS 'Name of that storey.';
COMMENT ON COLUMN ifc.v_space_schedule.area_m2 IS 'Floor area in square metres.';
COMMENT ON COLUMN ifc.v_space_schedule.perimeter_m IS 'Perimeter in metres.';
COMMENT ON COLUMN ifc.v_space_schedule.volume_m3 IS 'Volume in cubic metres.';
COMMENT ON COLUMN ifc.v_space_schedule.height_m IS 'Height in metres.';
COMMENT ON COLUMN ifc.v_space_schedule.omniclass_code IS
    'OmniClass Table 13 code, e.g. 13-51 24 11.';
COMMENT ON COLUMN ifc.v_space_schedule.omniclass_title IS
    'OmniClass Table 13 title, e.g. General Residential Space.';
COMMENT ON COLUMN ifc.v_space_schedule.category IS
    'Revit category the file states: Rooms, Spaces (MEP) or Areas (an area plan, not a '
    'room). NULL when the file states none.';
COMMENT ON COLUMN ifc.v_space_schedule.footprint_m2 IS
    'Area of the space''s plan, measured from its body at ingest, in square metres.';
COMMENT ON COLUMN ifc.v_space_schedule.duplicate_of IS
    'object_id of the space whose floor this one covers again: its plan lies at least 90 % '
    'inside larger spaces on the storey (or, without a plan, the same number, name, storey '
    'and floor area). A room exported twice, as a Revit Room and an MEP Space. NULL for the '
    'first or only one. Leave these rows out of area totals.';
