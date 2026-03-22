# CUI // SP-CTI
# Phase 64 Extension: Fine-Tuning & Custom Model Training

| Field | Value |
|-------|-------|
| Phase | 64 Extension |
| Status | Complete |
| ADRs | D-FT-1 through D-FT-22 |
| New Tables | 9 (ft_datasets, ft_dataset_examples, ft_training_jobs, ft_training_job_events, ft_model_versions, ft_active_models, ft_evaluations, ft_promotion_log, ft_hyperparam_results) |
| New Files | 20 tool files + 9 templates + 1 API blueprint + 1 config |
| Tests | 282 (7 test files) |
| Dashboard Pages | 9 |
| Security Gate | `fine_tuning` (4 blocking, 4 warning) |
| Marketplace Gate | Gate 10: `training_data_provenance` |

---

## Problem Statement

ICDEV™'s two-tier LLM architecture uses provider-managed models only. The worker tier (qwen3) is general-purpose and produces generic drafts for compliance exports, code generation, and proposal writing. Domain-specific tasks (e.g., GovProposal drafting past volumes, compliance artifact generation) would benefit from models fine-tuned on customer data. There is no mechanism to train, evaluate, serve, or manage custom models within the ICDEV™ ecosystem.

## Goals

1. Enable any ICDEV™ project or child app to train QLoRA fine-tuned models on domain data
2. Automatically slot fine-tuned models into the two-tier architecture as worker-tier replacements
3. Provide pure-Python evaluation (BLEU, ROUGE-L, perplexity) for air-gap environments
4. Support 4 training backends: Unsloth (local), OpenAI, Bedrock, Azure OpenAI
5. Enforce CUI boundary controls (SECRET data cannot go to cloud providers)
6. Publish LoRA adapters as marketplace assets with provenance verification

## Architecture

### Data Pipeline

```
Document Upload (PDF/Word)
    | doc_extractor.py (reuses pdf_provider.py)
Extract Text
    | chunker.py (reuses tools/rag/chunker.py)
Chunk Content
    | pair_generator.py (qwen3 scanner_function)
Generate Q&A Pairs
    | labeler.py + /finetune/label dashboard
Human Label (quality/compliance/relevance 1-5)
    | dataset_manager.py --export
Dataset (JSONL)
    | training_engine.py
Training Job
    | gguf_exporter.py
GGUF -> Ollama
    | evaluator.py + ab_evaluator.py
Evaluation (BLEU/ROUGE/perplexity)
    | promotion_manager.py
Active Model (replaces qwen3 in two-tier)
```

### Provider Pattern (D-FT-1)

```python
class FineTuneProvider(ABC):
    @abstractmethod
    def start_training(self, request: FineTuneRequest) -> FineTuneStatus: ...
    @abstractmethod
    def get_status(self, job_id: str) -> FineTuneStatus: ...
    @abstractmethod
    def cancel_training(self, job_id: str) -> bool: ...
    @abstractmethod
    def list_models(self) -> List[Dict]: ...
    @abstractmethod
    def check_availability(self) -> Dict: ...
    @abstractmethod
    def validate_dataset(self, path: str) -> Dict: ...
```

4 implementations: `UnslothLocalProvider`, `OpenAIFineTuneProvider`, `BedrockFineTuneProvider`, `AzureOpenAIFineTuneProvider`

### Router Integration (D-FT-6)

`_check_finetuned_override(function)` in `tools/llm/router.py` queries `ft_active_models` for a promoted model for the given function. If found, the fine-tuned model via Ollama replaces the default qwen3 worker tier. Claude still reviews the draft as tier-2. This is an additive lookup — if no fine-tuned model is active, falls through to default routing unchanged.

### RAG Bidirectional Integration

1. **RAG -> Finetune**: RAG chunks become training data via `pair_generator.py`
2. **Finetune -> RAG**: Fine-tuned models improve worker-tier draft quality when RAG-augmented
3. **RAG -> Training Context**: RAG context injected via `_rag_augment()` -- fine-tuned model learns to work WITH RAG context

## Database Schema

