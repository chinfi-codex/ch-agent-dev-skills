#!/usr/bin/env python3
"""Small, strict JSON Schema validator using only the Python standard library.

Public API:
    validate(schema, instance, path="$") -> list[str]
        Return human-readable instance validation errors.  A malformed schema,
        an unsupported keyword/format, or a non-local reference raises
        SchemaError instead of being silently ignored.
    load_validate(schema_path, instance) -> list[str]
        Load a UTF-8 JSON schema from *schema_path* and validate *instance*.

Supported keywords: type, properties, required, additionalProperties, items,
enum, const, pattern, minLength, maxLength, minimum, maximum, minItems,
maxItems, uniqueItems, anyOf, oneOf, allOf, $ref (local ``#/definitions/...``),
and format (date, date-time, sha256, git-sha).  ``$schema``, ``$id``, ``title``,
``description``, ``default``, ``examples`` and ``definitions`` are accepted as
schema metadata.  This is deliberately not a general JSON Schema
implementation; consumers must not add keywords without extending this module.
"""
from __future__ import annotations

import datetime as _datetime
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple


class SchemaError(ValueError):
    """The schema itself is malformed or uses unsupported functionality."""


_KEYWORDS = {
    "$schema", "$id", "$ref", "definitions", "title", "description", "default", "examples",
    "type", "properties", "required", "additionalProperties", "items", "enum", "const",
    "pattern", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems",
    "uniqueItems", "anyOf", "oneOf", "allOf", "format",
}
_FORMATS = {"date", "date-time", "sha256", "git-sha"}
_TYPES = {"null", "boolean", "object", "array", "number", "integer", "string"}
_MISSING = object()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json_equal(left: Any, right: Any) -> bool:
    """JSON equality: bool is never equal to 0/1, unlike Python equality."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if left is None or right is None:
        return left is None and right is None
    if _is_number(left) and _is_number(right):
        return left == right
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return (set(left) == set(right)
                and all(_json_equal(left[key], right[key]) for key in left))
    return type(left) is type(right) and left == right


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "number":
        return _is_number(value)
    if expected == "integer":
        return _is_int(value) or (isinstance(value, float) and value.is_integer())
    if expected == "string":
        return isinstance(value, str)
    raise SchemaError("unknown JSON type: %s" % expected)


def _non_negative_int(schema: Dict[str, Any], key: str, where: str) -> None:
    if key in schema and not (_is_int(schema[key]) and schema[key] >= 0):
        raise SchemaError("%s.%s must be a non-negative integer" % (where, key))


def _check_schema(schema: Any, where: str = "$schema") -> None:
    """Eagerly check the complete schema, including unused definitions/branches."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise SchemaError("%s must be an object or boolean" % where)
    unknown = sorted(set(schema) - _KEYWORDS)
    if unknown:
        raise SchemaError("%s uses unsupported keyword(s): %s" % (where, ", ".join(unknown)))

    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise SchemaError("%s.$ref must be a local reference" % where)
        if ref != "#" and not re.match(r"^#/definitions/[^/]+$", ref):
            raise SchemaError("%s.$ref only supports # or #/definitions/<name>" % where)

    if "type" in schema:
        kind = schema["type"]
        kinds = kind if isinstance(kind, list) else [kind]
        if not kinds or not all(isinstance(item, str) and item in _TYPES for item in kinds):
            raise SchemaError("%s.type contains an unsupported type" % where)
        if isinstance(kind, list) and len(set(kinds)) != len(kinds):
            raise SchemaError("%s.type contains duplicate types" % where)

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise SchemaError("%s.properties must be an object" % where)
    for name, child in properties.items():
        if not isinstance(name, str):
            raise SchemaError("%s.properties keys must be strings" % where)
        _check_schema(child, "%s.properties.%s" % (where, name))

    if "required" in schema:
        required = schema["required"]
        if (not isinstance(required, list)
                or not all(isinstance(item, str) and item for item in required)):
            raise SchemaError("%s.required must be an array of non-empty strings" % where)
        if len(set(required)) != len(required):
            raise SchemaError("%s.required contains duplicates" % where)

    additional = schema.get("additionalProperties", _MISSING)
    if additional is not _MISSING and not isinstance(additional, (bool, dict)):
        raise SchemaError("%s.additionalProperties must be boolean or a schema" % where)
    if isinstance(additional, dict):
        _check_schema(additional, "%s.additionalProperties" % where)

    if "items" in schema:
        _check_schema(schema["items"], "%s.items" % where)

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise SchemaError("%s.enum must be a non-empty array" % where)
        for index, value in enumerate(enum):
            if any(_json_equal(value, other) for other in enum[:index]):
                raise SchemaError("%s.enum contains duplicate values" % where)

    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise SchemaError("%s.pattern must be a string" % where)
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise SchemaError("%s.pattern is invalid: %s" % (where, exc))

    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        _non_negative_int(schema, key, where)
    for key in ("minimum", "maximum"):
        if key in schema and not _is_number(schema[key]):
            raise SchemaError("%s.%s must be a number" % (where, key))
    for lower, upper in (("minLength", "maxLength"), ("minItems", "maxItems"),
                         ("minimum", "maximum")):
        if lower in schema and upper in schema and schema[lower] > schema[upper]:
            raise SchemaError("%s.%s exceeds %s" % (where, lower, upper))
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise SchemaError("%s.uniqueItems must be boolean" % where)

    for keyword in ("anyOf", "oneOf", "allOf"):
        if keyword not in schema:
            continue
        children = schema[keyword]
        if not isinstance(children, list) or not children:
            raise SchemaError("%s.%s must be a non-empty array of schemas" % (where, keyword))
        for index, child in enumerate(children):
            _check_schema(child, "%s.%s[%d]" % (where, keyword, index))

    if "format" in schema:
        value = schema["format"]
        if not isinstance(value, str) or value not in _FORMATS:
            raise SchemaError("%s.format is unsupported: %r" % (where, value))

    definitions = schema.get("definitions", {})
    if not isinstance(definitions, dict):
        raise SchemaError("%s.definitions must be an object" % where)
    for name, child in definitions.items():
        if not isinstance(name, str) or not name:
            raise SchemaError("%s.definitions keys must be non-empty strings" % where)
        _check_schema(child, "%s.definitions.%s" % (where, name))


