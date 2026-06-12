# Traceability Analysis Prompt

## Role
You are building a full Requirements Traceability Matrix (RTM) linking:
Requirement → SysML Element → Code Module → Test File → NIST Control → UAT

## Trace Link Sources
- intake_requirements → safe_decomposition (by session_id)
- safe_decomposition → sysml_elements (via digital_thread_links)
- sysml_elements → code modules (via model_code_mappings)
- code modules → test files (via digital_thread_links)
- requirements → NIST controls (via project_controls and control_mapper)

## Coverage Calculation
- Fully traced: requirement has links at ALL levels
- Partially traced: some links missing
- Untraced: no downstream links at all

## Gap Analysis
For each gap, report:
- Which trace level is missing
- Severity (critical if code/test missing, medium if SysML missing)
- Recommended action to close the gap
