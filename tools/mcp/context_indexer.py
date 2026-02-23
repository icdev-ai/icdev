#!/usr/bin/env python3
# CUI // SP-CTI
"""CLAUDE.md section indexer for semantic layer MCP tools (Phase 44 — D277).

Parses CLAUDE.md into sections by ## headers, supports keyword search,
role-tailored context delivery, and table-of-contents generation.
Caches in memory, refreshes on mtime change.

Usage:
    from tools.mcp.context_indexer import ClaudeMdIndexer

    indexer = ClaudeMdIndexer()
    section = indexer.get_section("Testing Framework")
    results = indexer.search_sections("compliance")
    toc = indexer.get_toc()
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("icdev.context_indexer")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CLAUDE_MD = BASE_DIR / "CLAUDE.md"


class ClaudeMdIndexer:
    """Parse and index CLAUDE.md sections for on-demand context delivery.

    Sections are delimited by ## and ### markdown headers.
    Cache refreshes when file mtime changes.
    """

    def __init__(self, claude_md_path: Optional[Path] = None):
        self._path = claude_md_path or DEFAULT_CLAUDE_MD
        self._sections: Dict[str, dict] = {}  # name -> {content, level, line_number}
        self._raw_content: str = ""
        self._mtime: float = 0.0
        self._refresh()

    def _refresh(self) -> None:
        """Reload sections if file has been modified."""
        if not self._path.exists():
            self._sections = {}
            self._raw_content = ""
            return

        current_mtime = os.path.getmtime(str(self._path))
        if current_mtime == self._mtime and self._sections:
            return  # Cache hit

        self._mtime = current_mtime
        with open(self._path, "r", encoding="utf-8") as f:
            self._raw_content = f.read()
        self._parse_sections()

    def _parse_sections(self) -> None:
        """Parse markdown headers into section tree."""
        self._sections = {}
        lines = self._raw_content.split("\n")

        current_name = ""
        current_level = 0
        current_start = 0
        current_lines: List[str] = []

        for i, line in enumerate(lines):
            # Match ## or ### headers
            match = re.match(r'^(#{2,3})\s+(.+)$', line)
            if match:
                # Save previous section
                if current_name:
                    self._sections[current_name] = {
                        "content": "\n".join(current_lines).strip(),
                        "level": current_level,
                        "line_number": current_start + 1,
                    }

                current_name = match.group(2).strip()
                current_level = len(match.group(1))
                current_start = i
                current_lines = []
            else:
                current_lines.append(line)

        # Save final section
        if current_name:
            self._sections[current_name] = {
                "content": "\n".join(current_lines).strip(),
                "level": current_level,
                "line_number": current_start + 1,
            }

    def get_section(self, name: str) -> Optional[str]:
        """Get section content by exact name or case-insensitive match.

        Returns: Section content string, or None if not found.
        """
        self._refresh()

        # Exact match
        if name in self._sections:
            return self._sections[name]["content"]

        # Case-insensitive match
        name_lower = name.lower()
        for section_name, data in self._sections.items():
            if section_name.lower() == name_lower:
                return data["content"]

        # Partial match
        for section_name, data in self._sections.items():
            if name_lower in section_name.lower():
                return data["content"]

        return None

    def search_sections(self, keyword: str) -> List[str]:
        """Search sections by keyword in content and headers.

        Returns: List of matching section names.
        """
        self._refresh()
        keyword_lower = keyword.lower()
        matches = []

        for name, data in self._sections.items():
            if (keyword_lower in name.lower() or
                    keyword_lower in data["content"].lower()):
                matches.append(name)

        return matches

    def get_toc(self) -> List[dict]:
        """Get table of contents.

        Returns: List of {name, level, line_number}.
        """
        self._refresh()
        toc = []
        for name, data in self._sections.items():
            toc.append({
                "name": name,
                "level": data["level"],
                "line_number": data["line_number"],
            })
        return toc

    def get_sections_for_role(self, role: str) -> str:
        """Get concatenated content relevant to an agent role.

        Mapping: builder-agent -> Languages, Testing, Builder
                 compliance-agent -> Compliance, Crosswalk, Gates
                 etc.
        """
        self._refresh()

        # Role-to-section keyword mapping
        role_keywords = {
            "builder": ["Languages", "Testing", "Builder", "TDD"],
            "builder-agent": ["Languages", "Testing", "Builder", "TDD"],
            "compliance": ["Compliance", "Crosswalk", "Gates", "Security Gates", "NIST", "FedRAMP"],
            "compliance-agent": ["Compliance", "Crosswalk", "Gates", "Security Gates", "NIST", "FedRAMP"],
            "security": ["Security", "SAST", "Vulnerability", "Secret", "Gates", "ATLAS"],
            "security-agent": ["Security", "SAST", "Vulnerability", "Secret", "Gates", "ATLAS"],
            "architect": ["Architecture", "GOTCHA", "ATLAS", "MCP", "Agent"],
            "architect-agent": ["Architecture", "GOTCHA", "ATLAS", "MCP", "Agent"],
            "infrastructure": ["Infrastructure", "Terraform", "K8s", "Docker", "Cloud", "Deploy"],
            "infrastructure-agent": ["Infrastructure", "Terraform", "K8s", "Docker", "Cloud", "Deploy"],
            "orchestrator": ["Architecture", "Agent", "Workflow", "MCP", "GOTCHA"],
            "orchestrator-agent": ["Architecture", "Agent", "Workflow", "MCP", "GOTCHA"],
        }

        keywords = role_keywords.get(role, [])
        if not keywords:
            return ""

        sections = []
        for name, data in self._sections.items():
            for kw in keywords:
                if kw.lower() in name.lower():
                    sections.append(f"## {name}\n{data['content']}")
                    break

        return "\n\n".join(sections)

    @property
    def section_count(self) -> int:
        self._refresh()
        return len(self._sections)

    @property
    def section_names(self) -> List[str]:
        self._refresh()
        return list(self._sections.keys())
