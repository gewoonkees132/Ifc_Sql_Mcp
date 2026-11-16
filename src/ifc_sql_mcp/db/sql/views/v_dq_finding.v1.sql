-- ifc.v_dq_finding, version 1. Created by migration 0008.
-- Released view files are never edited: a change goes into v_dq_finding.v2.sql and a
-- new migration, so that migration 0008 keeps creating exactly this view.

CREATE VIEW ifc.v_dq_finding AS
SELECT
    latest.model_id,
    latest.dq_run_id,
    res.rule_id,
    res.severity,
    rule.dimension,
    res.object_id,
    o.global_id,
    o.ifc_class,
    o.name AS object_name,
    res.message
-- The latest successful run of each model, chosen as v_model_summary chooses it, so the
-- two always describe the same run.
FROM (
    SELECT DISTINCT ON (run.model_id) run.model_id, run.dq_run_id
    FROM ifc.dq_run AS run
    WHERE run.status = 'succeeded'
    ORDER BY run.model_id, run.finished_at DESC, run.dq_run_id DESC
) AS latest
INNER JOIN ifc.dq_result AS res ON latest.dq_run_id = res.dq_run_id
INNER JOIN ifc.dq_rule AS rule ON res.rule_id = rule.rule_id
LEFT JOIN ifc.object AS o ON res.object_id = o.object_id;

COMMENT ON VIEW ifc.v_dq_finding IS
    'One row per finding of each model''s latest successful data quality run (the run '
    'v_model_summary counts), with its rule''s dimension and the object it is about. '
    'Earlier runs are in ifc.dq_run and ifc.dq_result.';
COMMENT ON COLUMN ifc.v_dq_finding.model_id IS 'Model the finding is about.';
COMMENT ON COLUMN ifc.v_dq_finding.dq_run_id IS 'The run that found it: the model''s latest successful one.';
COMMENT ON COLUMN ifc.v_dq_finding.rule_id IS 'Rule that was violated; ifc.dq_rule describes it.';
COMMENT ON COLUMN ifc.v_dq_finding.severity IS 'error | warning | info, as at the time of the run.';
COMMENT ON COLUMN ifc.v_dq_finding.dimension IS
    'completeness | validity | consistency | uniqueness, from the rule.';
COMMENT ON COLUMN ifc.v_dq_finding.object_id IS
    'Object the finding is about; NULL for a finding about the model as a whole, or about '
    'an object that was never loaded (the message then gives its GlobalId).';
COMMENT ON COLUMN ifc.v_dq_finding.global_id IS 'GlobalId of that object.';
COMMENT ON COLUMN ifc.v_dq_finding.ifc_class IS 'Exact IFC entity of that object, e.g. IfcWallStandardCase.';
COMMENT ON COLUMN ifc.v_dq_finding.object_name IS 'IFC Name of that object, verbatim.';
COMMENT ON COLUMN ifc.v_dq_finding.message IS
    'What is wrong, with the offending value, e.g. PSet_Revit_Other.BarCode = ''BarCode''.';
