-- ifc.v_model_summary, version 1. Created by migration 0002.
-- Released view files are never edited: a change goes into v_model_summary.v2.sql and a
-- new migration, so that migration 0002 keeps creating exactly this view.

CREATE VIEW ifc.v_model_summary AS
SELECT
    m.model_id,
    m.file_name,
    m.discipline,
    m.ifc_schema,
    m.mvd,
    m.authoring_app,
    m.project_name,
    m.project_long_name,
    m.length_unit,
    m.ingested_at,
    objects.storey_count,
    objects.space_count,
    objects.element_count,
    (
        SELECT count(*)
        FROM ifc.property AS p
        INNER JOIN ifc.object AS po ON p.object_id = po.object_id
        WHERE po.model_id = m.model_id
    ) AS property_count,
    (
        SELECT count(*)
        FROM ifc.quantity AS q
        INNER JOIN ifc.object AS qo ON q.object_id = qo.object_id
        WHERE qo.model_id = m.model_id
    ) AS quantity_count,
    (
        SELECT count(*)
        FROM ifc.material AS mat
        WHERE mat.model_id = m.model_id
    ) AS material_count,
    dq.dq_run_id AS dq_run_id,
    dq.finished_at AS dq_finished_at,
    dq.error_count AS dq_error_count,
    dq.warning_count AS dq_warning_count,
    dq.info_count AS dq_info_count
FROM ifc.model AS m
LEFT JOIN LATERAL (
    SELECT
        count(*) FILTER (WHERE o.ifc_class_base = 'IfcBuildingStorey') AS storey_count,
        count(*) FILTER (WHERE o.ifc_class_base = 'IfcSpace') AS space_count,
        count(*) FILTER (WHERE o.object_kind = 'element') AS element_count
    FROM ifc.object AS o
    WHERE o.model_id = m.model_id
) AS objects ON true
-- Findings of the latest successful data quality run. The counts live inside this
-- subquery so that a model that was never checked shows NULL, not "0 errors".
LEFT JOIN LATERAL (
    SELECT
        run.dq_run_id,
        run.finished_at,
        (
            SELECT count(*) FROM ifc.dq_result AS res
            WHERE res.dq_run_id = run.dq_run_id AND res.severity = 'error'
        ) AS error_count,
        (
            SELECT count(*) FROM ifc.dq_result AS res
            WHERE res.dq_run_id = run.dq_run_id AND res.severity = 'warning'
        ) AS warning_count,
        (
            SELECT count(*) FROM ifc.dq_result AS res
            WHERE res.dq_run_id = run.dq_run_id AND res.severity = 'info'
        ) AS info_count
    FROM ifc.dq_run AS run
    WHERE run.model_id = m.model_id AND run.status = 'succeeded'
    ORDER BY run.finished_at DESC, run.dq_run_id DESC
    LIMIT 1
) AS dq ON true;

COMMENT ON VIEW ifc.v_model_summary IS
    'One row per model: file and project metadata, row counts and the findings of its '
    'latest successful data quality run.';
COMMENT ON COLUMN ifc.v_model_summary.model_id IS 'Model key.';
COMMENT ON COLUMN ifc.v_model_summary.file_name IS 'Base name of the IFC file.';
COMMENT ON COLUMN ifc.v_model_summary.discipline IS 'Discipline code, e.g. ARC.';
COMMENT ON COLUMN ifc.v_model_summary.ifc_schema IS 'IFC schema, e.g. IFC2X3.';
COMMENT ON COLUMN ifc.v_model_summary.mvd IS 'Model View Definition.';
COMMENT ON COLUMN ifc.v_model_summary.authoring_app IS 'Exporting application.';
COMMENT ON COLUMN ifc.v_model_summary.project_name IS 'IfcProject.Name.';
COMMENT ON COLUMN ifc.v_model_summary.project_long_name IS 'IfcProject.LongName.';
COMMENT ON COLUMN ifc.v_model_summary.length_unit IS 'Length unit declared by the file.';
COMMENT ON COLUMN ifc.v_model_summary.ingested_at IS 'When the model was loaded.';
COMMENT ON COLUMN ifc.v_model_summary.storey_count IS 'Number of storeys.';
COMMENT ON COLUMN ifc.v_model_summary.space_count IS 'Number of spaces.';
COMMENT ON COLUMN ifc.v_model_summary.element_count IS 'Number of physical elements.';
COMMENT ON COLUMN ifc.v_model_summary.property_count IS 'Number of property values.';
COMMENT ON COLUMN ifc.v_model_summary.quantity_count IS 'Number of quantity values.';
COMMENT ON COLUMN ifc.v_model_summary.material_count IS 'Number of distinct materials.';
COMMENT ON COLUMN ifc.v_model_summary.dq_run_id IS
    'Latest successful data quality run; NULL if the model was never checked.';
COMMENT ON COLUMN ifc.v_model_summary.dq_finished_at IS 'When that run finished.';
COMMENT ON COLUMN ifc.v_model_summary.dq_error_count IS 'Error findings in that run.';
COMMENT ON COLUMN ifc.v_model_summary.dq_warning_count IS 'Warning findings in that run.';
COMMENT ON COLUMN ifc.v_model_summary.dq_info_count IS 'Info findings in that run.';
