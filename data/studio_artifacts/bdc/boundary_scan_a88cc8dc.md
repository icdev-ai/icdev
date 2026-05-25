# Authorization Boundary Scan Report
**Generated:** 2026-05-19 23:58 UTC  
**Project:** default  
**Designs scanned:** 1

## Design `default-bdc-design`
**Total Nodes:** 7  **Edges:** 6

### Boundary Component Summary
| Component Category | Count |
|-------------------|-------|
| `boundary_node` | 2 |
| `isa_connection` | 1 |
| `data_flow` | 1 |
| `control` | 1 |
| `endpoint` | 1 |
| `classification` | 1 |

### ISA Connections
| ISA Label | Classification |
|-----------|----------------|
| ISA-001 External Agency | CUI |

### Classified Data Flows
**3 classified flow(s) detected**
| Source Node | Target Node | Flow Type |
|-------------|-------------|-----------|
| `n1` | `n3` | permits |
| `n4` | `n3` | enforces |
| `n6` | `n3` | marks |

### Required Boundary Control Check
- ✓ All required boundary controls present (PEP, VPC endpoints, classification marks)
