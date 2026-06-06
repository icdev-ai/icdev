# CUI // SP-CTI
"""(Intentionally minimal.)

The isolated-SQLite fixture for the task_factory / seed_validator tests is
defined module-locally inside those test files (an autouse fixture here would
leak into — and break — sibling kanban tests by redirecting ICDEV_DB_PATH).
"""
