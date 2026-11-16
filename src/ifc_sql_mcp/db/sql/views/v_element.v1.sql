-- ifc.v_element, version 1. Created by migration 0002.
-- Released view files are never edited: a change goes into v_element.v2.sql and a new
-- migration, so that migration 0002 keeps creating exactly this view.

CREATE VIEW ifc.v_element AS
SELECT
    o.object_id,
    o.model_id,
    m.discipline,
    o.global_id,
    o.ifc_class,
    o.ifc_class_base,
    o.name,
    e.revit_family,
    e.revit_type,
    e.revit_element_id,
    e.type_name,
    e.predefined_type,
    e.tag,
    e.storey_id,
    storey.name AS storey_name,
    storey_detail.elevation_m AS storey_elevation_m,
    space.object_id AS space_id,
    space.name AS space_number,
    space_detail.long_name AS space_name,
    q.length_m,
    q.area_m2,
    q.volume_m3,
    q.perimeter_m,
    q.height_m,
    materials.materials
FROM ifc.element AS e
INNER JOIN ifc.object AS o ON e.object_id = o.object_id
INNER JOIN ifc.model AS m ON o.model_id = m.model_id
LEFT JOIN ifc.object AS storey ON e.storey_id = storey.object_id
LEFT JOIN ifc.spatial_element AS storey_detail ON e.storey_id = storey_detail.object_id
-- Only a space counts as "the room the element is in"; most elements sit in a storey.
LEFT JOIN ifc.object AS space
    ON e.container_id = space.object_id AND space.ifc_class_base = 'IfcSpace'
LEFT JOIN ifc.spatial_element AS space_detail ON space.object_id = space_detail.object_id
-- LATERAL lets a filter on this view (one model, one class) look up quantities and
-- materials only for the rows it keeps, through the primary key indexes.
LEFT JOIN LATERAL (
    -- v_quantity has at most one row per name, so max() only picks that row's value.
    SELECT
        max(vq.value) FILTER (WHERE vq.name = 'length_m') AS length_m,
        max(vq.value) FILTER (WHERE vq.name = 'area_m2') AS area_m2,
        max(vq.value) FILTER (WHERE vq.name = 'volume_m3') AS volume_m3,
        max(vq.value) FILTER (WHERE vq.name = 'perimeter_m') AS perimeter_m,
        max(vq.value) FILTER (WHERE vq.name = 'height_m') AS height_m
    FROM ifc.v_quantity AS vq
    WHERE vq.object_id = e.object_id
) AS q ON true
LEFT JOIN LATERAL (
    SELECT string_agg(mat.name, ' / ' ORDER BY layer.layer_index) AS materials
    FROM ifc.element_material_layer AS layer
    INNER JOIN ifc.material AS mat ON layer.material_id = mat.material_id
    WHERE layer.element_id = e.object_id
) AS materials ON true;

COMMENT ON VIEW ifc.v_element IS
    'One flat row per physical element: identity, Revit name parts, storey, room, '
    'canonical quantities (from ifc.v_quantity) and materials. The usual starting point '
    'for questions about elements.';
COMMENT ON COLUMN ifc.v_element.object_id IS 'Element key (ifc.object / ifc.element).';
COMMENT ON COLUMN ifc.v_element.model_id IS 'Model the element was loaded from.';
COMMENT ON COLUMN ifc.v_element.discipline IS 'Discipline of the model, e.g. ARC or MEP.';
COMMENT ON COLUMN ifc.v_element.global_id IS 'IFC GlobalId.';
COMMENT ON COLUMN ifc.v_element.ifc_class IS 'Exact IFC entity, e.g. IfcWallStandardCase.';
COMMENT ON COLUMN ifc.v_element.ifc_class_base IS
    'Normalised IFC entity for filtering and grouping, e.g. IfcWall.';
COMMENT ON COLUMN ifc.v_element.name IS 'IFC Name, verbatim (Revit: Family:Type:ElementId).';
COMMENT ON COLUMN ifc.v_element.revit_family IS 'Revit family, from the name.';
COMMENT ON COLUMN ifc.v_element.revit_type IS 'Revit type, from the name; may contain colons.';
COMMENT ON COLUMN ifc.v_element.revit_element_id IS
    'Revit ElementId, from the name. Not unique: openings repeat their host''s id.';
COMMENT ON COLUMN ifc.v_element.type_name IS 'Name of the assigned IfcTypeObject, if any.';
COMMENT ON COLUMN ifc.v_element.predefined_type IS 'IFC PredefinedType, e.g. FLOOR.';
COMMENT ON COLUMN ifc.v_element.tag IS 'IFC Tag (Revit writes the ElementId here).';
COMMENT ON COLUMN ifc.v_element.storey_id IS 'Storey the element is on.';
COMMENT ON COLUMN ifc.v_element.storey_name IS 'Name of that storey, e.g. Level 1.';
COMMENT ON COLUMN ifc.v_element.storey_elevation_m IS 'Elevation of that storey in metres.';
COMMENT ON COLUMN ifc.v_element.space_id IS
    'Space (room) that directly contains the element; NULL when it sits in a storey.';
COMMENT ON COLUMN ifc.v_element.space_number IS 'Number of that space, e.g. A101.';
COMMENT ON COLUMN ifc.v_element.space_name IS 'Name of that space, e.g. Kitchen.';
COMMENT ON COLUMN ifc.v_element.length_m IS 'Length in metres.';
COMMENT ON COLUMN ifc.v_element.area_m2 IS
    'Area in square metres (walls: one face; slabs and spaces: plan area).';
COMMENT ON COLUMN ifc.v_element.volume_m3 IS 'Volume in cubic metres.';
COMMENT ON COLUMN ifc.v_element.perimeter_m IS 'Perimeter in metres.';
COMMENT ON COLUMN ifc.v_element.height_m IS 'Height in metres.';
COMMENT ON COLUMN ifc.v_element.materials IS
    'Material names in layer order, separated by " / ".';
