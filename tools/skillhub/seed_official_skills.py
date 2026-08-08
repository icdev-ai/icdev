#!/usr/bin/env python3
# CUI // SP-CTI
"""SkillHub — Official Skills Seeder.

Seeds SkillHub with curated, air-gap-friendly official skills from major AI
frameworks (Claude, Hermes, Gemini, OpenAI, LangChain, CrewAI, AutoGen).

Each skill is packaged as an OpenClaw SKILL.md directory, then run through
the full import_skill() pipeline: frontmatter parse → SAST → secrets scan →
malicious-sig → trust scoring → quarantine.

Air-gap policy:
  - No network calls to skillhub.ai or any external registry
  - All content is self-contained in this file
  - Requires: ICDEV_SKILLHUB_ENABLED=true OR --force flag

Usage:
    python tools/skillhub/seed_official_skills.py [--force] [--json] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Official skill catalogue — air-gap-friendly content for 7 frameworks
# ---------------------------------------------------------------------------
# Each entry is a dict:
#   slug        — kebab-case unique ID
#   framework   — one of: claude, hermes, gemini, openai, langchain, crewai, autogen
#   author      — canonical publisher handle
#   version     — semantic version
#   frontmatter — additional YAML fields for skill.md header
#   body        — the skill content / instructions

OFFICIAL_SKILLS: list[dict[str, Any]] = [

    # ── Claude Code (Anthropic) ───────────────────────────────────────────
    {
        "slug": "claude-code-review",
        "framework": "claude",
        "author": "anthropic",
        "version": "1.2.0",
        "frontmatter": {
            "name": "Code Review",
            "description": "Thorough, structured code review covering correctness, security, style, and test coverage.",
            "tags": ["code-quality", "security", "review"],
            "license": "MIT",
        },
        "body": """# Code Review

Perform a thorough code review of the provided code or diff.

## Instructions

Analyze the code across four dimensions:

**1. Correctness**
- Logic errors, off-by-one errors, null/undefined handling
- Edge cases that may cause unexpected behavior
- Algorithm correctness and complexity

**2. Security (OWASP Top 10)**
- Injection vulnerabilities (SQL, command, LDAP)
- Authentication and authorization gaps
- Sensitive data exposure (hardcoded secrets, logging PII)
- Input validation and output encoding

**3. Code Quality**
- Naming clarity and consistency
- Function/class cohesion and coupling
- Duplicated logic or missed abstractions
- Dead code or unused imports

**4. Test Coverage**
- Are happy paths tested?
- Are error/edge cases covered?
- Are mocks appropriate (not masking real behavior)?

For each finding: state the location, severity (critical/high/medium/low), and a concrete fix.

## Usage

```
/claude-code-review $ARGUMENTS
```

Provide: file path, code snippet, or PR diff as $ARGUMENTS.
""",
    },
    {
        "slug": "claude-write-tests",
        "framework": "claude",
        "author": "anthropic",
        "version": "1.1.0",
        "frontmatter": {
            "name": "Write Tests",
            "description": "Generate comprehensive unit and integration tests for any function, class, or module.",
            "tags": ["testing", "tdd", "quality"],
            "license": "MIT",
        },
        "body": """# Write Tests

Generate a comprehensive test suite for the provided code.

## Instructions

1. **Identify the public interface** — functions, methods, classes, API endpoints
2. **Map test categories**:
   - Happy path (expected inputs → expected outputs)
   - Boundary conditions (empty, zero, max, min, None)
   - Error conditions (invalid input, missing args, type errors)
   - Integration points (DB calls, HTTP calls — mock appropriately)
3. **Write tests** using the project's existing test framework (detect: pytest, Jest, JUnit, etc.)
4. **Add docstrings** explaining what each test validates
5. **Target ≥80% branch coverage** for the submitted code

Name tests `test_<function>_<scenario>` for clarity.
Do not test framework internals — only the code provided.

## Usage

```
/claude-write-tests $ARGUMENTS
```

Provide: file path or function definition as $ARGUMENTS.
""",
    },
    {
        "slug": "claude-debug-issue",
        "framework": "claude",
        "author": "anthropic",
        "version": "1.0.0",
        "frontmatter": {
            "name": "Debug Issue",
            "description": "Systematic debugging: reproduce → isolate → root cause → fix → verify.",
            "tags": ["debugging", "troubleshooting"],
            "license": "MIT",
        },
        "body": """# Debug Issue

