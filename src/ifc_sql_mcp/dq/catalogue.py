"""Load the data quality rule catalogue, ``rules/dq_rules.yaml``, and check it strictly.

The catalogue is code: it decides what every run writes, and its SQL runs as the owning
role. A typo must therefore fail loudly when the file is read, not show up later as a
rule that silently finds nothing. So this module refuses, with the rule and the reason:

* YAML syntax errors, and a key written twice in one mapping (PyYAML would keep the
  last one without a word);
* unknown or missing keys, values that are not text, and blank descriptions;
* a ``rule_id`` that the CHECK on ``dq_rule.rule_id`` would reject, or one used twice;
* a severity or dimension outside the CHECKs on ``dq_rule``;
* a rule with both or neither of ``issue_code`` and ``sql``;
* an ``issue_code`` that is not an :class:`IssueCode`, or one mapped by two rules;
* SQL that does not use ``:model_id``, which would let a rule report other models'
  objects, or that uses any other parameter.

The file lives inside the package, next to the code that reads it, so an installed
wheel can run ``ifc-sql dq`` without a checkout (the migrations and view files work the
same way).
"""

import re
import reprlib
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text

from ifc_sql_mcp.db.schema import DIMENSIONS, SEVERITIES
from ifc_sql_mcp.ingest.issues import IssueCode

CATALOGUE = Path(__file__).parents[1] / "rules" / "dq_rules.yaml"
"""The catalogue shipped with the package, which ``ifc-sql dq`` evaluates."""

REQUIRED_KEYS = ("rule_id", "description", "severity", "dimension")
EVALUATION_KEYS = ("issue_code", "sql")
MODEL_PARAMETER = "model_id"
"""The one bind parameter a SQL rule may use: the id of the model being checked."""

# The same pattern as the CHECK on dq_rule.rule_id.
_RULE_ID = re.compile(r"[a-z][a-z0-9_]*")
_ISSUE_CODES = frozenset(code.value for code in IssueCode)


class CatalogueError(ValueError):
    """The catalogue is malformed. The message names the file, the rule and the problem."""


@dataclass(frozen=True, slots=True)
class Rule:
    """One data quality rule, as written in the catalogue.

    Attributes:
        rule_id: Stable identifier; findings refer to it.
        description: What the rule checks and what a finding means.
        severity: ``error``, ``warning`` or ``info``; copied onto each finding.
        dimension: ``completeness``, ``validity``, ``consistency`` or ``uniqueness``.
        issue_code: For a rule evaluated from ``ifc.ingest_issue``: the issue code whose
            rows are its findings. ``None`` for a SQL rule.
        sql: For a SQL rule: a SELECT of ``object_id`` and ``message`` for the model in
            ``:model_id``. ``None`` for an issue rule.
    """

    rule_id: str
    description: str
    severity: str
    dimension: str
    issue_code: IssueCode | None = None
    sql: str | None = None