def _resolve(root: Dict[str, Any], ref: str) -> Any:
    if ref == "#":
        return root
    name = ref[len("#/definitions/"):]
    definitions = root.get("definitions", {})
    if name not in definitions:
        raise SchemaError("unresolved local $ref: %s" % ref)
    return definitions[name]


def _format_valid(value: str, format_name: str) -> bool:
    if format_name == "sha256":
        return re.fullmatch(r"[0-9a-f]{64}", value) is not None
    if format_name == "git-sha":
        return re.fullmatch(r"[0-9a-f]{40}", value) is not None
    if format_name == "date":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            return False
        try:
            _datetime.date.fromisoformat(value)
            return True
        except ValueError:
            return False
    if format_name == "date-time":
        # RFC3339 profile: explicit offset, full date and time, T/t and Z/z allowed.
        if re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})",
                value) is None:
            return False
        normalized = value.replace("t", "T").replace("z", "Z").replace("Z", "+00:00")
        try:
            parsed = _datetime.datetime.fromisoformat(normalized)
            return parsed.tzinfo is not None and parsed.utcoffset() is not None
        except ValueError:
            return False
    raise SchemaError("unsupported format: %s" % format_name)


def _child_path(path: str, key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
        return "%s.%s" % (path, key)
    return "%s[%s]" % (path, json.dumps(key, ensure_ascii=False))


def _validate(schema: Any, instance: Any, path: str, root: Dict[str, Any],
              active_refs: Set[Tuple[int, int, str]]) -> List[str]:
    if schema is True:
        return []
    if schema is False:
        return ["%s: forbidden by false schema" % path]
    errors: List[str] = []

    if "$ref" in schema:
        marker = (id(schema), id(instance), schema["$ref"])
        if marker in active_refs:
            raise SchemaError("cyclic $ref without validation progress at %s" % path)
        active_refs.add(marker)
        try:
            errors.extend(_validate(_resolve(root, schema["$ref"]), instance, path, root, active_refs))
        finally:
            active_refs.remove(marker)
        # JSON Schema applies sibling keywords as well; do not return early.

    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_matches(instance, kind) for kind in kinds):
            errors.append("%s: expected type %s, got %s" %
                          (path, "/".join(kinds), type(instance).__name__))
            return errors

    if "enum" in schema and not any(_json_equal(instance, item) for item in schema["enum"]):
        errors.append("%s: value is not in enum" % path)
    if "const" in schema and not _json_equal(instance, schema["const"]):
        errors.append("%s: value does not match const" % path)

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required property %r" % (path, key))
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors.extend(_validate(properties[key], value, _child_path(path, key), root, active_refs))
            elif additional is False:
                errors.append("%s: unknown property %r" % (path, key))
            elif additional is not True:
                errors.extend(_validate(additional, value, _child_path(path, key), root, active_refs))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: string is shorter than %d" % (path, schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append("%s: string is longer than %d" % (path, schema["maxLength"]))
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append("%s: string does not match pattern %s" % (path, schema["pattern"]))
        if "format" in schema and not _format_valid(instance, schema["format"]):
            errors.append("%s: invalid %s" % (path, schema["format"]))

    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: number is below minimum %s" % (path, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: number is above maximum %s" % (path, schema["maximum"]))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("%s: array has fewer than %d items" % (path, schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append("%s: array has more than %d items" % (path, schema["maxItems"]))
        if schema.get("uniqueItems"):
            for index, value in enumerate(instance):
                if any(_json_equal(value, other) for other in instance[:index]):
                    errors.append("%s[%d]: duplicate array item" % (path, index))
        if "items" in schema:
            for index, value in enumerate(instance):
                errors.extend(_validate(schema["items"], value, "%s[%d]" % (path, index), root, active_refs))

    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        branches = schema[keyword]
        results = [_validate(branch, instance, path, root, active_refs) for branch in branches]
        if keyword == "allOf":
            for branch_errors in results:
                errors.extend(branch_errors)
        elif keyword == "anyOf":
            if all(results):
                errors.append("%s: no anyOf branch matched" % path)
        else:
            matches = sum(not result for result in results)
            if matches != 1:
                errors.append("%s: oneOf matched %d branches, expected exactly one" % (path, matches))
    return errors


def validate(schema: Any, instance: Any, path: str = "$") -> List[str]:
    """Validate *instance* and return errors; raise SchemaError for bad schemas."""
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    _check_schema(schema)
    if not isinstance(schema, (dict, bool)):
        raise SchemaError("root schema must be an object or boolean")
    root = schema if isinstance(schema, dict) else {}
    return _validate(schema, instance, path, root, set())


def load_validate(schema_path: Any, instance: Any) -> List[str]:
    """Load *schema_path* as UTF-8 JSON and return ``validate`` results."""
    path = Path(schema_path)
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SchemaError("cannot load schema %s: %s" % (path, exc))
    return validate(schema, instance)