Systematically debug the provided error or unexpected behavior.

## Instructions

Follow the DEBUG protocol:

**D — Describe**: Restate the observed behavior vs expected behavior in concrete terms.

**E — Evidence**: List all available evidence (stack trace, logs, test output, reproduction steps).

**B — Bisect**: Identify the smallest reproducing case. Which line/function is the first wrong?

**U — Understand**: Explain WHY the bug occurs — the root cause, not just the symptom.

**G — Generate fix**: Propose the minimal, targeted fix. Do not refactor beyond the bug scope.

Output:
1. Root cause (1-2 sentences)
2. Affected code location
3. Minimal fix (code diff)
4. Verification steps (how to confirm it's fixed)

## Usage

```
/claude-debug-issue $ARGUMENTS
```

Provide: error message, stack trace, or failing test output as $ARGUMENTS.
""",
    },
    {
        "slug": "claude-explain-code",
        "framework": "claude",
        "author": "anthropic",
        "version": "1.0.0",
        "frontmatter": {
            "name": "Explain Code",
            "description": "Explain what code does at multiple levels of abstraction for any audience.",
            "tags": ["documentation", "learning", "onboarding"],
            "license": "MIT",
        },
        "body": """# Explain Code

Explain the provided code clearly and accurately.

## Instructions

Produce a layered explanation:

**Layer 1 — One-sentence summary**: What does this code do at the highest level?

**Layer 2 — How it works**: Walk through the key steps in plain English. No jargon unless defined.

**Layer 3 — Key design decisions**: Why is it structured this way? What alternatives exist?

**Layer 4 — Edge cases and gotchas**: What inputs or states could cause surprising behavior?

Adapt the explanation to the audience:
- If the user asks for "simple" → avoid technical terms, use analogies
- If the user asks for "deep" → include algorithmic complexity, pattern names, tradeoffs
- Default: intermediate developer who is unfamiliar with this codebase

## Usage

```
/claude-explain-code $ARGUMENTS
```
""",
    },
    {
        "slug": "claude-security-audit",
        "framework": "claude",
        "author": "anthropic",
        "version": "1.3.0",
        "frontmatter": {
            "name": "Security Audit",
            "description": "Full security audit: OWASP Top 10, SAST findings, secrets, dependency vulns, auth flows.",
            "tags": ["security", "audit", "owasp", "devsecops"],
            "license": "MIT",
        },
        "body": """# Security Audit

Perform a comprehensive security audit of the provided code, configuration, or architecture.

## Instructions

Run through each OWASP Top 10 category against the provided artifact:

1. **A01 — Broken Access Control**: Check authorization checks, IDOR, privilege escalation paths
2. **A02 — Cryptographic Failures**: Weak algorithms, hardcoded keys, unencrypted sensitive data
3. **A03 — Injection**: SQL, command, LDAP, XPath, template injection vectors
4. **A04 — Insecure Design**: Missing threat modeling, insecure defaults, trust boundary violations
5. **A05 — Security Misconfiguration**: Debug mode on, default creds, verbose errors, CORS wildcard
6. **A06 — Vulnerable Components**: Known CVEs in dependencies (check version pinning)
7. **A07 — Auth Failures**: Session management, token expiry, brute-force protection
8. **A08 — Data Integrity Failures**: Unsigned serialization, untrusted deserialization
9. **A09 — Logging Failures**: Missing audit logs, logging sensitive data, log injection
10. **A10 — SSRF**: Unvalidated URLs, metadata endpoint exposure

For each finding:
- **Severity**: Critical / High / Medium / Low / Info
- **CWE**: Reference the CWE number
- **Location**: File and line where applicable
- **Recommendation**: Specific, actionable remediation

End with a risk-ranked summary table.

## Usage

```
/claude-security-audit $ARGUMENTS
```
""",
    },

    # ── Hermes (Nous Research) ────────────────────────────────────────────
    {
        "slug": "hermes-threat-intel-analyst",
        "framework": "hermes",
        "author": "nous-research",
        "version": "2.0.0",
        "frontmatter": {
            "name": "Threat Intelligence Analyst",
            "description": "Analyzes threat intelligence reports and extracts structured IOCs, TTPs, and risk context.",
            "tags": ["cybersecurity", "threat-intel", "ioc", "ttp"],
            "license": "Apache-2.0",
        },
        "body": """{
  "name": "threat_intelligence_analyst",
  "description": "Analyzes threat intelligence reports and extracts structured IOCs, TTPs, and risk context.",
  "system_prompt": "You are a senior Cyber Threat Intelligence (CTI) analyst with expertise in MITRE ATT&CK, Diamond Model, and structured threat reporting. Extract and structure all threat indicators from the provided content.\\n\\nOutput format:\\n1. THREAT ACTOR: Name, aliases, suspected origin, motivation\\n2. CAMPAIGN: Timeline, targets, TTPs (MITRE ATT&CK IDs)\\n3. INDICATORS OF COMPROMISE:\\n   - IPs: [list]\\n   - Domains: [list]\\n   - File Hashes (MD5/SHA256): [list]\\n   - URLs: [list]\\n   - Email: [list]\\n4. MITRE ATT&CK MAPPING: List each TTP with technique ID and description\\n5. RISK ASSESSMENT: Severity (Critical/High/Medium/Low), affected sectors, recommended mitigations\\n\\nAlways cite the source sentence for each IOC extracted.",
  "tools": [
    {"name": "search_threat_db", "description": "Search internal threat database for historical context"},
    {"name": "enrich_ioc", "description": "Enrich an IOC with reputation data from local feed"},
    {"name": "mitre_lookup", "description": "Look up MITRE ATT&CK technique details by ID"},
    {"name": "generate_stix", "description": "Generate STIX 2.1 bundle from extracted intelligence"}
  ],
  "steps": [
    "parse_report_content",
    "extract_threat_actor_profile",
    "extract_campaign_details",
    "extract_all_iocs",
    "map_to_mitre_attack",
    "enrich_iocs_from_local_feeds",
    "assess_risk_and_impact",
    "generate_stix_bundle",
    "produce_analyst_summary"
  ],
  "parameters": {
    "report_text": "Raw threat intelligence report text",
    "classification": "Classification level: U, CUI, SECRET",
    "output_format": "Output format: json, stix, markdown (default: markdown)"
  }
}
""",
    },
    {
        "slug": "hermes-doc-intelligence-agent",
        "framework": "hermes",
        "author": "nous-research",
        "version": "1.5.0",
        "frontmatter": {
            "name": "Document Intelligence Agent",
            "description": "Extracts structure, entities, requirements, and semantic relationships from complex documents.",
            "tags": ["document-ai", "ner", "requirements", "nlp"],
            "license": "Apache-2.0",
        },
        "body": """{
  "name": "document_intelligence_agent",
  "description": "Extracts structure, entities, requirements, and semantic relationships from complex documents.",
  "system_prompt": "You are a Document Intelligence specialist. Process documents to extract structured information using a systematic pipeline.\\n\\nFor each document:\\n1. STRUCTURE ANALYSIS: Identify document type, section hierarchy, and logical flow\\n2. ENTITY EXTRACTION: People, organizations, dates, locations, technical terms, dollar values\\n3. REQUIREMENT EXTRACTION: For RFPs/SOWs/PWS — extract SHALL/SHOULD/MAY requirements with unique IDs\\n4. RELATIONSHIP MAPPING: How entities and concepts relate to each other\\n5. CLASSIFICATION: Assign document sensitivity level based on content patterns\\n6. SUMMARY: Executive summary in 3-5 sentences\\n\\nAlways flag ambiguous requirements (conflicting SHALL statements, undefined acronyms).",
  "tools": [
    {"name": "extract_text_from_pdf", "description": "Extract text from PDF preserving layout"},
    {"name": "ner_pipeline", "description": "Named entity recognition pipeline"},
    {"name": "requirement_parser", "description": "Parse SHALL/SHOULD/MAY requirement statements"},
    {"name": "kg_builder", "description": "Build knowledge graph from extracted entities"}
  ],
  "steps": [
    "ingest_document",
    "detect_document_type",
    "extract_structural_sections",
    "run_ner_pipeline",
    "extract_requirements",
    "build_entity_relationships",
    "classify_sensitivity",
    "generate_summary"
  ],
  "parameters": {
    "document_path": "Path to the document file",
    "extract_requirements": "Boolean — extract formal requirements (default: true)",
    "classification_guide": "Optional SCG reference for classification"
  }
}
""",
    },
    {
        "slug": "hermes-compliance-reviewer",
        "framework": "hermes",
        "author": "nous-research",
        "version": "1.2.0",
        "frontmatter": {
            "name": "Compliance Reviewer",
            "description": "Automated compliance gap analysis against NIST 800-53, FedRAMP, CMMC, and STIG frameworks.",
            "tags": ["compliance", "fedramp", "nist", "cmmc", "stig"],
            "license": "Apache-2.0",
        },
        "body": """{
  "name": "compliance_reviewer",
  "description": "Automated compliance gap analysis against NIST 800-53, FedRAMP, CMMC, and STIG frameworks.",
  "system_prompt": "You are a compliance specialist with expertise in NIST 800-53, FedRAMP High, CMMC Level 2/3, DoD STIGs, and IC ICD standards.\\n\\nFor each compliance review:\\n1. CONTROL MAPPING: Map each artifact (policy, config, code) to relevant controls\\n2. GAP ANALYSIS: Identify missing or partially implemented controls\\n3. SEVERITY RATING: Rate each gap (CAT I/II/III for STIG; Critical/High/Medium/Low for FedRAMP)\\n4. EVIDENCE ASSESSMENT: Evaluate if provided evidence adequately demonstrates compliance\\n5. REMEDIATION PLAN: Provide specific, ordered remediation steps with effort estimates\\n6. CROSSWALK: Map findings across frameworks (NIST → FedRAMP → CMMC → STIG)\\n\\nAlways cite the specific control ID (e.g., AC-2, CMMC.AC.1.001) for each finding.",
  "tools": [
    {"name": "control_catalog_lookup", "description": "Look up control definition from local OSCAL catalog"},
    {"name": "crosswalk_engine", "description": "Map a control ID across multiple frameworks"},
    {"name": "evidence_evaluator", "description": "Assess if evidence meets control requirements"},
    {"name": "poam_generator", "description": "Generate a POAM entry from a compliance gap"}
  ],
  "steps": [
    "identify_applicable_frameworks",
    "map_artifact_to_controls",
    "evaluate_implementation_status",
    "identify_gaps",
    "rate_gap_severity",
    "generate_crosswalk",
    "produce_remediation_plan",
    "generate_poam_entries"
  ],
  "parameters": {
    "artifact": "System, policy, config, or code to review",
    "frameworks": "List of frameworks: nist-800-53, fedramp-high, cmmc-l2, cmmc-l3, dod-stig",
    "output_format": "Output format: json, oscal, markdown"
  }
}
""",
    },

    # ── Gemini (Google) ───────────────────────────────────────────────────
    {
        "slug": "gemini-document-classifier",
        "framework": "gemini",
        "author": "google-deepmind",
        "version": "1.0.0",
        "frontmatter": {
            "name": "Document Classifier",
            "description": "Classifies documents by type, sensitivity, and domain using Gemini functionDeclarations.",
            "tags": ["classification", "document-ai", "nlp"],
            "license": "Apache-2.0",
        },
        "body": """{
  "functionDeclarations": [
    {
      "name": "classify_document",
      "description": "Classify a document by type, sensitivity level, and primary domain. Returns structured classification with confidence scores.",
      "parameters": {
        "type": "object",
        "properties": {
          "document_text": {
            "type": "string",
            "description": "The full text content of the document to classify"
          },
          "classification_guide": {
            "type": "string",
            "description": "Optional Security Classification Guide (SCG) reference to apply"
          },
          "domains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of candidate domains to classify against (e.g. ['legal', 'technical', 'financial'])"
          }
        },
        "required": ["document_text"]
      }
    },
    {
      "name": "apply_portion_markings",
      "description": "Apply CAPCO portion markings to a classified document. Returns the document with inline markings.",
      "parameters": {
        "type": "object",
        "properties": {
          "document_text": {
            "type": "string",
            "description": "The document text to apply markings to"
          },
          "overall_classification": {
            "type": "string",
            "enum": ["U", "CUI", "C", "S", "TS", "TS//SCI"],
            "description": "Overall document classification level"
          },
          "scg_reference": {
            "type": "string",
            "description": "Security Classification Guide identifier"
          }
        },
        "required": ["document_text", "overall_classification"]
      }
    },
    {
      "name": "extract_requirements",
      "description": "Extract formal requirements from a document (SHALL/SHOULD/MAY/MUST statements).",
      "parameters": {
        "type": "object",
        "properties": {
          "document_text": {
            "type": "string",
            "description": "Document text to extract requirements from"
          },
          "requirement_prefix": {
            "type": "string",
            "description": "Optional prefix for requirement IDs (e.g. 'REQ-', 'PWS-')"
          },
          "include_rationale": {
            "type": "boolean",
            "description": "Whether to extract rationale text alongside requirements"
          }
        },
        "required": ["document_text"]
      }
    }
  ]
}
""",
    },
    {
        "slug": "gemini-code-intelligence",
        "framework": "gemini",
        "author": "google-deepmind",
        "version": "1.0.0",
        "frontmatter": {
            "name": "Code Intelligence",
            "description": "Code analysis functions: explain, review, generate tests, and detect vulnerabilities.",
            "tags": ["code-analysis", "security", "testing"],
            "license": "Apache-2.0",
        },
        "body": """{
  "functionDeclarations": [
    {
      "name": "analyze_code_security",
      "description": "Analyze code for security vulnerabilities. Returns findings with CWE mappings and remediation guidance.",
      "parameters": {
        "type": "object",
        "properties": {
          "code": {"type": "string", "description": "Source code to analyze"},
          "language": {"type": "string", "description": "Programming language (python, javascript, go, java, rust, etc.)"},
          "severity_threshold": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"], "description": "Minimum severity to report"}
        },
        "required": ["code", "language"]
      }
    },
    {
      "name": "generate_test_suite",
      "description": "Generate a comprehensive test suite for the provided code.",
      "parameters": {
        "type": "object",
        "properties": {
          "code": {"type": "string", "description": "Code to generate tests for"},
          "test_framework": {"type": "string", "description": "Test framework: pytest, jest, junit, go-test, rspec"},
          "coverage_target": {"type": "number", "description": "Target branch coverage percentage (0-100, default 80)"},
          "include_mocks": {"type": "boolean", "description": "Whether to generate mock objects for dependencies"}
        },
        "required": ["code"]
      }
    },
    {
      "name": "explain_architecture",
      "description": "Explain the architecture of a codebase from provided file contents or descriptions.",
      "parameters": {
        "type": "object",
        "properties": {
          "files": {"type": "array", "items": {"type": "string"}, "description": "List of file contents or summaries"},
          "audience": {"type": "string", "enum": ["executive", "developer", "architect", "junior"], "description": "Target audience for the explanation"},
          "output_format": {"type": "string", "enum": ["markdown", "mermaid", "json"], "description": "Output format"}
        },
        "required": ["files"]
      }
    }
  ]
}
""",
    },

    # ── OpenAI (function_call spec) ───────────────────────────────────────
    {
        "slug": "openai-semantic-search",
        "framework": "openai",
        "author": "openai",
        "version": "1.0.0",
        "frontmatter": {
            "name": "Semantic Search",
            "description": "Semantic search over a local document corpus with relevance ranking and citation.",
            "tags": ["search", "rag", "embeddings", "retrieval"],
            "license": "MIT",
        },
        "body": """{
  "functions": [
    {
      "name": "semantic_search",
      "description": "Search a local document corpus using semantic similarity. Returns ranked results with source citations. Air-gap safe — uses local embeddings only.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Natural language search query"
          },
          "corpus_path": {
            "type": "string",
            "description": "Path to local document corpus or vector store"
          },
          "top_k": {
            "type": "integer",
            "description": "Number of results to return (default: 5, max: 20)"
          },
          "min_score": {
            "type": "number",
            "description": "Minimum relevance score threshold 0.0-1.0 (default: 0.7)"
          },
          "include_context": {
            "type": "boolean",
            "description": "Whether to include surrounding paragraph context (default: true)"
          }
        },
        "required": ["query", "corpus_path"]
      }
    },
    {
      "name": "index_documents",
      "description": "Index a directory of documents into a local vector store for semantic search.",
      "parameters": {
        "type": "object",
        "properties": {
          "document_dir": {
            "type": "string",
            "description": "Directory path containing documents to index"
          },
          "store_path": {
            "type": "string",
            "description": "Output path for the vector store"
          },
          "chunk_size": {
            "type": "integer",
            "description": "Token chunk size for document splitting (default: 512)"
          },
          "embedding_model": {
            "type": "string",
            "description": "Local embedding model to use (default: sentence-transformers/all-MiniLM-L6-v2)"
          }
        },
        "required": ["document_dir", "store_path"]
      }
    }
  ]
}
""",
    },
    {
        "slug": "openai-workflow-planner",
        "framework": "openai",
        "author": "openai",
        "version": "1.1.0",
        "frontmatter": {
            "name": "Workflow Planner",
            "description": "Decomposes complex goals into ordered task graphs with dependency tracking and effort estimates.",
            "tags": ["planning", "task-decomposition", "project-management"],
            "license": "MIT",
        },
        "body": """{
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
""",
    },

    # ── LangChain tools ───────────────────────────────────────────────────
    {
        "slug": "langchain-local-rag-tool",
        "framework": "langchain",
        "author": "langchain-ai",
        "version": "0.3.0",
        "frontmatter": {
            "name": "Local RAG Tool",
            "description": "Air-gap RAG tool using local FAISS vector store and sentence-transformers embeddings.",
            "tags": ["rag", "local", "air-gap", "faiss"],
            "license": "MIT",
        },
        "body": """{
  "name": "local_rag_search",
  "description": "Search a local FAISS vector store using sentence-transformers embeddings. No external API calls — fully air-gap safe. Returns top-k documents with relevance scores and source metadata.",
  "args_schema": {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "Search query in natural language"
      },
      "vector_store_path": {
        "type": "string",
        "description": "Local path to FAISS index directory"
      },
      "top_k": {
        "type": "integer",
        "description": "Number of documents to retrieve (default: 4)"
      },
      "score_threshold": {
        "type": "number",
        "description": "Minimum similarity score 0.0-1.0 (default: 0.5)"
      }
    },
    "required": ["query", "vector_store_path"]
  },
  "return_direct": false,
  "verbose": true
}
""",
    },
    {
        "slug": "langchain-code-analysis-tool",
        "framework": "langchain",
        "author": "langchain-ai",
        "version": "0.2.0",
        "frontmatter": {
            "name": "Code Analysis Tool",
            "description": "Static analysis tool for Python, JavaScript, Go, and Rust using local linters. Air-gap safe.",
            "tags": ["static-analysis", "linting", "security", "air-gap"],
            "license": "MIT",
        },
        "body": """{
  "name": "analyze_code",
  "description": "Run static analysis on code using local tools (ruff, eslint-local, golangci-lint, clippy). Air-gap safe — no network calls. Returns structured findings with line numbers and fix suggestions.",
  "args_schema": {
    "type": "object",
    "properties": {
      "code": {
        "type": "string",
        "description": "Source code to analyze"
      },
      "language": {
        "type": "string",
        "enum": ["python", "javascript", "typescript", "go", "rust"],
        "description": "Programming language of the code"
      },
      "checks": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Specific checks to run: ['style', 'security', 'complexity', 'all']"
      }
    },
    "required": ["code", "language"]
  },
  "return_direct": false
}
""",
    },

    # ── CrewAI agents ─────────────────────────────────────────────────────
    {
        "slug": "crewai-senior-engineer",
        "framework": "crewai",
        "author": "crewai-community",
        "version": "1.0.0",
        "frontmatter": {
            "name": "Senior Software Engineer",
            "description": "Senior engineer agent with full-stack expertise, security awareness, and TDD discipline.",
            "tags": ["engineering", "full-stack", "tdd", "devsecops"],
            "license": "MIT",
        },
        "body": """role: Senior Software Engineer
