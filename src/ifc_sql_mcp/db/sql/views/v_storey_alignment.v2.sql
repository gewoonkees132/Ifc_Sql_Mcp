-- ifc.v_storey_alignment, version 2. Created by migration 0017; it replaces version 1.
-- Version 1 grouped models by IfcProject.Name, so the audit's Italian tower, whose project
-- number is Revit's default 0001, was aligned as the fifth model of the Duplex, which has
-- the same default (finding A12). Version 2 groups by model.project_group, which leaves
-- Revit's placeholders out and takes the project given at ingest first, and it compares
-- only current models (v_model_current), so a replaced export is not counted twice.
-- Released view files are never edited: a change goes into v_storey_alignment.v3.sql and a
-- new migration, so that migration 0017 keeps creating exactly this view.

CREATE VIEW ifc.v_storey_alignment AS
WITH project_model AS (
    -- Current models belong to one project when their project_group is equal. The
    -- GlobalId cannot be used: discipline models of one building can be exported from
    -- different Revit projects (the Duplex architectural model's differs from the other
    -- three).
    SELECT
        m.model_id,
        m.discipline,
        m.project_group,
        m.project_name,
        count(*) OVER (PARTITION BY m.project_group) AS project_model_count
    FROM ifc.v_model_current AS m
    WHERE m.project_group IS NOT NULL
),

storey AS (
    SELECT
        pm.project_group,
        pm.project_name,
        -- The comparison key: runs of whitespace become one space, then the name is
        -- trimmed and lower-cased. 'Level 2', ' LEVEL  2' and 'level 2' are one storey;
        -- 'Level 02' and 'L2' are not, because guessing that would hide real mismatches.
        lower(btrim(regexp_replace(o.name, '\s+', ' ', 'g'))) AS storey_key,
        pm.model_id,
        pm.discipline,
        pm.project_model_count,
        o.object_id AS storey_id,
        o.name AS storey_name,
        s.elevation_m
    FROM project_model AS pm
    INNER JOIN ifc.object AS o ON pm.model_id = o.model_id
    INNER JOIN ifc.spatial_element AS s ON o.object_id = s.object_id
    WHERE o.ifc_class_base = 'IfcBuildingStorey' AND o.name ~ '\S'
),

storey_key_stats AS (
    -- What the project's models say about one storey key.
    SELECT
        st.project_group,
        st.storey_key,
        count(DISTINCT st.model_id) AS storey_model_count,
        max(st.elevation_m) - min(st.elevation_m) AS elevation_spread_m
    FROM storey AS st
    GROUP BY st.project_group, st.storey_key
)

SELECT
    st.project_group,
    st.project_name,
    st.storey_key,
    st.model_id,
    st.discipline,
    st.storey_id,
    st.storey_name,
    st.elevation_m,
    stats.storey_model_count,
    st.project_model_count,
    stats.elevation_spread_m
FROM storey AS st
INNER JOIN storey_key_stats AS stats
    ON st.project_group = stats.project_group AND st.storey_key = stats.storey_key;

COMMENT ON VIEW ifc.v_storey_alignment IS
    'Storeys compared across the current discipline models of one project (models with '
    'the same project_group, read from v_model_current), one row per storey and model. '
    'Storeys match on storey_key, the name with whitespace collapsed, trimmed and '
    'lower-cased. A storey is aligned when storey_model_count = project_model_count and '
    'elevation_spread_m = 0.';
COMMENT ON COLUMN ifc.v_storey_alignment.project_group IS
    'The project the models compared share: the key given at ingest, else their IFC project '
    'number or name. Revit''s placeholders (0001, Project Number, Project Name) never group.';
COMMENT ON COLUMN ifc.v_storey_alignment.project_name IS
    'IfcProject.Name of this model (Revit: Project Number).';
COMMENT ON COLUMN ifc.v_storey_alignment.storey_key IS
    'Normalised storey name the models are matched on, e.g. level 2.';
COMMENT ON COLUMN ifc.v_storey_alignment.model_id IS 'Model the storey belongs to.';
COMMENT ON COLUMN ifc.v_storey_alignment.discipline IS 'Discipline of that model, e.g. MEP.';
COMMENT ON COLUMN ifc.v_storey_alignment.storey_id IS 'The storey (ifc.object).';
COMMENT ON COLUMN ifc.v_storey_alignment.storey_name IS 'Storey name as the model writes it.';
COMMENT ON COLUMN ifc.v_storey_alignment.elevation_m IS
    'Elevation of this storey in this model, in metres (millimetre precision).';
COMMENT ON COLUMN ifc.v_storey_alignment.storey_model_count IS
    'Number of the project''s models that have a storey with this key.';
COMMENT ON COLUMN ifc.v_storey_alignment.project_model_count IS
    'Number of models in the project; more than storey_model_count means the storey is '
    'missing from some disciplines.';
COMMENT ON COLUMN ifc.v_storey_alignment.elevation_spread_m IS
    'Highest minus lowest elevation of this storey key across the project''s models; 0 when '
    'they agree, NULL when none has an elevation.';