9 new tables added to `tools/db/init_icdev_db.py`:

| Table | Purpose | Append-Only |
|-------|---------|-------------|
| `ft_datasets` | Versioned training dataset collections | No (status updates) |
| `ft_dataset_examples` | Individual training examples | Yes (D6) |
| `ft_training_jobs` | Training job lifecycle | No (status updates) |
| `ft_training_job_events` | Training events timeline | Yes (D6) |
| `ft_model_versions` | All trained adapter versions | No (status updates) |
| `ft_active_models` | Runtime routing overrides | No (activation/deactivation) |
| `ft_evaluations` | Evaluation results | Yes (D6) |
| `ft_promotion_log` | Promotion/demotion audit trail | Yes (D6) |
| `ft_hyperparam_results` | Hyperparameter search results | Yes (D6) |

## Configuration

`args/finetune_config.yaml` key sections:

- `local`: Unsloth engine, base models, quantization, distributed mode
- `gpu`: Min/preferred VRAM, CPU fallback for serving
- `lora`: Default rank/alpha/target_modules/dropout
- `training`: Learning rate, epochs, batch size, scheduler
- `evaluation`: Auto-eval, metrics, test set split, LLM judge
- `promotion`: Auto-promote thresholds (BLEU >= 0.30, ROUGE-L >= 0.40, perplexity improvement >= 10%)
- `retrain`: Threshold (50 new examples), cooldown (24h)
- `cloud`: OpenAI/Bedrock/Azure configs
- `marketplace`: Model card, SBOM, provenance requirements

## CLI Commands

```bash
# Dataset management
python tools/finetune/dataset_manager.py --create --name "my-dataset" --purpose general --json
python tools/finetune/dataset_manager.py --list --json
python tools/finetune/dataset_manager.py --export --dataset-id "ds-xxx" --output data.jsonl --json

# Pair generation from RAG chunks
python tools/finetune/pair_generator.py --dataset-id "ds-xxx" --source-type rag --json

# Training
python tools/finetune/training_engine.py --dataset-id "ds-xxx" --json
python tools/finetune/training_engine.py --dataset-id "ds-xxx" --provider openai --json

# Evaluation
python tools/finetune/evaluator.py --model-version-id "mv-xxx" --json
python tools/finetune/ab_evaluator.py --model-a "mv-xxx" --model-b "mv-yyy" --json

# Promotion
python tools/finetune/promotion_manager.py --check --model-version-id "mv-xxx" --json
python tools/finetune/promotion_manager.py --promote --model-version-id "mv-xxx" --function code_generation --json

# GPU detection
python tools/finetune/gpu_detector.py --json
```

## Dashboard Pages

| Route | Purpose |
|-------|---------|
| `/finetune` | Overview: stat grid, recent jobs, GPU status, active overrides |
| `/finetune/datasets` | Dataset management: create, list, versions |
| `/finetune/datasets/<id>` | Dataset detail: examples table with labeling |
| `/finetune/label` | Bulk labeling: multi-dimensional scoring, batch approve/reject |
| `/finetune/jobs` | Training jobs: status badges, loss curve SVGs |
| `/finetune/jobs/<id>` | Job detail: live loss curve, hyperparams, export |
| `/finetune/models` | Model versions: eval scores, A/B launcher |
| `/finetune/models/<id>` | Model detail: promotion history, serving status |
| `/finetune/evaluate` | Run evaluation, view A/B comparison |

## Architecture Decision Records

