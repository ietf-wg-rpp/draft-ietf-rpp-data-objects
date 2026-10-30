# Agents.md — .scripts/

Scoped instructions for coding tasks inside `.scripts/`. This directory holds
tooling that supports authoring `src/draft-ietf-rpp-data-objects.md`; it is not
part of the IETF draft itself. The root [Agents.md](../Agents.md) governs the
draft content — this file governs the Python tooling.

**Keep this file up to date.** Whenever `check_iana_consistency.py`'s parsing
model, data classes, or CLI surface change, update the relevant section below
in the same edit. Do not let it drift from the code.

## Files

- [check_iana_consistency.py](check_iana_consistency.py) — the one and only
  script. One-file by design: no sibling modules, no package. Do not split it
  up, and do not add a second script — extend this one.
- [check_iana_consistency.md](check_iana_consistency.md) — full specification
  of parsing rules, document layouts, and CLI behaviour. Update it whenever
  the parsing model or CLI changes; treat it as the authoritative reference,
  not this file.
- [prose_mappings.yaml](prose_mappings.yaml) — data, not code: the verbatim
  prose fragments that `--yaml-out` replaces by structured fields
  (`restrictions`, `conditions`). The only place prose may become
  structure — see the first hard rule.
- [object_model_template.yaml](object_model_template.yaml) — the verbose
  header-comment block prepended verbatim to `--yaml-out` output. Update this
  file (not a Python string literal) whenever the YAML schema changes.

## Hard rules for this directory

- **Parse structure, never prose.** The script derives data only from the
  draft's structural markup: headings and their nesting, `{#anchor}`s,
  `* Key: value` attribute bullets, bullet nesting, and pipe tables with
  their `Table:` captions. Never extract meaning from free text (no keyword sniffing inside a
  Description or Constraints sentence, no guessing from identifier
  prefixes or link text). When a
  needed fact exists only in prose, do not add a heuristic: report it to the
  user and propose a structured bullet/table for the draft instead.
  **Sole exception:** an exact sentence listed in
  [prose_mappings.yaml](prose_mappings.yaml) together with the structured
  fields it stands for. The script matches those strings verbatim and
  nothing else; never generalise a mapping into a pattern, regex or keyword
  rule in code, and never hard-code a mapping in Python.
- **Abort on inconsistent structure.** If the draft writes the same kind of
  information in more than one structural form, so that no single generic
  rule yields a consistent YAML (e.g. a value as a bullet in one object and
  in prose in another, or a table under differently named headings), stop
  and report the inconsistency with line numbers and a proposed draft
  change. Do not special-case individual objects to paper over it.
- **One file only.** `check_iana_consistency.py` must remain runnable as a
  single self-contained script (stdlib + PyYAML only). Do not introduce a
  package layout or additional `.py` modules. [requirements.txt](requirements.txt)
  pins the one external dependency (PyYAML, needed for `--yaml-out`); update it
  if that dependency set ever changes, but do not grow it into a general
  Python-project dependency file.
- **No throwaway scripts.** Never create ad-hoc `.py` files to test or explore
  behaviour, and never leave a `.work-*.py` migration script behind (those are
  permitted only transiently for draft-body bulk edits per the root
  Agents.md, and must be deleted after use — they do not belong in
  `.scripts/`). To verify a change to `check_iana_consistency.py`, add a
  `_test_*` function to the `_SELF_TESTS` list in the script itself and run
  `--self-test`. This is the only sanctioned way to verify behaviour.
- **Never assume when multiple designs are plausible.** If a change could
  reasonably be implemented more than one way (data shape, CLI flag name,
  default behaviour, what counts as "structured" vs "orphan" content), ask the
  user before writing code. Only proceed without asking when there is
  genuinely one reasonable reading.
- **Re-use the shared parser.** `parse_document()` is the single entry point
  for both the IANA consistency check and the YAML export. Any new output
  mode must consume `parse_document()`'s result rather than re-parsing the
  draft.
- **Never `pip install` into the global/system Python.** First check whether
  the needed package is already importable in the current environment (e.g.
  `python3 -c "import yaml"`) — if it is, just use that interpreter as-is, no
  venv needed. Only when a package is genuinely missing, create/reuse a
  virtualenv (see **Running** below) and install into that instead of running
  `pip install` against the global interpreter. This applies to
  `requirements.txt`, ad-hoc `pip install <pkg>` for testing, and any other
  Python package install in this repo — no exceptions without the
  confirmation called for by the root Agents.md's Working Agreement.

