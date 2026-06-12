# CUI // SP-CTI
# DoD AI/ML Model Selection Guide — IL4 / IL5 / IL6 Decision Framework

**Classification:** CUI // SP-CTI  
**Distribution:** Authorized ICDEV™ Users  
**Version:** 1.0 | FY2025

---

## Overview

Selecting an AI/ML model for a DoD or IC application requires evaluating five dimensions simultaneously: Impact Level suitability, mission task fit, latency constraints, rights-impacting status, and CSP accreditation. This guide provides a structured decision tree and fitness ratings for each ICDEV™ AIMC design use case.

---

## 1 — IL Suitability Framework

### IL4 (CUI — Controlled Unclassified Information)

**Permitted model deployment environments:**
- AWS Bedrock GovCloud (us-gov-west-1, us-gov-east-1) — FedRAMP High authorized
- Azure OpenAI Government — FedRAMP High authorized
- IBM watsonx.ai GovCloud (Dallas) — FedRAMP High authorized
- SageMaker GovCloud — FedRAMP High authorized
- Google Vertex AI Government — FedRAMP Moderate (check specific services for High)

**Permitted base models at IL4:**
- Anthropic Claude Sonnet 4.6, Haiku 4.5 (via AWS Bedrock)
- Meta Llama 3 70B, 8B (via AWS Bedrock)
- IBM Granite 13B (via watsonx.ai GovCloud)
- GPT-4o (via Azure OpenAI Government)
- Titan Embeddings G1 (via AWS Bedrock)
- Cohere Command R+ (via AWS Bedrock)

**Decision rule:** If the data contains CUI but not National Security information or classified material, IL4 deployment on any FedRAMP High CSP is acceptable.

### IL5 (CUI + National Security Systems)

**Permitted model deployment environments:**
- Ollama on-prem (air-gapped IL5 accredited enclave) — **preferred**
- vLLM on-prem GPU cluster (air-gapped)
- AWS Bedrock GovCloud with DoD IL5 ATO (limited availability; check current ATOs)
- Azure OpenAI Government with DoD IL5 ATO

**Permitted base models at IL5:**
- Llama 3 70B via Ollama Q4_K_M quantization (48GB VRAM)
- Llama 3 8B via Ollama (16GB VRAM — lower accuracy)
- Qwen3-32B via Ollama (AIMC IL5 recommended for code/structured tasks)
- LLaVA-Phi3 Vision via vLLM (imagery analysis)
- ONNX quantized models (embedded/edge IL5 systems)

**Decision rule:** IL5 data must not traverse unclassified network paths. Ollama local or air-gapped vLLM is the default. Cloud IL5 requires current DoD ATO — verify before design.

### IL6 (SECRET / Special Access Programs)

**Permitted model deployment environments:**
- SIPR-only on-prem inference (no commercial cloud)
- NSA Type 1 encryption enforced at all data paths

**Permitted models at IL6:**
- NSA-reviewed on-prem models only
- Llama 3 (ONNX quantized, offline) with NSA review
- Custom fine-tuned models with NSA Technology Transfer Program (TTP) sign-off

**Decision rule:** No commercial cloud for IL6. All model weights must reside on SIPR infrastructure with NSA Type 1 crypto.

---

## 2 — Task Fit Decision Tree

### Step 1 — What is the primary task?

```
Is the task document/text classification?
  → Yes → Is training data available (>10K labeled examples)?
             → Yes → Fine-tuned classifier (IBM Granite, DistilBERT, XGBoost)
             → No  → Zero-shot RAG (Claude Sonnet, Llama 3)
  
Is the task Q&A or knowledge retrieval?
  → Yes → Does it require retrieving from a document corpus?
             → Yes → RAG pipeline (Claude Sonnet + OpenSearch/FAISS)
             → No  → Direct LLM generation (Claude Sonnet few-shot)
  
Is the task tabular prediction (numeric features)?
  → Yes → XGBoost / LightGBM fine-tune (no LLM needed)
             → Explain predictions? → Add LLM explainer node
  
Is the task image/multimodal analysis?
  → Yes → IL5+? → vLLM LLaVA-Phi3 (air-gap)
           IL4?  → GPT-4o Vision (Azure OpenAI Gov)
  
Is the task anomaly detection over time-series?
  → Yes → Unsupervised: Isolation Forest / LSTM-AE (SageMaker)
           Supervised (labels available): XGBoost binary classifier
```

### Step 2 — What are the latency constraints?

| Constraint | Recommended Approach |
|------------|---------------------|
| <10ms (embedded) | ONNX quantized model, rule-based + ML hybrid |
| <100ms (real-time API) | XGBoost, DistilBERT, Llama 3 8B Q4 |
| <500ms (interactive UI) | Llama 3 70B Q4, Claude Haiku 4.5 |
| <2s (async pipeline) | Claude Sonnet 4.6, Llama 3 70B full |
| >2s (batch overnight) | Any model; GPT-4o for highest quality |

