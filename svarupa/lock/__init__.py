"""The architecture lockfile: the committed, diffable fingerprint of a system."""

from svarupa.identity import collision_check
from svarupa.lock.build import LockResult, build_lock, lock_records
from svarupa.lock.diff import ArchitectureDelta, diff, drift_check
from svarupa.lock.grammar import (
    SCHEMA_MAJOR,
    SCHEMA_MINOR,
    Header,
    Lockfile,
    Record,
    SchemaMismatch,
    dep_record,
    escape_field,
    module_record,
    unescape_field,
)

# Lives in the artifact directory but is the one file there meant to be
# committed. `emit` clears that directory to a declared set of names, and this
# name is deliberately not in it, which `tests/test_lock.py` asserts rather
# than leaving to luck.
LOCK_NAME = "architecture.lock"

__all__ = [
    "LOCK_NAME",
    "SCHEMA_MAJOR",
    "SCHEMA_MINOR",
    "ArchitectureDelta",
    "Header",
    "LockResult",
    "Lockfile",
    "Record",
    "SchemaMismatch",
    "build_lock",
    "collision_check",
    "dep_record",
    "diff",
    "drift_check",
    "escape_field",
    "lock_records",
    "module_record",
    "unescape_field",
]