## Running

Check first whether PyYAML is already available (only needed for
`--yaml-out`; `--check` alone needs nothing beyond the stdlib):

```bash
python3 -c "import yaml" && echo "already available"
```

If that succeeds, just run the script with the plain `python3` — no venv
needed. Only if it fails, create a virtualenv (repo root, `.venv/` is
gitignored) and install into that instead of the global Python:

```bash
python3 -m venv .venv
.venv/bin/pip install -r .scripts/requirements.txt
```

Run the script from `src/` (the script reads
`./draft-ietf-rpp-data-objects.md` relative to the current directory), using
whichever interpreter has PyYAML available — the global `python3` if the
check above succeeded, otherwise `../.venv/bin/python3`:

```bash
cd src
python3 ../.scripts/check_iana_consistency.py [OPTIONS]
```

Flags:

| Flag | Effect |
| ---- | ------ |
| `--check` / `--no-check` | Run the normative/IANA consistency check. Default: **on**. |
| `--generate` | With `--check`, also include ready-to-paste IANA markup for every `MISSING IN IANA` issue. |
| `--iana-out PATH` | Write the consistency report to `PATH` instead of stdout. |
| `--yaml-out PATH` | Write the full structured object model to `PATH` as YAML (see below). |
| `--level {error,warning,info}` | Report verbosity (`REPORT_LEVELS`): `error` = consistency errors only (default); `warning` = also orphan-prose warnings, as a report section; `info` = also the normative/IANA object inventories. |
| `--self-test` | Run the in-file test suite (`_SELF_TESTS`) and exit; ignores every other flag. |

`--check` and `--yaml-out` are independent — either, both, or neither may be
requested in one invocation (running with no flags reproduces the original
console-report-only behaviour, with `--check` defaulting on).

## Data model

Defined near the top of `check_iana_consistency.py`:

- `ElementDef` — a persisted data element: `identifier`, `name`,
  `cardinality`, `mutability`, `data_type`, `description`, `constraints`
  (`list[str]` — see below), `notes` (orphan prose).
- `ParamDef` — an operation input that is not part of the object's state:
  an Input Data Element or a Parameter (the draft's two terms; the list it
  sits in decides which). Same fields as `ElementDef` minus `mutability`,
  plus `extra_attrs` (`dict[str, str]`: attribute bullets beyond
  `PARAM_ATTR_KEYS`, kept verbatim and reported as not applicable; never
  exported — see YAML export below).
- `OperationDef` — `identifier`, `name`, `description` (prose after
  `* Identifier:`, never a `* Description:` bullet), `authorisation`
  (`list[str]`), `input`, `output` (`list[IOItemDef]` — `kind`, `target`,
  `cardinality` — or `None` when undeclared; Data Object
  operations get Uniform interface defaults via `apply_uniform_interface()`),
  `io_errors` (notation problems → `[OP IO INVALID]`), `notes`, `inputs`
  (Input Data Elements), `params` (Parameters), and IANA-only
  `inputs_declared`/`params_declared` flags. Normative inputs/params are
  the entries of the `Input Data Elements:` / `Parameters:` items of
  `input` (their `IOItemDef.definitions`, laid out like `* Data
  Elements:`), parsed by `_parse_io_definition()`; IANA ones
  come from the `Input Data Elements` / `Parameters` tables. The YAML
  shows them only inside `input`.
- `SubsectionDef` — a generic non-Data-Elements/Operations/Processes
  sub-heading nested inside an object (e.g. `dnsRecord`'s "RDATA Structures
  in EPP Profile"): `heading`, `anchor`, `notes` (prose + bullets, in
  order), `tables` (`TableDef`: `caption`, `anchor`, `columns`, `rows`),
  and nested `subsections` for deeper headings. Fenced blocks (diagrams)
  are skipped, not exported.
- `CommonTypeDef` — one H2 under `# Common Data Types`: `name`, `anchor`,
  `line`, `description` (`list[str]`), parsed by
  `parse_common_data_types()`; exported as `common_data_types`.
- `ObjectDef` — `identifier`, `name`, `obj_type` (`Component`/`Process`/
  `Resource`), `unique_identifier` (`* Unique Identifier:`; `""` if absent
  or `(None)`), `owner` (identifier of the Data Object whose `## Processes`
  section holds this Process Object; `""` otherwise), `extends`,
  `abstract` (bool, from `* Abstract: true`; raw value in `abstract_raw`,
  validated by `check_object_attrs()` → `[OBJ ATTR INVALID]`),
  `description`, `preamble` (orphan prose before the first
  element/operation), `elements`, `operations`, `subsections`,
  `supported_processes`.
