# check_iana_consistency.py — Specification and Processing Rules

## Purpose

Programmatically verifies consistency between the normative object definitions
and the IANA registry tables in `src/draft-ietf-rpp-data-objects.md`, and can
export the full structured object model — including prose not captured by any
structured field — as YAML.

Both the consistency check and the normative object model are built from a
single shared parse (`parse_document()`); the YAML export does not re-parse
the draft independently.

## Usage

PyYAML is needed only for `--yaml-out`; `--check` alone needs nothing beyond
the stdlib. Check whether it is already available before installing anything:

```bash
python3 -c "import yaml"
```

If that fails, install into a virtualenv — never into the global/system
Python (see [.scripts/CLAUDE.md](CLAUDE.md)):

```bash
python3 -m venv .venv
.venv/bin/pip install -r .scripts/requirements.txt
```

Run from the `src/` directory, using whichever interpreter has PyYAML
available:

```bash
cd src
python3 ../.scripts/check_iana_consistency.py [OPTIONS]
```

| Flag | Effect |
| ---- | ------ |
| `--check` / `--no-check` | Run the normative/IANA consistency check. Default: **on**. |
| `--generate` | With `--check`, also include ready-to-paste IANA markup for every `MISSING IN IANA` issue (see [Generated output](#generated-output)). |
| `--iana-out PATH` | Write the consistency report to `PATH` instead of stdout. |
| `--yaml-out PATH` | Write the full structured object model to `PATH` as YAML (see [YAML export](#yaml-export)). |
| `--self-test` | Run the in-file test suite and exit; ignores every other flag. |

`--check` and `--yaml-out` are independent: either, both, or neither may be
requested in a single invocation. Running with no flags reproduces the
original behaviour (consistency report to stdout, `--check` on by default).

Exit code 0 = no errors (or `--no-check`); exit code 1 = at least one
consistency error. Orphan-prose warnings (see below) are printed to stderr
after all requested output and never affect the exit code.

---

## Error codes

### Object level

| Code | Meaning |
| ---- | ------- |
| `[MISSING IN IANA]` | Normative object has no IANA entry |
| `[MISSING IN NORMATIVE]` | IANA entry has no normative object |
| `[ID MISMATCH]` | Same Object Name, different identifier |
| `[NAME MISMATCH]` | Same identifier, different Object Name |
| `[DUPLICATE NORMATIVE]` | Duplicate identifier in normative section |
| `[DUPLICATE IANA]` | Duplicate identifier in IANA section |

### Data-element level (per matched object pair)

| Code | Meaning |
| ---- | ------- |
| `[ELEM MISSING IN IANA]` | Normative element absent from IANA table |
| `[ELEM MISSING IN NORMATIVE]` | IANA table row absent from normative definition |
| `[ELEM NAME MISMATCH]` | Identifier matches, but Element Name differs |
| `[ELEM CARD MISMATCH]` | Identifier matches, but Cardinality differs |
| `[ELEM MUTABILITY MISMATCH]` | Identifier matches, but Mutability differs |
| `[ELEM TYPE MISMATCH]` | Identifier matches, but Data Type differs |
| `[ELEM DESC EMPTY]` | IANA Description cell is blank; normative has a description |

### Operation level (per matched object pair, and for unmatched objects)

| Code | Meaning |
| ---- | ------- |
| `[OP MISSING IN IANA]` | Normative operation has no IANA entry |
| `[OP MISSING IN NORMATIVE]` | IANA operation has no normative entry |
| `[OP MISSING IDENTIFIER]` | Normative operation heading has no `* Identifier:` bullet |
| `[OP DESC EMPTY]` | IANA operation Description field is blank; normative has a description |

Note: `[OP MISSING IDENTIFIER]` is reported even when the parent object itself
is `[MISSING IN IANA]`, so identifier gaps are always surfaced.

### Operation parameter level (per matched operation pair)

| Code | Meaning |
| ---- | ------- |
| `[PARAM MISSING IN IANA]` | Normative parameter absent from IANA Parameters table |
| `[PARAM MISSING IN NORMATIVE]` | IANA parameter row absent from normative definition |
| `[PARAM NAME MISMATCH]` | Identifier matches, but Parameter Name differs |
| `[PARAM CARD MISMATCH]` | Identifier matches, but Cardinality differs |
| `[PARAM TYPE MISMATCH]` | Identifier matches, but Data Type differs |
| `[PARAM DESC EMPTY]` | IANA description cell is blank; normative has a description |

### Supported-process level (normative-only, no IANA table involved)

| Code | Meaning |
| ---- | ------- |
| `[PROCESS MISSING IN NORMATIVE]` | An inherited-process bullet in a Data Object's `## Processes` list names an Object **Name** that does not match any normative object (short form accepted: `Renew Process` → `Renew Process Object`) |
| `[EXTENDS MISSING IN NORMATIVE]` | An object's `* Extends:` attribute names an Object **Name** that does not match any normative object |

`supported_processes` and `extends` are normative-model concepts only; neither
has an IANA table counterpart, so there is no `MISSING IN IANA` variant for them.

---

## Normative sections parsed

Controlled by `NORMATIVE_TOPLEVEL_SECTIONS`:

```
# Component Objects
# Process Objects
# Domain Name Data Object
# Contact Data Object
# Host Data Object
# Organisation Data Object
# User Object
```

The IANA section begins at `# IANA Considerations` and is excluded from
normative parsing.

---

## Normative document layouts

Object discovery is a single top-down recursive walk
(`_walk_normative_objects()` / `_parse_object_body()`), not two separate
code paths keyed off document content. Two structural rules from the draft
itself drive it:

1. **Heading level scales with nesting, the internal layout does not.**
   Every object type uses the same sub-structure (Data Elements, Operations,
   and any other subsection are sibling headings/markers one level deeper
   than the object's own heading) — only *how deep* the object itself starts
   varies:
   - **Data Objects** (`Object Type: Resource`) start at **H1**
     (`# Domain Name Data Object` *is* the object). Its own header bullets
     live under a child `## Object Description` heading (H2); `## Data
     Elements` and `## Operations` are sibling H2 headings.
   - **Component and Process Objects** start at **H2**
     (`## Period Object`) inside an envelope H1 (`# Component Objects` /
     `# Process Objects`). Header bullets sit directly under the H2 (no
     `Object Description` wrapper); `* Data Elements:` is a *bullet*, not a
     heading, introducing an indented element list; `### Operations` (H3)
     holds the object's operations, one level deeper than its own H2.
   - **A Process Object embedded in a Data Object** (via that Data Object's
     own `## Processes` sub-section, e.g. `### Domain Create Process
     Object` under `## Processes` inside `# Domain Name Data Object`) starts
     at **H3** — one level deeper than the usual Component/Process H2
     convention, because the whole thing is nested inside the owning Data
     Object's H1. Its `obj_type` is `Process`, not `Resource`, even though
     it lives inside a Resource-section H1 — `obj_type` is inherited context
     that changes exactly at the `## Processes` heading, nowhere else.
2. **Component Objects never define Operations** (see `OBJ_TYPES_WITHOUT_OPERATIONS`).
   If an `Operations`-named heading is nonetheless found under one, it is
   captured as a generic subsection (see `SubsectionDef` below) instead of
   parsed as real operations, with a warning appended to `PROSE_WARNINGS`.

### Component and Process objects

```
## Period Object

* Name: Foo Object
* Identifier: foo
* Description: …
* Data Elements:
  * Element Name           ← 2-space indent
    * Identifier: elemId   ← 4-space indent
    * Cardinality: 1
    * Mutability: read-write
    * Data Type: String
    * Description: …

### Operations

#### Create (Transfer Request) {#anchor}

* Identifier: create

The following transient data elements are defined for this operation:

* Param Name
  * Identifier: paramId
  * Cardinality: 0-1
  * Data Type: String
  * Description: …
```

Body extent: from the object's own H2 heading up to the next heading at or
shallower than its own level (a sibling object's H2, or the enclosing H1's
end) — computed generically via `_find_sibling_headings()`, not a hardcoded
"next H2" scan.

### Data Objects

```
# Domain Name Data Object

## Object Description

* Name: Domain Name Data Object
* Identifier: domainName
* Description: …

## Data Elements

* Element Name           ← 0-indent
  * Identifier: elemId   ← 2-space indent
  * Cardinality: 1
  * Mutability: read-write
  * Data Type: String
  * Description: …

## Operations

### Create Operation
…

## Processes

### Domain Create Process Object {#domain-create-process}

* Name: Domain Create Process Object
* Identifier: domainCreateProcess
* Data Elements:
  * Process ID
    * Identifier: processId
    …

#### Operations

##### Create {#domain-create-process-create}
…
```

Operations under `## Operations` come in two shapes, both handled by
`_parse_operations_group()` generically at whatever heading level the
object's own `Operations` sibling sits:

**Direct operations** — a heading whose text does not itself end in the
word "Operations" (e.g. `### Create Operation`) is one operation directly.

**Group containers** — a heading whose text ends in "Operations" (e.g.
`### Transfer Operations`, `### Restore Operations`) is a container; its
individual operations are headings one level deeper still (e.g.
`#### Transfer Create Operation`).

```
### Transfer Operations

#### Transfer Create Operation

* Identifier: transferCreate

In addition, the following transient data element is defined for this operation:

* Transfer Period
  * Identifier: transferPeriod
  * Cardinality: 0-1
  * Data Type: Period Object
  * Description: …
```

Group-container child operations are parsed identically to direct
operations — they must have a `* Identifier:` bullet and may have transient
parameters. Operations missing the identifier bullet are recorded as
`[OP MISSING IDENTIFIER]`.

A group-container heading with no child headings underneath (pure
cross-reference prose, e.g. some `### Restore Operations` sections) produces
no operations and no errors.

### The "## Processes" section of a Data Object

A Data Object's `## Processes` section (a sibling of `## Operations`)
declares which Process Objects the object supports. It has two parts, in
this order:

1. One line of intro prose.
2. The **inherited** processes — those used unmodified — as a structured
   bullet list.
3. The **extended** processes — each a full object-specific Process Object
   definition at H3, carrying an `* Extends:` attribute naming the generic
   object it extends (by **Name**, not identifier).

```
## Processes

The Domain Name Data Object supports the following Process Objects …

* Renew Process
* Restore Process
  * Constraints: OPTIONAL; available only when the RGP feature is supported.

### Domain Transfer Process Object {#domain-transfer-process}

* Name: Domain Transfer Process Object
* Identifier: domainTransferProcess
* Extends: Transfer Process Object
* Unique Identifier: processId
* Data Elements:
  …
```

Each bullet before the first H3 is one `SupportedProcessDef`, parsed by
`_parse_supported_processes()`. The **bullet text itself is the reference**
to the Process Object, by Name — there is no `Identifier` attribute, which
would be a redundant second lookup key alongside the one `Extends:` already
uses. `resolve_process_name()` accepts the short form, so `Renew Process`
resolves to the object named `Renew Process Object`
(`[PROCESS MISSING IN NORMATIVE]` if unresolved).

The only attribute a bullet may carry is `Constraints` (optional), with the
same list-valued convention as element/param Constraints (inline value,
`(None)`, or a nested bullet list).

`_parse_supported_processes_for_object()` stops the bullet list at the first
H3 heading, so the embedded Process Objects' own `* Identifier:` bullets are
never swept into it. Those H3 blocks are parsed as their own `ObjectDef`s by
`_walk_normative_objects()` with `obj_type = "Process"` (see **Normative
document layouts** above); their `* Extends:` value lands in
`ObjectDef.extends` and is resolved by Object Name
(`[EXTENDS MISSING IN NORMATIVE]` if unresolved).

### Generic subsections

Any sibling heading of an object that is not `Object Description`,
`Data Elements`, `Operations`, or `Processes` — e.g. `### RDATA Structures
in EPP Profile {#rdata-structures}` inside the `dnsRecord` Component Object
— is captured as a `SubsectionDef` (`heading`, `anchor`, `notes`) via
`_parse_object_subsections()` / `_parse_subsection_body()`, so its content
(prose paragraphs and 0-indent bullet lists, in document order) is preserved
for `--yaml-out` instead of silently dropped.

### Operation identifier requirement

Every operation heading MUST contain a `* Identifier: <id>` bullet directly
inside the heading block. Operations missing this bullet are recorded and
reported as `[OP MISSING IDENTIFIER]`. They are excluded from all further
comparisons (IANA matching, parameter checking) but the error is always
emitted, even when the parent object has no IANA entry yet.

### Transient parameters

Parameters are parsed from bullet lists that appear after a line containing
the phrase "transient data element" (case-insensitive). Each parameter bullet
must have at minimum an `* Identifier:` sub-bullet; `* Cardinality:` and
`* Data Type:` are also captured when present.

### Constraints (and other list-valued attributes)

`* Constraints:` on an element or parameter is always parsed into a
**list of strings** (`_collect_constraints_list()` /
`_parse_nested_bullet_list()`), not a single string:

- A single-line inline value (`* Constraints: MUST be positive.`) becomes a
  one-item list.
- `(None)` (or nothing at all) becomes an empty list `[]`.
- A blank inline value immediately followed by a nested bullet list (e.g.
  the Status Object's `label` element) becomes one list entry per
  top-level bullet; further nesting under a bullet (a numbered sub-list, a
  further bullet list) is newline-folded into that same entry, keeping the
  original markers (`1.`, `2.`, `` * `addPeriod`: ``, …). The nested list's
  indentation is read from the source line itself, never assumed as a fixed
  offset.

`* Authorisation:` on an operation (see below) uses the identical mechanism.

### Blank lines and `A>` asides between attribute bullets

Blank lines and `A>` editorial asides (e.g. an inline `TODO`/`TBD`/`TBC`
note) are a valid editorial pattern **anywhere** in the structure, including
between an element's, parameter's, or object header's own attribute bullets
— not just before or after a whole block. `_collect_element_attrs()` and
`_parse_object_header()` both skip blank lines and asides wherever they
appear while collecting `* Key: value` bullets, so an aside inserted between
e.g. `* Cardinality:` and `* Mutability:` does not truncate attribute
collection or get misattributed as orphan prose.

### Operation body fields (Description / Authorisation / Input / Output)

Every operation's body follows this document's own convention, which
differs from elements/objects:

- **Description** is never a `* Description:` bullet (that form is
  element/object-only) — it is plain prose written directly after
  `* Identifier:`. Every prose paragraph between `* Identifier:` and the
  first recognised field bullet below (or the transient-params marker) is
  the description, newline-joined if there is more than one paragraph.
- **`* Authorisation:`** always has a blank inline value followed by a
  nested bullet list — parsed the same way as a Constraints sub-list (one
  string per top-level bullet, deeper nesting such as "Pull transfer:" /
  "Push transfer:" sub-cases newline-folded into that entry). Its
  indentation is read from the source, not assumed.
- **`* Input:`** / **`* Output:`** are always a single inline value (never a
  list) — captured as plain strings.
- Any prose **after** the last recognised field bullet (e.g. a further
  explanatory paragraph following Authorisation) is NOT part of the
  description — it stays in the operation's `notes`.

---

## IANA section layout

### Object block

```
Object: <identifier>

Object Name: <name>

Object Type: Component | Process | Resource

Description: <text>

Reference: [This-ID]

Data Elements
| Element Identifier | Element Name | Card. | Mutability | Data Type | Description |
| ------------------ | ------------ | ----- | ---------- | --------- | ----------- |
| …                  | …            | …     | …          | …         | …           |

Operations

Operation: <name>

Operation Identifier: <id>

Description: <text>

Parameters
| Identifier | Name | Card. | Data Type | Description |
| ---------- | ---- | ----- | --------- | ----------- |
| …          | …    | …     | …         | …           |
```

One blank line separates every field. Multiple `Operation:` blocks may follow
the `Operations` keyword.

`Parameters: (None)` is used when an operation has no parameters.

`Operation Identifier:` is optional in IANA; if absent the script derives the
identifier as the lower-cased first word of the Operation Name.

### Column header aliases

The script accepts two variants for element identifier/name columns:

| Canonical key | Accepted headers |
| ------------- | ---------------- |
| element identifier | `Element Identifier`, `Identifier` |
| element name | `Element Name`, `Name` |
| cardinality | `Card.`, `Cardinality` |
| mutability | `Mutability` |
| data type | `Data Type` |

---

## Type comparison

Data Type strings are normalised before comparison:

- Leading/trailing and internal whitespace collapsed to a single space
- Spaces around square brackets removed: `Dictionary [Integer]` ≡ `Dictionary[Integer]`

---

## Object type mapping

Each normative H1 section (`SECTION_TO_OBJ_TYPE`) has a default `obj_type`,
inherited by every object found directly inside it:

| Normative H1 section | Default Object Type |
| -------------------- | ------------------- |
| `# Component Objects` | `Component` |
| `# Process Objects` | `Process` |
| `# Domain Name Data Object` | `Resource` |
| `# Contact Data Object` | `Resource` |
| `# Host Data Object` | `Resource` |
| `# Organisation Data Object` | `Resource` |
| `# User Object` | `Resource` |

**Exception:** an object embedded under a Data Object's own `## Processes`
sub-section (e.g. `domainCreateProcess` under `# Domain Name Data Object`)
has `obj_type = "Process"`, overriding the enclosing section's `Resource`
default — inherited context changes exactly at the `## Processes` heading
(see `_walk_normative_objects()`), nowhere else. This is why `obj_type` must
never be derived solely from "which H1 section contains this object."

---

## Generated output (`--generate`)

When `--generate` is passed, a `GENERATED IANA MARKUP FOR MISSING ENTRIES`
section is appended to stdout after the error list. It covers:

### Missing objects → full IANA block

A complete block ready to paste into the IANA section, including:
- `Object:` / `Object Name:` / `Object Type:` / `Description:` / `Reference:`
  fields, each separated by a blank line
- `Data Elements` pipe table with auto-sized columns; `Description` column
  filled from the normative `* Description:` attribute
- `Operations` section with one `Operation:` block per normative operation,
  `Description:` filled from normative, and a `Parameters` table (or
  `Parameters: (None)`) with descriptions filled in

Column header style:
- Resource objects: `Identifier` / `Name`
- Component/Process objects: `Element Identifier` / `Element Name`

### Missing elements → single table row

One pipe-table row per missing element, with the `Description` cell filled
from the normative `* Description:` attribute:
```
| identifier | Name | Card. | Mutability | Data Type | Description text |
```

### Empty element descriptions → single corrected row (`[ELEM DESC EMPTY]`)

When the IANA table row exists but its Description cell is blank and the
normative definition has a description, a corrected row is output (same
format as for missing elements).

### Missing operations → full operation block

A complete `Operation:` block with `Operation Identifier:`, `Description:`
filled from the normative `* Description:` bullet, and a `Parameters` table
(or `Parameters: (None)`) with descriptions filled in.

### Empty operation descriptions → full corrected block (`[OP DESC EMPTY]`)

When the IANA operation exists but its `Description:` field is blank and the
normative definition has a description, the full corrected operation block is
output (same format as for missing operations).

### Missing parameters → single table row

One pipe-table row per missing parameter, with the `Description` cell filled
from the normative `* Description:` attribute:
```
| identifier | Name | Card. | Data Type | Description text |
```

### Empty parameter descriptions → single corrected row (`[PARAM DESC EMPTY]`)

When the IANA parameter row exists but its Description cell is blank and the
normative definition has a description, a corrected row is output (same
format as for missing parameters).

---

## Orphan prose extraction

Beyond the recognised structured attributes (Name, Identifier, Cardinality,
Mutability, Data Type, Description, Constraints), the draft contains free-form
explanatory prose written directly into object/element/operation blocks
instead of a structured bullet — section intros, operation-body explanations,
numbered lists, etc. `extract_orphan_prose()` is the single function that
collects this text everywhere it can occur, so it is preserved (for
`--yaml-out`) rather than silently dropped.

A line is orphan prose if it is **not**: blank, a heading, a table row
(`| ... |`), a recognised `* Key: value` attribute bullet (see
`_KNOWN_ATTR_KEYS` in the script), an element/operation/parameter header
bullet, or an `A>` aside note. Aside notes are recognised and always dropped
— never captured, never warned about — since they are editorial TODO/TBD/TBC
markers, not spec content.

Consecutive non-blank orphan lines are joined with a single space into one
paragraph string; a blank line starts a new paragraph. This means a wrapped
sentence spanning several source lines becomes one YAML string, and an
unspaced numbered list (no blank lines between items) is joined into a single
paragraph too — if that produces an unwanted merge, add blank lines between
the list items at the source.

### Attachment points

| Extraction site | Target field | Line range |
| --- | --- | --- |
| `parse_section_notes()` | top-level `section_notes[<h1 heading text>]` | from just after the H1 heading to the first object inside it |
| `_parse_object_body()` | `ObjectDef.preamble` | from the end of the object's header attribute bullets to its first element/operation |
| `_parse_normative_elements_nested()` / `_parse_normative_elements_flat()` | `ElementDef.notes` | from just after the element header bullet to the next element (or block end) |
| `_parse_normative_operations_nested()` (via `_parse_operation_body_fields()`) | `OperationDef.notes` | whatever remains of the operation body after description/authorisation/input/output, excluding the parsed parameter bullet sub-range |
| `_parse_normative_params_from_bullets()` | `ParamDef.notes` | from just after the parameter header bullet to the next parameter (or block end) |
| `_parse_subsection_body()` | `SubsectionDef.notes` | a generic subsection's body — prose paragraphs and 0-indent bullet lists, in document order (not `extract_orphan_prose`, which treats bullets as headers to skip; see **Generic subsections** above) |

### Warnings

Every non-empty extraction is appended to the module-level `PROSE_WARNINGS`
list. `main()` prints all accumulated warnings to stderr after the requested
report/YAML output, one line per orphan-prose occurrence, naming the source
line and how many paragraphs were found. This is intentional: an orphan-prose
warning signals prose that likely belongs in a proper `* Description:` or
`* Constraints:` bullet at the source, and the warning is a nudge to fix the
draft — the YAML export having a populated `notes`/`preamble` field is not
itself an error, only a signal worth reviewing.

---

## YAML export

`--yaml-out PATH` writes the full structured **normative** object model (not
the IANA registry tables — those are checked for consistency against this
model by `--check`, not re-exported) to `PATH` as YAML.

### Structure

Top-level keys: `section_notes`, `components`, `processes`, `resources`.
`components`/`processes`/`resources` each hold a list of objects, keyed by
`ObjectDef.obj_type` via `OBJ_TYPE_TO_YAML_KEY` (note: `obj_type` reflects
the object's *actual* type, not necessarily its enclosing H1 section — see
**Object type mapping** above). Each object dict:

```yaml
identifier: domainName
name: Domain Name Data Object
object_type: Resource
description: "..."
preamble: []              # orphan prose before the first element/operation
elements:
  - identifier: name
    name: Name
    cardinality: "1"
    mutability: create-only
    data_type: String
    description: "..."
    constraints: []        # list of strings — see Constraints above
    notes: []              # orphan prose inside this element's own block
operations:
  - identifier: create
    name: Create Operation
    description: "..."     # prose after "* Identifier:" — see Operation body fields above
    authorisation: []      # list of strings — see Operation body fields above
    input: ""
    output: ""
    notes: []              # orphan prose beyond description/authorisation/input/output
    params:
      - identifier: ...
        name: ...
        cardinality: "..."
        data_type: "..."
        description: "..."
        constraints: []
        notes: []
subsections:                # non-Data-Elements/Operations/Processes headings
  - heading: "RDATA Structures in EPP Profile"
    anchor: "{#rdata-structures}"
    notes: []                # prose paragraphs and bullets, in document order
extends: ""                 # Object NAME this object extends, from "* Extends:"; "" if none
supported_processes:        # inherited-process bullets from the object's "## Processes"
  - name: Renew Process      # the reference itself, by Object Name (short form accepted)
    constraints: []          # list of strings — same convention as element/param Constraints
    notes: []                # orphan prose inside this entry's own block
```

Nothing in the structured fields is duplicated into `notes`/`preamble`/
`section_notes`/`subsections` and vice versa — those fields hold only the
leftover content the parser could not attribute to a recognised bullet or
heading.

### Header comment

The YAML file begins with a verbose comment block loaded verbatim from
[object_model_template.yaml](object_model_template.yaml) (`render_yaml()`
reads every leading `#`/blank line from that file and prepends it to the
rendered data). Update that template file, not a Python string literal, when
the schema changes — it is the schema documentation shown to anyone reading
the generated YAML.

### Dependency

`--yaml-out` requires PyYAML (`import yaml`). If unavailable, `render_yaml()`
prints an install instruction (`pip install pyyaml`) to stderr and exits 1.
`--check` alone has no PyYAML dependency.

---

## Self-tests

The script has no separate test files. `_test_*` functions collected in the
`_SELF_TESTS` list at the bottom of `check_iana_consistency.py` are the sole
verification mechanism, run via:

```bash
python3 ../.scripts/check_iana_consistency.py --self-test
```

`--self-test` ignores every other flag. Coverage includes: Constraints
list-parsing for nested and flat element/parameter layouts (including
`(None)` → `[]` and reading the nested list's indentation from the source);
blank-line/`A>`-aside tolerance between attribute bullets (elements,
parameters, and object headers); operation description/authorisation/
input/output field parsing, including the multi-paragraph-description-vs-
trailing-notes split; generic-subsection discovery and its exclusion rules
(`Data Elements`/`Operations`/`Processes`/`Object Description`, plus the
Component-never-has-Operations warning path); the top-down object walk
(`_walk_normative_objects`), including a Process Object embedded in a Data
Object's `## Processes` section and an aside before an object's header;
orphan-prose extraction (paragraph joining, aside skipping,
empty-when-fully-structured); Organisation/User section recognition; an
end-to-end check that the real draft's `organisation`/`user` objects parse;
YAML model structure; and a YAML round-trip (render → `yaml.safe_load` →
verify).

When changing parsing behaviour or the YAML schema, add or update a `_test_*`
function in the same change — do not verify by hand-running the script and
eyeballing output only, and never create a separate throwaway script to
check behaviour (per [.scripts/CLAUDE.md](CLAUDE.md)).
