#!/usr/bin/env python3
"""Contract tests for the strict standard-library JSON Schema subset."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json_schema as js


class JsonSchemaTest(unittest.TestCase):
    def assertValid(self, schema, value):
        self.assertEqual(js.validate(schema, value), [])

    def assertInvalid(self, schema, value, contains=None):
        errors = js.validate(schema, value)
        self.assertTrue(errors, "value unexpectedly validated")
        if contains:
            self.assertIn(contains, "\n".join(errors))

    def test_object_properties_required_and_additional_schema(self):
        schema = {
            "type": "object",
            "required": ["name", "count"],
            "properties": {
                "name": {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^[a-z]+$"},
                "count": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "additionalProperties": False,
        }
        self.assertValid(schema, {"name": "abc", "count": 2})
        self.assertInvalid(schema, {"name": "A", "count": True}, "$.count")
        self.assertInvalid(schema, {"name": "abc"}, "required")
        errors = js.validate(schema, {"name": "abc", "count": 1, "extra": 1})
        self.assertTrue(any("unknown property 'extra'" in error for error in errors))
        self.assertValid({"additionalProperties": {"type": "string"}}, {"x": "ok"})
        self.assertInvalid({"additionalProperties": {"type": "string"}}, {"x": 1}, "$.x")

    def test_type_union_enum_const_and_bool_is_not_integer(self):
        self.assertValid({"type": ["string", "null"]}, None)
        self.assertInvalid({"type": "integer"}, True)
        self.assertInvalid({"type": "boolean"}, 1)
        self.assertInvalid({"enum": [1]}, True)
        self.assertValid({"const": 1}, 1.0)
        self.assertInvalid({"const": True}, 1)
        self.assertValid({"const": {"x": [1]}}, {"x": [1]})

    def test_array_items_limits_and_json_unique(self):
        schema = {"type": "array", "items": {"type": "integer"},
                  "minItems": 1, "maxItems": 3, "uniqueItems": True}
        self.assertValid(schema, [1, 2])
        self.assertInvalid(schema, [], "fewer")
        self.assertInvalid(schema, [1, 2, 3, 4], "more")
        self.assertInvalid(schema, [1, True], "$[1]")
        self.assertInvalid(schema, [True, True], "duplicate")
        self.assertInvalid(schema, [{"x": 1}, {"x": 1}], "duplicate")

    def test_any_one_all_of(self):
        self.assertValid({"anyOf": [{"type": "string"}, {"type": "integer"}]}, 1)
        self.assertInvalid({"anyOf": [{"type": "string"}, {"type": "integer"}]}, False, "anyOf")
        self.assertValid({"oneOf": [{"type": "number"}, {"type": "string"}]}, 1)
        self.assertInvalid({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 1, "oneOf matched 2")
        self.assertValid({"allOf": [{"type": "string"}, {"minLength": 2}]}, "ok")
        self.assertInvalid({"allOf": [{"type": "string"}, {"minLength": 3}]}, "ok", "shorter")

    def test_local_definitions_ref(self):
        schema = {
            "definitions": {"oid": {"type": "string", "format": "git-sha"}},
            "type": "object",
            "properties": {"base": {"$ref": "#/definitions/oid"},
                           "again": {"$ref": "#/definitions/oid", "minLength": 40}},
        }
        value = {"base": "a" * 40, "again": "b" * 40}
        self.assertValid(schema, value)
        self.assertInvalid(schema, {"base": "a" * 7}, "$.base")
        with self.assertRaisesRegex(js.SchemaError, "unresolved"):
            js.validate({"$ref": "#/definitions/missing"}, {})
        with self.assertRaisesRegex(js.SchemaError, "local"):
            js.validate({"$ref": "https://example.com/schema.json"}, {})

    def test_formats(self):
        self.assertValid({"type": "string", "format": "date"}, "2026-11-14")
        self.assertInvalid({"type": "string", "format": "date"}, "2026-02-30")
        self.assertValid({"type": "string", "format": "date-time"}, "2026-11-14T10:20:30.123Z")
        self.assertValid({"type": "string", "format": "date-time"}, "2026-11-14T10:20:30+08:00")
        self.assertInvalid({"type": "string", "format": "date-time"}, "2026-11-14 10:20:30")
        self.assertInvalid({"type": "string", "format": "date-time"}, "2026-11-14T10:20:30")
        self.assertValid({"type": "string", "format": "sha256"}, "a" * 64)
        self.assertInvalid({"type": "string", "format": "sha256"}, "A" * 64)
        self.assertValid({"type": "string", "format": "git-sha"}, "0" * 40)
        self.assertInvalid({"type": "string", "format": "git-sha"}, "0" * 7)

    def test_unsupported_keyword_and_format_fail_even_when_unused(self):
        with self.assertRaisesRegex(js.SchemaError, "unsupported keyword.*patternProperties"):
            js.validate({"type": "object", "patternProperties": {"x": {}}}, {})
        with self.assertRaisesRegex(js.SchemaError, "unsupported keyword.*minProperties"):
            js.validate({"definitions": {"unused": {"minProperties": 1}}}, {})
        with self.assertRaisesRegex(js.SchemaError, "format is unsupported"):
            js.validate({"type": "string", "format": "email"}, "x@example.com")
        with self.assertRaisesRegex(js.SchemaError, "pattern is invalid"):
            js.validate({"pattern": "["}, "x")
        with self.assertRaisesRegex(js.SchemaError, "non-negative integer"):
            js.validate({"minItems": True}, [])

    def test_custom_path_and_load_validate(self):
        errors = js.validate({"type": "string"}, 1, path="$.candidate")
        self.assertTrue(errors[0].startswith("$.candidate:"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.json"
            path.write_text(json.dumps({"type": "array", "items": {"const": 1}}), encoding="utf-8")
            self.assertEqual(js.load_validate(path, [1, 1]), [])
            self.assertTrue(js.load_validate(path, [1, 2]))


if __name__ == "__main__":
    unittest.main()