- `ElementDef` also carries `direct_access` (bool, default `False`),
  `visibility` (`default`/`onDemand`, default `default`), `extra_attrs`
  (bullets beyond `ELEMENT_ATTR_KEYS`, reported as `[ELEM ATTR UNKNOWN]`)
  and `attr_values` (raw Direct Access/Visibility values, validated by
  `check_element_attrs()` → `[ELEM ATTR INVALID]`).

`NORMATIVE_TOPLEVEL_SECTIONS` / `SECTION_TO_OBJ_TYPE` list every H1 section
the parser treats as normative and its default `obj_type`, and must be
extended together whenever a new top-level Data Object section is added to
the draft. **`obj_type` is not always equal to the enclosing H1 section's
default** — a Process Object embedded in a Data Object's own
`## Processes` sub-section (e.g. `domainCreateProcess`) has `obj_type =
"Process"` even though it lives inside a `Resource`-section H1; see
**Object discovery** below.

## Object discovery (top-down walk)

`parse_normative_objects()` iterates each normative H1 section and delegates
to `_walk_normative_objects()`, a top-down recursive walk over the heading
tree — there is no single "is this nested?" boolean sniffed from document
content. Two structural rules from the draft drive it:

- **Heading level scales with nesting; the sub-structure does not.** Data
  Elements / Operations / any other subsection are always siblings one
  heading level deeper than the object's own heading — Resource objects
  start at H1, Component/Process objects at H2, and a Process Object
  embedded in a Data Object's `## Processes` section at H3 (one level
  deeper than usual, because it's nested inside the Data Object's H1).
  `_find_sibling_headings()` finds these siblings generically at whatever
  level is passed in.
- **Group headings nest objects one level deeper.** In an envelope section,
  a heading with no object header but with child headings
  (e.g. `## Messages`, `## Discovery`) is a group; `_walk_envelope_level()`
  recurses into it, so its children are objects at H3 (or deeper). Its
  intro prose goes to `section_notes` keyed by the group heading text.
  Prose between an object's heading and its `* Name:` bullet is skipped by
  `_skip_to_object_header()` and kept at the start of the object's
  `preamble` (`_object_lead_prose()`).
- **Component Objects never have Operations** (`OBJ_TYPES_WITHOUT_OPERATIONS`).
  `_parse_normative_operations_for_object()` skips the lookup entirely for
  Components; an `Operations`-named heading found there anyway is captured
  by `_parse_object_subsections()` as a generic `SubsectionDef` with a
  `PROSE_WARNINGS` entry, not parsed as real operations.

`_parse_object_header()` (Name/Identifier/Description bullets) and
`_collect_element_attrs()` both tolerate blank lines and `A>` asides
appearing anywhere around or between the bullets they collect — a valid
editorial pattern in this document, not just before/after a whole block.

## Constraints / Authorisation (list-valued attributes)

`* Constraints:` (elements, params) and `* Authorisation:` (operations) are
always parsed into a `list[str]`, via `_collect_constraints_list()` /
`_parse_nested_bullet_list()`: a single inline value becomes a one-item
list, `(None)`/absent becomes `[]`, and a blank inline value followed by a
nested bullet list becomes one entry per top-level bullet (deeper nesting —
a numbered sub-list, a further bullet list — newline-folded into that same
entry). The nested list's indentation is always read from the source line,
never assumed as a fixed offset from the parent attribute's indent.

## Orphan prose extraction

`extract_orphan_prose()` is the mechanism used for most sites where prose
that isn't tied to a recognised structured attribute needs to be preserved
(for `--yaml-out`) instead of silently dropped. It walks a line range and
collects every line that is **not**: blank, a heading, a table row, a
recognised `* Key: value` attribute bullet (see `_KNOWN_ATTR_KEYS`), an
element/operation/param header bullet, or an `A>` aside (asides are dropped
entirely, never warned about, never emitted). Consecutive non-blank lines are
joined into one paragraph string; a blank line starts a new paragraph.
`_parse_subsection_body()` is a sibling mechanism for generic subsections,
where bullets ARE content (not headers to skip) — it preserves document
order between prose paragraphs and bullet-list entries.

