# CUI // SP-CTI
"""Plan step 020 (dt-odc-twin-01) migration tests — delegates to test_migration_028.

The ODC twin implementation plan labels this step '020', but the DB migration
is numbered 028 (next available in the sequential migration table).
All substantive tests live in test_migration_028.py.
"""

from tests.test_migration_028 import (  # noqa: F401
    test_up_creates_table,
    test_up_idempotent,
    test_table_has_required_columns,
    test_indexes_created,
    test_state_check_constraint,
    test_insert_and_select,
    conn,
)
