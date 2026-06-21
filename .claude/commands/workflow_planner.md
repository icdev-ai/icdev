---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: Decomposes complex goals into ordered task graphs with dependency tracking
  and effort estimates.
name: workflow-planner
tags:
- planning
- task-decomposition
- project-management
---
# Workflow Planner

CUI // SP-CTI

## Overview

Decomposes complex goals into ordered task graphs with dependency tracking and effort estimates.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** openai
- **Original URL:** local://official-seed/openai/openai-workflow-planner
- **Import Date:** 2026-06-14T15:45:42.808579+00:00
- **SHA-256:** 3ca4857e827cadc1e0ad2f7788dc59db2ef78bc6bd8876ea527c37181cb07743
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Workflow Planner

CUI // SP-CTI

## Overview

Decomposes complex goals into ordered task graphs with dependency tracking and effort estimates.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** openai
- **Source:** OpenClaw Community (SkillHub)
- **Author:** openai
- **Original Version:** 1.1.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "functions": [
    {
      "name": "decompose_goal",
      "description": "Decompose a high-level goal into an ordered task graph with dependencies, effort estimates, and acceptance criteria.",
      "parameters": {
        "type": "object",
        "properties": {
          "goal": {"type": "string", "description": "The high-level goal to decompose"},
          "context": {"type": "string", "description": "Project context, constraints, and available resources"},
          "max_depth": {"type": "integer", "description": "Maximum decomposition depth (default: 3)"},
          "effort_unit": {"type": "string", "enum": ["hours", "days", "story_points"], "description": "Unit for effort estimates"}
        },
        "required": ["goal"]
      }
    },
    {
      "name": "identify_dependencies",
      "description": "Identify task dependencies and produce a critical path analysis.",
      "parameters": {
        "type": "object",
        "properties": {
          "tasks": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "effort": {"type": "number"}
              }
            },
            "description": "List of tasks to analyze"
          }
        },
        "required": ["tasks"]
      }
    }
  ]
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

