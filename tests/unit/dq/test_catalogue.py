"""The rule catalogue: the shipped file, and every way a broken one is refused."""

from pathlib import Path

import pytest
import yaml

from ifc_sql_mcp.dq.catalogue import CatalogueError, Rule, load_catalogue, parse_catalogue
from ifc_sql_mcp.ingest.issues import IssueCode

SHIPPED = load_catalogue()

REMOVE = object()
"""Passed to ``_rule_yaml`` to leave a key out."""

MODEL_SQL = (
    "SELECT p.object_id, 'empty' AS message FROM ifc.property AS p "
    "JOIN ifc.object AS o ON o.object_id = p.object_id WHERE o.model_id = :model_id"
)


def _rule_yaml(**changes: object) -> str:
    """A catalogue with one valid issue rule, with ``changes`` applied to its keys."""
    rule: dict[str, object] = {
        "rule_id": "negative_quantity",
        "description": "A quantity is negative.",
        "severity": "warning",
        "dimension": "validity",
        "issue_code": "negative_quantity",
    } | changes
    kept = {key: value for key, value in rule.items() if value is not REMOVE}
    return yaml.safe_dump({"rules": [kept]}, sort_keys=False)


# --------------------------------------------------------------------------- the shipped file


def test_every_issue_code_has_exactly_one_rule() -> None:
    """A new IssueCode cannot be forgotten: this fails until the catalogue maps it."""
    mapped = [rule.issue_code for rule in SHIPPED if rule.issue_code is not None]

    assert sorted(mapped) == sorted(IssueCode)


def test_issue_rules_are_named_after_their_issue_code() -> None:
    assert [r.rule_id for r in SHIPPED if r.issue_code is not None] == [
        r.issue_code.value for r in SHIPPED if r.issue_code is not None
    ]


def test_the_sql_rules_are_the_documented_ones() -> None:
    assert [rule.rule_id for rule in SHIPPED if rule.sql is not None] == [
        "value_is_property_name",
        "value_is_not_applicable",
        "value_is_empty",
        "element_without_storey",
        "space_without_area",
        "wall_area_inconsistent",
        "space_volume_inconsistent",
        "space_perimeter_implausible",
    ]


def test_every_severity_and_dimension_is_used() -> None:
    """Each choice in the CHECKs on dq_rule has at least one rule, so the scale is real."""
    assert {rule.severity for rule in SHIPPED} == {"error", "warning", "info"}
    assert {rule.dimension for rule in SHIPPED} == {
        "completeness",
        "validity",
        "consistency",
        "uniqueness",
    }


# --------------------------------------------------------------------------- what a rule becomes


def test_a_catalogue_parses_into_rules_in_file_order() -> None:
    content = f"""
rules:
  - rule_id: value_is_empty
    description: >
      A text property
      is empty.
    severity: info
    dimension: completeness
    sql: |
      {MODEL_SQL}
  - rule_id: negative_quantity
    description: A quantity is negative.
    severity: warning
    dimension: validity
    issue_code: negative_quantity
"""

    assert parse_catalogue(content, "test.yaml") == (
        Rule(
            rule_id="value_is_empty",
            description="A text property is empty.",  # folded, and stripped
            severity="info",
            dimension="completeness",
            sql=MODEL_SQL,
        ),
        Rule(
            rule_id="negative_quantity",
            description="A quantity is negative.",
            severity="warning",
            dimension="validity",
            issue_code=IssueCode.NEGATIVE_QUANTITY,
        ),
    )


# --------------------------------------------------------------------------- refused


