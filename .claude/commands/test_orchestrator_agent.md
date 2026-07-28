---
allowed-tools:
- Read
- Grep
- Glob
context: fork
description: AutoGen multi-agent orchestrator that generates, runs, and iteratively
  fixes failing tests.
name: test-orchestrator-agent
tags:
- testing
- autogen
- tdd
- ci
---
# Test Orchestrator Agent

CUI // SP-CTI

> **⚠️ Reference seed — NOT an executable capability (oss2-fix-03 / D3).**
> This card embeds an AutoGen agent definition, but **AutoGen is not an ICDEV
> dependency and nothing executes it** — `autogen` is imported nowhere in `tools/`.
> It is retained as a design reference from the SkillHub seed, not a wired agent.
> For the capability it describes, use ICDEV's actual implementation: the `/test`
> command (`.claude/commands/test.md`) or the test orchestrator
> (`tools/testing/test_orchestrator.py`).

## Overview

AutoGen multi-agent orchestrator that generates, runs, and iteratively fixes failing tests.

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** microsoft
- **Original URL:** local://official-seed/autogen/autogen-test-orchestrator
- **Import Date:** 2026-06-14T15:45:43.014265+00:00
- **SHA-256:** 5997c65f52b9affdfd16c9e08177e1db82c63a78c35e507bd35ce63a6fd43562
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** 0.3
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

# Test Orchestrator Agent

CUI // SP-CTI

## Overview

AutoGen multi-agent orchestrator that generates, runs, and iteratively fixes failing tests.

## Provenance

- **Enhanced by:** ICDEV™ (Innovation + Creative + Research engines)
- **Original Author:** microsoft
- **Source:** OpenClaw Community (SkillHub)
- **Author:** microsoft
- **Original Version:** 0.4.0
- **Compatibility Score:** 94/100
- **Auto-Adaptations:** 3

## Instructions

{
  "name": "TestOrchestratorAgent",
  "description": "Orchestrates a multi-agent test generation and verification workflow. Coordinates a TestWriterAgent and TestRunnerAgent to achieve passing tests through iteration.",
  "system_message": "You are a test orchestration agent. Your job is to:\n\n1. Receive code to test\n2. Instruct the TestWriterAgent to generate a comprehensive test suite\n3. Instruct the TestRunnerAgent to execute the tests\n4. Analyze failures and instruct the TestWriterAgent to fix them\n5. Repeat until all tests pass OR max_iterations is reached\n6. Report the final test coverage and any remaining failures\n\nAlways require at least: happy path, one error case, one edge case per function.\nTerminate with TESTS_COMPLETE when all tests pass.",
  "human_input_mode": "NEVER",
  "max_consecutive_auto_reply": 12,
  "code_execution_config": {
    "work_dir": ".tmp/test_workspace",
    "use_docker": false,
    "timeout": 60,
    "last_n_messages": 3
  }
}


## Enrichment (ICDEV™ Intelligence)

*Auto-generated on 2026-06-14 by Innovation + Creative + Research engines*

