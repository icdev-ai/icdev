# Document Feature

Generate concise markdown documentation for implemented features by analyzing code changes and specifications.

## Variables

run_id: $1
spec_path: $2 if provided, otherwise leave it blank
screenshots_dir: $3 if provided, otherwise leave it blank

## Instructions

### 1. Analyze Changes
- Run `git diff origin/main --stat` to see files changed
- Run `git diff origin/main --name-only` to get list of changed files
- For significant changes (>50 lines), run `git diff origin/main <file>` on specific files

### 2. Read Specification (if provided)
- If `spec_path` is provided, read it to understand requirements and success criteria

### 3. Generate Documentation
- Create documentation in `docs/features/` directory
- Filename format: `feature-{run_id}-{descriptive-name}.md`
- All documentation MUST include CUI markings: `CUI // SP-CTI`

### 4. Final Output
- Return exclusively the path to the documentation file created

## Documentation Format

```md
# CUI // SP-CTI
# <Feature Title>

**Run ID:** <run_id>
**Date:** <current date>
**Specification:** <spec_path or "N/A">

## Overview

<2-3 sentence summary of what was built and why>

## What Was Built

- <Component/feature 1>
- <Component/feature 2>

## Technical Implementation

### Files Modified

- `<file_path>`: <what was changed/added>

### Key Changes

<3-5 bullet points of most important technical changes>

## How to Use

1. <Step 1>
2. <Step 2>

## Configuration

<Configuration options, environment variables, or settings>

## Testing

<How to test the feature>

## NIST 800-53 Controls

<Relevant controls addressed by this feature>

## Notes

<Additional context, limitations, or future considerations>

# CUI // SP-CTI
```

## Report

- IMPORTANT: Return exclusively the path to the documentation file created and nothing else.
