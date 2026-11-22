-- ifc.v_quantity, version 1. Created by migration 0002.
-- Released view files are never edited: a change goes into v_quantity.v2.sql and a new
-- migration, so that migration 0002 keeps creating exactly this view.

CREATE VIEW ifc.v_quantity AS
SELECT DISTINCT ON (q.object_id, q.name)
    q.object_id,
    q.name,
    q.value,
    q.source,
    q.source_ref
FROM ifc.quantity AS q
ORDER BY
    q.object_id,
    q.name,
    -- Precedence: an IFC quantity set written by the exporter, then Revit's own parameter,
    -- then a value computed from the geometry by this project.
    CASE q.source
        WHEN 'ifc_qto' THEN 1
        WHEN 'revit_pset' THEN 2
        WHEN 'geometry' THEN 3
    END;

COMMENT ON VIEW ifc.v_quantity IS
    'One value per object and canonical quantity: where several sources supply one, the '
    'first of ifc_qto, revit_pset, geometry wins. Use this instead of ifc.quantity unless '
    'you want to compare sources.';
COMMENT ON COLUMN ifc.v_quantity.object_id IS 'Measured object (ifc.object).';
COMMENT ON COLUMN ifc.v_quantity.name IS
    'Canonical quantity: length_m, area_m2, volume_m3, perimeter_m or height_m.';
COMMENT ON COLUMN ifc.v_quantity.value IS 'Value in the unit named by the suffix of name.';
COMMENT ON COLUMN ifc.v_quantity.source IS 'Source of the chosen value.';
COMMENT ON COLUMN ifc.v_quantity.source_ref IS
    'Exact origin of the chosen value, e.g. PSet_Revit_Dimensions.Area.';
