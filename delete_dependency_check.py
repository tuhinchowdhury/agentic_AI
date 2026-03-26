import argparse
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class FieldInfo:
    container: str  # e.g., Query, Mutation, SomeType, SomeInput
    name: str        # field name
    return_type_named: str  # normalized named type (e.g., "User" from "[User!]!")
    return_type_raw: str    # raw SDL type token (best-effort)
    arg_type_named: Tuple[str, ...]  # normalized named types used in arguments


TYPE_DEF_RE = re.compile(r"^(type|input|interface|union)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


def normalize_named_type(type_token: str) -> str:
    """
    Best-effort normalization: turns "[User!]!" into "User" and "String!" into "String".
    """
    # Strip list/brackets and non-identifier chars, then take the first identifier.
    # Examples:
    #  - [User!]! -> User
    #  - User -> User
    cleaned = type_token.replace("[", " ").replace("]", " ")
    cleaned = cleaned.replace("!", " ")
    m = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", cleaned)
    return m.group(1) if m else type_token.strip()


def parse_sdl(schema_text: str) -> Tuple[Dict[str, List[FieldInfo]], Set[str]]:
    """
    Parse SDL in a very lightweight way:
    - Detect container blocks (type/input/interface/union)
    - Extract field lines as 'name(...optional args...): ReturnType'
    - Extract argument types inside (...)

    Returns:
      - fields_by_name: fieldName -> list[FieldInfo] (field can exist in multiple containers)
      - complex_type_names: set of container/type names considered non-scalar
    """
    lines = schema_text.splitlines()

    # Containers that represent complex types (not scalars/enums).
    complex_type_names: Set[str] = set()

    fields_by_name: Dict[str, List[FieldInfo]] = {}

    current_container: Optional[str] = None
    in_block = False

    # Matches a field/prop line inside a container block (best-effort):
    #   items: [String!]!
    #   delete(index: Int!): [String!]!
    #   insert(value: String!): [String!]!
    field_line_re = re.compile(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*"  # field name
        r"(?:\(([^)]*)\))?\s*"              # optional args content
        r":\s*([A-Za-z0-9_\[\]!]+)\s*$"     # return type token (best-effort, no spaces)
    )

    arg_type_re = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z0-9_\[\]!]+)")

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Container start?
        m_def = TYPE_DEF_RE.match(line)
        if m_def:
            current_container = m_def.group(2)
            complex_type_names.add(current_container)
            # Some unions can be like: union X = A | B (single-line, no braces).
            in_block = "{" in line
            continue

        # Block end
        if in_block and line.startswith("}"):
            in_block = False
            current_container = None
            continue

        if not in_block or not current_container:
            continue

        m_field = field_line_re.match(raw)
        if not m_field:
            continue

        field_name = m_field.group(1)
        args_blob = m_field.group(2)  # can be None
        return_type_raw = m_field.group(3)

        arg_type_named: List[str] = []
        if args_blob:
            for am in arg_type_re.finditer(args_blob):
                arg_type_token = am.group(2)
                arg_type_named.append(normalize_named_type(arg_type_token))

        fi = FieldInfo(
            container=current_container,
            name=field_name,
            return_type_named=normalize_named_type(return_type_raw),
            return_type_raw=return_type_raw,
            arg_type_named=tuple(arg_type_named),
        )
        fields_by_name.setdefault(field_name, []).append(fi)

    return fields_by_name, complex_type_names


def collect_all_type_references(fields_by_name: Dict[str, List[FieldInfo]]) -> List[Tuple[str, FieldInfo, str]]:
    """
    Returns a list of (ref_type_named, fieldInfo, ref_kind) references.
    We treat both:
      - field return type
      - field argument types
    as "references" to named types.
    """
    refs: List[Tuple[str, FieldInfo, str]] = []
    for _, field_list in fields_by_name.items():
        for fi in field_list:
            refs.append((fi.return_type_named, fi, "return"))
            for t in fi.arg_type_named:
                refs.append((t, fi, "arg"))
    return refs


