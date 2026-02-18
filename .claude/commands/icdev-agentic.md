# CUI // SP-CTI

# /icdev-agentic — Generate Agentic Application

Generate a mini-ICDEV clone application with full GOTCHA framework, ATLAS workflow,
own agents, memory system, and CI/CD. The child app has everything ICDEV has except
the ability to generate new applications.

## Workflow

1. **Gather Requirements**
   - Ask the user for: application name, description/spec, project type, cloud provider
   - Confirm: impact level, compliance needs (ATO?), MBSE needed?

2. **Assess Fitness**
   ```bash
   python tools/builder/agentic_fitness.py --spec "<user_spec>" --project-id "<project_id>" --json
   ```
   Review the scorecard. If overall_score < 4.0, inform the user that traditional architecture is recommended.

3. **Confirm User Decisions**
   Present the scorecard and ask for confirmation on:
   - Cloud provider (aws/gcp/azure/oracle) and region
   - MBSE enabled? ATO required?
   - Port offset (default 1000)
   - Parent callback URL (optional)

4. **Generate Blueprint**
   ```bash
   python tools/builder/app_blueprint.py \
     --fitness-scorecard <scorecard_path> \
     --user-decisions '<json>' \
     --app-name "<name>" \
     --cloud-provider <provider> \
     --cloud-region <region> \
     --impact-level <IL> \
     --json --output <blueprint_path>
   ```

5. **Scaffold and Generate**
   ```bash
   python tools/builder/scaffolder.py \
     --project-path <path> --name <name> --type <type> \
     --agentic --fitness-scorecard <scorecard_path> \
     --user-decisions '<json>' \
     --cloud-provider <provider> --cloud-region <region>
   ```

6. **Initialize Child App**
   ```bash
   cd <child_path>
   python tools/db/init_<name>_db.py
   python tools/memory/memory_read.py --format markdown
   ```

7. **Verify and Report**
   - Check CLAUDE.md exists
   - Check agent cards in tools/agent/cards/
   - Check .mcp.json has CSP servers
   - Report summary to user

## Output
After generation, provide the user with:
- Path to the generated application
- Number of agents, tools, and goals
- Cloud provider MCP servers configured
- Next steps for the user
