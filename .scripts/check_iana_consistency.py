#!/usr/bin/env python3
"""
Verify consistency between normative object definitions and IANA registry tables
in the RPP data-objects draft.

Checks performed
----------------
Object level:
  [MISSING IN IANA]      - normative object has no IANA entry
  [MISSING IN NORMATIVE] - IANA entry has no normative object
  [ID MISMATCH]          - same Object Name, different identifier
  [NAME MISMATCH]        - same identifier, different Object Name
  [DUPLICATE …]          - duplicate identifier within one section

Data-element level:
  [ELEM MISSING IN IANA]      - normative element absent from IANA table
  [ELEM MISSING IN NORMATIVE] - IANA table row absent from normative definition
  [ELEM NAME MISMATCH]        - identifier matches, but Element Name differs
  [ELEM CARD MISMATCH]        - identifier matches, but Cardinality differs
  [ELEM MUTABILITY MISMATCH]  - identifier matches, but Mutability differs
  [ELEM TYPE MISMATCH]        - identifier matches, but Data Type differs
  [ELEM DESC EMPTY]           - IANA description cell is blank; normative has one

Operation level:
  [OP MISSING IN IANA]      - normative operation has no IANA entry
  [OP MISSING IN NORMATIVE] - IANA operation has no normative entry
  [OP NAME MISMATCH]        - same identifier, different Operation Name
  [OP DESC EMPTY]           - IANA operation Description field is blank; normative has one

Operation parameter level:
  [PARAM MISSING IN IANA]      - normative parameter absent from IANA table
  [PARAM MISSING IN NORMATIVE] - IANA parameter row absent from normative
  [PARAM NAME MISMATCH]        - identifier matches, but Parameter Name differs
  [PARAM CARD MISMATCH]        - identifier matches, but Cardinality differs
  [PARAM TYPE MISMATCH]        - identifier matches, but Data Type differs
  [PARAM DESC EMPTY]           - IANA description cell is blank; normative has one

Supported-process level (normative-only, no IANA table involved):
  [PROCESS MISSING IN NORMATIVE]  - an inherited-process bullet in a Data Object's
                                     "## Processes" list names an Object Name that
                                     does not match any normative object
  [EXTENDS MISSING IN NORMATIVE]  - an object's "* Extends:" attribute names an
                                     Object Name that does not match any normative
                                     object
"""

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DRAFT_FILE = Path("./draft-ietf-rpp-data-objects.md")

# Header-comment template for --yaml-out; loaded and prepended verbatim so
# the explanatory text lives in one static file, not in Python string literals.
YAML_TEMPLATE_FILE = Path(__file__).resolve().parent / "object_model_template.yaml"

# Object Type label (as used in ObjectDef.obj_type) → top-level YAML list key
OBJ_TYPE_TO_YAML_KEY: dict[str, str] = {
    "Component": "components",
    "Process":   "processes",
    "Resource":  "resources",
}

NORMATIVE_TOPLEVEL_SECTIONS = [
    "# Component Objects",
    "# Process Objects",
    "# Domain Name Data Object",
    "# Contact Data Object",
    "# Host Data Object",
    "# Organisation Data Object",
    "# User Object",
]

IANA_SECTION_MARKER = "# IANA Considerations"

# Map H1 section prefix → Object Type label used in IANA tables
SECTION_TO_OBJ_TYPE: dict[str, str] = {
    "# Component Objects": "Component",
    "# Process Objects":   "Process",
    "# Domain Name Data Object": "Resource",
    "# Contact Data Object":     "Resource",
    "# Host Data Object":        "Resource",
    "# Organisation Data Object": "Resource",
    "# User Object":              "Resource",
}

# H1 sections that are an ENVELOPE containing multiple objects at H2 (each
# object's own heading is one level deeper than the section heading).
# Resource/Data Object sections are NOT envelopes: the object IS the H1
# itself ("# Domain Name Data Object"), with "Object Description" /
# "Data Elements" / "Operations" as sibling H2 headings underneath it.
# This is the single source of truth for OBJ_HEADING_LEVEL below - derive
# from it rather than sniffing document content (e.g. "does '* Data
# Elements:' appear as a literal bullet?").
ENVELOPE_SECTIONS = {"# Component Objects", "# Process Objects"}

# Object Type → the heading depth (H-level) at which objects of that type
# begin. Component/Process objects begin at H2 (nested inside their envelope
# H1); Resource/Data objects begin at H1 (the object IS the H1). Sibling
# sub-sections (Data Elements, Operations, and any other subsection) are
# always exactly one level deeper than this.
OBJ_HEADING_LEVEL: dict[str, int] = {
    "Component": 2,
    "Process": 2,
    "Resource": 1,
}

