# CUI // SP-CTI
# Phase DDS — DDC Data Science: Explore, Query Sandbox, Quality Rules

**Status:** COMPLETE
**Date:** 2026-05-08
**Routes:** `/data/explore`, `/data/query`, `/data/quality`
**Project key:** `dds`

## Summary

Three data-science tabs added to the Data Design Canvas (DDC) at `/data/`:
1. **Data Explorer** (`/data/explore`) — classification-aware database profiler
2. **SQL Sandbox** (`/data/query`) — read-only SQL editor with audit trail
3. **Quality Rules** (`/data/quality`) — completeness/uniqueness/range/pattern/freshness checks

## Epic 1: Data Explorer

Module: `tools/data_canvas/data_profiler.py`  
Tables: `dd_explore_profiles`, `dd_explore_sessions`  
Max rows: 50,000. Backends: sqlite, postgresql, duckdb.

## Epic 2: SQL Sandbox

Module: `tools/data_canvas/query_sandbox.py`  
Table: `dd_query_history`  
SELECT/WITH/EXPLAIN only. Hard limit: 1,000 rows.

## Epic 3: Quality Rules

Module: `tools/data_canvas/quality_engine.py`  
Tables: `dd_quality_rules`, `dd_quality_runs`  
Check types: completeness, uniqueness, range, pattern, freshness.  
Feeds DDC assessment via DDC-QUA-001 when quality_score < 70.

## Infrastructure

- Constants: DS_CHECK_TYPES, DS_DB_TYPES, DS_PROFILER_MAX_ROWS, DS_QUERY_MAX_ROWS in constants.py
- Config: args/data_canvas_config.yaml explore/query/quality sections
- DB: Tables in tools/data_canvas/db/init_db.py SCHEMA
- icdev/ mirrors: templates mirrored to icdev/tools/dashboard/templates/data_canvas/
- Nav: Explore/Query/Quality links in index.html
