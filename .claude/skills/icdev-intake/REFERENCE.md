# /icdev-intake — Step-by-Step Reference

## Step 1: Validate Project
Verify the project exists:
```bash
python -c "
import sqlite3, sys
conn = sqlite3.connect('data/icdev.db')
row = conn.execute('SELECT id, name, impact_level FROM projects WHERE id=?', ('$PROJECT_ID',)).fetchone()
if not row: print('ERROR: Project not found'); sys.exit(1)
print(f'Project: {row[1]} (impact_level={row[2]})')
conn.close()
"
```

## Step 2: Create New Session (if --new)
Use the `create_intake_session` MCP tool from icdev-requirements server:
```bash
python tools/requirements/intake_engine.py --project-id $PROJECT_ID --customer-name "$CUSTOMER" --org "$ORG" --impact $IMPACT --json
```
- Creates session record in intake_sessions table
- Stores customer info, impact level, classification context
- Loads ambiguity patterns from `context/requirements/ambiguity_patterns.json`
- Loads gap patterns from `context/requirements/gap_patterns.json`
- Logs audit trail: intake_session_created

## Step 3: Resume Existing Session (if --resume)
```bash
python tools/requirements/intake_engine.py --session-id $SESSION_ID --resume --json
```
- Loads session state and full conversation history
- Summarizes where we left off (last phase, turn count, readiness score)
- Continues from last phase

## Step 4: Load Intake Conversation Prompt
Load the intake agent system prompt:
```bash
cat hardprompts/requirements/intake_conversation.md
```
- Establishes the Requirements Analyst persona
- Defines structured question flow: mission → users → security → data → integration → timeline
- Sets ground rules for extraction and gap detection

## Step 5: Conversational Intake Loop
Process each customer message:
```bash
python tools/requirements/intake_engine.py --session-id $SESSION_ID --message "$CUSTOMER_MESSAGE" --json
```
- Extracts requirements from customer response (type, priority, source)
- Detects ambiguities and flags them with clarification suggestions
- Detects gap signals (security, performance, data, compliance, ATO boundary)
- Returns extracted requirements, detected ambiguities, and gap signals

## Step 6: Document Upload (when customer provides a document)
Upload and extract:
```bash
python tools/requirements/document_extractor.py --session-id $SESSION_ID --file "$DOC_PATH" --json
```
- Supports PDF (pypdf), DOCX (python-docx), TXT, MD
- Extracts requirements using rules from `context/requirements/document_extraction_rules.json`
- Merges extracted requirements into session
- Reports extraction count and any parsing warnings

## Step 7: Gap Detection
Run after every 5 turns or on demand:
```bash
python tools/requirements/gap_detector.py --session-id $SESSION_ID --json
```
- Checks 10 gap categories: security, compliance, testability, interfaces, data, performance, availability, authorization, audit, encryption
- References NIST 800-53 controls for each gap
- Provides remediation recommendations
- Returns gap list sorted by severity

## Step 8: Readiness Scoring
Run alongside gap detection:
```bash
python tools/requirements/readiness_scorer.py --session-id $SESSION_ID --json
```
- Scores 5 dimensions: completeness, clarity, feasibility, compliance, testability
- Uses rubric from `context/requirements/readiness_rubric.json`
- Thresholds: 0.7 = decomposition, 0.8 = COA, 0.9 = implementation
- Reports trend across turns

## Step 9: SAFe Decomposition (when readiness >= 0.7)
Decompose requirements into SAFe hierarchy:
```bash
python tools/requirements/decomposition_engine.py --session-id $SESSION_ID --target story --bdd --json
```
- Generates: Epic > Capability > Feature > Story > Enabler
- T-shirt size estimation per item (XS through XXL)
- WSJF scoring for prioritization
- BDD acceptance criteria (Gherkin Given/When/Then) when --bdd flag set
- Uses templates from `context/requirements/safe_templates.json`

## Step 10: Export & Summary
Export decomposed requirements:
```bash
python tools/requirements/intake_engine.py --session-id $SESSION_ID --export --json
```
Print final summary:
- Total requirements captured (by type and priority)
- Readiness score (final, per dimension)
- Gaps resolved vs outstanding
- SAFe items generated (count by level)
- BDD scenarios count
- Next step recommendation (proceed to ANVIL Architect phase)
