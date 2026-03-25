# CUI // SP-CTI
# FreeRTOS-ai Research Dossier — Three-Engine Synthesis

**Date:** 2026-03-03
**Engines Used:** Research Engine, Innovation Engine, Creative Engine
**Classification:** CUI // SP-CTI
**Status:** Research Complete — Ready for Fitness Assessment

---

## Executive Summary

Three ICDEV™ engines were run in parallel to assess the feasibility and architecture for **FreeRTOS-ai** — a child application that integrates ICDEV™'s compliance, multi-agent orchestration, and traceability framework with FreeRTOS embedded development.

### Key Finding: Three-Tier Architecture

LLMs **cannot** run on FreeRTOS-class MCUs (KB-MB RAM vs GB required). The integration architecture is a **three-tier model**:

```
Tier 1: FreeRTOS MCU (Cortex-M, ESP32, RISC-V)
├── TinyML/TFLite Micro inference (anomaly detection, keyword spotting)
├── Sensor data collection + actuator control
├── MQTT/BLE telemetry to Tier 2
└── OTA firmware updates

Tier 2: Edge Gateway (Raspberry Pi, Jetson, Greengrass Core)
├── Local LLM inference (llama.cpp, whisper.cpp, ONNX Runtime)
├── Multi-agent orchestration coordinator
├── Edge inference (SageMaker Neo / DLR runtime)
└── Forwards to Tier 3 when needed

Tier 3: Cloud/ICDEV™ (Bedrock, SageMaker, ICDEV™ Agents)
├── Full LLM orchestration (Bedrock AgentCore)
├── Model training + deployment to Tier 2
├── Compliance monitoring + audit trail
├── Self-healing firmware crash analysis
└── ICDEV™ FORGE/ANVIL workflow
```

---

## Research Engine Findings

### FreeRTOS Architecture Constraints
| Constraint | Value | Implication |
|-----------|-------|-------------|
| Kernel footprint | 6-12 KB flash | Cannot add large frameworks |
| RAM per task | ~500 bytes min | Agent contexts must be minimal |
| Memory schemes | 5 heap types + static alloc | Must support safety-critical static allocation |
| Supported MCUs | 40+ ports (ARM Cortex-M/A/R, RISC-V, ESP32, etc.) | Multi-architecture support required |
| Max practical tasks | 15-30 | Lightweight agent model needed |

### TinyML on FreeRTOS — Mature
- **TFLite Micro**: 16 KB core runtime, inference-only, FreeRTOS compatible
- **Edge Impulse**: End-to-end MLOps, generates C++ libraries for FreeRTOS
- **ESP-DL**: Vendor-backed ML for ESP32 (YOLO11n, MobileNetV2)
- **CMSIS-NN**: ARM's optimized NN kernels for Cortex-M

### Competitors
| RTOS | AI/ML Story | Threat Level |
|------|------------|--------------|
| **Zephyr** | Best-positioned: TFLite Micro, Edge Impulse, Ztest/Twister, CMake+West | HIGH |
| **Embassy (Rust)** | Memory-safe embedded, comparable performance to FreeRTOS | MEDIUM |
| **VxWorks** | DO-178C certified, commercial AI Toolkit | LOW (different market) |
| **QNX** | ISO 26262 ASIL D, automotive ADAS AI SDK | LOW (different market) |
| **ThreadX/Eclipse** | IEC 62443 pre-certified, Azure IoT integration | MEDIUM |

### Regulatory Landscape
| Framework | FreeRTOS-ai Relevance | ICDEV™ Status |
|-----------|----------------------|--------------|
| DO-178C (avionics) | DAL A-E traceability | Not implemented — needs new assessor |
| IEC 62443 (industrial) | SL 1-4 cybersecurity | Not implemented — needs new assessor |
| ISO 26262 (automotive) | ASIL A-D functional safety | Not implemented — needs new assessor |
| IEC 62304 (medical devices) | Class A-C software lifecycle | Not implemented — needs new assessor |
| EU AI Act (embedded AI) | Annex III high-risk AI | Implemented (Phase 57) — extend for embedded |
| NIST AI RMF (IoT) | 4 functions, 12 subcategories | Implemented (Phase 37) — extend for IoT |
| NIST 800-53 (cyber) | Full catalog | Implemented — core ICDEV™ |
| EO 14028 (SBOM) | Mandatory for firmware | Implemented — extend for embedded SBOM |

---