# Object Types that never have an Operations sub-section, per the document's
# own structuring rule: Component Objects are pure data structures with no
# behaviour of their own.
OBJ_TYPES_WITHOUT_OPERATIONS = {"Component"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ElementDef:
    identifier: str
    name: str
    cardinality: str
    mutability: str
    data_type: str
    line: int  # 1-based line number
    description: str = ""
    constraints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ParamDef:
    """Operation parameter (transient, not persisted)."""
    identifier: str
    name: str
    cardinality: str
    data_type: str
    line: int  # 1-based line number
    description: str = ""
    constraints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class OperationDef:
    name: str          # human-readable name (e.g. "Create", "Read")
    identifier: str    # machine-readable id (e.g. "create", "read")
    line: int          # 1-based line number
    description: str = ""
    authorisation: list[str] = field(default_factory=list)
    input: str = ""
    output: str = ""
    params: list[ParamDef] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SupportedProcessDef:
    """
    One entry in the inherited-process bullet list of a Data Object's
    "## Processes" section: a generic Process Object the Data Object
    supports UNMODIFIED, e.g.:

      * Renew Process
        * Constraints: (None)

    The bullet text itself is the reference to the Process Object, by Name
    (the same lookup key "* Extends:" uses) — there is no Identifier
    attribute, which would be a redundant second key. The short form is
    accepted: "Renew Process" resolves to the object named "Renew Process
    Object" (see resolve_process_name).

    Processes the Data Object *extends* are not listed here — they appear
    as their own H3 Process Object definitions under the same
    "## Processes" section, each carrying an "* Extends:" attribute (see
    ObjectDef.extends).
    """
    name: str          # bullet text, e.g. "Renew Process"
    line: int          # 1-based line number
    constraints: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SubsectionDef:
    """
    A non-Operations H3 subsection nested inside an object's H2 (e.g.
    "### RDATA Structures in EPP Profile {#rdata-structures}" inside the
    dnsRecord Component Object) - explanatory content that is neither a data
    element nor an operation, but belongs to the object it's nested under.
    """
    heading: str       # full heading text, e.g. "RDATA Structures in EPP Profile"
    anchor: str        # "{#rdata-structures}" anchor if present, else ""
    line: int          # 1-based line of the heading
    notes: list[str] = field(default_factory=list)


@dataclass
class ObjectDef:
    name: str
    identifier: str
    source: str        # "normative" or "iana"
    line: int          # 1-based line of the Name / "Object:" declaration
    obj_type: str = ""       # "Component", "Process", or "Resource"
    extends: str = ""        # "* Extends:" — the NAME of the object this one extends
    description: str = ""
    elements: list[ElementDef] = field(default_factory=list)
    operations: list[OperationDef] = field(default_factory=list)
    preamble: list[str] = field(default_factory=list)
    subsections: list[SubsectionDef] = field(default_factory=list)
    supported_processes: list[SupportedProcessDef] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOP_LEVEL_NAME_RE  = re.compile(r"^\* Name:\s*(.+)$")
TOP_LEVEL_IDENT_RE = re.compile(r"^\* Identifier:\s*(\S+)")

_WS_RE        = re.compile(r"\s+")
_BRACKET_L_RE = re.compile(r"\s*\[\s*")   # optional space before/inside [
_BRACKET_R_RE = re.compile(r"\s*\]\s*")   # optional space before/inside ]


def _norm(value: str) -> str:
    """Collapse internal whitespace and strip edges for comparison."""
    return _WS_RE.sub(" ", value).strip()


def _norm_type(value: str) -> str:
    """
    Normalise a Data Type string for comparison:
    - collapse whitespace
    - remove spaces around square brackets: "Dictionary [Integer]" == "Dictionary[Integer]"
    """
    v = _norm(value)
    v = _BRACKET_L_RE.sub("[", v)
    v = _BRACKET_R_RE.sub("]", v)
    return v


def _parse_table_row(line: str) -> list[str] | None:
    """
    Parse a GFM pipe-table row.
    Returns a list of stripped cell strings, or None for separator rows.
    """
    line = line.strip()
    if not line.startswith("|"):
        return None
    # Separator row: | --- | --- |
    if re.match(r"^\|[-| :]+\|$", line):
        return None
    cells = line.split("|")
    # strip leading/trailing empty strings produced by the surrounding pipes
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return [c.strip() for c in cells] if cells else None


def in_normative_section(current_h1: str | None) -> bool:
    if current_h1 is None:
        return False
    for marker in NORMATIVE_TOPLEVEL_SECTIONS:
        if current_h1.startswith(marker):
            return True
    return False


# ---------------------------------------------------------------------------
# Normative element parser
# ---------------------------------------------------------------------------

def _collect_element_attrs(lines: list[str], start: int, end: int,
                            indent: int) -> dict[str, tuple[str, int]]:
    """
    Collect attribute bullets at exactly *indent* spaces of indentation.
    Returns {attr_key_lower: (value, 1-based-line)}.
    Blank lines and "A>" editorial asides are skipped wherever they appear,
    including between attribute bullets - a valid editorial pattern in this
    document. Stops at any other shallower-indented, non-blank, non-aside
    line.
    """
    attr_re = re.compile(r"^" + " " * indent + r"\* ([\w /]+):\s*(.*)$")
    attrs: dict[str, tuple[str, int]] = {}
    for j in range(start, end):
        raw = lines[j]
        m = attr_re.match(raw)
        if m:
            key = m.group(1).strip().lower()
            val = m.group(2).strip()
            attrs[key] = (val, j + 1)
        elif raw.strip() == "" or _ASIDE_RE.match(raw):
            continue
        elif not raw.startswith(" " * indent):
            # stepped back to shallower indent - stop
            break
    return attrs


def _parse_nested_bullet_list(lines: list[str], start: int, end: int,
                               indent: int) -> tuple[list[str], int]:
    """
    Parse a bullet list at exactly *indent* spaces of indentation into a list
    of strings, one per top-level bullet at that indent. Any lines nested
    more deeply under a bullet (a further sub-list, a numbered list, etc.)
    are appended to that same bullet's string as additional newline-joined
    lines, stripped of leading whitespace but keeping their original marker
    (e.g. "1.", "* `addPeriod`:") so the embedded structure stays readable.

    Used to parse a "* Constraints:" (or similarly blank) attribute value
    that continues as a deeper-indented bullet list rather than inline text.

    Returns (items, consumed_end): consumed_end is the 0-based index one past
    the last line belonging to this list, so callers can exclude
    [start, consumed_end) from orphan-prose scanning.
    """
    items: list[str] = []
    bullet_re = re.compile(r"^" + " " * indent + r"\* (.+)$")
    i = start
    while i < end:
        raw = lines[i]
        m = bullet_re.match(raw)
        if m:
            item_lines = [m.group(1).strip()]
            i += 1
            while i < end:
                nested = lines[i]
                if nested.strip() == "":
                    i += 1
                    continue
                if not nested.startswith(" " * (indent + 1)):
                    break  # back to this indent or shallower - next bullet or end
                item_lines.append(nested.strip())
                i += 1
            items.append("\n".join(item_lines))
            continue
        if raw.strip() == "":
            i += 1
            continue
        # A non-blank, non-bullet line ends the list: at indent > 0 this is
        # any line shallower than *indent* (a real sibling boundary); at
        # indent == 0 there is no shallower level to check against, so any
        # non-bullet content line itself ends the list.
        if indent == 0 or not raw.startswith(" " * indent):
            break
        i += 1
    return items, i


def _collect_constraints_list(lines: list[str], attr_end: int,
                               attrs: dict[str, tuple[str, int]],
                               attr_indent: int) -> tuple[list[str], tuple[int, int] | None]:
    """
    Resolve the "constraints" attribute (from *attrs*, as collected by
    _collect_element_attrs at *attr_indent*) into a list of strings.

    - "(None)" (or empty with nothing following) -> []
    - Non-empty inline value (e.g. "MUST be positive.") -> single-item list
    - Empty inline value immediately followed by a bullet list -> one list
      item per bullet, via _parse_nested_bullet_list. The sub-list's
      indentation is read from the source (the leading whitespace of the
      first non-blank line after the "* Constraints:" line) rather than
      assumed as a fixed offset from *attr_indent*, and is required to be
      deeper than attr_indent — otherwise it is not part of this attribute.

    Returns (constraints, consumed_range). consumed_range is the (start, end)
    0-based line range of a nested bullet list, if one was parsed, so the
    caller can exclude it from orphan-prose scanning; None otherwise.
    """
    cons_val, cons_line = attrs.get("constraints", ("", 0))
    if cons_val and cons_val != "(None)":
        return [cons_val], None
    if not cons_val and cons_line:
        # Blank inline value: look for a nested bullet list right after the
        # "* Constraints:" line. cons_line is 1-based; this is the next line,
        # 0-based.
        nested_start = cons_line
        j = nested_start
        while j < attr_end and lines[j].strip() == "":
            j += 1
        if j < attr_end:
            first = lines[j]
            actual_indent = len(first) - len(first.lstrip(" "))
            is_bullet = re.match(r"^ *\* \S", first) is not None
            if is_bullet and actual_indent > attr_indent:
                nested_list, consumed_end = _parse_nested_bullet_list(
                    lines, nested_start, attr_end, indent=actual_indent)
                if nested_list:
                    return nested_list, (nested_start, consumed_end)
    return [], None


# ---------------------------------------------------------------------------
# Orphan prose extraction
# ---------------------------------------------------------------------------
#
# "Orphan prose" is any line inside a parsed block (object preamble, element,
# operation, or parameter) that is not one of the recognised structured
# attribute bullets (Name/Identifier/Cardinality/Mutability/Data Type/
# Description/Constraints), not a heading, not a table row, and not an
# element/operation/param header bullet. It typically consists of
# explanatory paragraphs the author wrote directly in the block instead of
# using the Description/Constraints attributes. Consecutive non-blank orphan
# lines are joined into one paragraph string; a blank line starts a new
# paragraph. Aside notes ("A>") are recognised but always dropped.
#
# Every extracted paragraph is also recorded in PROSE_WARNINGS so the
# operator can be told to move the text into a proper structured attribute
# at the source.

PROSE_WARNINGS: list[str] = []

_ASIDE_RE = re.compile(r"^\s*A>")
_HEADING_RE = re.compile(r"^#{1,6} ")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_KNOWN_ATTR_KEYS = {
    "name", "identifier", "cardinality", "mutability", "data type",
    "description", "constraints", "unique identifier", "object type",
    "reference", "extends",
}


def _is_attr_bullet(raw: str) -> bool:
    """True if *raw* is a recognised '* Key: value' attribute bullet at any indent."""
    m = re.match(r"^\s*\* ([\w /]+):", raw)
    if not m:
        return False
    return m.group(1).strip().lower() in _KNOWN_ATTR_KEYS


def _is_header_bullet(raw: str) -> bool:
    """
    True if *raw* is an element/operation/parameter *header* bullet, i.e. a
    top-of-block bullet whose text is a name/label rather than a
    'Key: value' attribute (e.g. "  * Repository ID" introducing an element).
    """
    m = re.match(r"^\s*\* (.+)$", raw)
    if not m:
        return False
    return not _is_attr_bullet(raw)


def extract_orphan_prose(lines: list[str], start: int, end: int,
                          label: str, source_ref: str,
                          skip_ranges: list[tuple[int, int]] | None = None) -> list[str]:
    """
    Walk lines[start:end] and collect orphan prose paragraphs.

    Skips: blank lines (paragraph separators), heading lines, table rows,
    recognised attribute bullets, header bullets (element/operation/param
    names), "A>" aside lines (dropped silently, not warned about), and any
    line falling inside *skip_ranges* (half-open [lo, hi) pairs) — used to
    exclude a nested bullet list already consumed as a structured attribute's
    value (e.g. a multi-item "* Constraints:" sub-list), so it is not also
    captured here as orphan prose.

    *label* / *source_ref* are used only to compose the warning message
    (e.g. label="element 'repositoryId'", source_ref="line 631").
    """
    paragraphs: list[str] = []
    current: list[str] = []
    skip_ranges = skip_ranges or []

    def _flush():
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for j in range(start, end):
        if any(lo <= j < hi for lo, hi in skip_ranges):
            _flush()
            continue

        raw = lines[j]
        stripped = raw.strip()

        if stripped == "":
            _flush()
            continue
        if _ASIDE_RE.match(raw):
            _flush()
            continue
        if _HEADING_RE.match(stripped):
            _flush()
            continue
        if _TABLE_ROW_RE.match(raw):
            _flush()
            continue
        if _is_attr_bullet(raw):
            _flush()
            continue
        if _is_header_bullet(raw):
            _flush()
            continue

        current.append(stripped)

    _flush()

    if paragraphs:
        PROSE_WARNINGS.append(
            f"{source_ref}: {label} has {len(paragraphs)} orphan prose "
            f"paragraph(s) not captured by a structured attribute — "
            f"consider moving this text into a Description/Constraints "
            f"bullet at the source."
        )

    return paragraphs


def _parse_normative_elements_nested(lines: list[str], body_start: int,
                                     body_end: int) -> list[ElementDef]:
    """
    Component / Process object layout:

      * Data Elements:          ← 0-indent
        * Element Name          ← 2-space indent  (element header)
          * Identifier: foo     ← 4-space indent  (attribute)
          * Cardinality: 1
          * Mutability: read-write
          * Data Type: String
    """
    elements: list[ElementDef] = []
    in_data_elements = False
    i = body_start
    while i < body_end:
        raw = lines[i]

        if raw == "* Data Elements:":
            in_data_elements = True
            i += 1
            continue

        if in_data_elements:
            # Another top-level bullet ends the block
            if re.match(r"^\* \S", raw):
                break

            m_elem = re.match(r"^  \* (.+)$", raw)
            if m_elem:
                elem_name_raw = m_elem.group(1).strip()
                elem_line = i + 1
                # Find end of this element's block (next 2-space header or body_end)
                elem_end = body_end
                for k in range(i + 1, body_end):
                    if re.match(r"^  \* \S", lines[k]) or re.match(r"^\* \S", lines[k]):
                        elem_end = k
                        break
                attrs = _collect_element_attrs(lines, i + 1, body_end, indent=4)
                elem_id = attrs.get("identifier",  ("", 0))[0]
                card    = attrs.get("cardinality", ("", 0))[0]
                mutab   = attrs.get("mutability",  ("", 0))[0]
                dtype   = attrs.get("data type",   ("", 0))[0]
                desc    = attrs.get("description", ("", 0))[0]
                cons, cons_range = _collect_constraints_list(
                    lines, elem_end, attrs, attr_indent=4)
                id_line = attrs.get("identifier",  ("", elem_line))[1]
                if elem_id:
                    notes = extract_orphan_prose(
                        lines, i + 1, elem_end,
                        label=f"element '{elem_id}'",
                        source_ref=f"line {elem_line}",
                        skip_ranges=[cons_range] if cons_range else None)
                    elements.append(ElementDef(
                        identifier=elem_id,
                        name=elem_name_raw,
                        cardinality=card,
                        mutability=mutab,
                        data_type=dtype,
                        line=id_line,
                        description=desc,
                        constraints=cons,
                        notes=notes,
                    ))
        i += 1
    return elements


def _parse_normative_elements_flat(lines: list[str], body_start: int,
                                   body_end: int) -> list[ElementDef]:
    """
    Data Object (domainName, contact, host) layout inside "## Data Elements":

      * Element Name            ← 0-indent  (element header)
        * Identifier: foo       ← 2-space indent  (attribute)
        * Cardinality: 1
        * Mutability: read-write
        * Data Type: String
    """
    elements: list[ElementDef] = []
    i = body_start
    while i < body_end:
        raw = lines[i]

        m_elem = re.match(r"^\* (.+)$", raw)
        if m_elem:
            elem_name_raw = m_elem.group(1).strip()
            # Skip object-header bullets
            if elem_name_raw.startswith("Name:") or elem_name_raw.startswith("Identifier:"):
                i += 1
                continue
            elem_line = i + 1
            # Find end of this element's block (next 0-indent bullet or body_end)
            elem_end = body_end
            for k in range(i + 1, body_end):
                if re.match(r"^\* \S", lines[k]):
                    elem_end = k
                    break
            attrs = _collect_element_attrs(lines, i + 1, body_end, indent=2)
            elem_id = attrs.get("identifier",  ("", 0))[0]
            card    = attrs.get("cardinality", ("", 0))[0]
            mutab   = attrs.get("mutability",  ("", 0))[0]
            dtype   = attrs.get("data type",   ("", 0))[0]
            desc    = attrs.get("description", ("", 0))[0]
            cons, cons_range = _collect_constraints_list(
                lines, elem_end, attrs, attr_indent=2)
            id_line = attrs.get("identifier",  ("", elem_line))[1]
            if elem_id:
                notes = extract_orphan_prose(
                    lines, i + 1, elem_end,
                    label=f"element '{elem_id}'",
                    source_ref=f"line {elem_line}",
                    skip_ranges=[cons_range] if cons_range else None)
                elements.append(ElementDef(
                    identifier=elem_id,
                    name=elem_name_raw,
                    cardinality=card,
                    mutability=mutab,
                    data_type=dtype,
                    line=id_line,
                    description=desc,
                    constraints=cons,
                    notes=notes,
                ))
        i += 1
    return elements


def _parse_supported_processes(lines: list[str], body_start: int,
                                body_end: int) -> list[SupportedProcessDef]:
    """
    Parse the inherited-process bullet list of a Data Object's
    "## Processes" section. The bullet text IS the reference to the Process
    Object (by Name); only Constraints may follow as an attribute:

      * Renew Process            ← 0-indent  (the reference itself)
        * Constraints: (None)    ← 2-space indent  (attribute)
    """
    processes: list[SupportedProcessDef] = []
    i = body_start
    while i < body_end:
        raw = lines[i]

        m_proc = re.match(r"^\* (\S.*)$", raw)
        if m_proc and not _is_attr_bullet(raw):
            proc_name_raw = m_proc.group(1).strip()
            proc_line = i + 1
            proc_end = body_end
            for k in range(i + 1, body_end):
                if re.match(r"^\* \S", lines[k]):
                    proc_end = k
                    break
            attrs = _collect_element_attrs(lines, i + 1, proc_end, indent=2)
            cons, cons_range = _collect_constraints_list(
                lines, proc_end, attrs, attr_indent=2)
            notes = extract_orphan_prose(
                lines, i + 1, proc_end,
                label=f"supported process '{proc_name_raw}'",
                source_ref=f"line {proc_line}",
                skip_ranges=[cons_range] if cons_range else None)
            processes.append(SupportedProcessDef(
                name=proc_name_raw,
                line=proc_line,
                constraints=cons,
                notes=notes,
            ))
        i += 1
    return processes


# ---------------------------------------------------------------------------
# Normative operation parser
# ---------------------------------------------------------------------------

def _parse_normative_params_from_bullets(lines: list[str], start: int,
                                         end: int) -> list[ParamDef]:
    """
    Parse transient operation parameters from a bullet block.
    Used for both nested (Process, 4-space attrs) and flat (Data Object, 2-space attrs).

    Nested (Process objects – params live inside the #### heading block):
      * Param Name              ← 0-indent
        * Identifier: foo       ← 2-space indent
        * Cardinality: 0-1
        * Data Type: String

    The function tries 2-space attrs first and falls back to 4-space if not found.
    """
    params: list[ParamDef] = []
    i = start
    while i < end:
        raw = lines[i]
        m_param = re.match(r"^\* (.+)$", raw)
        if m_param:
            param_name_raw = m_param.group(1).strip()
            # Skip bullet lines that are not parameter headers
            if re.match(r"^(Identifier|Cardinality|Data Type|Mutability|"
                        r"Input|Output|Authorisation|Constraints|Description|"
                        r"In EPP):", param_name_raw):
                i += 1
                continue
            param_line = i + 1
            # Find end of this param's block (next 0-indent bullet or end)
            param_end = end
            for k in range(i + 1, end):
                if re.match(r"^\* \S", lines[k]):
                    param_end = k
                    break
            # Try 2-space indent first, then 4-space
            attr_indent = 2
            attrs = _collect_element_attrs(lines, i + 1, end, indent=attr_indent)
            if not attrs.get("identifier"):
                attr_indent = 4
                attrs = _collect_element_attrs(lines, i + 1, end, indent=attr_indent)
            param_id = attrs.get("identifier",  ("", 0))[0]
            card     = attrs.get("cardinality", ("", 0))[0]
            dtype    = attrs.get("data type",   ("", 0))[0]
            desc     = attrs.get("description", ("", 0))[0]
            cons, cons_range = _collect_constraints_list(
                lines, param_end, attrs, attr_indent=attr_indent)
            id_line  = attrs.get("identifier",  ("", param_line))[1]
            if param_id:
                notes = extract_orphan_prose(
                    lines, i + 1, param_end,
                    label=f"parameter '{param_id}'",
                    source_ref=f"line {param_line}",
                    skip_ranges=[cons_range] if cons_range else None)
                params.append(ParamDef(
                    identifier=param_id,
                    name=param_name_raw,
                    cardinality=card,
                    data_type=dtype,
                    line=id_line,
                    description=desc,
                    constraints=cons,
                    notes=notes,
                ))
        i += 1
    return params


_OP_FIELD_BULLET_RE = re.compile(r"^\* (Authorisation|Input|Output):\s*(.*)$")
_OP_TRANSIENT_MARKER_RE = re.compile(r"transient data element", re.IGNORECASE)


def _parse_operation_body_fields(lines: list[str], body_start: int,
                                  body_end: int) -> tuple[str, list[str], str, str, int]:
    """
    Parse an operation's body (the block after its "* Identifier:" bullet)
    into (description, authorisation, input, output, fields_end).

    Convention used throughout this document: an operation's description is
    plain prose (never a "* Description:" bullet, which is element/object-
    only) immediately following "* Identifier:", ending at the first
    recognised field bullet - "* Authorisation:", "* Input:", or
    "* Output:" - or the "transient data element" params marker, whichever
    comes first. That prose is collected via _parse_subsection_body (so
    paragraph breaks are preserved as separate description lines, joined
    with "\\n") rather than extract_orphan_prose's space-joining, matching
    the newline-preserving convention chosen for multi-paragraph
    descriptions.

    - "* Authorisation:" is always followed by a nested bullet list (never
      an inline value) - parsed the same way as a Constraints sub-list via
      _parse_nested_bullet_list, into a list of strings.
    - "* Input:"/"* Output:" are always a single inline value, never a list.

    fields_end is the 0-based index one past the last recognised field
    bullet's own content, for the caller to exclude from further notes/
    params scanning.
    """
    # Locate the first recognised field bullet or the transient-params
    # marker, whichever comes first - that is where description prose ends.
    desc_end = body_end
    for j in range(body_start, body_end):
        if _OP_FIELD_BULLET_RE.match(lines[j]) or _OP_TRANSIENT_MARKER_RE.search(lines[j]):
            desc_end = j
            break

    desc_paragraphs = _parse_subsection_body(lines, body_start, desc_end)
    description = "\n".join(desc_paragraphs)

    authorisation: list[str] = []
    op_input = ""
    op_output = ""
    fields_end = desc_end
    j = desc_end
    while j < body_end:
        m = _OP_FIELD_BULLET_RE.match(lines[j])
        if not m:
            break
        field_name = m.group(1)
        inline_val = m.group(2).strip()
        if field_name == "Authorisation":
            if inline_val:
                authorisation = [inline_val]
                j += 1
            else:
                # Blank inline value: the nested bullet list's indentation is
                # read from the source (not assumed) - same approach as
                # _collect_constraints_list, to stay correct if the document
                # ever uses a different indent depth.
                k = j + 1
                while k < body_end and lines[k].strip() == "":
                    k += 1
                if k < body_end and re.match(r"^ *\* \S", lines[k]):
                    actual_indent = len(lines[k]) - len(lines[k].lstrip(" "))
                    items, consumed_end = _parse_nested_bullet_list(
                        lines, k, body_end, indent=actual_indent)
                    authorisation = items
                    j = consumed_end
                else:
                    j += 1
        elif field_name == "Input":
            op_input = inline_val
            j += 1
        else:  # Output
            op_output = inline_val
            j += 1
        # Skip a following blank line before checking for the next field bullet.
        while j < body_end and lines[j].strip() == "":
            j += 1
        fields_end = j
    return description, authorisation, op_input, op_output, fields_end


def _parse_normative_operations_nested(lines: list[str],
                                       ops_start: int,
                                       ops_end: int,
                                       heading_re: re.Pattern) -> list[OperationDef]:
    """
    Parse operations from a heading-based block.

    heading_re matches the operation heading line (e.g. "#### Create {#...}")
    and capture group 1 must be the operation name.

    Within each operation block, `* Identifier: <id>` provides the machine id.
    Operations without a `* Identifier:` bullet are recorded with identifier=""
    so that callers can report them as [OP MISSING IDENTIFIER].
    Transient params are bullet items after a "following transient" marker.
    """
    operations: list[OperationDef] = []
    i = ops_start
    while i < ops_end:
        raw = lines[i].rstrip()
        m_heading = heading_re.match(raw)
        if not m_heading:
            i += 1
            continue

        op_name = m_heading.group(1).strip()
        op_line = i + 1
        op_id   = ""
        id_line = i  # 0-based index of the "* Identifier:" line, if found

        # Scan for * Identifier: within the block (block_end computed below)
        heading_prefix = raw[:raw.index(" ")]  # e.g. "####" or "###"
        block_end = ops_end
        for k in range(i + 1, ops_end):
            stripped = lines[k].rstrip()
            if re.match(r"^#{1,6} ", stripped):
                other_prefix = stripped[:stripped.index(" ")]
                if len(other_prefix) <= len(heading_prefix):
                    block_end = k
                    break

        for j in range(i + 1, block_end):
            m_id = re.match(r"^\* Identifier:\s*(\S+)", lines[j])
            if m_id:
                op_id = m_id.group(1).strip()
                id_line = j
                break

        body_start = id_line + 1
        op_desc, authorisation, op_input, op_output, fields_end = (
            _parse_operation_body_fields(lines, body_start, block_end))

        # Find params: look for "transient data element" marker, then parse bullets
        params: list[ParamDef] = []
        param_start = block_end
        for j in range(fields_end, block_end):
            if re.search(r"transient data element", lines[j], re.IGNORECASE):
                param_start = j + 1
                break

        if param_start < block_end:
            params = _parse_normative_params_from_bullets(
                lines, param_start, block_end)

        # Orphan prose covers whatever remains of the operation body -
        # excluding the description prose and field bullets (already parsed
        # above) and the param bullet sub-block - but including the
        # "transient data element" marker line itself, which is explanatory
        # prose.
        notes_end = param_start if param_start < block_end else block_end
        notes = extract_orphan_prose(
            lines, fields_end, notes_end,
            label=f"operation '{op_id or op_name}'",
            source_ref=f"line {op_line}")

        # Always record the operation; identifier="" signals missing identifier
        operations.append(OperationDef(
            name=op_name,
            identifier=op_id,
            line=op_line,
            description=op_desc,
            authorisation=authorisation,
            input=op_input,
            output=op_output,
            params=params,
            notes=notes,
        ))
        i = block_end  # jump past this block to avoid re-scanning

    return operations


def _parse_subsection_body(lines: list[str], start: int, end: int) -> list[str]:
    """
    Parse a subsection's body into an ordered list of entries, preserving
    document order between prose paragraphs and 0-indent bullet lists
    (unlike extract_orphan_prose, which treats all bullets as element/
    operation/param headers to be skipped - not applicable here, since a
    subsection body's bullets ARE its content, e.g. the per-record-type list
    in "RDATA Structures in EPP Profile").

    - A run of consecutive non-blank, non-bullet lines becomes one prose
      paragraph entry (space-joined).
    - A run of 0-indent "* " bullets becomes one entry per bullet, each
      folded the same way as a Constraints sub-list (deeper-nested lines
      under a bullet are newline-joined into that same entry).
    - Headings, table rows, and "A>" asides are skipped (asides dropped
      silently, matching extract_orphan_prose's behaviour).
    """
    entries: list[str] = []
    para: list[str] = []

    def _flush_para():
        if para:
            entries.append(" ".join(para))
            para.clear()

    i = start
    while i < end:
        raw = lines[i]
        stripped = raw.strip()

        if stripped == "":
            _flush_para()
            i += 1
            continue
        if _ASIDE_RE.match(raw) or _HEADING_RE.match(stripped) or _TABLE_ROW_RE.match(raw):
            _flush_para()
            i += 1
            continue
        if re.match(r"^\* \S", raw):
            _flush_para()
            bullet_items, consumed_end = _parse_nested_bullet_list(
                lines, i, end, indent=0)
            entries.extend(bullet_items)
            i = consumed_end
            continue

        para.append(stripped)
        i += 1

    _flush_para()
    return entries


def _heading_re_for_level(level: int) -> re.Pattern:
    """Match a heading at exactly *level* (e.g. level=2 -> '^## [^#]')."""
    return re.compile(r"^#{" + str(level) + r"} [^#]")


def _find_sibling_headings(lines: list[str], obj_start: int, obj_end: int,
                            level: int) -> list[tuple[int, int, str, str]]:
    """
    Find every heading at exactly *level* within (obj_start, obj_end) -
    the object's direct sub-sections, per the document's structuring rule
    that Data Elements / Operations / any other subsection all sit at
    exactly one level deeper than the object's own heading. Stops at the
    first heading shallower than *level* (a sibling object or the section's
    end), since that marks the end of this object's own span.

    Returns a list of (heading_line, block_end, heading_text, anchor) tuples,
    where block_end is the 0-based index one past this sub-section's content
    (up to the next heading at *level* or shallower, or obj_end).
    """
    own_re = _heading_re_for_level(level)
    shallower_res = [_heading_re_for_level(l) for l in range(1, level)]
    text_re = re.compile(r"^#{" + str(level) + r"} (.+?)(?:\s*(\{#[^}]*\}))?\s*$")

    results: list[tuple[int, int, str, str]] = []
    i = obj_start
    while i < obj_end:
        stripped = lines[i].rstrip()
        if any(r.match(stripped) for r in shallower_res):
            break
        m = own_re.match(stripped)
        if m:
            heading_line = i
            m_text = text_re.match(stripped)
            heading_text = m_text.group(1).strip() if m_text else stripped.lstrip("# ").strip()
            anchor = (m_text.group(2) or "").strip() if m_text else ""
            block_end = obj_end
            for k in range(i + 1, obj_end):
                ks = lines[k].rstrip()
                if own_re.match(ks) or any(r.match(ks) for r in shallower_res):
                    block_end = k
                    break
            results.append((heading_line, block_end, heading_text, anchor))
            i = block_end
            continue
        i += 1
    return results


def _parse_operations_group(lines: list[str], group_start: int, group_end: int,
                             op_level: int) -> list[OperationDef]:
    """
    Parse operations at exactly *op_level* within an Operations sub-section
    (group_start, group_end). A heading at op_level whose text ends in the
    word "Operations" (e.g. "Transfer Operations") is a group container:
    its individual operations are one level deeper still (op_level + 1,
    e.g. "Transfer Create Operation"). Any other heading at op_level is
    itself a direct operation (e.g. "Create Operation", or a Process
    object's "Create {#...}").
    """
    group_name_re = re.compile(r"^#{" + str(op_level) + r"} (.+\bOperations)\s*(?:\{[^}]*\})?\s*$")
    operations: list[OperationDef] = []
    for heading_line, block_end, heading_text, _anchor in _find_sibling_headings(
            lines, group_start, group_end, op_level):
        if heading_text == "Processes":
            # The Data Object's structured "### Processes" subsection is
            # parsed separately by _parse_supported_processes, not as an
            # operation - skip it here regardless of nesting level.
            continue
        stripped = lines[heading_line].rstrip()
        if group_name_re.match(stripped):
            # Group container: descend one level for its individual operations.
            child_op_re = re.compile(r"^#{" + str(op_level + 1) + r"} (.+?)(?:\s*\{[^}]*\})?\s*$")
            operations.extend(_parse_normative_operations_nested(
                lines, heading_line + 1, block_end, child_op_re))
        else:
            # Direct operation: parse this single heading as one operation.
            this_op_re = re.compile(r"^#{" + str(op_level) + r"} (.+?)(?:\s*\{[^}]*\})?\s*$")
            operations.extend(_parse_normative_operations_nested(
                lines, heading_line, block_end, this_op_re))
    return operations


def _parse_normative_operations_for_object(lines: list[str], obj_start: int,
                                           obj_end: int, obj_heading_level: int,
                                           obj_type: str, obj_id: str) -> list[OperationDef]:
    """
    Find and parse the object's Operations sub-section, a sibling heading at
    obj_heading_level + 1 named exactly "Operations":

      Resource/Data Object (obj_heading_level=1):
        ## Operations                    <- level 2
          ### Create Operation           <- level 3 (direct operation)
          ### Transfer Operations        <- level 3 (group container)
            #### Transfer Create Operation  <- level 4 (individual operation)

      Component/Process Object (obj_heading_level=2):
        ### Operations                   <- level 3
          #### Create {#...}             <- level 4 (individual operation)

    Component Objects never have an Operations sub-section (the document's
    own structuring rule): if obj_type is "Component", this returns []
    without even searching, regardless of what headings are present -
    any "Operations"-named heading found there is left for
    _parse_object_subsections to capture generically (with a warning).
    """
    if obj_type in OBJ_TYPES_WITHOUT_OPERATIONS:
        return []

    ops_level = obj_heading_level + 1
    for heading_line, block_end, heading_text, _anchor in _find_sibling_headings(
            lines, obj_start, obj_end, ops_level):
        if heading_text == "Operations":
            return _parse_operations_group(
                lines, heading_line + 1, block_end, ops_level + 1)
    return []


def _parse_supported_processes_for_object(
        lines: list[str], obj_start: int, obj_end: int,
        obj_heading_level: int, obj_type: str) -> list[SupportedProcessDef]:
    """
    Find and parse the INHERITED-process bullet list in a Data Object's
    "## Processes" sub-section (a sibling of "## Operations", one level
    deeper than the object's own heading):

      ## Processes                     <- obj_heading_level + 1
        <one line of intro prose>
        * Renew Process                <- inherited, used unmodified
          * Identifier: renewProcess
        ### Domain Transfer Process Object   <- EXTENDED, its own ObjectDef

    Only the bullet list before the first H3 belongs here: the H3
    sub-headings are object-specific Process Objects that extend a generic
    one, each parsed as its own ObjectDef by _walk_normative_objects (and
    carrying an "* Extends:" attribute naming what it extends). Processes
    listed as bullets are the ones used unmodified, with no extending
    object of their own.
    """
    if obj_type in OBJ_TYPES_WITHOUT_OPERATIONS:
        return []

    sub_level = obj_heading_level + 1
    for heading_line, block_end, heading_text, _anchor in _find_sibling_headings(
            lines, obj_start, obj_end, sub_level):
        if heading_text != "Processes":
            continue
        # The inherited-process list ends where the first embedded Process
        # Object heading (one level deeper) begins.
        list_end = block_end
        child_headings = _find_sibling_headings(
            lines, heading_line + 1, block_end, sub_level + 1)
        if child_headings:
            list_end = child_headings[0][0]
        return _parse_supported_processes(lines, heading_line + 1, list_end)
    return []


def _parse_object_subsections(lines: list[str], obj_start: int, obj_end: int,
                               obj_heading_level: int, obj_type: str,
                               obj_id: str) -> list[SubsectionDef]:
    """
    Find every sibling sub-section of the object (a heading at
    obj_heading_level + 1) that is not one of the document's own structural
    headings, each already parsed elsewhere:
      - "Object Description": holds the object's own header attribute
        bullets (Name/Identifier/Description), parsed by _parse_object_header.
      - "Data Elements": parsed by the element parsers.
      - "Operations": parsed by _parse_normative_operations_for_object, and
        only for object types that have one.
      - "Processes": a Data Object's container for embedded Process
        Objects, e.g. Domain Name Data Object's "## Processes" holding
        "### Domain Create Process Object" - each is already parsed as its
        own top-level ObjectDef by parse_normative_objects, so re-capturing
        the heading here would duplicate that content as garbled subsection
        prose.
    Anything else - e.g. "### RDATA Structures in EPP Profile
    {#rdata-structures}" inside the dnsRecord Component Object - is a
    genuine generic subsection. Its body (prose paragraphs and bullet
    lists, in document order) is captured via _parse_subsection_body so
    nothing is silently dropped.

    Component Objects never have an Operations sub-section: if a heading
    named exactly "Operations" is nonetheless found under one, it is
    captured here as a generic subsection (not parsed as operations) and a
    warning is emitted, since this contradicts the document's own
    structuring rule and likely signals a mistake at the source.
    """
    sub_level = obj_heading_level + 1
    operations_allowed = obj_type not in OBJ_TYPES_WITHOUT_OPERATIONS

    subsections: list[SubsectionDef] = []
    for heading_line, block_end, heading_text, anchor in _find_sibling_headings(
            lines, obj_start, obj_end, sub_level):
        if heading_text in ("Object Description", "Data Elements", "Processes"):
            continue
        if heading_text == "Operations":
            if operations_allowed:
                continue  # parsed separately as the object's operations
            PROSE_WARNINGS.append(
                f"line {heading_line + 1}: Component object '{obj_id}' has an "
                f"'Operations' sub-section, but Component Objects never "
                f"define operations — captured as a generic subsection "
                f"instead; consider removing it or reclassifying the object "
                f"at the source."
            )
        notes = _parse_subsection_body(lines, heading_line + 1, block_end)
        subsections.append(SubsectionDef(
            heading=heading_text, anchor=anchor,
            line=heading_line + 1, notes=notes,
        ))
    return subsections


# ---------------------------------------------------------------------------
# Normative object parser
# ---------------------------------------------------------------------------

def _obj_type_for_h1(h1: str | None) -> str:
    if h1 is None:
        return ""
    for prefix, obj_type in SECTION_TO_OBJ_TYPE.items():
        if h1.startswith(prefix):
            return obj_type
    return ""


def _parse_object_body(lines: list[str], obj_start: int, obj_end: int,
                        header_end: int, obj_heading_level: int,
                        obj_type: str, obj_id: str, obj_line: int,
                        obj_name: str, obj_desc: str,
                        obj_extends: str = "") -> ObjectDef:
    """
    Parse one object's body (elements, operations, preamble, subsections)
    given its already-known span and identity. Called top-down by
    _walk_normative_objects for every object heading it discovers, with
    obj_type/obj_heading_level as explicit inherited context - never
    inferred by scanning back up from inside the body.
    """
    is_nested = obj_heading_level > 1

    if is_nested:
        elements = _parse_normative_elements_nested(lines, obj_start, obj_end)
        preamble_end = obj_end
        for k in range(header_end, obj_end):
            if lines[k] == "* Data Elements:":
                preamble_end = k
                break
        preamble = extract_orphan_prose(
            lines, header_end, preamble_end,
            label=f"object '{obj_id}' preamble",
            source_ref=f"line {obj_line}")
    else:
        # Locate the "## Data Elements" sub-section start and end.
        data_elem_start = obj_end  # default: not found → no elements
        data_elem_end   = obj_end
        data_elem_heading = obj_end
        for k in range(obj_start, obj_end):
            if re.match(r"^## Data Elements\s*$", lines[k].strip()):
                data_elem_heading = k
                data_elem_start = k + 1
                for m in range(k + 1, obj_end):
                    if re.match(r"^#{1,2} [^#]", lines[m].strip()):
                        data_elem_end = m
                        break
                break
        elements = _parse_normative_elements_flat(
            lines, data_elem_start, data_elem_end)
        preamble = extract_orphan_prose(
            lines, header_end, data_elem_heading,
            label=f"object '{obj_id}' preamble",
            source_ref=f"line {obj_line}")
        data_elem_intro_end = data_elem_end
        for k in range(data_elem_start, data_elem_end):
            if re.match(r"^\* \S", lines[k]):
                data_elem_intro_end = k
                break
        preamble += extract_orphan_prose(
            lines, data_elem_start, data_elem_intro_end,
            label=f"object '{obj_id}' Data Elements intro",
            source_ref=f"line {data_elem_start + 1}")

    operations = _parse_normative_operations_for_object(
        lines, obj_start, obj_end, obj_heading_level, obj_type, obj_id)

    supported_processes = _parse_supported_processes_for_object(
        lines, obj_start, obj_end, obj_heading_level, obj_type)

    subsections = _parse_object_subsections(
        lines, obj_start, obj_end, obj_heading_level, obj_type, obj_id)

    return ObjectDef(
        name=obj_name,
        identifier=obj_id,
        source="normative",
        line=obj_line,
        obj_type=obj_type,
        extends=obj_extends,
        description=obj_desc,
        elements=elements,
        operations=operations,
        preamble=preamble,
        subsections=subsections,
        supported_processes=supported_processes,
    )


def _parse_object_header(lines: list[str], obj_start: int,
                          search_end: int) -> tuple[str, str, str, str, int] | None:
    """
    Parse the object's own header attribute bullets ("* Name:", "* Identifier:",
    "* Description:", "* Extends:") starting at obj_start (which may itself be
    the "* Name:" line, for nested objects, or the line right after an
    "## Object Description" heading, for flat/Resource objects - the caller
    positions obj_start appropriately in each case).

    Returns (name, identifier, description, extends, header_end) or None if no
    "* Name:" bullet is found at or shortly after obj_start (leading blank
    lines - e.g. the blank line a heading is always followed by - and "A>"
    aside lines, e.g. an editorial TODO before an object's header bullets,
    are skipped). header_end is the 0-based index one past the last header
    attribute bullet.
    """
    while obj_start < search_end and (
            lines[obj_start].strip() == "" or _ASIDE_RE.match(lines[obj_start])):
        obj_start += 1
    if obj_start >= search_end:
        return None
    m_name = TOP_LEVEL_NAME_RE.match(lines[obj_start])
    if not m_name:
        return None
    obj_name = m_name.group(1).strip()
    obj_id = ""
    obj_desc = ""
    obj_extends = ""
    header_end = min(obj_start + 10, search_end)
    for j in range(obj_start + 1, min(obj_start + 10, search_end)):
        m_id = TOP_LEVEL_IDENT_RE.match(lines[j])
        if m_id:
            obj_id = m_id.group(1).strip()
            continue
        m_desc = re.match(r"^\* Description:\s*(.+)$", lines[j])
        if m_desc:
            obj_desc = m_desc.group(1).strip()
            continue
        m_extends = re.match(r"^\* Extends:\s*(.+)$", lines[j])
        if m_extends:
            obj_extends = m_extends.group(1).strip()
            continue
        # "* Data Elements:" / "* Operations:" / "* Object Type:" mark the
        # true end of the object's own header attributes.
        if re.match(r"^\* (Object Type|Data Elements|Operations):", lines[j]):
            if obj_id:
                header_end = j
                break
        # Other recognised header attributes (e.g. "* Unique Identifier:")
        # belong to the header and must not terminate it - the attributes
        # above may still follow them.
        elif _is_attr_bullet(lines[j]):
            continue
        elif (lines[j].strip() and not lines[j].startswith(" ") and
                re.match(r"^\* ", lines[j])):
            if obj_id:
                header_end = j
                break
    if not obj_id:
        return None
    return obj_name, obj_id, obj_desc, obj_extends, header_end


def _walk_normative_objects(lines: list[str], h1_start: int, h1_end: int,
                             obj_type: str) -> list[ObjectDef]:
    """
    Top-down recursive walk that discovers and parses every object inside a
    normative H1 section, given the section's own obj_type as inherited
    context (Component/Process for an envelope section, Resource for a Data
    Object section). Returns a flat list - embedded Process Objects (found
    under a Data Object's own "## Processes" sub-section) are included
    alongside their owning Data Object, not nested inside it.

    - Envelope sections (Component/Process Objects): every H2 child heading
      is one object at heading level 2.
    - Data Object sections: the H1 itself is the one object, at heading
      level 1. If it has a "## Processes" child (heading level 2), every H3
      child of THAT is a further embedded object, at heading level 3, with
      obj_type "Process" (overriding the section's own "Resource") -
      inherited context changes exactly at the "Processes" heading, nowhere
      else.
    """
    objects: list[ObjectDef] = []

    if obj_type in ("Component", "Process"):
        # Envelope section: every H2 child is one object.
        for heading_line, block_end, _heading_text, _anchor in _find_sibling_headings(
                lines, h1_start + 1, h1_end, level=2):
            header = _parse_object_header(lines, heading_line + 1, block_end)
            if header is None:
                continue
            obj_name, obj_id, obj_desc, obj_extends, header_end = header
            objects.append(_parse_object_body(
                lines, heading_line + 1, block_end, header_end,
                obj_heading_level=2, obj_type=obj_type, obj_id=obj_id,
                obj_line=heading_line + 2, obj_name=obj_name, obj_desc=obj_desc,
                obj_extends=obj_extends))
        return objects

    # Data Object section: the H1 itself is the object. Its "* Name:" bullet
    # sits under a "## Object Description" child heading (level 2).
    for heading_line, block_end, heading_text, _anchor in _find_sibling_headings(
            lines, h1_start + 1, h1_end, level=2):
        if heading_text == "Object Description":
            header = _parse_object_header(lines, heading_line + 1, block_end)
            if header is not None:
                obj_name, obj_id, obj_desc, obj_extends, header_end = header
                objects.append(_parse_object_body(
                    lines, h1_start + 1, h1_end, header_end,
                    obj_heading_level=1, obj_type=obj_type, obj_id=obj_id,
                    obj_line=heading_line + 2, obj_name=obj_name, obj_desc=obj_desc,
                    obj_extends=obj_extends))
        elif heading_text == "Processes":
            # Embedded Process Objects: every H3 child of "## Processes" is
            # its own object at heading level 3, obj_type "Process".
            for p_heading_line, p_block_end, _pt, _pa in _find_sibling_headings(
                    lines, heading_line + 1, block_end, level=3):
                p_header = _parse_object_header(lines, p_heading_line + 1, p_block_end)
                if p_header is None:
                    continue
                p_name, p_id, p_desc, p_extends, p_header_end = p_header
                objects.append(_parse_object_body(
                    lines, p_heading_line + 1, p_block_end, p_header_end,
                    obj_heading_level=3, obj_type="Process", obj_id=p_id,
                    obj_line=p_heading_line + 2, obj_name=p_name, obj_desc=p_desc,
                    obj_extends=p_extends))
    return objects


def parse_section_notes(lines: list[str],
                         iana_start: int) -> dict[str, list[str]]:
    """
    Extract H1-level intro prose for each normative section: the orphan text
    between the H1 heading and whatever introduces the first object.

    - Component/Process Objects (nested layout): objects start at H2, so the
      intro runs from the H1 heading to the first H2 heading. This also
      captures shared cross-object content living directly under the H1
      (e.g. the Process Object ID paragraph and its own attribute bullets are
      still excluded via _is_attr_bullet/_is_header_bullet, so only the
      prose sentences are kept, not the data-element bullets themselves).
    - Data Objects (flat layout): the object's own header bullets
      ("* Name: ...") start immediately, so there is no separate H1-level
      intro to extract; the section is skipped (returns no entry).
    """
    notes: dict[str, list[str]] = {}
    i = 0
    while i < iana_start:
        stripped = lines[i].strip()
        if re.match(r"^# [^#]", stripped) and in_normative_section(stripped):
            h1 = stripped
            intro_end = iana_start
            for k in range(i + 1, iana_start):
                ks = lines[k].strip()
                if re.match(r"^## [^#]", ks) or TOP_LEVEL_NAME_RE.match(lines[k]):
                    intro_end = k
                    break
                if re.match(r"^# [^#]", ks):
                    intro_end = k
                    break
            section_notes = extract_orphan_prose(
                lines, i + 1, intro_end,
                label=f"section '{h1}' intro",
                source_ref=f"line {i + 1}")
            if section_notes:
                notes[h1] = section_notes
        i += 1
    return notes


def parse_normative_objects(lines: list[str],
                             iana_start: int) -> list[ObjectDef]:
    """
    Top-down driver: find each normative H1 section, determine its own span
    and obj_type, then delegate to _walk_normative_objects to discover and
    parse every object inside it (including any Process Objects embedded in
    a Data Object's own "## Processes" sub-section).
    """
    objects: list[ObjectDef] = []
    i = 0
    while i < iana_start:
        stripped = lines[i].strip()
        if re.match(r"^# [^#]", stripped) and in_normative_section(stripped):
            h1_start = i
            h1_end = iana_start
            for k in range(i + 1, iana_start):
                if re.match(r"^# [^#]", lines[k].strip()):
                    h1_end = k
                    break
            obj_type = _obj_type_for_h1(stripped)
            objects.extend(_walk_normative_objects(lines, h1_start, h1_end, obj_type))
            i = h1_end
            continue
        i += 1
    return objects


# ---------------------------------------------------------------------------
# IANA object parser
# ---------------------------------------------------------------------------

def _parse_iana_params_table(lines: list[str], start: int,
                              end: int) -> list[ParamDef]:
    """
    Parse a Parameters pipe-table in the IANA section.
    Columns: Identifier | Name | Card. | Data Type | Description
    """
    params: list[ParamDef] = []
    COL_ALIASES: dict[str, list[str]] = {
        "identifier": ["identifier"],
        "name":       ["name"],
        "cardinality": ["card.", "cardinality"],
        "data type":  ["data type"],
        "description": ["description"],
    }
    header_cols: list[str] = []
    i = start
    while i < end:
        rk = lines[i].rstrip()
        cells = _parse_table_row(rk)
        if cells is None:
            if rk.strip() and not rk.strip().startswith("|"):
                break  # non-table line ends table
            i += 1
            continue
        if not header_cols:
            header_cols = [_norm(c).lower() for c in cells]
            i += 1
            continue

        def _col(canonical: str) -> str:
            for alias in COL_ALIASES.get(canonical, [canonical]):
                try:
                    idx = header_cols.index(alias)
                    return cells[idx] if idx < len(cells) else ""
                except ValueError:
                    pass
            return ""

        param_id = _col("identifier")
        if param_id:
            params.append(ParamDef(
                identifier=param_id,
                name=_col("name"),
                cardinality=_col("cardinality"),
                data_type=_col("data type"),
                line=i + 1,
                description=_col("description"),
            ))
        i += 1
    return params


def _parse_iana_operations(lines: list[str], ops_start: int,
                            obj_end: int) -> list[OperationDef]:
    """
    Parse operations from the IANA section after the "Operations" keyword.

    Format:
      Operation: <Name>

      Operation Identifier: <id>    ← optional

      Description: ...

      Parameters
      | Identifier | Name | Card. | Data Type | Description |
      | ...        | ...  | ...   | ...       | ...         |

    or:
      Parameters: (None)
    """
    operations: list[OperationDef] = []
    i = ops_start
    while i < obj_end:
        rk = lines[i].rstrip()
        m_op = re.match(r"^Operation:\s*(.+)$", rk)
        if not m_op:
            i += 1
            continue

        op_name = m_op.group(1).strip()
        op_line = i + 1
        op_id   = ""

        # Find end of this operation block (next "Operation:" or obj_end)
        block_end = obj_end
        for k in range(i + 1, obj_end):
            if re.match(r"^Operation:\s*\S", lines[k].rstrip()):
                block_end = k
                break

        # Scan for "Operation Identifier:" and "Description:" within the block
        op_desc = ""
        for j in range(i + 1, block_end):
            rj = lines[j].rstrip()
            m_id = re.match(r"^Operation Identifier:\s*(.+)$", rj)
            if m_id:
                # Take only the first token (ignore trailing parenthetical)
                op_id = m_id.group(1).strip().split()[0]
                continue
            m_desc = re.match(r"^Description:\s*(.*)$", rj)
            if m_desc:
                op_desc = m_desc.group(1).strip()

        if not op_id:
            # Fall back: derive id from name (lowercase first word)
            op_id = op_name.lower().split()[0]

        # Find "Parameters" keyword and parse table or "(None)"
        params: list[ParamDef] = []
        for j in range(i + 1, block_end):
            rj = lines[j].rstrip()
            if rj.strip() == "Parameters":
                params = _parse_iana_params_table(lines, j + 1, block_end)
                break
            if re.match(r"^Parameters:\s*\(None\)", rj):
                break

        operations.append(OperationDef(
            name=op_name,
            identifier=op_id,
            line=op_line,
            description=op_desc,
            params=params,
        ))
        i = block_end

    return operations


def parse_iana_objects(lines: list[str], iana_start: int) -> list[ObjectDef]:
    objects: list[ObjectDef] = []
    i = iana_start

    while i < len(lines):
        raw = lines[i].rstrip()

        m_obj = re.match(r"^Object:\s*(\S+)\s*$", raw)
        if not m_obj:
            i += 1
            continue

        obj_id   = m_obj.group(1).strip()
        obj_line = i + 1
        obj_name = ""

        j = i + 1
        while j < min(i + 6, len(lines)):
            m_n = re.match(r"^Object Name:\s*(.+)$", lines[j].rstrip())
            if m_n:
                obj_name = m_n.group(1).strip()
                break
            j += 1

        if not obj_name:
            i += 1
            continue

        # Determine end of this object block
        obj_end = len(lines)
        for k in range(i + 1, len(lines)):
            if re.match(r"^Object:\s*\S+\s*$", lines[k].rstrip()):
                obj_end = k
                break

        # Scan for "Data Elements" table
        elements: list[ElementDef] = []
        k = i + 1
        in_table = False
        header_cols: list[str] = []

        # Column name aliases → canonical key.
        # Some tables use "Element Identifier" / "Element Name", others use
        # the shorter "Identifier" / "Name" (e.g. domainName, host tables).
        COL_ALIASES: dict[str, list[str]] = {
            "element identifier": ["element identifier", "identifier"],
            "element name":       ["element name", "name"],
            "cardinality":        ["card.", "cardinality"],
            "mutability":         ["mutability"],
            "data type":          ["data type"],
            "description":        ["description"],
        }

        ops_start_in_block = obj_end  # position of "Operations" keyword

        while k < obj_end:
            rk = lines[k].rstrip()

            if rk.strip() == "Operations":
                ops_start_in_block = k + 1
                # Stop element scanning
                break

            if not in_table:
                if rk.strip() == "Data Elements":
                    in_table = True
                    header_cols = []
            else:
                cells = _parse_table_row(rk)
                if cells is None:
                    # Non-table line ends the table
                    if rk.strip() and not rk.strip().startswith("|"):
                        in_table = False
                    k += 1
                    continue

                if not header_cols:
                    # First real row = header
                    header_cols = [_norm(c).lower() for c in cells]
                    k += 1
                    continue

                # Data row
                def _col(canonical: str) -> str:
                    for alias in COL_ALIASES.get(canonical, [canonical]):
                        try:
                            idx = header_cols.index(alias)
                            return cells[idx] if idx < len(cells) else ""
                        except ValueError:
                            pass
                    return ""

                elem_id   = _col("element identifier")
                elem_name = _col("element name")
                card      = _col("cardinality")
                mutab     = _col("mutability")
                dtype     = _col("data type")
                desc      = _col("description")

                if elem_id:
                    elements.append(ElementDef(
                        identifier=elem_id,
                        name=elem_name,
                        cardinality=card,
                        mutability=mutab,
                        data_type=dtype,
                        line=k + 1,
                        description=desc,
                    ))
            k += 1

        # Parse operations if present
        operations: list[OperationDef] = []
        if ops_start_in_block < obj_end:
            operations = _parse_iana_operations(lines, ops_start_in_block, obj_end)

        objects.append(ObjectDef(
            name=obj_name,
            identifier=obj_id,
            source="iana",
            line=obj_line,
            elements=elements,
            operations=operations,
        ))
        i = obj_end

    return objects


# ---------------------------------------------------------------------------
# Consistency checks
# ---------------------------------------------------------------------------

def check_objects(normative: list[ObjectDef],
                  iana: list[ObjectDef]) -> list[str]:
    errors: list[str] = []

    iana_by_id   = {o.identifier: o for o in iana}
    iana_by_name = {o.name: o for o in iana}
    norm_ids     = {o.identifier for o in normative}
    norm_by_name = {o.name: o for o in normative}

    for obj in normative:
        if obj.identifier not in iana_by_id:
            if obj.name in iana_by_name:
                iana_obj = iana_by_name[obj.name]
                errors.append(
                    f"[ID MISMATCH] name='{obj.name}':\n"
                    f"  normative (line {obj.line}):  identifier='{obj.identifier}'\n"
                    f"  IANA     (line {iana_obj.line}): identifier='{iana_obj.identifier}'"
                )
            else:
                errors.append(
                    f"[MISSING IN IANA] normative object at line {obj.line}: "
                    f"identifier='{obj.identifier}', name='{obj.name}'"
                )
            continue

        iana_obj = iana_by_id[obj.identifier]
        if iana_obj.name != obj.name:
            errors.append(
                f"[NAME MISMATCH] identifier='{obj.identifier}':\n"
                f"  normative (line {obj.line}):  name='{obj.name}'\n"
                f"  IANA     (line {iana_obj.line}): name='{iana_obj.name}'"
            )

    for obj in iana:
        if obj.identifier not in norm_ids:
            if obj.name in norm_by_name:
                continue  # already reported as [ID MISMATCH]
            errors.append(
                f"[MISSING IN NORMATIVE] IANA object at line {obj.line}: "
                f"identifier='{obj.identifier}', name='{obj.name}'"
            )

    seen: dict[str, int] = {}
    for obj in normative:
        if obj.identifier in seen:
            errors.append(
                f"[DUPLICATE NORMATIVE] identifier='{obj.identifier}' "
                f"at lines {seen[obj.identifier]} and {obj.line}."
            )
        seen[obj.identifier] = obj.line

    seen = {}
    for obj in iana:
        if obj.identifier in seen:
            errors.append(
                f"[DUPLICATE IANA] identifier='{obj.identifier}' "
                f"at lines {seen[obj.identifier]} and {obj.line}."
            )
        seen[obj.identifier] = obj.line

    return errors


def resolve_process_name(name: str, norm_names: set[str]) -> str | None:
    """
    Resolve a process reference written as an Object Name against the
    normative object index, accepting the short form used in a Data
    Object's "## Processes" bullet list: "Renew Process" resolves to
    "Renew Process Object". Returns the matched Object Name, or None.
    """
    name = _norm(name)
    if name in norm_names:
        return name
    expanded = f"{name} Object"
    if expanded in norm_names:
        return expanded
    return None


def check_supported_processes(norm_obj: ObjectDef,
                               norm_names: set[str]) -> list[str]:
    """
    Verify a Data Object's process declarations resolve to real normative
    objects. Both references are by Object Name:
      - every inherited-process bullet in its "## Processes" list (short
        form accepted, see resolve_process_name);
      - an object's "* Extends:" attribute (exact Name).
    Normative-only cross-check: neither concept has an IANA table
    counterpart.
    """
    errors: list[str] = []
    prefix = f"object '{norm_obj.identifier}'"

    for proc in norm_obj.supported_processes:
        if resolve_process_name(proc.name, norm_names) is None:
            errors.append(
                f"[PROCESS MISSING IN NORMATIVE] {prefix}, supported process "
                f"at line {proc.line}: '{proc.name}' does not match the Name "
                f"of any normative object."
            )

    if norm_obj.extends and _norm(norm_obj.extends) not in norm_names:
        errors.append(
            f"[EXTENDS MISSING IN NORMATIVE] {prefix} at line {norm_obj.line}: "
            f"Extends '{norm_obj.extends}' does not match the Name of any "
            f"normative object."
        )

    return errors


def check_elements(norm_obj: ObjectDef, iana_obj: ObjectDef) -> list[str]:
    errors: list[str] = []
    prefix = f"object '{norm_obj.identifier}'"

    iana_elems = {e.identifier: e for e in iana_obj.elements}
    norm_elems = {e.identifier: e for e in norm_obj.elements}

    for ne in norm_obj.elements:
        if ne.identifier not in iana_elems:
            errors.append(
                f"[ELEM MISSING IN IANA] {prefix}: "
                f"element '{ne.identifier}' ('{ne.name}') at normative line {ne.line} "
                f"has no row in the IANA table."
            )
            continue

        ie = iana_elems[ne.identifier]
        if _norm(ne.name) != _norm(ie.name):
            errors.append(
                f"[ELEM NAME MISMATCH] {prefix}, element '{ne.identifier}':\n"
                f"    normative (line {ne.line}):  name='{ne.name}'\n"
                f"    IANA     (line {ie.line}): name='{ie.name}'"
            )
        if _norm(ne.cardinality) != _norm(ie.cardinality):
            errors.append(
                f"[ELEM CARD MISMATCH] {prefix}, element '{ne.identifier}':\n"
                f"    normative (line {ne.line}):  cardinality='{ne.cardinality}'\n"
                f"    IANA     (line {ie.line}): cardinality='{ie.cardinality}'"
            )
        if _norm(ne.mutability) != _norm(ie.mutability):
            errors.append(
                f"[ELEM MUTABILITY MISMATCH] {prefix}, element '{ne.identifier}':\n"
                f"    normative (line {ne.line}):  mutability='{ne.mutability}'\n"
                f"    IANA     (line {ie.line}): mutability='{ie.mutability}'"
            )
        if _norm_type(ne.data_type) != _norm_type(ie.data_type):
            errors.append(
                f"[ELEM TYPE MISMATCH] {prefix}, element '{ne.identifier}':\n"
                f"    normative (line {ne.line}):  data_type='{ne.data_type}'\n"
                f"    IANA     (line {ie.line}): data_type='{ie.data_type}'"
            )
        if ne.description and not ie.description:
            errors.append(
                f"[ELEM DESC EMPTY] {prefix}, element '{ne.identifier}' "
                f"(IANA line {ie.line}): IANA description is empty."
            )

    for ie in iana_obj.elements:
        if ie.identifier not in norm_elems:
            errors.append(
                f"[ELEM MISSING IN NORMATIVE] {prefix}: "
                f"IANA table row '{ie.identifier}' ('{ie.name}') at line {ie.line} "
                f"has no matching normative element definition."
            )

    return errors


def check_operations(norm_obj: ObjectDef, iana_obj: ObjectDef) -> list[str]:
    errors: list[str] = []
    prefix = f"object '{norm_obj.identifier}'"

    iana_ops  = {op.identifier: op for op in iana_obj.operations}
    # Operations with identifier="" are excluded from id-keyed lookup but
    # reported individually below.
    norm_ops  = {op.identifier: op for op in norm_obj.operations if op.identifier}

    for nop in norm_obj.operations:
        # Report operations that are missing a * Identifier: bullet
        if not nop.identifier:
            errors.append(
                f"[OP MISSING IDENTIFIER] {prefix}: "
                f"operation '{nop.name}' at normative line {nop.line} "
                f"has no '* Identifier:' bullet."
            )
            continue
        if nop.identifier not in iana_ops:
            errors.append(
                f"[OP MISSING IN IANA] {prefix}: "
                f"operation '{nop.identifier}' ('{nop.name}') at normative line {nop.line} "
                f"has no entry in the IANA Operations table."
            )
            continue

        iop = iana_ops[nop.identifier]
        # Name comparison: IANA names often differ stylistically, so only warn
        # when they differ significantly (skip for now — names are informal)

        if nop.description and not iop.description:
            errors.append(
                f"[OP DESC EMPTY] {prefix}, operation '{nop.identifier}' "
                f"(IANA line {iop.line}): IANA Description field is empty."
            )

        # Check parameters
        iana_params = {p.identifier: p for p in iop.params}
        norm_params = {p.identifier: p for p in nop.params}

        for np in nop.params:
            if np.identifier not in iana_params:
                errors.append(
                    f"[PARAM MISSING IN IANA] {prefix}, operation '{nop.identifier}': "
                    f"parameter '{np.identifier}' ('{np.name}') at normative line {np.line} "
                    f"has no row in the IANA Parameters table."
                )
                continue
            ip = iana_params[np.identifier]
            if _norm(np.name) != _norm(ip.name):
                errors.append(
                    f"[PARAM NAME MISMATCH] {prefix}, op '{nop.identifier}', "
                    f"param '{np.identifier}':\n"
                    f"    normative (line {np.line}):  name='{np.name}'\n"
                    f"    IANA     (line {ip.line}): name='{ip.name}'"
                )
            if _norm(np.cardinality) != _norm(ip.cardinality):
                errors.append(
                    f"[PARAM CARD MISMATCH] {prefix}, op '{nop.identifier}', "
                    f"param '{np.identifier}':\n"
                    f"    normative (line {np.line}):  cardinality='{np.cardinality}'\n"
                    f"    IANA     (line {ip.line}): cardinality='{ip.cardinality}'"
                )
            if _norm_type(np.data_type) != _norm_type(ip.data_type):
                errors.append(
                    f"[PARAM TYPE MISMATCH] {prefix}, op '{nop.identifier}', "
                    f"param '{np.identifier}':\n"
                    f"    normative (line {np.line}):  data_type='{np.data_type}'\n"
                    f"    IANA     (line {ip.line}): data_type='{ip.data_type}'"
                )
            if np.description and not ip.description:
                errors.append(
                    f"[PARAM DESC EMPTY] {prefix}, op '{nop.identifier}', "
                    f"param '{np.identifier}' (IANA line {ip.line}): "
                    f"IANA description cell is empty."
                )

        for ip in iop.params:
            if ip.identifier not in norm_params:
                errors.append(
                    f"[PARAM MISSING IN NORMATIVE] {prefix}, operation '{iop.identifier}': "
                    f"IANA parameter '{ip.identifier}' ('{ip.name}') at line {ip.line} "
                    f"has no matching normative parameter definition."
                )

    for iop in iana_obj.operations:
        if iop.identifier not in norm_ops:
            errors.append(
                f"[OP MISSING IN NORMATIVE] {prefix}: "
                f"IANA operation '{iop.identifier}' ('{iop.name}') at line {iop.line} "
                f"has no matching normative operation definition."
            )

    return errors


# ---------------------------------------------------------------------------
# IANA table generators
# ---------------------------------------------------------------------------

def generate_iana_table(obj: ObjectDef) -> str:
    """
    Generate the full IANA registry block for a normative object that is
    missing from the IANA section.

    Resource objects use shorter column headers (Identifier / Name);
    Component and Process objects use the longer form (Element Identifier /
    Element Name).  All blocks follow the blank-line-separated field format
    used in the existing IANA section.
    """
    is_resource = obj.obj_type == "Resource"
    id_hdr   = "Identifier"   if is_resource else "Element Identifier"
    name_hdr = "Name"         if is_resource else "Element Name"

    out: list[str] = []
    out.append(f"Object: {obj.identifier}")
    out.append("")
    out.append(f"Object Name: {obj.name}")
    out.append("")
    out.append(f"Object Type: {obj.obj_type or 'TBD'}")
    out.append("")
    out.append(f"Description: {obj.description or 'TBD'}")
    out.append("")
    out.append("Reference: [This-ID]")
    out.append("")
    out.append("Data Elements")

    if obj.elements:
        desc_w = max(len("Description"), max(len(e.description) for e in obj.elements))
        id_w   = max(len(id_hdr),   max(len(e.identifier)  for e in obj.elements))
        name_w = max(len(name_hdr), max(len(e.name)        for e in obj.elements))
        card_w = max(len("Card."),  max(len(e.cardinality) for e in obj.elements))
        mut_w  = max(len("Mutability"), max(len(e.mutability) for e in obj.elements))
        type_w = max(len("Data Type"),  max(len(e.data_type)  for e in obj.elements))

        def _row(id_: str, name: str, card: str, mut: str, dtype: str,
                 desc: str = "") -> str:
            return (f"| {id_:<{id_w}} | {name:<{name_w}} | {card:<{card_w}}"
                    f" | {mut:<{mut_w}} | {dtype:<{type_w}} | {desc:<{desc_w}} |")

        sep = (f"| {'-' * id_w} | {'-' * name_w} | {'-' * card_w}"
               f" | {'-' * mut_w} | {'-' * type_w} | {'-' * desc_w} |")

        out.append(_row(id_hdr, name_hdr, "Card.", "Mutability", "Data Type",
                        "Description"))
        out.append(sep)
        for e in obj.elements:
            out.append(_row(e.identifier, e.name, e.cardinality,
                            e.mutability, e.data_type, e.description))
    else:
        out.append(f"| {id_hdr} | {name_hdr} | Card. | Mutability | Data Type | Description |")
        out.append(f"| {'-' * len(id_hdr)} | {'-' * len(name_hdr)} | ----- | ---------- | --------- | ----------- |")

    # Operations section
    if obj.operations:
        out.append("")
        out.append("Operations")
        for op in obj.operations:
            out.append("")
            out.append(f"Operation: {op.name}")
            out.append("")
            out.append(f"Operation Identifier: {op.identifier}")
            out.append("")
            out.append(f"Description: {op.description}")
            out.append("")
            if op.params:
                pdesc_w = max(len("Description"), max(len(p.description) for p in op.params))
                pid_w  = max(len("Identifier"), max(len(p.identifier) for p in op.params))
                pnm_w  = max(len("Name"),       max(len(p.name)       for p in op.params))
                pcd_w  = max(len("Card."),      max(len(p.cardinality) for p in op.params))
                pty_w  = max(len("Data Type"),  max(len(p.data_type)   for p in op.params))

                def _prow(id_: str, name: str, card: str, dtype: str,
                          desc: str = "") -> str:
                    return (f"| {id_:<{pid_w}} | {name:<{pnm_w}} | {card:<{pcd_w}}"
                            f" | {dtype:<{pty_w}} | {desc:<{pdesc_w}} |")

                psep = (f"| {'-' * pid_w} | {'-' * pnm_w} | {'-' * pcd_w}"
                        f" | {'-' * pty_w} | {'-' * pdesc_w} |")

                out.append("Parameters")
                out.append(_prow("Identifier", "Name", "Card.", "Data Type", "Description"))
                out.append(psep)
                for p in op.params:
                    out.append(_prow(p.identifier, p.name, p.cardinality,
                                     p.data_type, p.description))
            else:
                out.append("Parameters: (None)")

    return "\n".join(out)


def generate_iana_row(elem: ElementDef) -> str:
    """
    Generate a single IANA table row for a normative element that is missing
    from an existing IANA table.
    """
    return (f"| {elem.identifier} | {elem.name} | {elem.cardinality}"
            f" | {elem.mutability} | {elem.data_type} | {elem.description} |")


def generate_iana_op_block(op: OperationDef) -> str:
    """
    Generate the IANA operation block for a normative operation that is missing
    from the IANA Operations section.
    """
    out: list[str] = []
    out.append(f"Operation: {op.name}")
    out.append("")
    out.append(f"Operation Identifier: {op.identifier}")
    out.append("")
    out.append(f"Description: {op.description}")
    out.append("")
    if op.params:
        pdesc_w = max(len("Description"), max(len(p.description) for p in op.params))
        pid_w = max(len("Identifier"), max(len(p.identifier) for p in op.params))
        pnm_w = max(len("Name"),       max(len(p.name)       for p in op.params))
        pcd_w = max(len("Card."),      max(len(p.cardinality) for p in op.params))
        pty_w = max(len("Data Type"),  max(len(p.data_type)   for p in op.params))

        def _prow(id_: str, name: str, card: str, dtype: str,
                  desc: str = "") -> str:
            return (f"| {id_:<{pid_w}} | {name:<{pnm_w}} | {card:<{pcd_w}}"
                    f" | {dtype:<{pty_w}} | {desc:<{pdesc_w}} |")

        psep = (f"| {'-' * pid_w} | {'-' * pnm_w} | {'-' * pcd_w}"
                f" | {'-' * pty_w} | {'-' * pdesc_w} |")

        out.append("Parameters")
        out.append(_prow("Identifier", "Name", "Card.", "Data Type", "Description"))
        out.append(psep)
        for p in op.params:
            out.append(_prow(p.identifier, p.name, p.cardinality,
                             p.data_type, p.description))
    else:
        out.append("Parameters: (None)")
    return "\n".join(out)


def generate_iana_param_row(param: ParamDef) -> str:
    """
    Generate a single IANA Parameters table row for a missing parameter.
    """
    return (f"| {param.identifier} | {param.name} | {param.cardinality}"
            f" | {param.data_type} | {param.description} |")


# ---------------------------------------------------------------------------
# YAML export
# ---------------------------------------------------------------------------

def _param_to_dict(p: ParamDef) -> dict:
    return {
        "identifier": p.identifier,
        "name": p.name,
        "cardinality": p.cardinality,
        "data_type": p.data_type,
        "description": p.description,
        "constraints": p.constraints,
        "notes": list(p.notes),
    }


def _element_to_dict(e: ElementDef) -> dict:
    return {
        "identifier": e.identifier,
        "name": e.name,
        "cardinality": e.cardinality,
        "mutability": e.mutability,
        "data_type": e.data_type,
        "description": e.description,
        "constraints": e.constraints,
        "notes": list(e.notes),
    }


def _operation_to_dict(op: OperationDef) -> dict:
    return {
        "identifier": op.identifier,
        "name": op.name,
        "description": op.description,
        "authorisation": list(op.authorisation),
        "input": op.input,
        "output": op.output,
        "notes": list(op.notes),
        "params": [_param_to_dict(p) for p in op.params],
    }


def _subsection_to_dict(sub: SubsectionDef) -> dict:
    return {
        "heading": sub.heading,
        "anchor": sub.anchor,
        "notes": list(sub.notes),
    }


def _supported_process_to_dict(p: SupportedProcessDef) -> dict:
    return {
        "name": p.name,
        "constraints": list(p.constraints),
        "notes": list(p.notes),
    }


def _object_to_dict(obj: ObjectDef) -> dict:
    return {
        "identifier": obj.identifier,
        "name": obj.name,
        "object_type": obj.obj_type,
        "extends": obj.extends,
        "description": obj.description,
        "preamble": list(obj.preamble),
        "elements": [_element_to_dict(e) for e in obj.elements],
        "operations": [_operation_to_dict(op) for op in obj.operations],
        "subsections": [_subsection_to_dict(s) for s in obj.subsections],
        "supported_processes": [_supported_process_to_dict(p) for p in obj.supported_processes],
    }


def build_yaml_model(normative: list[ObjectDef],
                      section_notes: dict[str, list[str]]) -> dict:
    """
    Assemble the plain-dict YAML model from parsed normative objects and
    section-level intro prose. Keys: section_notes, components, processes,
    resources — see object_model_template.yaml for the full field reference.
    """
    model: dict = {
        "section_notes": dict(section_notes),
        "components": [],
        "processes": [],
        "resources": [],
    }
    for obj in normative:
        key = OBJ_TYPE_TO_YAML_KEY.get(obj.obj_type)
        if key is None:
            continue
        model[key].append(_object_to_dict(obj))
    return model


def render_yaml(normative: list[ObjectDef],
                 section_notes: dict[str, list[str]]) -> str:
    """
    Render the full structured object model as a YAML document, prefixed
    verbatim with the header comment block from YAML_TEMPLATE_FILE.
    """
    if yaml is None:
        print("ERROR: PyYAML is required for --yaml-out (pip install pyyaml).",
              file=sys.stderr)
        sys.exit(1)

    header_lines: list[str] = []
    if YAML_TEMPLATE_FILE.exists():
        for line in YAML_TEMPLATE_FILE.read_text().splitlines():
            if line.startswith("#") or line.strip() == "":
                header_lines.append(line)
            else:
                # Reached the template's placeholder data (section_notes: {}, …)
                break

    model = build_yaml_model(normative, section_notes)
    body = yaml.safe_dump(model, sort_keys=False, allow_unicode=True,
                           width=100, default_flow_style=False)

    return "\n".join(header_lines) + "\n\n" + body


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_document(
    path: Path,
) -> tuple[list[ObjectDef], list[ObjectDef], dict[str, list[str]]]:
    """
    Shared entry point for both the IANA consistency check and the YAML
    export: parses the draft once and returns (normative, iana, section_notes).
    """
    lines = path.read_text().splitlines()

    iana_start = None
    for i, line in enumerate(lines):
        if line.strip() == IANA_SECTION_MARKER:
            iana_start = i
            break
    if iana_start is None:
        print(f"ERROR: '{IANA_SECTION_MARKER}' not found.", file=sys.stderr)
        sys.exit(1)

    normative     = parse_normative_objects(lines, iana_start)
    iana          = parse_iana_objects(lines, iana_start)
    section_notes = parse_section_notes(lines, iana_start)
    return normative, iana, section_notes


def run_consistency_check(normative: list[ObjectDef], iana: list[ObjectDef],
                           generate: bool) -> tuple[list[str], bool]:
    """
    Run the full consistency check and return (report_lines, ok).

    ok is True iff no consistency errors were found. report_lines is the
    complete human-readable report (object/element inventories, errors, and
    optionally generated IANA markup), suitable for printing to stdout or
    writing to a file.
    """
    out: list[str] = []

    out.append(f"Normative objects found ({len(normative)}):")
    for o in normative:
        out.append(f"  line {o.line:4d}  id={o.identifier!r:30s}  name={o.name!r}"
                    f"  elements={len(o.elements)}  ops={len(o.operations)}")

    out.append(f"\nIANA objects found ({len(iana)}):")
    for o in iana:
        out.append(f"  line {o.line:4d}  id={o.identifier!r:30s}  name={o.name!r}"
                    f"  elements={len(o.elements)}  ops={len(o.operations)}")

    errors = check_objects(normative, iana)

    norm_names = {_norm(o.name) for o in normative}
    for norm_obj in normative:
        errors.extend(check_supported_processes(norm_obj, norm_names))

    iana_by_id  = {o.identifier: o for o in iana}
    elem_errors: list[tuple[ObjectDef, list[str]]] = []
    op_errors:   list[tuple[ObjectDef, list[str]]] = []
    _empty_obj = ObjectDef(name="", identifier="", source="iana", line=0)
    for norm_obj in normative:
        iana_obj = iana_by_id.get(norm_obj.identifier)
        if iana_obj is not None:
            obj_elem_errors = check_elements(norm_obj, iana_obj)
            if obj_elem_errors:
                elem_errors.append((norm_obj, obj_elem_errors))
            errors.extend(obj_elem_errors)

        # check_operations is always run: against the real IANA object when it
        # exists, or against an empty stub when the object is missing from IANA
        # (so that [OP MISSING IDENTIFIER] errors are always surfaced).
        obj_op_errors = check_operations(norm_obj, iana_obj if iana_obj is not None else _empty_obj)
        if obj_op_errors:
            op_errors.append((norm_obj, obj_op_errors))
        errors.extend(obj_op_errors)

    out.append("")
    if errors:
        out.append(f"CONSISTENCY ERRORS ({len(errors)}):")
        for e in errors:
            out.append(f"  - {e}")

        if generate:
            out.append("")
            out.append("=" * 72)
            out.append("GENERATED IANA MARKUP FOR MISSING ENTRIES")
            out.append("=" * 72)

            # Missing objects → full table block (incl. operations)
            iana_names = {x.name for x in iana}
            missing_obj_ids = {
                o.identifier for o in normative
                if o.identifier not in iana_by_id and o.name not in iana_names
            }
            for obj in normative:
                if obj.identifier in missing_obj_ids:
                    out.append("")
                    out.append(f"--- [MISSING IN IANA] object '{obj.identifier}' ---")
                    out.append("")
                    out.append(generate_iana_table(obj))

            # Missing elements → single row per element
            for norm_obj, obj_errors in elem_errors:
                iana_obj = iana_by_id[norm_obj.identifier]
                iana_elem_ids = {e.identifier for e in iana_obj.elements}
                missing_elems = [
                    e for e in norm_obj.elements
                    if e.identifier not in iana_elem_ids
                ]
                if missing_elems:
                    out.append("")
                    out.append(f"--- [ELEM MISSING IN IANA] object '{norm_obj.identifier}' ---")
                    for elem in missing_elems:
                        out.append(generate_iana_row(elem))

            # Elements with empty IANA description → corrected row
            for norm_obj, obj_errors in elem_errors:
                iana_obj = iana_by_id.get(norm_obj.identifier)
                if iana_obj is None:
                    continue
                iana_elem_by_id = {e.identifier: e for e in iana_obj.elements}
                empty_desc_elems = [
                    ne for ne in norm_obj.elements
                    if ne.description
                    and ne.identifier in iana_elem_by_id
                    and not iana_elem_by_id[ne.identifier].description
                ]
                if empty_desc_elems:
                    out.append("")
                    out.append(f"--- [ELEM DESC EMPTY] object '{norm_obj.identifier}' ---")
                    for elem in empty_desc_elems:
                        out.append(generate_iana_row(elem))

            # Missing operations → full operation block
            for norm_obj, obj_errors in op_errors:
                iana_obj = iana_by_id.get(norm_obj.identifier)
                iana_op_ids = {op.identifier for op in iana_obj.operations} if iana_obj else set()
                missing_ops = [
                    op for op in norm_obj.operations
                    if op.identifier and op.identifier not in iana_op_ids
                ]
                if missing_ops:
                    out.append("")
                    out.append(f"--- [OP MISSING IN IANA] object '{norm_obj.identifier}' ---")
                    for op in missing_ops:
                        out.append("")
                        out.append(generate_iana_op_block(op))

            # Operations with empty IANA description → corrected block
            for norm_obj, obj_errors in op_errors:
                iana_obj = iana_by_id.get(norm_obj.identifier)
                if iana_obj is None:
                    continue
                iana_op_by_id = {op.identifier: op for op in iana_obj.operations}
                empty_desc_ops = [
                    nop for nop in norm_obj.operations
                    if nop.identifier and nop.description
                    and nop.identifier in iana_op_by_id
                    and not iana_op_by_id[nop.identifier].description
                ]
                if empty_desc_ops:
                    out.append("")
                    out.append(f"--- [OP DESC EMPTY] object '{norm_obj.identifier}' ---")
                    for op in empty_desc_ops:
                        out.append("")
                        out.append(generate_iana_op_block(op))

            # Missing operation parameters → single row per parameter
            for norm_obj, obj_errors in op_errors:
                iana_obj = iana_by_id.get(norm_obj.identifier)
                iana_ops = {op.identifier: op for op in iana_obj.operations} if iana_obj else {}
                for nop in norm_obj.operations:
                    iop = iana_ops.get(nop.identifier)
                    if iop is None:
                        continue
                    iana_param_ids = {p.identifier for p in iop.params}
                    missing_params = [
                        p for p in nop.params
                        if p.identifier not in iana_param_ids
                    ]
                    if missing_params:
                        out.append("")
                        out.append(f"--- [PARAM MISSING IN IANA] object '{norm_obj.identifier}', "
                                    f"operation '{nop.identifier}' ---")
                        for param in missing_params:
                            out.append(generate_iana_param_row(param))

            # Parameters with empty IANA description → corrected row
            for norm_obj, obj_errors in op_errors:
                iana_obj = iana_by_id.get(norm_obj.identifier)
                if iana_obj is None:
                    continue
                iana_op_by_id = {op.identifier: op for op in iana_obj.operations}
                for nop in norm_obj.operations:
                    iop = iana_op_by_id.get(nop.identifier)
                    if iop is None:
                        continue
                    iana_param_by_id = {p.identifier: p for p in iop.params}
                    empty_desc_params = [
                        np for np in nop.params
                        if np.description
                        and np.identifier in iana_param_by_id
                        and not iana_param_by_id[np.identifier].description
                    ]
                    if empty_desc_params:
                        out.append("")
                        out.append(f"--- [PARAM DESC EMPTY] object '{norm_obj.identifier}', "
                                    f"operation '{nop.identifier}' ---")
                        for param in empty_desc_params:
                            out.append(generate_iana_param_row(param))

        return out, False

    out.append("OK: all normative object and element definitions match their IANA entries.")
    return out, True


# ---------------------------------------------------------------------------
# Self-tests (run with --self-test; not a throwaway script, kept in-file)
# ---------------------------------------------------------------------------

def _test_element_constraints_and_notes_nested():
    """Constraints bullet and orphan prose are captured for nested (Component) elements."""
    lines = [
        "* Name: Widget Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "* Data Elements:",
        "  * Size",
        "    * Identifier: size",
        "    * Cardinality: 1",
        "    * Mutability: read-write",
        "    * Data Type: Integer",
        "    * Description: The size.",
        "    * Constraints: MUST be positive.",
        "    Some extra explanatory sentence about size.",
        "  * Colour",
        "    * Identifier: colour",
        "    * Cardinality: 0-1",
        "    * Mutability: read-write",
        "    * Data Type: String",
    ]
    elements = _parse_normative_elements_nested(lines, 0, len(lines))
    assert len(elements) == 2, elements
    size = elements[0]
    assert size.identifier == "size"
    assert size.constraints == ["MUST be positive."], size.constraints
    assert size.notes == ["Some extra explanatory sentence about size."], size.notes
    colour = elements[1]
    assert colour.constraints == []
    assert colour.notes == []


def _test_element_attrs_tolerate_blank_lines_and_asides_between_bullets():
    """
    Blank lines and "A>" editorial asides are a valid pattern anywhere in
    the structure, including BETWEEN an element's own attribute bullets
    (not just before/after a whole block) - both must be skipped without
    breaking attribute collection or losing/misattributing later attributes.
    """
    lines = [
        "* Name: Widget Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "* Data Elements:",
        "  * Size",
        "    * Identifier: size",
        "",
        "A> TODO: reconsider this cardinality",
        "",
        "    * Cardinality: 1",
        "    A> TBD: mutability still under discussion",
        "    * Mutability: read-write",
        "",
        "    * Data Type: Integer",
        "    * Description: The size.",
        "    * Constraints: MUST be positive.",
    ]
    elements = _parse_normative_elements_nested(lines, 0, len(lines))
    assert len(elements) == 1, elements
    size = elements[0]
    assert size.identifier == "size"
    assert size.cardinality == "1", size.cardinality
    assert size.mutability == "read-write", size.mutability
    assert size.data_type == "Integer", size.data_type
    assert size.description == "The size."
    assert size.constraints == ["MUST be positive."], size.constraints
    # Asides must never leak into notes (or anywhere else).
    assert not any("TODO" in n or "TBD" in n for n in size.notes), size.notes


def _test_element_constraints_flat():
    """Constraints bullet is captured for flat (Data Object) elements."""
    lines = [
        "* Name: domainName Data Object",
        "* Identifier: domainName",
        "## Data Elements",
        "* Name",
        "  * Identifier: name",
        "  * Cardinality: 1",
        "  * Mutability: create-only",
        "  * Data Type: String",
        "  * Description: The domain name.",
        "  * Constraints: MUST be a valid FQDN.",
    ]
    elements = _parse_normative_elements_flat(lines, 3, len(lines))
    assert len(elements) == 1, elements
    assert elements[0].constraints == ["MUST be a valid FQDN."], elements[0].constraints


def _test_element_constraints_none_becomes_empty_list():
    lines = [
        "* Name: Widget Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "* Data Elements:",
        "  * Size",
        "    * Identifier: size",
        "    * Cardinality: 1",
        "    * Mutability: read-write",
        "    * Data Type: Integer",
        "    * Description: The size.",
        "    * Constraints: (None)",
    ]
    elements = _parse_normative_elements_nested(lines, 0, len(lines))
    assert elements[0].constraints == []


def _test_element_constraints_nested_bullet_list_reads_indent_from_source():
    """
    Regression test for the Status Object 'label' element bug: a blank
    '* Constraints:' value followed by a deeper-indented bullet list must be
    parsed as a multi-item constraints list, not misclassified as orphan
    prose. The sub-list's indentation is read from the source (here 6 spaces,
    i.e. attr_indent(4) + 2) rather than assumed as a fixed offset -- this
    test uses that same +2 delta since that's what the real draft uses, but
    the parser itself must not hardcode it (see _collect_constraints_list).
    """
    lines = [
        "* Name: Status Object",
        "* Identifier: status",
        "* Description: Represents a status.",
        "* Data Elements:",
        "  * Label",
        "    * Identifier: label",
        "    * Cardinality: 1",
        "    * Mutability: create-only",
        "    * Data Type: String",
        "    * Description: machine-readable enum label of a status",
        "    * Constraints:      ",
        "      * Exact list of allowed status labels depends on the provisioning object type.",
        "      * The status labels MUST use camel case notation.",
        "      * Statuses MAY be of three categories:",
        "        1. those explicitly set by a server.",
        "        2. those explicitly set by a client.",
        "  * Reason",
        "    * Identifier: reason",
        "    * Cardinality: 0-1",
        "    * Mutability: read-only",
        "    * Data Type: String",
    ]
    elements = _parse_normative_elements_nested(lines, 0, len(lines))
    assert len(elements) == 2, elements
    label = elements[0]
    assert label.identifier == "label"
    assert label.constraints == [
        "Exact list of allowed status labels depends on the provisioning object type.",
        "The status labels MUST use camel case notation.",
        "Statuses MAY be of three categories:\n"
        "1. those explicitly set by a server.\n"
        "2. those explicitly set by a client.",
    ], label.constraints
    # The nested bullet list must not also leak into notes.
    assert label.notes == [], label.notes


def _test_header_end_stops_at_data_elements_bullet():
    """
    Regression test for the Status Object preamble bug: header_end must stop
    at "* Data Elements:" (or "* Operations:"/"* Object Type:"), not fall
    through to an arbitrary line-count cap that can land deep inside a child
    element's block and leak unrelated content into the object's preamble.
    """
    lines = [
        "# Component Objects",
        "",
        "## Status Object",
        "",
        "* Name: Status Object",
        "* Identifier: status",
        "* Description: Represents a status.",
        "* Data Elements:",
        "  * Label",
        "    * Identifier: label",
        "    * Cardinality: 1",
        "    * Mutability: create-only",
        "    * Data Type: String",
        "    * Description: machine-readable enum label of a status",
        "    * Constraints:      ",
        "      * Statuses MAY be of three categories:",
        "        1. those explicitly set by a server.",
        "        2. those explicitly set by a client.",
        "        3. those neither.",
        "",
        "# IANA Considerations",
    ]
    iana_start = len(lines) - 1
    objects = parse_normative_objects(lines, iana_start)
    status = [o for o in objects if o.identifier == "status"]
    assert len(status) == 1, objects
    assert status[0].preamble == [], status[0].preamble


def _test_subsection_parsing_preserves_order_and_folds_bullets():
    """
    Regression test for the dnsRecord 'RDATA Structures in EPP Profile' gap:
    a non-Operations H3 nested inside a Component Object must be captured as
    a subsection, with prose and bullet-list entries in document order.
    """
    lines = [
        "* Name: Widget Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "* Data Elements:",
        "  * Size",
        "    * Identifier: size",
        "    * Cardinality: 1",
        "    * Mutability: read-write",
        "    * Data Type: Integer",
        "",
        "### Widget Sub Info {#widget-sub-info}",
        "",
        "Intro sentence.",
        "",
        "* First bullet item.",
        "* Second bullet item.",
        "",
        "Trailing sentence.",
    ]
    subsections = _parse_object_subsections(
        lines, 0, len(lines), obj_heading_level=2, obj_type="Component", obj_id="widget")
    assert len(subsections) == 1, subsections
    sub = subsections[0]
    assert sub.heading == "Widget Sub Info"
    assert sub.anchor == "{#widget-sub-info}"
    assert sub.notes == [
        "Intro sentence.",
        "First bullet item.",
        "Second bullet item.",
        "Trailing sentence.",
    ], sub.notes


def _test_param_attrs_tolerate_blank_lines_and_asides_between_bullets():
    """
    Blank lines and "A>" asides are a valid pattern between a transient
    operation parameter's own attribute bullets too, not just element
    attributes - must not break or misattribute parameter parsing.
    """
    lines = [
        "* Hosts Filter",
        "  * Identifier: hostsFilter",
        "",
        "A> TODO: reconsider default value",
        "",
        "  * Cardinality: 0-1",
        "  A> TBD: data type still under review",
        "  * Data Type: String",
        "  * Description: Controls which host information is returned.",
    ]
    params = _parse_normative_params_from_bullets(lines, 0, len(lines))
    assert len(params) == 1, params
    p = params[0]
    assert p.identifier == "hostsFilter"
    assert p.cardinality == "0-1", p.cardinality
    assert p.data_type == "String", p.data_type
    assert p.description == "Controls which host information is returned."
    assert not any("TODO" in n or "TBD" in n for n in p.notes), p.notes


def _test_operation_description_authorisation_input_output():
    """
    Regression test based on the Transfer Process Object's Read/Delete/
    Approve operations: description is plain prose after "* Identifier:"
    (never a "* Description:" bullet), Authorisation is a nested bullet
    list, Input/Output are single-line values.
    """
    lines = [
        "#### Read (Transfer Query) {#transfer-read}",
        "",
        "* Identifier: transferRead",
        "",
        "The Read operation allows a client to determine the real-time status "
        "of a pending or recently completed transfer request.",
        "",
        "* Authorisation:",
        "  * This operation MUST be accessible to both the sponsoring client and the gaining client.",
        "  * Server policy determines whether other clients may query transfer status and what information is returned.",
        "",
        "* Input: None",
        "* Output: Transfer Process Object",
        "",
        "#### Delete (Transfer Cancel) {#transfer-delete}",
    ]
    heading_re = re.compile(r"^#### (.+?)(?:\s*\{[^}]*\})?\s*$")
    ops = _parse_normative_operations_nested(lines, 0, len(lines), heading_re)
    # A second OperationDef is expected for the trailing "#### Delete (...)"
    # heading in this snippet, which has no body (no "* Identifier:") since
    # the snippet is truncated there - correctly reported with identifier=""
    # (see [OP MISSING IDENTIFIER]), not a bug in the field parsing under test.
    op = ops[0]
    assert op.identifier == "transferRead"
    assert op.description == (
        "The Read operation allows a client to determine the real-time status "
        "of a pending or recently completed transfer request."
    ), op.description
    assert op.authorisation == [
        "This operation MUST be accessible to both the sponsoring client and the gaining client.",
        "Server policy determines whether other clients may query transfer status and what information is returned.",
    ], op.authorisation
    assert op.input == "None"
    assert op.output == "Transfer Process Object"
    assert op.notes == []


def _test_operation_description_multi_paragraph_and_trailing_notes():
    """
    Regression test based on domainName's Create Operation: a second prose
    paragraph AFTER the Authorisation bullet stays in notes, not description
    - only prose BEFORE the first recognised field bullet is description.
    """
    lines = [
        "### Create Operation",
        "",
        "* Identifier: create",
        "",
        "The Create operation allows a client to provision a new resource.",
        "",
        "* Authorisation:",
        "  * Generally each client is authorised to create new objects.",
        "",
        "The Create operation implicitly initiates a further process.",
        "",
        "### Read Operation",
    ]
    heading_re = re.compile(r"^### (?!.*\bOperations\s*$)(.+?)(?:\s*\{[^}]*\})?\s*$")
    ops = _parse_normative_operations_nested(lines, 0, len(lines), heading_re)
    # A second (empty) OperationDef is expected for the trailing "### Read
    # Operation" heading, truncated with no body in this snippet.
    op = ops[0]
    assert op.description == "The Create operation allows a client to provision a new resource."
    assert op.authorisation == ["Generally each client is authorised to create new objects."]
    assert op.notes == ["The Create operation implicitly initiates a further process."], op.notes


def _test_subsection_operations_heading_excluded_for_process():
    """Process Objects DO have Operations; it must be excluded from generic subsections."""
    lines = [
        "* Name: Widget Process Object",
        "* Identifier: widgetProcess",
        "* Description: A widget process.",
        "* Data Elements:",
        "  * Size",
        "    * Identifier: size",
        "    * Cardinality: 1",
        "    * Mutability: read-write",
        "    * Data Type: Integer",
        "",
        "### Operations",
        "",
        "#### Create {#create}",
    ]
    subsections = _parse_object_subsections(
        lines, 0, len(lines), obj_heading_level=2, obj_type="Process", obj_id="widgetProcess")
    assert subsections == []


def _test_subsection_component_never_has_operations():
    """
    Regression test for rule (b): Component Objects never define operations.
    An 'Operations' heading under one is captured as a generic subsection
    (not parsed as operations) and a warning is emitted, rather than being
    silently treated the same as a Process object's real Operations section.
    """
    lines = [
        "* Name: Widget Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "* Data Elements:",
        "  * Size",
        "    * Identifier: size",
        "    * Cardinality: 1",
        "    * Mutability: read-write",
        "    * Data Type: Integer",
        "",
        "### Operations",
        "",
        "#### Create {#create}",
        "",
        "* Identifier: create",
    ]
    before = len(PROSE_WARNINGS)
    subsections = _parse_object_subsections(
        lines, 0, len(lines), obj_heading_level=2, obj_type="Component", obj_id="widget")
    assert len(subsections) == 1, subsections
    assert subsections[0].heading == "Operations"
    assert len(PROSE_WARNINGS) == before + 1
    assert "widget" in PROSE_WARNINGS[-1] and "Component" in PROSE_WARNINGS[-1]

    operations = _parse_normative_operations_for_object(
        lines, 0, len(lines), obj_heading_level=2, obj_type="Component", obj_id="widget")
    assert operations == []


def _test_subsection_flat_data_object_operations_excluded():
    """
    Regression test: for a flat (Data Object) layout, singular H3 operation
    headings like "### Create Operation" do not themselves contain the word
    "Operations" and must not be misclassified as generic subsections -
    the whole "## Operations" H2 span must be excluded by heading boundary,
    not by matching "Operations" in the heading text.
    """
    lines = [
        "* Name: Widget Data Object",
        "* Identifier: widget",
        "## Data Elements",
        "* Size",
        "  * Identifier: size",
        "  * Cardinality: 1",
        "  * Mutability: read-write",
        "  * Data Type: Integer",
        "## Operations",
        "### Create Operation",
        "* Identifier: create",
        "The Create operation does something.",
        "### Transfer Operations",
        "#### Transfer Create Operation",
        "* Identifier: transferCreate",
    ]
    subsections = _parse_object_subsections(
        lines, 0, len(lines), obj_heading_level=1, obj_type="Resource", obj_id="widget")
    assert subsections == [], subsections


def _test_subsection_object_description_heading_excluded():
    """
    Regression test: a flat Data Object's "## Object Description" heading
    (holding its own Name/Identifier/Description header bullets) must not be
    captured as a generic subsection.
    """
    lines = [
        "## Object Description",
        "* Name: Widget Data Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "## Data Elements",
        "* Size",
        "  * Identifier: size",
        "  * Cardinality: 1",
        "  * Mutability: read-write",
        "  * Data Type: Integer",
    ]
    subsections = _parse_object_subsections(
        lines, 0, len(lines), obj_heading_level=1, obj_type="Resource", obj_id="widget")
    assert subsections == [], subsections


def _test_subsection_processes_heading_excluded():
    """
    Regression test: a Data Object's "## Processes" sub-section (containing
    embedded Process Objects like "### Domain Create Process Object", each
    already parsed as its own top-level ObjectDef) must not be re-captured
    as generic subsection prose, which would duplicate and garble its content.
    """
    lines = [
        "* Name: Widget Data Object",
        "* Identifier: widget",
        "## Data Elements",
        "* Size",
        "  * Identifier: size",
        "  * Cardinality: 1",
        "  * Mutability: read-write",
        "  * Data Type: Integer",
        "## Processes",
        "### Widget Create Process Object {#widget-create-process}",
        "* Name: Widget Create Process Object",
        "* Identifier: widgetCreateProcess",
        "* Description: Created implicitly.",
        "* Data Elements:",
        "  * Foo",
        "    * Identifier: foo",
        "    * Cardinality: 1",
        "    * Mutability: read-only",
        "    * Data Type: String",
    ]
    subsections = _parse_object_subsections(
        lines, 0, len(lines), obj_heading_level=1, obj_type="Resource", obj_id="widget")
    assert subsections == [], subsections


def _test_parse_supported_processes_basic():
    """
    The bullet text alone is the process reference; only Constraints may
    follow as an attribute.
    """
    lines = [
        "* Renew Process",
        "* Restore Process",
        "  * Constraints: OPTIONAL; available only when the RGP feature is supported.",
    ]
    processes = _parse_supported_processes(lines, 0, len(lines))
    assert [p.name for p in processes] == [
        "Renew Process", "Restore Process"], processes
    assert processes[0].constraints == []
    assert processes[1].constraints == [
        "OPTIONAL; available only when the RGP feature is supported."], processes[1].constraints


def _test_resolve_process_name_accepts_short_form():
    """
    A "## Processes" bullet may use the short form ("Renew Process") for an
    object named "Renew Process Object"; an exact Name also resolves.
    """
    norm_names = {"Renew Process Object", "Transfer Process Object"}
    assert resolve_process_name("Renew Process", norm_names) == "Renew Process Object"
    assert resolve_process_name("Transfer Process Object", norm_names) == "Transfer Process Object"
    assert resolve_process_name("Bogus Process", norm_names) is None


def _test_parse_supported_processes_stops_at_extended_process_heading():
    """
    A Data Object's "## Processes" section holds an intro line, then the
    inherited-process bullet list, then H3 definitions of the processes it
    EXTENDS. Only the bullet list before the first H3 is a
    SupportedProcessDef - the H3 blocks are separate ObjectDefs and their
    own "* Identifier:" bullets must not be swept into the list.
    """
    lines = [
        "* Name: Widget Data Object",
        "* Identifier: widget",
        "## Data Elements",
        "* Size",
        "  * Identifier: size",
        "  * Cardinality: 1",
        "  * Mutability: read-write",
        "  * Data Type: Integer",
        "## Operations",
        "### Create Operation",
        "* Identifier: create",
        "## Processes",
        "",
        "The Widget Data Object supports the following Process Objects.",
        "",
        "* Renew Process",
        "",
        "### Widget Transfer Process Object {#widget-transfer-process}",
        "",
        "* Name: Widget Transfer Process Object",
        "* Identifier: widgetTransferProcess",
        "* Extends: Transfer Process Object",
        "* Data Elements:",
        "  * Process ID",
        "    * Identifier: processId",
        "    * Cardinality: 0-1",
        "    * Mutability: read-only",
        "    * Data Type: String",
    ]
    processes = _parse_supported_processes_for_object(
        lines, 0, len(lines), obj_heading_level=1, obj_type="Resource")
    assert [p.name for p in processes] == ["Renew Process"], processes

    operations = _parse_normative_operations_for_object(
        lines, 0, len(lines), obj_heading_level=1, obj_type="Resource", obj_id="widget")
    assert [op.identifier for op in operations] == ["create"], operations


def _test_parse_object_header_captures_extends():
    """
    "* Extends:" is an object header attribute (naming the extended object
    by Name) and must not terminate header parsing early.
    """
    lines = [
        "* Name: Widget Transfer Process Object",
        "* Identifier: widgetTransferProcess",
        "* Extends: Transfer Process Object",
        "* Unique Identifier: processId",
        "* Description: A widget transfer.",
        "* Data Elements:",
    ]
    header = _parse_object_header(lines, 0, len(lines))
    assert header is not None
    name, ident, desc, extends, _end = header
    assert name == "Widget Transfer Process Object"
    assert ident == "widgetTransferProcess"
    assert extends == "Transfer Process Object", extends
    assert desc == "A widget transfer.", desc


def _test_parse_object_header_unique_identifier_does_not_end_header():
    """
    Regression test: "* Unique Identifier:" is a header attribute, not a
    terminator. Every Process Object in the draft writes it BEFORE
    "* Description:", so treating it as end-of-header silently dropped the
    description of every such object.
    """
    lines = [
        "* Name: Transfer Process Object",
        "* Identifier: transferProcess",
        "* Unique Identifier: processId",
        "* Description: Represents a transfer request.",
        "* Data Elements:",
    ]
    header = _parse_object_header(lines, 0, len(lines))
    assert header is not None
    _name, _ident, desc, _extends, header_end = header
    assert desc == "Represents a transfer request.", desc
    assert lines[header_end] == "* Data Elements:", (header_end, lines[header_end])


def _test_check_supported_processes_resolves_names_and_extends():
    """
    Both references resolve by Object Name: [PROCESS MISSING IN NORMATIVE]
    for an inherited-process bullet, [EXTENDS MISSING IN NORMATIVE] for an
    object's Extends.
    """
    obj = ObjectDef(
        name="Widget Transfer Process Object", identifier="widgetTransferProcess",
        source="normative", line=1, obj_type="Process",
        extends="Transfer Process Object",
        supported_processes=[SupportedProcessDef(name="Bogus Process", line=3)],
    )
    norm_names = {"Widget Transfer Process Object"}  # neither reference present
    errors = check_supported_processes(obj, norm_names)
    assert any("[PROCESS MISSING IN NORMATIVE]" in e and "Bogus Process" in e
               for e in errors), errors
    assert any("[EXTENDS MISSING IN NORMATIVE]" in e and "Transfer Process Object" in e
               for e in errors), errors
    assert len(errors) == 2, errors

    # "Bogus Process" resolves via the short form to "Bogus Process Object".
    norm_names_ok = norm_names | {"Transfer Process Object", "Bogus Process Object"}
    assert check_supported_processes(obj, norm_names_ok) == []


def _test_walk_normative_objects_skips_aside_before_header():
    """
    Regression test for the "Disclose Object" bug: an "A>" aside line
    between an object's H2 heading and its "* Name:" bullet (in addition to
    the usual blank line) must not prevent the object from being found.
    """
    lines = [
        "# Component Objects",
        "",
        "## Disclose Object",
        "",
        "A> TODO: Model Disclose in universal (extendible) way",
        "",
        "* Name: Disclose",
        "* Identifier: disclose",
        "* Description: TBD",
        "",
        "## Restore Report Object",
        "",
        "* Name: Restore Report Object",
        "* Identifier: restoreReport",
        "* Description: A report.",
        "* Data Elements:",
    ]
    objects = _walk_normative_objects(lines, 0, len(lines), obj_type="Component")
    ids = {o.identifier for o in objects}
    assert ids == {"disclose", "restoreReport"}, ids


def _test_object_header_tolerates_blank_lines_and_asides_between_attrs():
    """
    Blank lines and "A>" asides between an object's OWN header attribute
    bullets (Name/Identifier/Description), not just before the block, must
    not break header parsing or terminate it early.
    """
    lines = [
        "# Component Objects",
        "",
        "## Widget Object",
        "",
        "* Name: Widget Object",
        "",
        "A> TODO: reconsider naming",
        "",
        "* Identifier: widget",
        "A> TBD: description still under review",
        "* Description: A widget.",
        "* Data Elements:",
        "  * Size",
        "    * Identifier: size",
        "    * Cardinality: 1",
        "    * Mutability: read-write",
        "    * Data Type: Integer",
    ]
    objects = _walk_normative_objects(lines, 0, len(lines), obj_type="Component")
    assert len(objects) == 1, objects
    obj = objects[0]
    assert obj.identifier == "widget"
    assert obj.name == "Widget Object"
    assert obj.description == "A widget."


def _test_walk_normative_objects_flat_data_object():
    """
    Regression test: a top-down walk of a Data Object section correctly
    parses the H1-level object (elements via "## Data Elements", operations
    via "## Operations"), with the "## Object Description" wrapper heading
    correctly recognised as marking (not itself being) the object.
    """
    lines = [
        "# Widget Data Object",
        "",
        "## Object Description",
        "",
        "* Name: Widget Data Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "",
        "## Data Elements",
        "",
        "* Size",
        "  * Identifier: size",
        "  * Cardinality: 1",
        "  * Mutability: read-write",
        "  * Data Type: Integer",
        "",
        "## Operations",
        "",
        "### Create Operation",
        "",
        "* Identifier: create",
    ]
    objects = _walk_normative_objects(lines, 0, len(lines), obj_type="Resource")
    assert len(objects) == 1, objects
    obj = objects[0]
    assert obj.identifier == "widget"
    assert obj.obj_type == "Resource"
    assert len(obj.elements) == 1 and obj.elements[0].identifier == "size"
    assert len(obj.operations) == 1 and obj.operations[0].identifier == "create"


def _test_walk_normative_objects_process_embedded_in_data_object():
    """
    Regression test for the domainCreateProcess bug: a Process Object
    embedded in a Data Object's own "## Processes" sub-section (H3, one
    level deeper than the usual Component/Process H2 convention) must be
    discovered as its own object, with obj_type "Process" - NOT the
    enclosing section's "Resource" - and correctly parsed elements/operations.
    """
    lines = [
        "# Widget Data Object",
        "",
        "## Object Description",
        "",
        "* Name: Widget Data Object",
        "* Identifier: widget",
        "* Description: A widget.",
        "",
        "## Data Elements",
        "",
        "* Size",
        "  * Identifier: size",
        "  * Cardinality: 1",
        "  * Mutability: read-write",
        "  * Data Type: Integer",
        "",
        "## Processes",
        "",
        "### Widget Create Process Object {#widget-create-process}",
        "",
        "* Name: Widget Create Process Object",
        "* Identifier: widgetCreateProcess",
        "* Description: Created implicitly.",
        "* Data Elements:",
        "  * Foo",
        "    * Identifier: foo",
        "    * Cardinality: 1",
        "    * Mutability: read-only",
        "    * Data Type: String",
        "",
        "#### Operations",
        "",
        "##### Create {#widget-create-process-create}",
        "",
        "* Identifier: create",
    ]
    objects = _walk_normative_objects(lines, 0, len(lines), obj_type="Resource")
    ids = {o.identifier: o for o in objects}
    assert set(ids) == {"widget", "widgetCreateProcess"}, ids
    assert ids["widget"].obj_type == "Resource"
    proc = ids["widgetCreateProcess"]
    assert proc.obj_type == "Process"
    assert len(proc.elements) == 1 and proc.elements[0].identifier == "foo"
    assert len(proc.operations) == 1 and proc.operations[0].identifier == "create"


def _test_orphan_prose_skips_asides_and_joins_paragraphs():
    lines = [
        "* Name: Foo",
        "* Identifier: foo",
        "* Description: bar.",
        "This is orphan sentence one",
        "continued on a second line.",
        "",
        "A> TODO: this aside must never appear in output.",
        "",
        "This is a second paragraph.",
    ]
    before = len(PROSE_WARNINGS)
    notes = extract_orphan_prose(lines, 0, len(lines), label="test", source_ref="line 1")
    assert notes == [
        "This is orphan sentence one continued on a second line.",
        "This is a second paragraph.",
    ], notes
    assert not any("A>" in n or "TODO" in n for n in notes)
    assert len(PROSE_WARNINGS) == before + 1


def _test_orphan_prose_empty_when_fully_structured():
    lines = [
        "* Name: Foo",
        "* Identifier: foo",
        "* Cardinality: 1",
        "* Mutability: read-only",
        "* Data Type: String",
        "* Description: bar.",
        "* Constraints: (None)",
    ]
    before = len(PROSE_WARNINGS)
    notes = extract_orphan_prose(lines, 0, len(lines), label="test", source_ref="line 1")
    assert notes == []
    assert len(PROSE_WARNINGS) == before


def _test_organisation_and_user_sections_recognised():
    assert in_normative_section("# Organisation Data Object")
    assert in_normative_section("# User Object")
    assert _obj_type_for_h1("# Organisation Data Object") == "Resource"
    assert _obj_type_for_h1("# User Object") == "Resource"


def _test_parse_document_covers_organisation_and_user():
    """End-to-end: the real draft's Organisation and User objects are parsed."""
    if not DRAFT_FILE.exists():
        return  # skip when not run from src/
    normative, _iana, _section_notes = parse_document(DRAFT_FILE)
    ids = {o.identifier for o in normative}
    assert "organisation" in ids, sorted(ids)
    assert "user" in ids, sorted(ids)


def _test_build_yaml_model_structure():
    obj = ObjectDef(
        name="Widget Object", identifier="widget", source="normative", line=1,
        obj_type="Component", description="A widget.",
        elements=[ElementDef(identifier="size", name="Size", cardinality="1",
                              mutability="read-write", data_type="Integer", line=2,
                              description="The size.", constraints=["MUST be positive."],
                              notes=["Extra note."])],
        operations=[OperationDef(identifier="create", name="Create", line=3,
                                  description="Creates it.", notes=[],
                                  params=[ParamDef(identifier="p1", name="P1",
                                                    cardinality="0-1", data_type="String",
                                                    line=4, description="", constraints=[],
                                                    notes=[])])],
        preamble=["Intro sentence."],
    )
    model = build_yaml_model([obj], {"# Component Objects": ["Section intro."]})
    assert model["components"][0]["identifier"] == "widget"
    assert model["components"][0]["elements"][0]["constraints"] == ["MUST be positive."]
    assert model["components"][0]["elements"][0]["notes"] == ["Extra note."]
    assert model["components"][0]["operations"][0]["params"][0]["identifier"] == "p1"
    assert model["components"][0]["supported_processes"] == []
    assert model["section_notes"]["# Component Objects"] == ["Section intro."]
    assert model["processes"] == []
    assert model["resources"] == []


def _test_render_yaml_roundtrips_and_has_header_comment():
    if yaml is None:
        return  # PyYAML unavailable — covered separately by the --yaml-out error path
    obj = ObjectDef(
        name="Widget Object", identifier="widget", source="normative", line=1,
        obj_type="Component", description="A widget.",
    )
    text = render_yaml([obj], {})
    assert text.lstrip().startswith("#"), "expected header comment block"
    parsed = yaml.safe_load(text)
    assert parsed["components"][0]["identifier"] == "widget"


_SELF_TESTS = [
    _test_element_constraints_and_notes_nested,
    _test_element_attrs_tolerate_blank_lines_and_asides_between_bullets,
    _test_element_constraints_flat,
    _test_element_constraints_none_becomes_empty_list,
    _test_element_constraints_nested_bullet_list_reads_indent_from_source,
    _test_header_end_stops_at_data_elements_bullet,
    _test_param_attrs_tolerate_blank_lines_and_asides_between_bullets,
    _test_operation_description_authorisation_input_output,
    _test_operation_description_multi_paragraph_and_trailing_notes,
    _test_subsection_parsing_preserves_order_and_folds_bullets,
    _test_subsection_operations_heading_excluded_for_process,
    _test_subsection_component_never_has_operations,
    _test_subsection_flat_data_object_operations_excluded,
    _test_subsection_object_description_heading_excluded,
    _test_subsection_processes_heading_excluded,
    _test_parse_supported_processes_basic,
    _test_resolve_process_name_accepts_short_form,
    _test_parse_supported_processes_stops_at_extended_process_heading,
    _test_parse_object_header_captures_extends,
    _test_parse_object_header_unique_identifier_does_not_end_header,
    _test_check_supported_processes_resolves_names_and_extends,
    _test_walk_normative_objects_skips_aside_before_header,
    _test_object_header_tolerates_blank_lines_and_asides_between_attrs,
    _test_walk_normative_objects_flat_data_object,
    _test_walk_normative_objects_process_embedded_in_data_object,
    _test_orphan_prose_skips_asides_and_joins_paragraphs,
    _test_orphan_prose_empty_when_fully_structured,
    _test_organisation_and_user_sections_recognised,
    _test_parse_document_covers_organisation_and_user,
    _test_build_yaml_model_structure,
    _test_render_yaml_roundtrips_and_has_header_comment,
]


def run_self_tests() -> bool:
    """Run all _test_* functions; print a pass/fail summary. Returns True iff all pass."""
    failures: list[str] = []
    for test in _SELF_TESTS:
        PROSE_WARNINGS.clear()
        try:
            test()
            print(f"  PASS  {test.__name__}")
        except AssertionError as e:
            failures.append(test.__name__)
            print(f"  FAIL  {test.__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - surface any unexpected error as a failure
            failures.append(test.__name__)
            print(f"  ERROR {test.__name__}: {e!r}")

    print()
    if failures:
        print(f"{len(failures)}/{len(_SELF_TESTS)} self-tests FAILED: {', '.join(failures)}")
        return False
    print(f"All {len(_SELF_TESTS)} self-tests passed.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check consistency between normative object definitions and IANA tables, "
                     "and/or export the full structured object model as YAML."
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help=(
            "For every MISSING IN IANA issue, include generated IANA markup: "
            "full table block for missing objects, single row for missing elements, "
            "full operation block for missing operations, single row for missing parameters."
        ),
    )
    check_group = parser.add_mutually_exclusive_group()
    check_group.add_argument(
        "--check", dest="check", action="store_true", default=True,
        help="Run the normative/IANA consistency check (default: on).",
    )
    check_group.add_argument(
        "--no-check", dest="check", action="store_false",
        help="Skip the normative/IANA consistency check.",
    )
    parser.add_argument(
        "--iana-out", metavar="PATH", type=Path,
        help="Write the consistency-check report to PATH instead of stdout.",
    )
    parser.add_argument(
        "--yaml-out", metavar="PATH", type=Path,
        help="Write the full structured object model (normative definitions, "
             "including prose not covered by structured fields) to PATH as YAML.",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="Run the in-file test suite and exit (ignores all other flags).",
    )
    args = parser.parse_args()

    if args.self_test:
        return 0 if run_self_tests() else 1

    if not DRAFT_FILE.exists():
        print(f"ERROR: file not found: {DRAFT_FILE}", file=sys.stderr)
        return 1

    normative, iana, section_notes = parse_document(DRAFT_FILE)

    exit_code = 0

    if args.check:
        report_lines, ok = run_consistency_check(normative, iana, args.generate)
        report_text = "\n".join(report_lines)
        if args.iana_out:
            args.iana_out.write_text(report_text + "\n")
            print(f"Consistency report written to {args.iana_out}")
        else:
            print(report_text)
        if not ok:
            exit_code = 1

    if args.yaml_out:
        yaml_text = render_yaml(normative, section_notes)
        args.yaml_out.write_text(yaml_text)
        print(f"YAML object model written to {args.yaml_out}")

    if PROSE_WARNINGS:
        print(f"\n{len(PROSE_WARNINGS)} orphan-prose warning(s):", file=sys.stderr)
        for w in PROSE_WARNINGS:
            print(f"  - {w}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