def analyze_delete(schema_text: str, target_field_name: str) -> Tuple[dict, int]:
    fields_by_name, complex_type_names = parse_sdl(schema_text)

    if target_field_name not in fields_by_name:
        return (
            {
                "targetField": target_field_name,
                "summary": {"found": False, "anyDependent": False, "dependentCount": 0},
                "results": [],
                "error": f"Field '{target_field_name}' not found in schema.",
            },
            2,
        )

    all_refs = collect_all_type_references(fields_by_name)

    # For excluding "this field occurrence", use container+name+return_type_raw+args (best-effort identity).
    def field_identity(fi: FieldInfo) -> Tuple[str, str, str, Tuple[str, ...]]:
        return (fi.container, fi.name, fi.return_type_raw, fi.arg_type_named)

    matches = fields_by_name[target_field_name]
    results: List[dict] = []

    for fi in matches:
        ident = field_identity(fi)
        return_t = fi.return_type_named
        owning_type = fi.container

        # If the type that OWNS the field is referenced by some other field (outside the owning container),
        # then deleting the field is likely to be "dependent" in practice because it is queryable
        # through those other schema paths.
        owning_type_referenced_outside_container = any(
            (ref_type_named == owning_type and ref_fi.container != fi.container)
            for (ref_type_named, ref_fi, _kind) in all_refs
        )

        # Scalars/enums are safe only if the owning type isn't referenced elsewhere.
        if return_t not in complex_type_names:
            if owning_type_referenced_outside_container:
                results.append(
                    {
                        "container": fi.container,
                        "field": fi.name,
                        "returnType": return_t,
                        "decision": "DEPENDENT",
                        "reason": "owning type is referenced elsewhere in schema",
                    }
                )
            else:
                results.append(
                    {
                        "container": fi.container,
                        "field": fi.name,
                        "returnType": return_t,
                        "decision": "SAFE_TO_DELETE",
                        "reason": "non-complex return type and owning type not referenced elsewhere",
                    }
                )
            continue

        other_references = []
        for (ref_type_named, ref_fi, _kind) in all_refs:
            if ref_type_named != return_t:
                continue
            if field_identity(ref_fi) == ident:
                continue
            other_references.append(ref_fi)

        if other_references:
            results.append(
                {
                    "container": fi.container,
                    "field": fi.name,
                    "returnType": return_t,
                    "decision": "SAFE_TO_DELETE",
                    "reason": "return type referenced elsewhere in schema",
                }
            )
        else:
            results.append(
                {
                    "container": fi.container,
                    "field": fi.name,
                    "returnType": return_t,
                    "decision": "DEPENDENT",
                    "reason": "return type only referenced by this field occurrence",
                }
            )

    dependent_count = sum(1 for r in results if r["decision"] == "DEPENDENT")
    analysis = {
        "targetField": target_field_name,
        "summary": {"found": True, "anyDependent": dependent_count > 0, "dependentCount": dependent_count},
        "results": results,
    }
    return analysis, 0


def check_delete(schema_text: str, target_field_name: str) -> int:
    """
    Backwards-compatible mode: prints human-readable output.
    """
    analysis, exit_code = analyze_delete(schema_text, target_field_name)
    if exit_code != 0:
        print(analysis.get("error", "Unknown error"))
        return exit_code

    for r in analysis["results"]:
        print(
            f"{r['container']}.{r['field']}: returnType='{r['returnType']}' => {r['decision']} "
            f"({r['reason']})"
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check whether deleting a given GraphQL field is safe based on schema SDL."
    )
    parser.add_argument(
        "--schema",
        default="schema.txt",
        help="Path to schema text (SDL). Defaults to 'schema.txt'.",
    )
    parser.add_argument(
        "--field",
        required=True,
        help="Field name to check (e.g., insert, delete, items).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON instead of human-readable lines.",
    )
    args = parser.parse_args()

    schema_path = args.schema

    # Convenience fallback: if schema.txt doesn't exist, try schema.graphql.
    try_paths = [schema_path]
    if schema_path == "schema.txt":
        try_paths.append("schema.graphql")

    schema_text = None
    for p in try_paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                schema_text = f.read()
            break
        except FileNotFoundError:
            continue

    if schema_text is None:
        raise SystemExit(
            f"Could not read schema. Tried: {', '.join(try_paths)}"
        )

    if args.json:
        analysis, exit_code = analyze_delete(schema_text, args.field)
        print(json.dumps(analysis))
        raise SystemExit(exit_code)

    raise SystemExit(check_delete(schema_text, args.field))


if __name__ == "__main__":
    main()

