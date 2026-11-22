-- ifc.v_takeoff, version 1. Created by migration 0002.
-- Released view files are never edited: a change goes into v_takeoff.v2.sql and a new
-- migration, so that migration 0002 keeps creating exactly this view.

CREATE VIEW ifc.v_takeoff AS
WITH element_quantity AS (
    -- One row per element, its preferred quantities as columns.
    SELECT
        vq.object_id,
        max(vq.value) FILTER (WHERE vq.name = 'length_m') AS length_m,
        max(vq.value) FILTER (WHERE vq.name = 'area_m2') AS area_m2,
        max(vq.value) FILTER (WHERE vq.name = 'volume_m3') AS volume_m3
    FROM ifc.v_quantity AS vq
    GROUP BY vq.object_id
),

element_material AS (
    -- One row per element and distinct material. A material can fill several layers of
    -- one element (plasterboard on both faces of a stud wall), so count those layers and
    -- add up their thickness.
    SELECT
        layer.element_id,
        layer.material_id,
        count(*) AS layer_count,
        sum(layer.thickness_m) AS thickness_m
    FROM ifc.element_material_layer AS layer
    GROUP BY layer.element_id, layer.material_id
)

SELECT
    o.model_id,
    m.discipline,
    e.storey_id,
    storey.name AS storey_name,
    o.ifc_class_base,
    em.material_id,
    mat.name AS material_name,
    count(*) AS element_count,
    sum(eq.length_m) AS length_m,
    -- A layer covers the element's main face, so a layered material's area is the element
    -- area once per layer, and its volume is that area times the layer thickness. Without
    -- layers (or without any material), the element's own area and volume are used.
    sum(
        CASE
            WHEN em.thickness_m IS NOT NULL THEN eq.area_m2 * em.layer_count
            ELSE eq.area_m2
        END
    ) AS area_m2,
    sum(
        CASE
            WHEN em.thickness_m IS NOT NULL THEN eq.area_m2 * em.thickness_m
            ELSE eq.volume_m3
        END
    ) AS volume_m3
FROM ifc.element AS e
INNER JOIN ifc.object AS o ON e.object_id = o.object_id
INNER JOIN ifc.model AS m ON o.model_id = m.model_id
LEFT JOIN ifc.object AS storey ON e.storey_id = storey.object_id
LEFT JOIN element_quantity AS eq ON e.object_id = eq.object_id
LEFT JOIN element_material AS em ON e.object_id = em.element_id
LEFT JOIN ifc.material AS mat ON em.material_id = mat.material_id
GROUP BY
    o.model_id,
    m.discipline,
    e.storey_id,
    storey.name,
    o.ifc_class_base,
    em.material_id,
    mat.name;

COMMENT ON VIEW ifc.v_takeoff IS
    'Quantity takeoff per model, storey, element class and material. An element made of '
    'several materials counts once under each. For layered elements (walls, slabs, roofs) '
    'area and volume are per material layer; elements whose materials have no layers '
    '(IfcMaterial, IfcMaterialList) contribute their whole area and volume to each '
    'material. NULL storey or material means the element has none.';
COMMENT ON COLUMN ifc.v_takeoff.model_id IS 'Model.';
COMMENT ON COLUMN ifc.v_takeoff.discipline IS 'Discipline of the model.';
COMMENT ON COLUMN ifc.v_takeoff.storey_id IS 'Storey; NULL for elements not on a storey.';
COMMENT ON COLUMN ifc.v_takeoff.storey_name IS 'Name of the storey.';
COMMENT ON COLUMN ifc.v_takeoff.ifc_class_base IS 'Normalised IFC entity, e.g. IfcWall.';
COMMENT ON COLUMN ifc.v_takeoff.material_id IS 'Material; NULL for elements without one.';
COMMENT ON COLUMN ifc.v_takeoff.material_name IS 'Name of the material.';
COMMENT ON COLUMN ifc.v_takeoff.element_count IS 'Number of elements in the group.';
COMMENT ON COLUMN ifc.v_takeoff.length_m IS 'Total element length in metres.';
COMMENT ON COLUMN ifc.v_takeoff.area_m2 IS
    'Total area in square metres; for layered materials, element area times layer count.';
COMMENT ON COLUMN ifc.v_takeoff.volume_m3 IS
    'Total volume in cubic metres; for layered materials, element area times thickness.';
