# [TEMPLATE: CUI // SP-CTI]
# Cross-Language Translation — `/icdev-translate`

You are an ICDEV orchestrator performing cross-language code translation using the Phase 43 translation pipeline.

## Workflow

### 1. Confirm Parameters
Ask the user to confirm:
- **Source path**: Directory containing source code
- **Source language**: One of: python, java, javascript, typescript, go, rust, csharp
- **Target language**: Must be different from source
- **Output directory**: Where translated project will be written
- **Project ID**: ICDEV project identifier (optional)

### 2. Dry Run (Preview)
Run extraction + type-checking without LLM calls to preview scope:

```bash
python tools/translation/translation_manager.py \
  --source-path "$SOURCE_PATH" \
  --source-language "$SOURCE_LANG" \
  --target-language "$TARGET_LANG" \
  --output-dir "$OUTPUT_DIR" \
  --project-id "$PROJECT_ID" \
  --dry-run --json
```

Report to user:
- Number of extractable units (functions, classes, interfaces)
- Type compatibility percentage
- Any type-checking warnings
- Estimated translation scope

### 3. Run Full Pipeline
After user confirms, run the full translation:

```bash
python tools/translation/translation_manager.py \
  --source-path "$SOURCE_PATH" \
  --source-language "$SOURCE_LANG" \
  --target-language "$TARGET_LANG" \
  --output-dir "$OUTPUT_DIR" \
  --project-id "$PROJECT_ID" \
  --validate --json
```

### 4. Review Validation Results
Parse the validation report and present:
- **Gate result**: pass / warn / fail
- **API surface match**: percentage of public APIs preserved
- **Type coverage**: percentage of types successfully mapped
- **Compliance**: CUI markings present in all files
- **Mocked units**: any units that failed and were stubbed
- **Lint issues**: any target language lint warnings

### 5. Compliance Bridge (Optional)
If the user wants compliance inheritance:

```bash
python tools/modernization/compliance_bridge.py \
  --plan-id "$PROJECT_ID" \
  --validate --json
```

### 6. Test Translation (Optional)
If the user wants tests translated:

```bash
python tools/translation/test_translator.py \
  --source-test-dir "$SOURCE_PATH/tests" \
  --source-language "$SOURCE_LANG" \
  --target-language "$TARGET_LANG" \
  --output-dir "$OUTPUT_DIR/tests" \
  --ir-file "$OUTPUT_DIR/source_ir.json" \
  --project-id "$PROJECT_ID" --json
```

### 7. Report Results
Summarize:
- Total units translated / mocked / failed
- Output directory location
- Build file generated (pom.xml, go.mod, Cargo.toml, etc.)
- Dashboard link: `/translations` to review in web UI
- Next steps (compile, run tests, review mocked units)

## Arguments
$ARGUMENTS
