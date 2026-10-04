"""Generate TypeScript aliases from the subset of OpenAPI schemas used here."""

import json
from pathlib import Path


def ts_type(schema):
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for union in ("oneOf", "anyOf"):
        if union in schema:
            return " | ".join(ts_type(item) for item in schema[union])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    kind = schema.get("type")
    if kind == "null":
        return "null"
    if kind == "string":
        return "string"
    if kind in ("integer", "number"):
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "array":
        return f"Array<{ts_type(schema['items'])}>"
    if kind == "object":
        if "properties" not in schema:
            return "Record<string, unknown>"
        required = schema.get("required", [])
        fields = []
        for name, value in schema["properties"].items():
            optional = "" if name in required else "?"
            fields.append(f"  {json.dumps(name)}{optional}: {ts_type(value)};")
        return "{\n" + "\n".join(fields) + "\n}"
    if set(schema) <= {"title", "description", "default", "examples"}:
        return "unknown"
    raise ValueError(f"Unsupported schema shape: {schema}")


if __name__ == "__main__":
    directory = Path(__file__).resolve().parent
    specification = json.loads((directory / "openapi.json").read_text())
    header = (
        "// GENERATED FROM openapi.json. Change the OpenAPI source, then regenerate; "
        "do not edit these definitions independently.\n"
        "// Runtime validation is still required. Monetary numbers are integer HKD cents.\n\n"
    )
    definitions = "\n\n".join(
        f"export type {name} = {ts_type(schema)};"
        for name, schema in specification["components"]["schemas"].items()
    )
    (directory / "types.ts").write_text(header + definitions + "\n")
    print(f"Generated {len(specification['components']['schemas'])} TypeScript types.")