TWO_RULES = """
rules:
  - {first}
  - {second}
"""
ISSUE_RULE = (
    "{{rule_id: {id}, description: d, severity: info, dimension: validity, issue_code: {code}}}"
)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        pytest.param("rules: [\n", "not valid YAML: expected the node content", id="syntax"),
        pytest.param(
            "rules:\n  - rule_id: a\n    severity: info\n    severity: error\n",
            "not valid YAML: duplicate key 'severity' (line 4, column 5)",
            id="duplicate key",
        ),
        pytest.param(
            "rules:\n  - ? [a, b]\n    : 1\n",
            "not valid YAML: found unhashable key (line 2, column 7)",
            id="list as key",
        ),
        pytest.param(
            "rules: \x00",
            "not valid YAML: unacceptable character #x0000: special characters are not allowed",
            id="forbidden character",
        ),
        pytest.param("", "expected a mapping with the key 'rules', got None", id="empty"),
        pytest.param("- a\n", "expected a mapping with the key 'rules', got ['a']", id="list"),
        pytest.param(
            "rules: [x]\nversion: 1\n", "unknown top-level key 'version'; only 'rules'", id="extra"
        ),
        pytest.param("rules: {}\n", "'rules' must be a non-empty list", id="rules mapping"),
        pytest.param("rules: []\n", "'rules' must be a non-empty list", id="no rules"),
        pytest.param(
            "rules:\n  - just text\n",
            "rule 1: expected a mapping of keys to values, got 'just text'",
            id="rule is text",
        ),
        pytest.param(
            _rule_yaml(severty="info"),
            "rule 1 (negative_quantity): unknown key 'severty'; the keys are rule_id, "
            "description, severity, dimension, issue_code, sql",
            id="unknown key",
        ),
        pytest.param(
            _rule_yaml(dimension=REMOVE),
            "rule 1 (negative_quantity): missing key 'dimension'",
            id="missing key",
        ),
        pytest.param(
            _rule_yaml(rule_id=REMOVE), "rule 1: missing key 'rule_id'", id="missing rule_id"
        ),
        pytest.param(
            _rule_yaml(severity=False),
            "rule 1 (negative_quantity): severity must be text, got False",
            id="severity: no",
        ),
        pytest.param(_rule_yaml(rule_id=7), "rule 1: rule_id must be text, got 7", id="number"),
        pytest.param(
            _rule_yaml(rule_id="Negative-Quantity"),
            "rule_id must be snake_case (a lower-case letter, then lower-case letters, "
            "digits or _), got 'Negative-Quantity'",
            id="rule_id format",
        ),
        pytest.param(
            _rule_yaml(description="   "),
            "rule 1 (negative_quantity): description must not be blank",
            id="blank description",
        ),
        pytest.param(
            _rule_yaml(severity="critical"),
            "severity must be one of error, warning, info; got 'critical'",
            id="severity",
        ),
        pytest.param(
            _rule_yaml(dimension="accuracy"),
            "dimension must be one of completeness, validity, consistency, uniqueness; "
            "got 'accuracy'",
            id="dimension",
        ),
        pytest.param(
            _rule_yaml(sql=MODEL_SQL),
            "give exactly one of issue_code and sql, which says how the rule is evaluated; "
            "got issue_code and sql",
            id="both evaluations",
        ),
        pytest.param(_rule_yaml(issue_code=REMOVE), "evaluated; got neither", id="no evaluation"),
        pytest.param(
            _rule_yaml(issue_code="negative_area"),
            "issue_code 'negative_area' is not an IssueCode (src/ifc_sql_mcp/ingest/issues.py)",
            id="unknown issue code",
        ),
        pytest.param(
            _rule_yaml(issue_code=REMOVE, sql=" \n"),
            "rule 1 (negative_quantity): sql must not be blank",
            id="blank sql",
        ),
        pytest.param(
            _rule_yaml(issue_code=REMOVE, sql="SELECT object_id, 'x' AS message FROM ifc.object"),
            "sql must select the model's rows with :model_id; without it the rule would "
            "report the objects of every model",
            id="sql without model",
        ),
        pytest.param(
            _rule_yaml(issue_code=REMOVE, sql=MODEL_SQL + " AND p.value_text = ':BarCode'"),
            "sql may use no parameter but :model_id, found :BarCode (a ':' followed by a word "
            "counts as a parameter even inside a string literal)",
            id="sql with another parameter",
        ),
        pytest.param(
            TWO_RULES.format(
                first=ISSUE_RULE.format(id="a", code="negative_quantity"),
                second=ISSUE_RULE.format(id="a", code="non_finite_value"),
            ),
            "rules 1 and 2 both have rule_id 'a'",
            id="duplicate rule_id",
        ),
        pytest.param(
            TWO_RULES.format(
                first=ISSUE_RULE.format(id="a", code="negative_quantity"),
                second=ISSUE_RULE.format(id="b", code="negative_quantity"),
            ),
            "rules 'a' and 'b' both map issue_code 'negative_quantity'; each issue code has "
            "exactly one rule",
            id="issue code mapped twice",
        ),
    ],
)
def test_a_malformed_catalogue_is_refused_with_the_reason(content: str, message: str) -> None:
    with pytest.raises(CatalogueError) as excinfo:
        parse_catalogue(content, "dq_rules.yaml")

    assert str(excinfo.value).startswith("dq_rules.yaml")
    assert message in str(excinfo.value)


def test_a_missing_catalogue_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "dq_rules.yaml"

    with pytest.raises(CatalogueError, match="cannot read the rule catalogue: No such file"):
        load_catalogue(path)