## Innovation Engine Findings

### Top Innovation Signals (by weighted score)
1. **CVE-2024-38373** (CVSS 9.6) — Critical DNS buffer over-read in FreeRTOS-Plus-TCP
2. **CVE-2024-28115** (CVSS 8.8) — Kernel ROP privilege escalation
3. **EU AI Act for Embedded** — Annex III applies to AI safety components in products
4. **LLM-Assisted Self-Healing** — First-of-kind academic project (FYP, Feb 2026)
5. **AWS Greengrass V2 ML Inference** — Production edge AI pattern for Gov/DoD
6. **TFLite Micro** (2.8K stars) — De facto MCU ML inference standard
7. **NIST AI RMF for IoT** — Extends existing ICDEV™ assessor
8. **27 FreeRTOS CVEs** in NVD — Active security research on FreeRTOS

### Architecture Gap: No Multi-Agent Framework for RTOS
No established multi-agent orchestration framework exists for FreeRTOS/embedded:
- **micro-ROS**: Closest (DDS pub/sub), but robotics-focused
- **MQTT-based DIY**: Common pattern but no standard framework
- **Matter Protocol**: Smart home only
- **Greengrass Components**: Linux-only, not bare-metal

---

## Creative Engine Findings

### Top 3 Pain Points (by composite score)

1. **Unified RTOS + AI/ML Lifecycle Framework** (8.45/10)
   - No integrated model lifecycle management for RTOS
   - No inference scheduling, model OTA, or inference telemetry
   - Pure greenfield opportunity

2. **Embedded TDD/BDD Testing Framework** (8.35/10)
   - #1 reason developers migrate to Zephyr
   - Cannot test FreeRTOS task logic on host machines
   - Need host-based scheduler simulation + HAL/BSP mocking

3. **AI/ML Integration Support** (8.10/10)
   - Developers manually duct-taping TFLite Micro into FreeRTOS
   - No memory management, scheduling, or lifecycle support for ML
   - Demand growing as edge AI accelerates

### Additional Pain Points
4. **No standard build system** — FreeRTOS has no CMake/West equivalent
5. **No package manager** — Dependencies manually managed
6. **Configuration complexity** — 70+ macros in FreeRTOSConfig.h
7. **No compliance tooling** — No SBOM, STIG, audit trail for firmware
8. **Debugging difficulty** — Hard faults, priority inversion, stack overflow
9. **No HAL abstraction** — Vendor-locked code, not portable

### Competitive Gap: Zephyr Is Winning
Zephyr has built decisive advantages in:
- Build system (CMake + West)
- Configuration (Kconfig)
- Testing (Ztest + Twister)
- Networking (comprehensive built-in)
- AI/ML integration (TFLite Micro, sensor framework)

---

## FreeRTOS-ai Child App Specification