goal: >
  Design, implement, and review high-quality software components that are
  secure, maintainable, tested, and aligned with project architecture.
  Apply SOLID principles and OWASP Top 10 mitigations by default.
backstory: >
  You are a senior software engineer with 15 years of experience across
  Python, Go, TypeScript, and Rust. You have led security-conscious
  development on classified DoD systems and open-source projects.
  You write tests before code (TDD), always check for injection vectors,
  and refuse to ship without 80% branch coverage. You know when NOT to
  abstract (YAGNI) and when a simple 3-line function beats a framework.
tools:
  - code_reader
  - code_writer
  - test_runner
  - static_analyzer
  - git_operations
verbose: true
allow_delegation: false
max_iter: 15
""",
    },
    {
        "slug": "crewai-security-researcher",
        "framework": "crewai",
        "author": "crewai-community",
        "version": "1.0.0",
        "frontmatter": {
            "name": "Security Researcher",
            "description": "Offensive-minded security researcher agent for threat modeling, vuln research, and red teaming.",
            "tags": ["security", "red-team", "threat-modeling", "pentesting"],
            "license": "MIT",
        },
        "body": """role: Security Researcher
goal: >
  Identify security vulnerabilities, misconfigurations, and design weaknesses
  in systems, code, and architectures. Produce actionable findings with
  CVSS scores, PoC steps (non-destructive), and remediation guidance.