def load_catalogue(path: Path = CATALOGUE) -> tuple[Rule, ...]:
    """Read and check the catalogue at ``path``; return its rules in file order.

    Raises:
        CatalogueError: if the file cannot be read or breaks any rule listed in this
            module's docstring.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogueError(f"{path}: cannot read the rule catalogue: {exc.strerror}") from exc
    return parse_catalogue(content, source=path.name)


def parse_catalogue(content: str, source: str) -> tuple[Rule, ...]:
    """Check a catalogue given as text; ``source`` names it in error messages."""
    document = _parse_yaml(content, source)
    if not isinstance(document, dict) or "rules" not in document:
        raise CatalogueError(
            f"{source}: expected a mapping with the key 'rules', got {reprlib.repr(document)}"
        )
    extra = [key for key in document if key != "rules"]
    if extra:
        raise CatalogueError(f"{source}: unknown top-level key {extra[0]!r}; only 'rules'")
    entries = document["rules"]
    if not isinstance(entries, list) or not entries:
        raise CatalogueError(f"{source}: 'rules' must be a non-empty list of rules")

    rules = [_rule(entry, f"{source}: rule {n}") for n, entry in enumerate(entries, start=1)]
    _check_unique(rules, source)
    return tuple(rules)


# --------------------------------------------------------------------------- one rule


def _rule(entry: object, where: str) -> Rule:
    """Check one entry of ``rules``; ``where`` says which, e.g. ``dq_rules.yaml: rule 3``."""
    if not isinstance(entry, dict):
        raise CatalogueError(
            f"{where}: expected a mapping of keys to values, got {reprlib.repr(entry)}"
        )
    if isinstance(entry.get("rule_id"), str):
        where = f"{where} ({entry['rule_id']})"

    allowed = REQUIRED_KEYS + EVALUATION_KEYS
    unknown = [key for key in entry if key not in allowed]
    if unknown:
        raise CatalogueError(
            f"{where}: unknown key {unknown[0]!r}; the keys are {', '.join(allowed)}"
        )
    missing = [key for key in REQUIRED_KEYS if key not in entry]
    if missing:
        raise CatalogueError(f"{where}: missing key {missing[0]!r}")
    for key, value in entry.items():
        if not isinstance(value, str):
            # YAML reads unquoted yes, no, on, off and numbers as other types.
            raise CatalogueError(f"{where}: {key} must be text, got {reprlib.repr(value)}")
    values: dict[str, str] = entry

    rule_id = values["rule_id"]
    if not _RULE_ID.fullmatch(rule_id):
        raise CatalogueError(
            f"{where}: rule_id must be snake_case (a lower-case letter, then lower-case "
            f"letters, digits or _), got {rule_id!r}"
        )
    description = values["description"].strip()
    if not description:
        raise CatalogueError(f"{where}: description must not be blank")
    _check_choice(values["severity"], "severity", SEVERITIES, where)
    _check_choice(values["dimension"], "dimension", DIMENSIONS, where)

    evaluations = [key for key in EVALUATION_KEYS if key in values]
    if len(evaluations) != 1:
        raise CatalogueError(
            f"{where}: give exactly one of issue_code and sql, which says how the rule is "
            f"evaluated; got {' and '.join(evaluations) or 'neither'}"
        )
    return Rule(
        rule_id=rule_id,
        description=description,
        severity=values["severity"],
        dimension=values["dimension"],
        issue_code=_issue_code(values["issue_code"], where) if "issue_code" in values else None,
        sql=_sql(values["sql"], where) if "sql" in values else None,
    )


def _check_choice(value: str, key: str, choices: tuple[str, ...], where: str) -> None:
    if value not in choices:
        raise CatalogueError(f"{where}: {key} must be one of {', '.join(choices)}; got {value!r}")


def _issue_code(code: str, where: str) -> IssueCode:
    if code not in _ISSUE_CODES:
        raise CatalogueError(
            f"{where}: issue_code {code!r} is not an IssueCode (src/ifc_sql_mcp/ingest/issues.py)"
        )
    return IssueCode(code)


def _sql(sql: str, where: str) -> str:
    sql = sql.strip()
    if not sql:
        raise CatalogueError(f"{where}: sql must not be blank")
    # Exactly the parameters SQLAlchemy will see when it runs the query.
    parameters = set(text(sql).compile().params)
    if MODEL_PARAMETER not in parameters:
        raise CatalogueError(
            f"{where}: sql must select the model's rows with :{MODEL_PARAMETER}; without "
            "it the rule would report the objects of every model"
        )
    others = sorted(parameters - {MODEL_PARAMETER})
    if others:
        raise CatalogueError(
            f"{where}: sql may use no parameter but :{MODEL_PARAMETER}, found :{others[0]} "
            "(a ':' followed by a word counts as a parameter even inside a string literal)"
        )
    return sql


def _check_unique(rules: list[Rule], source: str) -> None:
    """Refuse a rule_id used twice, and an issue code that two rules map."""
    by_id: dict[str, int] = {}
    by_code: dict[IssueCode, str] = {}
    for position, rule in enumerate(rules, start=1):
        if rule.rule_id in by_id:
            raise CatalogueError(
                f"{source}: rules {by_id[rule.rule_id]} and {position} both have rule_id "
                f"{rule.rule_id!r}"
            )
        by_id[rule.rule_id] = position
        if rule.issue_code is None:
            continue
        if rule.issue_code in by_code:
            raise CatalogueError(
                f"{source}: rules {by_code[rule.issue_code]!r} and {rule.rule_id!r} both map "
                f"issue_code {rule.issue_code.value!r}; each issue code has exactly one rule"
            )
        by_code[rule.issue_code] = rule.rule_id


# --------------------------------------------------------------------------- YAML


class _StrictLoader(yaml.SafeLoader):
    """PyYAML's safe loader, except that a key repeated in one mapping is an error.

    PyYAML keeps the last of two equal keys without a word, so a rule with ``severity``
    written twice would silently get the second value.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        """Build the mapping as SafeLoader does, after checking its keys are distinct."""
        seen: set[str] = set()
        for key_node, _ in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue  # SafeLoader rejects keys that are lists or mappings itself
            if key_node.value in seen:
                raise yaml.constructor.ConstructorError(
                    problem=f"duplicate key {key_node.value!r}", problem_mark=key_node.start_mark
                )
            seen.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


def _parse_yaml(content: str, source: str) -> object:
    try:
        # A subclass of SafeLoader: it builds only plain data, never arbitrary objects.
        return yaml.load(content, Loader=_StrictLoader)  # noqa: S506
    except yaml.YAMLError as exc:
        raise CatalogueError(f"{source}: not valid YAML: {_yaml_problem(exc)}") from exc


def _yaml_problem(exc: yaml.YAMLError) -> str:
    """Say what is wrong and where, without PyYAML's '<unicode string>' stream name."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        return f"{exc.problem} (line {mark.line + 1}, column {mark.column + 1})"
    # A ReaderError (a character YAML forbids) puts the stream name on a second line.
    return str(exc).splitlines()[0]
