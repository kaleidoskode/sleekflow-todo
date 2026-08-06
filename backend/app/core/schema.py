"""OpenAPI schema customisation.

Swagger UI renders ``anyOf: [string, null]`` (Pydantic's default for
nullable fields in OpenAPI 3.1) as a type-selector dropdown that hides the
field description.  Post-process the generated schema to flatten these
back to the simpler ``{type: X, nullable: true}`` form.
"""

from typing import Any


def _flatten_anyof(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively flatten ``anyOf`` nullables in a schema subtree."""
    if not isinstance(schema, dict):
        return schema

    # {anyOf: [{type: T, ...}, {type: "null"}]}  →  {type: T, ..., nullable: true}
    anyof = schema.get("anyOf")
    if isinstance(anyof, list) and len(anyof) == 2 and anyof[1] == {"type": "null"}:
        result: dict[str, Any] = {**anyof[0], "nullable": True}
        for key in ("description", "examples"):
            if key in schema and key not in result:
                result[key] = schema[key]
        schema = result

    # Recurse into nested schemas (properties, items, etc.)
    for key in list(schema):
        value = schema[key]
        if isinstance(value, dict):
            schema[key] = _flatten_anyof(value)
        elif isinstance(value, list):
            schema[key] = [_flatten_anyof(v) if isinstance(v, dict) else v for v in value]
    return schema


def flatten_nullable_schemas(openapi: dict[str, Any]) -> dict[str, Any]:
    """Post-process an OpenAPI doc so Swagger UI shows nullable field descriptions."""
    schemas = openapi.get("components", {}).get("schemas", {})
    for name in schemas:
        schemas[name] = _flatten_anyof(schemas[name])
    return openapi