### Step 3 — Is this rights-impacting per OMB M-25-21?

If yes (personnel decisions, eligibility, benefits, enforcement):
- Add `caio-override` node to AADC design
- Set `rights_impacting=1` in AIMC design
- DoD RAI Equitable principle score must be ≥ 70 before deployment
- CAIO review is mandatory before APPROVED lifecycle state
- Demographic parity analysis required in model card

---

## 3 — AIMC Design Fitness Ratings

Fitness ratings assess each ICDEV™ AIMC design against five criteria (1–5 scale):

| Design | IL Fit | Task Fit | Latency | DoD RAI | Rights Risk |
|--------|--------|----------|---------|---------|-------------|
| aimc-dod-001 (Doc Classif.) | IL4 ✓ (4) | High (5) | Good (4) | 88 (4) | Low (1) |
| aimc-dod-002 (SIGINT NLP) | IL5 ✓ (5) | High (5) | Moderate (3) | 90 (4) | Low (1) |
| aimc-dod-003 (Pred. Maint.) | IL4 ✓ (4) | High (5) | Excellent (5) | 84 (4) | Low (1) |
| aimc-dod-004 (Threat Score) | IL4 ✓ (4) | High (5) | Moderate (3) | 82 (4) | Low (1) |
| aimc-dod-005 (FAR Q&A) | IL4 ✓ (5) | Very High (5) | Moderate (3) | 90 (5) | None (0) |
| aimc-dod-006 (Personnel) | IL4 ✓ (4) | High (4) | Good (4) | 72 (3) | **HIGH (5)** |
| aimc-dod-007 (EO SAR) | IL5 ✓ (5) | Very High (5) | Moderate (3) | 88 (4) | Low (1) |
| aimc-dod-008 (AI Audit) | IL4 ✓ (5) | Very High (5) | Moderate (3) | 95 (5) | None (0) |

**Key finding:** `aimc-dod-006` Personnel Readiness is the only design with HIGH rights risk. CAIO review is a mandatory gate — do not demo as "production ready" without noting the DoD RAI equitable audit is pending.

---

## 4 — CSP Selection for DoD Programs

### AWS Bedrock GovCloud (Recommended default for IL4)
- Confirmed FedRAMP High authorization
- Claude Sonnet 4.6 available (Anthropic in Bedrock)
- Prompt caching: 60–71% token cost reduction on policy/FAR/DFARS workloads
- Native AWS GovCloud integration with DoD JWCC contract vehicle

### IBM watsonx.ai GovCloud (Recommended for document classification)
- IBM Granite 13B — optimized for structured document tasks
- Better than GPT-4o on DoD acquisition document classification benchmarks
- Available via JWCC contract; FedRAMP High authorized (Dallas)

### SageMaker GovCloud (Recommended for tabular ML)
- XGBoost, LightGBM, scikit-learn fine-tuning at scale
- Best choice for maintenance prediction, financial anomaly detection
- Native GCSS-Army data pipeline integration via AWS Direct Connect GovCloud

### Ollama On-Prem (Mandatory for IL5)
- Llama 3 70B Q4_K_M: 48GB VRAM, 890ms p99 latency
- Qwen3-32B: Better on structured/JSON tasks than Llama 3 70B
- Zero internet egress — satisfies NSA-AI-003 air-gap mandate
- Deploy on DoD-accredited GPU cluster (A100 80GB ×4 minimum for 70B)

---

## 5 — Quick Reference: Model Recommendation by AIMC Use Case

| Use Case | Recommended Model | Alt Model | IL | Notes |
|----------|-------------------|-----------|-----|-------|
| Doc Classification | IBM Granite 13B LoRA | DistilBERT fine-tune | IL4 | 94.1% accuracy |
| SIGINT NLP | Llama 3 70B (Ollama Q4) | Qwen3-32B | IL5 | Air-gap mandatory |
| Pred. Maintenance | XGBoost + GPT-4o (explain) | LightGBM | IL4 | SHAP required |
| Threat Scoring | Llama 3 70B (Bedrock) | Claude Haiku | IL4 | STIX RAG corpus |
| FAR/DFARS Q&A | Claude Sonnet 4.6 | Claude Haiku 4.5 | IL4 | 62% prompt cache |
| Personnel Readiness | XGBoost + Claude Sonnet (judge) | — | IL4 | CAIO review gate |
| EO Change Detection | LLaVA-Phi3 (vLLM) | GPT-4o Vision | IL5 | Air-gap mandatory |
| AI Audit Response | Claude Sonnet 4.6 | Claude Haiku 4.5 | IL4 | 71% prompt cache |

---

*CUI // SP-CTI — Handle per ICDEV™ classification policy.*
