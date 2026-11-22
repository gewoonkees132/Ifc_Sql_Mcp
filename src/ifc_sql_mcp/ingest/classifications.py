"""Collect classification codes from IFC references and from Revit OmniClass parameters.

Codes arrive in two ways in the sample files:

* **IfcClassificationReference**, used by the Revit 2013 files for UniFormat. They are
  attached to type objects, so each instance inherits its type's references
  (``extract.py`` resolves that). Many carry an empty ItemReference; a code is the
  table's key, so those are dropped and reported.
* **Revit parameters.** Spaces carry ``OmniClass Table 13 Category`` as
  ``"13-51 24 11: General Residential Space"``; families carry ``OmniClass Number`` and
  ``OmniClass Title`` (Table 23, products). They are parsed into code and title.

The Table 13 system name is exactly ``OmniClass Table 13``, because
``v_space_schedule`` filters on it.
"""

import re
from collections.abc import Iterable, Sequence

from ifc_sql_mcp.ingest.issues import Issue, IssueCode
from ifc_sql_mcp.ingest.properties import TypedValue
from ifc_sql_mcp.ingest.raw import RawReference
from ifc_sql_mcp.ingest.records import ClassificationRecord

OMNICLASS_TABLE_13 = "OmniClass Table 13"
OMNICLASS_CATEGORY_PROPERTY = "OmniClass Table 13 Category"
OMNICLASS_NUMBER_PROPERTY = "OmniClass Number"
OMNICLASS_TITLE_PROPERTY = "OmniClass Title"

# "13-51 24 11: General Residential Space"; the title part is optional.
_TABLE_13 = re.compile(r"(?P<code>13-[0-9]{2}(?: [0-9]{2})*)(?:\s*:\s*(?P<title>.*))?")
# "23.40.35.17.47.11": the first pair is the OmniClass table.
_OMNICLASS_NUMBER = re.compile(r"(?P<table>[0-9]{2})(?:\.[0-9]{2})+")


def parse_omniclass_category(value: str) -> tuple[str, str | None] | None:
    """Parse ``"13-51 24 11: General Residential Space"`` into code and title.

    Returns:
        ``(code, title)``, with ``title`` ``None`` if absent, or ``None`` if the value
        does not start with a Table 13 code.
    """
    match = _TABLE_13.fullmatch(value.strip())
    if match is None:
        return None
    title = (match.group("title") or "").strip()
    return match.group("code"), title or None


def omniclass_table(number: str) -> str | None:
    """Return the system name for an OmniClass number, e.g. ``OmniClass Table 23``."""
    match = _OMNICLASS_NUMBER.fullmatch(number.strip())
    return f"OmniClass Table {match.group('table')}" if match else None


def _from_parameters(
    global_id: str, values: Sequence[TypedValue]
) -> tuple[list[ClassificationRecord], list[Issue]]:
    records: list[ClassificationRecord] = []
    issues: list[Issue] = []
    for value in values:
        # Empty parameters are common (Revit writes every parameter) and are found by
        # the data quality rules on property values, so they are not reported here.
        if value.text is None or not value.text.strip():
            continue
        if value.name == OMNICLASS_CATEGORY_PROPERTY:
            parsed = parse_omniclass_category(value.text)
            if parsed is None:
                message = f"{value.ref} = {value.text!r} has no OmniClass Table 13 code"
                issues.append(Issue(IssueCode.OMNICLASS_UNPARSED, global_id, message))
                continue
            code, title = parsed
            records.append(ClassificationRecord(global_id, OMNICLASS_TABLE_13, code, title))
        elif value.name == OMNICLASS_NUMBER_PROPERTY:
            system = omniclass_table(value.text)
            if system is None:
                message = f"{value.ref} = {value.text!r} is not an OmniClass number"
                issues.append(Issue(IssueCode.OMNICLASS_UNPARSED, global_id, message))
                continue
            title = next(
                (
                    other.text.strip()
                    for other in values
                    if other.set_name == value.set_name
                    and other.name == OMNICLASS_TITLE_PROPERTY
                    and other.text
                    and other.text.strip()
                ),
                None,
            )
            records.append(ClassificationRecord(global_id, system, value.text.strip(), title))
    return records, issues


def _from_references(
    global_id: str, references: Iterable[RawReference]
) -> tuple[list[ClassificationRecord], list[Issue]]:
    records: list[ClassificationRecord] = []
    issues: list[Issue] = []
    for reference in references:
        system = (reference.system or "").strip()
        code = (reference.code or "").strip()
        if not system or not code:
            missing = "code" if system else "system"
            message = (
                f"classification reference {reference.title!r} in {reference.system!r} "
                f"has no {missing}"
            )
            issues.append(Issue(IssueCode.INCOMPLETE_CLASSIFICATION_REFERENCE, global_id, message))
            continue
        title = (reference.title or "").strip() or None
        records.append(ClassificationRecord(global_id, system, code, title))
    return records, issues


def collect_classifications(
    global_id: str, values: Sequence[TypedValue], references: Iterable[RawReference]
) -> tuple[list[ClassificationRecord], list[Issue]]:
    """Return one classification per (system, code) for an object.

    Args:
        global_id: The object.
        values: Its resolved property values (for the OmniClass parameters).
        references: Its classification references, inherited ones included.

    Returns:
        Records sorted by system and code, and the issues found. If one system and code
        appear more than once, a title beats no title and the alphabetically first title
        beats the others, so the choice does not depend on the order in the file.
    """
    from_params, issues = _from_parameters(global_id, values)
    from_refs, ref_issues = _from_references(global_id, references)
    issues.extend(ref_issues)

    def rank(record: ClassificationRecord) -> tuple[str, str, bool, str]:
        return (record.system, record.code, record.title is None, record.title or "")

    by_key: dict[tuple[str, str], ClassificationRecord] = {}
    for record in sorted(from_params + from_refs, key=rank):
        kept = by_key.setdefault((record.system, record.code), record)
        if record.title is not None and kept.title != record.title:
            message = (
                f"{record.system} {record.code} has titles {kept.title!r} and "
                f"{record.title!r}; kept the first"
            )
            issues.append(Issue(IssueCode.CONFLICTING_CLASSIFICATION, global_id, message))
    return list(by_key.values()), issues
