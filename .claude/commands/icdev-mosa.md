# CUI // SP-CTI

# /icdev-mosa — Modular Open Systems Approach

Assess MOSA compliance (6 families per DoDI 5000.75), analyze code modularity
metrics, generate Interface Control Documents (ICDs), produce Technical Standard
Profiles (TSPs), enforce modular design rules, and collect MOSA evidence for cATO.

## Workflow

### 1. MOSA Assessment
```bash
# Run full MOSA compliance assessment
python tools/compliance/mosa_assessor.py --project-id "$ARGUMENTS" --json

# Gate check
python tools/compliance/mosa_assessor.py --project-id "$ARGUMENTS" --gate

# Include in multi-regime assessment
python tools/compliance/multi_regime_assessor.py --project-id "$ARGUMENTS" --json
```

### 2. Modularity Analysis
```bash
# Analyze code modularity metrics (coupling, cohesion, circular deps)
python tools/mosa/modular_design_analyzer.py --project-dir /path --project-id "$ARGUMENTS" --store --json

# Quick analysis without DB storage
python tools/mosa/modular_design_analyzer.py --project-dir /path --json
```

### 3. ICD Generation
```bash
# Generate ICDs for all discovered interfaces
python tools/mosa/icd_generator.py --project-id "$ARGUMENTS" --all --json

# Generate ICD for specific interface
python tools/mosa/icd_generator.py --project-id "$ARGUMENTS" --interface-id "iface-123" --json
```

### 4. TSP Generation
```bash
# Generate Technical Standard Profile
python tools/mosa/tsp_generator.py --project-id "$ARGUMENTS" --json
```

### 5. Code Enforcement
```bash
# Scan for MOSA violations
python tools/mosa/mosa_code_enforcer.py --project-dir /path --json

# With fix suggestions
python tools/mosa/mosa_code_enforcer.py --project-dir /path --fix-suggestions --json
```

### 6. cATO Evidence (Optional)
```bash
# Collect MOSA evidence for cATO
python tools/compliance/cato_monitor.py --project-id "$ARGUMENTS" --mosa-evidence

# Full cATO readiness (includes MOSA if enabled)
python tools/compliance/cato_monitor.py --project-id "$ARGUMENTS" --readiness
```

## Decision Flow

1. **Starting MOSA?** --> Run assessment first (`--json`) to establish baseline
2. **Need modularity metrics?** --> Run modular_design_analyzer with `--store`
3. **Need interface docs?** --> Generate ICDs for all external interfaces
4. **Need standards profile?** --> Generate TSP to document adopted standards
5. **Need code quality?** --> Run code enforcer to find MOSA violations
6. **Pursuing cATO?** --> Enable mosa_config.yaml cato_integration and collect evidence

## MOSA 6 Families (DoDI 5000.75)
| Family | Code | Weight | Description |
|--------|------|--------|-------------|
| Modular Architecture | MOSA-ARCH | 25% | Module boundaries, coupling, cohesion |
| Open Standards | MOSA-STD | 20% | API standards, data formats, protocols |
| Open Interfaces | MOSA-INT | 20% | ICD, versioning, backward compatibility |
| Data Rights | MOSA-DR | 15% | Gov rights, licensing, source delivery |
| Competitive Sourcing | MOSA-CS | 10% | Replaceability, vendor lock-in analysis |
| Continuous Assessment | MOSA-CA | 10% | Metrics tracking, evolution planning |

## Related Commands
- `/icdev-comply` — Compliance artifact generation
- `/icdev-intake` — Requirements intake (MOSA auto-detection)
- `/icdev-zta` — Zero Trust Architecture
- `/icdev-devsecops` — DevSecOps pipeline security
- `/icdev-build` — TDD build with MOSA interface generation