### Mission Statement
FreeRTOS-ai bridges the gap between embedded RTOS firmware development and enterprise-grade AI/compliance tooling, enabling DoD/Gov developers to build, test, deploy, and maintain AI-enabled embedded systems with full NIST/FedRAMP/IEC compliance traceability.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FreeRTOS-ai (Child App)                    │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  Embedded     │  │  Edge AI     │  │  Compliance &    │   │
│  │  Build Tools  │  │  Pipeline    │  │  Traceability    │   │
│  │              │  │              │  │                  │   │
│  │ • CMake gen  │  │ • TFLite     │  │ • SBOM (firmware)│   │
│  │ • FreeRTOS   │  │   Micro integ│  │ • CVE triage     │   │
│  │   scaffold   │  │ • Edge       │  │ • IEC 62443      │   │
│  │ • HAL stub   │  │   Impulse    │  │ • DO-178C trace  │   │
│  │   generation │  │ • Model OTA  │  │ • ISO 26262      │   │
│  │ • Config     │  │ • Inference  │  │ • EU AI Act      │   │
│  │   optimizer  │  │   scheduler  │  │ • Audit trail    │   │
│  │ • Host sim   │  │ • TinyML     │  │ • Digital thread │   │
│  │   testing    │  │   training   │  │ • FIPS 140-2     │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  Multi-Agent  │  │  Fleet       │  │  Self-Healing    │   │
│  │  Orchestrator │  │  Management  │  │  & Monitoring    │   │
│  │              │  │              │  │                  │   │
│  │ • MQTT agent │  │ • Device     │  │ • Crash log      │   │
│  │   protocol   │  │   registry   │  │   analysis (LLM) │   │
│  │ • Edge coord │  │ • OTA deploy │  │ • Firmware        │   │
│  │ • Task DAG   │  │ • Health     │  │   attestation    │   │
│  │ • A2A bridge │  │   heartbeat  │  │ • Stack overflow │   │
│  │   to ICDEV™   │  │ • Telemetry  │  │   detection      │   │
│  │ • State      │  │   dashboard  │  │ • Auto-rollback  │   │
│  │   machines   │  │ • Shadow     │  │ • Priority inver │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                              │
│  FORGE Framework │ ANVIL Workflow │ 10-12 Agents            │
│  TDD/BDD for C   │ Memory System  │ ICDEV™ A2A Bridge        │
└─────────────────────────────────────────────────────────────┘
```

### Core Capabilities

#### 1. Embedded Build System (C/C++ First-Class Support)
- CMake project generation for FreeRTOS + vendor SDKs
- FreeRTOSConfig.h optimizer (analyzes code, recommends heap/stack/tick settings)
- HAL stub generation for host-based testing
- Cross-compilation toolchain management (ARM GCC, ESP-IDF, vendor toolchains)
- Linker script generation with memory map validation

#### 2. Edge AI Pipeline
- TFLite Micro integration scaffolding (model loading, arena allocation, inference task)
- Edge Impulse project integration (data pipeline → model → C++ library → FreeRTOS task)
- Model OTA update support (modular OTA — update model without full firmware reflash)
- Inference scheduler (FreeRTOS task for periodic/event-driven inference)
- TinyML training pipeline (data collection → cloud training → quantization → deployment)
- Inference telemetry (latency, accuracy, memory usage tracking)

#### 3. Multi-Agent Orchestration for Embedded
- **Lightweight MQTT Agent Protocol**: CBOR-encoded agent messages (< 256 bytes)
- **Agent State Machines**: Each agent is a FreeRTOS task with a finite state machine
- **Edge Coordinator**: Runs on gateway device, orchestrates fleet of MCU agents
- **A2A Bridge to ICDEV™**: Gateway bridges MQTT agent protocol to ICDEV™'s JSON-RPC A2A
- **Task DAG**: Deterministic task scheduling using FreeRTOS priorities + dependencies
- **Hierarchical Delegation**: MCU agents → Edge coordinator → Cloud ICDEV™ agents

#### 4. Compliance & Traceability
- **Embedded SBOM**: CycloneDX BOM including FreeRTOS version, vendor SDK, HAL, TFLite Micro, all dependencies
- **CVE Triage Pipeline**: Auto-check FreeRTOS CVEs (27 known), vendor SDK CVEs
- **IEC 62443 Assessor**: Industrial cybersecurity for IACS (4 security levels)
- **DO-178C Traceability**: Requirements → Design → Code → Tests → Evidence matrix (DAL A-E)
- **ISO 26262 Assessor**: Automotive functional safety (ASIL A-D)
- **IEC 62304 Assessor**: Medical device software lifecycle (Class A-C)
- **EU AI Act Extension**: Embedded-specific Annex III requirements for AI safety components
- **FIPS 140-2/3**: Cryptographic module validation for secure boot, TLS, key storage
- **Digital Thread**: From requirements → SysML model → FreeRTOS task → test case → SBOM

#### 5. Testing Framework for Embedded
- **Host-Based Simulation**: FreeRTOS POSIX port with mocked HAL/BSP for desktop testing
- **TDD for C**: CppUTest/Unity test framework with FreeRTOS primitive mocks
- **BDD for Embedded**: Gherkin specs for embedded behavior (Given sensor reads X, When threshold exceeded, Then actuator Y activated)
- **Hardware-in-the-Loop**: Integration with JTAG/SWD for on-target tests
- **Inference Testing**: TFLite Micro model accuracy/latency validation on host + target
- **Static Analysis**: Cppcheck, PC-lint, MISRA C compliance checker

#### 6. Fleet Management & OTA
- **Device Registry**: Track firmware version, model version, health status per device
- **OTA Pipeline**: Code-signed firmware updates via AWS IoT OTA or custom MQTT
- **Health Heartbeat**: Periodic device telemetry (CPU, memory, stack watermarks)
- **Telemetry Dashboard**: Visualization of fleet health, inference metrics, anomalies
- **Device Shadow**: AWS IoT Device Shadow integration for desired/reported state
- **Firmware Attestation**: Secure boot chain verification + TPM-based attestation

#### 7. Self-Healing for Firmware
- **Crash Log Analysis**: LLM-powered analysis of HardFault dumps, stack traces, and watchdog resets
- **Auto-Rollback**: Detect bootloop, rollback to last known good firmware via MCUboot
- **Stack Overflow Detection**: Proactive monitoring via FreeRTOS stack watermark hooks
- **Priority Inversion Detection**: Runtime analysis of mutex hold times and task priorities
- **Firmware Repair**: LLM generates targeted patches, validated via host simulation, deployed via OTA

### Target Hardware Support
| MCU Family | Vendor | Use Case |
|-----------|--------|----------|
| Cortex-M4/M7 | STM32, NXP, Nordic | General embedded, industrial |
| Cortex-M33/M55 | STM32, NXP | TrustZone security, ML accelerator |
| Cortex-R5/R52 | TI, Renesas | Safety-critical automotive |
| ESP32/ESP32-S3 | Espressif | IoT, edge AI (ESP-DL) |
| RISC-V | SiFive, Espressif | Open ISA, growing adoption |
| Cortex-A7/A53 | NXP i.MX | Linux gateway tier |

### Agents (10-12)
| Agent | Port | Role |
|-------|------|------|
| Orchestrator | 9443 | Task routing, workflow management |
| Architect | 9444 | ANVIL A/T phases, firmware design |
| Embedded Builder | 9445 | TDD C/C++ code gen, CMake, linker scripts |
| Compliance | 9446 | ATO artifacts, IEC 62443, DO-178C, ISO 26262 |
| Security | 9447 | SAST (Cppcheck/MISRA), CVE triage, secret detection |
| Knowledge | 9449 | Self-healing patterns, embedded best practices |
| Monitor | 9450 | Fleet health, telemetry, inference metrics |
| Edge AI | 9451 | TFLite Micro integration, model lifecycle |
| Fleet Manager | 9452 | Device registry, OTA, firmware attestation |
| MBSE | 9453 | SysML model-to-firmware traceability |
| DevSecOps | 9457 | Embedded CI/CD, firmware signing, FIPS |

### New Compliance Frameworks (Beyond Parent ICDEV™)
| Framework | Catalog File | Use Case |
|-----------|-------------|----------|
| IEC 62443 | `iec_62443_requirements.json` | Industrial cybersecurity |
| DO-178C | `do_178c_objectives.json` | Avionics software |
| ISO 26262 | `iso_26262_requirements.json` | Automotive functional safety |
| IEC 62304 | `iec_62304_requirements.json` | Medical device software |
| MISRA C:2023 | `misra_c_rules.json` | C coding standard |
| FIPS 140-3 | `fips_140_3_requirements.json` | Cryptographic modules |

### Language Support: C/C++ First-Class
| Aspect | Tool |
|--------|------|
| Scaffold | FreeRTOS project (CMake + vendor SDK) |
| Lint | Cppcheck + PC-lint + MISRA C checker |
| Format | clang-format |
| SAST | Cppcheck (security), MISRA C compliance |
| Dep Audit | FreeRTOS CVE DB + vendor SDK CVE tracking |
| BDD | Gherkin + cJSON-based step executor |
| Code Gen | FreeRTOS task, queue, timer, ISR scaffolding |

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| FreeRTOS MCUs cannot run LLMs | HIGH | Three-tier architecture: MCU → Gateway → Cloud |
| No existing embedded multi-agent standard | MEDIUM | Build on MQTT patterns, bridge to ICDEV™ A2A |
| Safety certification complexity (DO-178C) | HIGH | Start with advisory assessment, not certification |
| Zephyr competitive threat | MEDIUM | Support both FreeRTOS and Zephyr in future |
| Hardware diversity (40+ MCU ports) | MEDIUM | Start with top 5 MCU families, expand |
| C/C++ testing is harder than Python/Java | MEDIUM | Host simulation with FreeRTOS POSIX port |

---

## Next Steps

1. **Fitness Assessment** — Run `agentic_fitness.py` with this spec
2. **Blueprint Generation** — Generate app blueprint from fitness scorecard
3. **Child App Generation** — Use `child_app_generator.py` pipeline
4. **FORGE Validation** — Run `forge_validator.py --gate` post-generation
5. **Prototype** — Start with Cortex-M7 + TFLite Micro + MQTT agent protocol
