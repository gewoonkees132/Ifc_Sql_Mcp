"""Parse Revit element names (``Family:Type:ElementId``) into their parts.

Revit 2011 and 2013 name every element after its family, type and ElementId. Looking
at the sample files, there are three shapes:

* ``Basic Wall:Interior - Partition (92mm Stud):190140``: system families (walls,
  floors, pipes) write ``Family:Type:ElementId``.
* ``M_Single-Flush:0762 x 2032mm:0762 x 2032mm:150173``: loadable families add the
  instance's label before the id, giving ``Family:Type:Label:ElementId``. The label
  usually repeats the type, but not always (a panelboard is
  ``...:400 A:Unit A Panelboard:575479``), so the type is the *second* segment, which
  matches the element's IfcTypeObject name.
* Openings and stair parts append a part number to their host's name:
  ``...:145788:1`` or ``Stair:Residential - 200mm Max Riser 250mm Tread:151086:1``. Their
  ElementId is the host's, which is why ``revit_element_id`` is not unique.
* Revit 2019 to 2022 name a stair's parts after the stair with the part after a space:
  ``Precast Stair:Stair:1287438 Run 1``, ``...:1287438 Landing 2``, ``...:4959975
  Stringer 10`` (:data:`STAIR_PARTS`). They keep the stair's family, type and ElementId;
  their Tag is their own id, so it is not compared (audit finding A14: 278 such names on
  the audit's tower, all 182 of its stair flights among them).

A parser that takes the last segment as the ElementId gets the third shape wrong. This
one reads the id from the end, uses the element's Tag (where Revit also writes the
ElementId) to decide between "last segment" and "segment before a part number", and
only then splits the rest into family and type. A name with more segments than these
shapes allow keeps its ElementId but gets no family or type, and is reported.
"""

import re
from dataclasses import dataclass

from ifc_sql_mcp.ingest.issues import Issue, IssueCode

_DIGITS = re.compile(r"[0-9]+")
_BIGINT_MAX = 2**63 - 1

STAIR_PARTS = ("Run", "Landing", "Support", "Stringer")
"""The part words Revit 2019 to 2022 write after a stair's ElementId, as the audit's files
show them; another word is not guessed at and stays reported."""
_STAIR_PART = re.compile(rf"([0-9]+) (?:{'|'.join(STAIR_PARTS)}) [0-9]+")


@dataclass(frozen=True, slots=True)
class RevitName:
    """The parts of a Revit element name; each is ``None`` when it cannot be determined."""

    family: str | None
    type: str | None
    element_id: int | None


_NOTHING = RevitName(family=None, type=None, element_id=None)


def _as_id(segment: str) -> int | None:
    """Return the segment as an ElementId (a positive bigint), or ``None``."""
    if not _DIGITS.fullmatch(segment):
        return None
    number = int(segment)
    return number if 0 < number <= _BIGINT_MAX else None


def parse_revit_name(
    global_id: str, name: str | None, tag: str | None, *, named_after_host: bool = False
) -> tuple[RevitName, list[Issue]]:
    """Split a Revit element name into family, type and ElementId.

    Args:
        global_id: The element, for issue messages.
        name: IFC Name of the element.
        tag: IFC Tag of the element; Revit writes the ElementId there, except on most
            openings.
        named_after_host: True for a feature element (an opening). Revit names it after
            the wall it cuts and tags it with its own id, so a Tag that differs from the
            name's ElementId is normal and not reported (audit finding A13: 2,072 such
            findings on the audit's tower).

    Returns:
        The parsed parts and any issues. A missing name is not an issue (it is visible
        as a NULL name); a name that does not fit the pattern is.
    """
    if name is None or not name.strip():
        return _NOTHING, []
    segments = name.split(":")
    part = _STAIR_PART.fullmatch(segments[-1])
    stair_id = _as_id(part.group(1)) if part else None
    if stair_id is not None and len(segments) >= 3:
        return _parts(global_id, name, stair_id, segments[:-1], [])

    # Each candidate is (ElementId, the segments before it).
    candidates: list[tuple[int, list[str]]] = []
    last = _as_id(segments[-1])
    # Family:Type[:Label]:ElementId:n, where n is a part number smaller than the id.
    if len(segments) >= 4 and last is not None:
        before_last = _as_id(segments[-2])
        if before_last is not None and last < before_last:
            candidates.append((before_last, segments[:-2]))
    # Family:Type[:Label]:ElementId
    if len(segments) >= 3 and last is not None:
        candidates.append((last, segments[:-1]))

    if not candidates:
        message = f"name {name!r} does not end in an ElementId"
        return _NOTHING, [Issue(IssueCode.REVIT_NAME_UNPARSED, global_id, message)]

    issues: list[Issue] = []
    tag_id = _as_id(tag.strip()) if tag else None
    by_tag = [candidate for candidate in candidates if candidate[0] == tag_id]
    element_id, body = by_tag[0] if by_tag else candidates[0]
    if tag_id is not None and not by_tag and not named_after_host:
        message = f"name {name!r} gives ElementId {element_id}, but Tag is {tag!r}"
        issues.append(Issue(IssueCode.REVIT_TAG_MISMATCH, global_id, message))

    return _parts(global_id, name, element_id, body, issues)


def _parts(
    global_id: str, name: str, element_id: int, body: list[str], issues: list[Issue]
) -> tuple[RevitName, list[Issue]]:
    """Family and type from the segments before the ElementId."""
    # body is [Family, Type] or [Family, Type, Label]; anything else is ambiguous.
    if len(body) not in (2, 3) or not body[0].strip() or not body[1].strip():
        message = f"name {name!r} has no clear Family:Type before ElementId {element_id}"
        issues.append(Issue(IssueCode.REVIT_NAME_UNPARSED, global_id, message))
        return RevitName(family=None, type=None, element_id=element_id), issues
    return RevitName(family=body[0], type=body[1], element_id=element_id), issues
