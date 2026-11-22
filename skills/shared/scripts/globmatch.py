#!/usr/bin/env python3
"""Shared glob matching with the exact semantics frozen at process_check.

Spec-tree projections (spec_tree.py), the strict declared-scope check
(process_check.py) and any future consumer must agree on one glob language;
this module is that single definition.  Moved verbatim from process_check.py
so matching at split time cannot drift from matching at implementation time.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any, Optional


def glob_to_regex(pattern: str) -> re.Pattern:
    index, out = 0, []
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return re.compile("".join(out) + r"\Z")


def match_any(path: str, patterns):
    for pattern in patterns or []:
        pattern = str(pattern).strip()
        if not pattern:
            continue
        if glob_to_regex(pattern).match(path) or (pattern.endswith("/") and path.startswith(pattern)) or fnmatch.fnmatch(path, pattern):
            return pattern
    return None


def is_concrete_path(pattern: str) -> bool:
    """True when a declared-scope entry names one exact path, not a glob."""
    value = str(pattern).strip()
    return bool(value) and not any(char in value for char in "*?[") and not value.endswith("/")
