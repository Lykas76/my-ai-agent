"""Small strict JSON Schema subset; unknown arguments are rejected."""
import math


def validate(value, schema, depth=0):
    if depth > 8 or not isinstance(schema, dict):
        raise ValueError("Invalid tool arguments")
    kind = schema.get("type")
    valid = {"object": type(value) is dict, "string": type(value) is str,
             "integer": type(value) is int, "number": type(value) in (int, float),
             "boolean": type(value) is bool}.get(kind, False)
    if not valid:
        raise ValueError("Invalid tool arguments")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(value) - set(properties) or set(schema.get("required", ())) - set(value):
            raise ValueError("Invalid tool arguments")
        for key, item in value.items():
            validate(item, properties[key], depth + 1)
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4000):
            raise ValueError("Invalid tool arguments")
    elif kind in ("integer", "number"):
        if not math.isfinite(value) or not schema.get("minimum", -1e15) <= value <= schema.get("maximum", 1e15):
            raise ValueError("Invalid tool arguments")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Invalid tool arguments")


def object_schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}