Every non-empty extraction is also appended to the module-level
`PROSE_WARNINGS` list and, at `--level warning`/`info`, emitted as an
`ORPHAN-PROSE WARNINGS` section of the consistency report (to stderr when
`--no-check` leaves no report) — this is
intentional: orphan prose is a symptom of prose that should have been written
as a proper structured bullet at the source, and the warning is a nudge to
fix the draft, not just the tooling.

Call sites feed these two functions, each attaching the result to a
different field:

| Call site | Target field | Range |
| --- | --- | --- |
| `parse_section_notes()` | top-level `section_notes[h1]` | H1 heading → first object |
| `_walk_envelope_level()` | top-level `section_notes[group heading]` | group heading → first child heading |
| `_object_lead_prose()` | start of `ObjectDef.preamble` | object heading → `* Name:` bullet |
| `_parse_object_body()` | `ObjectDef.preamble` | object header attrs → first element/operation |
| `_parse_normative_elements_nested/flat()` | `ElementDef.notes` | element header → next element/end |
| `_parse_normative_operations_nested()` (via `_parse_operation_body_fields()`) | `OperationDef.notes` | body after description, excluding field bullets with their nested lists |
| `_parse_io_definition()` | `ParamDef.notes` | an Input Data Elements / Parameters entry's own nested lines |
| `_parse_subsection_body()` | `SubsectionDef.notes` | a generic subsection's whole body |

If you add a new structured attribute key (e.g. a new bullet type), add it to
`_KNOWN_ATTR_KEYS` so it isn't misclassified as orphan prose.

## YAML export (`--yaml-out`)

`main()` builds the plain-dict tree with `build_yaml_model()`, applies
`apply_prose_mappings()` to it, and `render_yaml(model)` prepends the
comment header from `object_model_template.yaml` verbatim before dumping it
with PyYAML (`yaml.safe_dump`). Top-level keys: `section_notes`,
`common_data_types`, `components`, `processes`, `resources`.

**Prose mappings.** Every element, input, parameter and Common Data Type
dict carries `restrictions: {}` and every object `conditions: []`.
`load_prose_mappings()` / `validate_prose_mappings()` read
`prose_mappings.yaml`; `apply_prose_mappings()` finds each entry's verbatim
`text` anywhere in those holders' description/constraints (and in every
object subsection's `notes`, which take only `object` fields), removes it, and
puts the entry's `set` fields into `restrictions` (every `*_from` key in
`VALUES_FROM_KEYS` — `allowed_values_from`, `client_values_from`,
`server_only_values_from` — resolved from the parsed tables by anchor,
optionally filtered by a `where: {column: [values]}` row filter) and its `object` fields into the
owning object (`conditions` appended; `OBJECT_FLAG_KEYS` booleans such as
`at_least_one_status_by_server` set, and present only where mapped). Unmatched text (`[MAPPING STALE]`), conflicting values
(`[MAPPING CONFLICT]`) and bad entries (`[MAPPING INVALID]`) fail
`--yaml-out` like `extra_attrs` do. This works on the YAML dicts only;
`parse_document()`'s result and the IANA check are untouched. New
restriction keys go into `RESTRICTION_KEYS` and the template.

The template file is the single source of truth for the schema documentation shown to readers of the
generated YAML — update it, not a Python docstring, when the schema changes.

`extra_attrs` (elements, inputs, parameters) is never exported. Any entry
means the draft has an attribute the export routine does not cover, so
`yaml_export_errors()` makes `--yaml-out` fail (errors to stderr, no file
written, exit 1). Prose-mapping errors are added to the same list. Handle a new attribute by adding a real field, not a
generic pass-through.

The YAML reflects **normative** object definitions only (the draft body), not
the IANA registry tables — `--check` is what verifies the two stay in sync.
Full field-by-field schema (including `constraints`/`authorisation` as
lists, and `subsections`): see
[check_iana_consistency.md § YAML export](check_iana_consistency.md#yaml-export).

PyYAML is a hard runtime dependency for `--yaml-out` (not vendored, not
hand-rolled): if `import yaml` fails, `render_yaml()` prints an install
instruction and exits 1. `--check` alone does not require PyYAML.

## Testing

There are no separate test files. Verification lives entirely inside
`check_iana_consistency.py` as `_test_*` functions collected in
`_SELF_TESTS` and run via `run_self_tests()` (triggered by `--self-test`).
When changing parsing behaviour or the YAML schema, add or update a
`_test_*` function in the same edit — do not verify by hand-running the
script and eyeballing output only, and do not create a separate throwaway
script to check behaviour.
