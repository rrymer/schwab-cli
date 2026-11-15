"""Select arbitrary fields from Schwab's JSON records for CSV output.

Field names are case-sensitive and are not restricted to a hardcoded schema.
"""
from __future__ import annotations

import csv
import json
import sys
from collections.abc import Iterable, Mapping
from typing import Any


def parse_fields(specification: str) -> tuple[str, ...]:
    """Parse a comma-separated list, preserving the requested column order."""
    fields = tuple(name.strip() for name in specification.split(","))
    if not fields or any(not name for name in fields):
        raise ValueError("--fields requires nonempty comma-separated field names")
    if len(set(fields)) != len(fields):
        raise ValueError("--fields cannot contain duplicate field names")
    return fields


def print_fields(rows: Iterable[Mapping[str, Any]], fields: tuple[str, ...]) -> None:
    """Write a CSV header and one row per record without modifying source JSON.

    Missing or null properties produce empty cells. Complex values are encoded
    as JSON in their CSV cells. Names match keys in the actual API response.
    """
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(fields)
    for row in rows:
        values = []
        for field in fields:
            value = row.get(field)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, separators=(",", ":"))
            values.append(value)
        writer.writerow(values)