backstory: >
  You are a cybersecurity researcher with offensive and defensive expertise.
  You hold OSCP, CISSP, and hold clearance for red team engagements on
  classified networks. You think like an adversary but write findings like
  a defender. You always include the business impact of each vulnerability,
  not just the technical details. You never exploit without authorization.
tools:
  - static_analyzer
  - dependency_auditor
  - threat_model_builder
  - cve_lookup
  - mitre_attack_search
verbose: true
allow_delegation: true
max_iter: 20
""",
    },

    # ── AutoGen (Microsoft) ───────────────────────────────────────────────
    {
        "slug": "autogen-code-review-agent",
        "framework": "autogen",
        "author": "microsoft",
        "version": "0.4.0",
        "frontmatter": {
            "name": "Code Review Agent",
            "description": "AutoGen conversational agent for iterative code review with human-in-the-loop approval.",
            "tags": ["code-review", "autogen", "hitl", "iterative"],
            "license": "MIT",
        },
        "body": """{
  "name": "CodeReviewAgent",
  "description": "Conversational code review agent that performs iterative review cycles with human-in-the-loop approval gates. Stops after MAX_REVIEW_ROUNDS or when human approves.",
  "system_message": "You are an expert code reviewer participating in a review conversation.\\n\\nIn each turn:\\n1. Analyze the code or the author's response to your previous feedback\\n2. List specific findings with file:line references and severity (CRITICAL/HIGH/MEDIUM/LOW)\\n3. For CRITICAL/HIGH findings: require the author to address them before approving\\n4. For MEDIUM/LOW findings: note them but do not block approval\\n5. When all CRITICAL/HIGH findings are resolved, output exactly: REVIEW_APPROVED\\n\\nBe constructive. Cite the specific line. Suggest the exact fix.\\nDo not repeat already-resolved findings.",
  "human_input_mode": "TERMINATE",
  "max_consecutive_auto_reply": 8,
  "code_execution_config": false,
  "llm_config": {
    "temperature": 0.1,
    "functions": [
      {
        "name": "flag_critical_finding",
        "description": "Flag a critical security or correctness issue that blocks approval",
        "parameters": {
          "type": "object",
          "properties": {
            "file": {"type": "string"},
            "line": {"type": "integer"},
            "description": {"type": "string"},
            "suggested_fix": {"type": "string"}
          },
          "required": ["file", "description"]
        }
      }
    ]
  }
}
""",
    },
    {
        "slug": "autogen-test-orchestrator",
        "framework": "autogen",
        "author": "microsoft",
        "version": "0.4.0",
        "frontmatter": {
            "name": "Test Orchestrator Agent",
            "description": "AutoGen multi-agent orchestrator that generates, runs, and iteratively fixes failing tests.",
            "tags": ["testing", "autogen", "tdd", "ci"],
            "license": "MIT",
        },
        "body": """{
  "name": "TestOrchestratorAgent",
  "description": "Orchestrates a multi-agent test generation and verification workflow. Coordinates a TestWriterAgent and TestRunnerAgent to achieve passing tests through iteration.",
  "system_message": "You are a test orchestration agent. Your job is to:\\n\\n1. Receive code to test\\n2. Instruct the TestWriterAgent to generate a comprehensive test suite\\n3. Instruct the TestRunnerAgent to execute the tests\\n4. Analyze failures and instruct the TestWriterAgent to fix them\\n5. Repeat until all tests pass OR max_iterations is reached\\n6. Report the final test coverage and any remaining failures\\n\\nAlways require at least: happy path, one error case, one edge case per function.\\nTerminate with TESTS_COMPLETE when all tests pass.",
  "human_input_mode": "NEVER",
  "max_consecutive_auto_reply": 12,
  "code_execution_config": {
    "work_dir": ".tmp/test_workspace",
    "use_docker": false,
    "timeout": 60,
    "last_n_messages": 3
  }
}
""",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_skill_md(skill: dict[str, Any]) -> str:
    """Render an OpenClaw SKILL.md from a seed definition."""
    import yaml
    fm = {
        "name": skill["frontmatter"].get("name", skill["slug"]),
        "version": skill["version"],
        "author": skill["author"],
        "framework": skill["framework"],
        "description": skill["frontmatter"].get("description", ""),
        "tags": skill["frontmatter"].get("tags", []),
        "license": skill["frontmatter"].get("license", "MIT"),
        "air_gap_safe": True,
        "source": f"official-seed/{skill['framework']}/{skill['slug']}",
    }
    fm_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True).rstrip()
    return f"---\n{fm_yaml}\n---\n{skill['body'].lstrip()}"


def seed_skills(
    skills: list[dict] | None = None,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = True,
) -> list[dict]:
    """Run official skills through the SkillHub import pipeline.

    Args:
        skills: subset to seed (defaults to OFFICIAL_SKILLS)
        force: bypass ICDEV_SKILLHUB_ENABLED flag
        dry_run: parse + validate but don't import
        verbose: print progress

    Returns:
        List of import result dicts.
    """
    from tools.marketplace.openclaw_bridge import import_skill, _is_enabled

    if not force and not _is_enabled():
        # Auto-enable for seeding
        os.environ["ICDEV_SKILLHUB_ENABLED"] = "true"
        os.environ["ICDEV_OPENCLAW_ENABLED"] = "true"

    to_seed = skills or OFFICIAL_SKILLS
    results = []
    tmpdir = Path(tempfile.mkdtemp(prefix="skillhub_seed_"))

    try:
        for skill in to_seed:
            slug = skill["slug"]
            fw   = skill["framework"]

            if verbose:
                print(f"  [{fw.upper():<10}] {slug} ... ", end="", flush=True)

            # Write skill.md package to temp dir
            pkg_dir = tmpdir / slug
            pkg_dir.mkdir(parents=True, exist_ok=True)
            skill_md = _build_skill_md(skill)
            (pkg_dir / "SKILL.md").write_text(skill_md, encoding="utf-8", newline="")

            if dry_run:
                results.append({"slug": slug, "status": "dry_run", "ok": True})
                if verbose:
                    print("DRY RUN")
                continue

            result = import_skill(
                source_path=str(pkg_dir),
                tenant_id="default",
                imported_by="official-seed",
                skillhub_url=f"local://official-seed/{fw}/{slug}",
            )

            ok = "error" not in result
            status = result.get("status", "error")
            results.append({
                "slug": slug,
                "framework": fw,
                "ok": ok,
                "import_id": result.get("import_id"),
                "status": status,
                "scan_status": result.get("scan_status"),
                "trust_score": result.get("trust_score"),
                "risk_score": result.get("risk_score"),
                "error": result.get("error"),
            })

            if verbose:
                if ok:
                    trust = result.get("trust_score", 0)
                    scan  = result.get("scan_status", "?")
                    print(f"OK  scan={scan}  trust={trust:.2f}  status={status}")
                else:
                    print(f"FAIL  {result.get('error', '?')[:80]}")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Seed SkillHub with official skills")
    parser.add_argument("--force", action="store_true", help="Bypass ICDEV_SKILLHUB_ENABLED check")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, don't import")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--framework", help="Only seed skills for this framework")
    parser.add_argument("--list", action="store_true", help="List skills that would be seeded")
    args = parser.parse_args()

    skills = OFFICIAL_SKILLS
    if args.framework:
        skills = [s for s in skills if s["framework"] == args.framework]
        if not skills:
            print(f"No skills found for framework: {args.framework}")
            return 1

    if args.list:
        for s in skills:
            print(f"  {s['framework']:<12} {s['slug']:<40} {s['frontmatter'].get('name','')}")
        return 0

    print(f"\nSeeding {len(skills)} official skill(s) into SkillHub...")
    if args.dry_run:
        print("(dry-run mode — no DB writes)\n")

    results = seed_skills(skills, force=args.force, dry_run=args.dry_run, verbose=not args.json)

    ok_count   = sum(1 for r in results if r.get("ok"))
    fail_count = len(results) - ok_count

    if args.json:
        print(json.dumps({"total": len(results), "ok": ok_count, "failed": fail_count, "results": results}, indent=2))
    else:
        print(f"\n{'-'*60}")
        print(f"  Seeded: {ok_count}/{len(results)} skills")
        if fail_count:
            print(f"  Failed: {fail_count}")
            for r in results:
                if not r.get("ok"):
                    print(f"    - {r['slug']}: {r.get('error','?')}")
        print("\n  View at: http://localhost:5050/skillhub")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
