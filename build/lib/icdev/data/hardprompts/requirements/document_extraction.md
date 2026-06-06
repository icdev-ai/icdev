# Document Requirements Extraction Prompt

> CUI // SP-CTI

Extract structured requirements from the provided document.

## Input
- Document type: {{document_type}} (SOW/CDD/CONOPS/SRD)
- Document content: {{document_content}}
- Extraction rules: {{extraction_rules}}

## Extraction Process

1. **Identify Sections**: Parse document structure and identify requirement-bearing sections
2. **Extract Requirements**: For each 'shall'/'must'/'will' statement:
   - Capture the raw text
   - Classify type (functional, security, interface, performance, etc.)
   - Assign priority based on language strength (shall=critical, should=medium, may=low)
   - Note the source section and page/paragraph
3. **Generate BDD Criteria**: For each extracted requirement, generate preliminary Given/When/Then
4. **Detect Gaps**: Compare against standard DoD requirement categories
5. **Flag Ambiguities**: Identify vague language per ambiguity patterns

## Output Format
```json
{
  "document_summary": "Brief description of what the document covers",
  "sections_found": [...],
  "requirements_extracted": [
    {
      "raw_text": "The system shall...",
      "refined_text": "Cleaned, structured version",
      "type": "functional",
      "priority": "critical",
      "source_section": "Section 3.2 - PWS",
      "source_page": "12",
      "preliminary_bdd": "Given ... When ... Then ...",
      "ambiguities": ["'timely' is undefined"],
      "related_controls": ["AC-2"]
    }
  ],
  "total_extracted": 0,
  "gaps_vs_standard_categories": [...]
}
```
