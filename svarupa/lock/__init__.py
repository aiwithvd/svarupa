"""The architecture lockfile: the committed, diffable fingerprint of a system."""

from svarupa.lock.grammar import (
    SCHEMA_MAJOR,
    SCHEMA_MINOR,
    Header,
    Lockfile,
    Record,
    SchemaMismatch,
    collision_check,
    dep_record,
    escape_field,
    module_record,
    unescape_field,
)

__all__ = [
    "SCHEMA_MAJOR",
    "SCHEMA_MINOR",
    "Header",
    "Lockfile",
    "Record",
    "SchemaMismatch",
    "collision_check",
    "dep_record",
    "escape_field",
    "module_record",
    "unescape_field",
]
