-- ifc.v_model_current, version 1. Created by migration 0017.
-- Released view files are never edited: a change goes into v_model_current.v2.sql and a
-- new migration, so that migration 0017 keeps creating exactly this view.

CREATE VIEW ifc.v_model_current AS
-- A model is current until a later one replaces it (ifc-sql ingest --replaces): an
-- architect re-exports every week, and a project-wide count must not hold last week's
-- walls as well as this week's (audit finding A12). The replaced model stays loaded, for
-- comparison, and every tool still reads it by its model_id.
SELECT
    m.model_id,
    m.file_name,
    m.discipline,
    m.project_group,
    m.project_key,
    m.project_name,
    m.project_long_name,
    m.replaces_model_id,
    m.ingested_at
FROM ifc.model AS m
WHERE NOT EXISTS (
    SELECT 1 FROM ifc.model AS later WHERE later.replaces_model_id = m.model_id
);

COMMENT ON VIEW ifc.v_model_current IS
    'The models no later model replaces: the current version of each. Project-wide views '
    'read these. A replaced model stays loaded and is still read by its model_id.';
COMMENT ON COLUMN ifc.v_model_current.model_id IS 'The model.';
COMMENT ON COLUMN ifc.v_model_current.file_name IS 'Base name of the ingested IFC file.';
COMMENT ON COLUMN ifc.v_model_current.discipline IS 'Discipline code given at ingest, e.g. ARC.';
COMMENT ON COLUMN ifc.v_model_current.project_group IS
    'The project it is grouped in: project_key, else the IFC project number or name unless '
    'it is a Revit placeholder. NULL: it joins no project.';
COMMENT ON COLUMN ifc.v_model_current.project_key IS
    'Project given at ingest (--project), or inherited from the model it replaces.';
COMMENT ON COLUMN ifc.v_model_current.project_name IS 'IfcProject.Name (Revit: Project Number).';
COMMENT ON COLUMN ifc.v_model_current.project_long_name IS
    'IfcProject.LongName (Revit: Project Name).';
COMMENT ON COLUMN ifc.v_model_current.replaces_model_id IS
    'The earlier version this model replaces, if any.';
COMMENT ON COLUMN ifc.v_model_current.ingested_at IS 'When the model was committed.';