| ADR | Decision |
|-----|----------|
| D-FT-1 | FineTuneProvider ABC with 4 implementations. Graceful ImportError on missing SDKs |
| D-FT-2 | Unsloth as sole local QLoRA engine (MIT, air-gap safe) |
| D-FT-3 | Training job events append-only. Job/model tables allow UPDATE for status |
| D-FT-4 | CUI data cannot leave classification boundary -- cloud blocked for SECRET |
| D-FT-5 | GGUF export via Unsloth with Q4_K_M quantization. Ollama registration |
| D-FT-6 | Additive runtime override via ft_active_models -- does NOT modify llm_config.yaml |
| D-FT-7 | Multi-version coexistence via Ollama model tags |
| D-FT-8 | GPU auto-detection: torch.cuda -> nvidia-smi -> CPU fallback |
| D-FT-9 | Datasets append-only versioned with content-hashed snapshots |
| D-FT-10 | Auto-generate Q&A pairs from RAG chunks via qwen3 |
| D-FT-11 | Document extraction reuses RAG PDF pipeline |
| D-FT-12 | Dashboard labeling UI: quality/compliance/relevance scores (1-5) |
| D-FT-13 | Hyperparameter search: grid/random over LoRA rank, LR, epochs, batch size |
| D-FT-14 | Pure Python BLEU/ROUGE-L/perplexity scoring (air-gap safe) |
| D-FT-15 | A/B evaluation with paired t-test |
| D-FT-16 | Auto-promotion thresholds: BLEU >= 0.30, ROUGE-L >= 0.40, perplexity >= 10% |
| D-FT-17 | Auto-retrain when new_examples >= threshold (default 50) |
| D-FT-18 | LoRA adapters as marketplace asset type with 10-gate pipeline |
| D-FT-19 | Child apps inherit parent's promoted adapters |
| D-FT-20 | Cloud providers: OpenAI, Bedrock, Azure OpenAI. Long-running poll |
| D-FT-21 | Multi-GPU via accelerate library prefix |
| D-FT-22 | Full PROV-AGENT provenance chain from source document to active model |

## Security

### Fine-Tuning Gate

```yaml
fine_tuning:
  blocking:
    - training_data_cui_boundary_violation
    - cloud_finetune_exceeds_classification_level
    - lora_adapter_unsigned_for_marketplace
    - training_data_provenance_missing
  warning:
    - training_dataset_below_min_size
    - eval_score_below_baseline
    - gpu_vram_insufficient
    - auto_retrain_disabled
```

### Marketplace Gate 10: Training Data Provenance

LoRA adapter assets must include:
- `adapter_config.json` with base_model, LoRA rank/alpha/target_modules
- `training_metadata.json` with dataset_id, training_job_id, provenance chain
- Classification marking on training metadata

### CUI Boundary Enforcement (D-FT-4)

Cloud fine-tuning is blocked when `project.classification > cloud_max_classification`. SECRET/TOP_SECRET data can only be fine-tuned locally via Unsloth.

## Testing

```bash
pytest tests/test_finetune_provider.py -v         # ABC contract, factory (21 tests)
pytest tests/test_finetune_gpu_detector.py -v      # CUDA, VRAM, fallback (20 tests)
pytest tests/test_finetune_dataset.py -v           # CRUD, versioning, export (32 tests)
pytest tests/test_finetune_training_engine.py -v   # Job lifecycle, status (65 tests)
pytest tests/test_finetune_evaluator.py -v         # BLEU, ROUGE-L, perplexity (67 tests)
pytest tests/test_finetune_router_integration.py -v # LLM router override (23 tests)
pytest tests/test_finetune_cloud_providers.py -v   # Cloud provider tests (74 tests)

# All tests: 282 total
pytest tests/test_finetune_*.py -v --tb=short
```

## Marketplace Integration

- `lora_adapter` added to `VALID_ASSET_TYPES` in `catalog_manager.py`
- `adapter_config.json` added to `ASSET_TYPE_FILES` in `publish_pipeline.py`
- Gate 10 (`training_data_provenance`) added to `asset_scanner.py` as blocking gate
- Package: adapter_config.json, adapter_model.safetensors, training_metadata.json, model_card.md, SBOM.json

## Child App Integration

- `fine_tuning` added to `CONDITIONAL_DIRS` in `child_app_generator.py`
- `fine_tuning` capability added to `capability_registry.yaml` with `trigger: { flag: "fine_tuning_enabled" }`
- Blueprint generates `fine_tuning: true` when `user_decisions.fine_tuning_enabled`
- During generation: copies `tools/finetune/`, copies promoted adapters, adds FT tables to DB init
