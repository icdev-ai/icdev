# Fine-Tuning (Phase 64 Extension)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Fine-Tuning (Phase 64 Extension)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| A/B Evaluator | tools/finetune/ab_evaluator.py | Model A/B comparison (D-FT-15) | --model-a, --model-b, --json | Comparison results |
| Azure Provider | tools/finetune/azure_provider.py | Azure OpenAI fine-tuning provider (D-FT-20) | (library) | AzureOpenAIFineTuneProvider |
| Dataset Manager | tools/finetune/dataset_manager.py | Dataset CRUD and versioning (D-FT-9) | --create, --list, --export, --json | Dataset records |
| Doc Extractor | tools/finetune/doc_extractor.py | Document extraction for training pairs (D-FT-11) | --extract, --json | Extracted text |
| GGUF Exporter | tools/finetune/gguf_exporter.py | GGUF model export with Q4_K_M quantization (D-FT-5) | (library) | GGUF files |
| GPU Detector | tools/finetune/gpu_detector.py | GPU auto-detection for training (D-FT-8) | --json | GPU info |
| Labeler | tools/finetune/labeler.py | Dataset example labeling engine (D-FT-12) | (library) | Label results |
| Model Registry | tools/finetune/model_registry.py | Fine-tuned model version tracking (D-FT-7) | (library) | Model records |
| Pair Generator | tools/finetune/pair_generator.py | Q&A training pair generation from RAG (D-FT-10) | --generate-filtered, --dataset-id, --json | Training pairs |
| Promotion Manager | tools/finetune/promotion_manager.py | Model auto-promotion pipeline (D-FT-16) | --check, --promote, --json | Promotion results |
| Retrain Trigger | tools/finetune/retrain_trigger.py | Auto-retrain when threshold exceeded (D-FT-17) | --check, --json | Trigger status |
| Training Engine | tools/finetune/training_engine.py | Unsloth/cloud QLoRA training (D-FT-2) | --dataset-id, --provider, --json | Training job |
| Unsloth Provider | tools/finetune/unsloth_provider.py | Local Unsloth QLoRA provider (D-FT-2) | (library) | UnslothLocalProvider |
| RAG-FT Pipeline | tools/finetune/rag_ft_pipeline.py | Automated RAG-to-FT pipeline (D-KARL-5) | --run, --dry-run, --status, --json | Pipeline results |
| KG Pair Generator | tools/finetune/kg_pair_generator.py | KG community-based FT pair generation (D-KARL-6) | --graph-id, --dataset-id, --strategy, --json | Generated pairs |
| Quality Monitor | tools/finetune/quality_monitor.py | RAG eval feedback loop with retrain triggers (D-KARL-8) | --check, --status, --json | Quality status |
| HP Search | tools/finetune/hp_search.py | Hyperparameter search orchestrator for fine-tuning (grid/random search over LoRA params) | --create, --run-next, --record, --status, --list, --json | Search/trial results |
| Trajectory Capture | tools/finetune/trajectory_capture.py | Auto-capture successful agent tool-call traces as ShareGPT JSONL training data; compliance/build workflows → RL trajectories stored in ft_trajectories + ft_trajectory_steps (append-only) | --start --workflow-type --task, --record --session-id --tool --input --output, --finalize --session-id --outcome --reward --response, --export --output-path --min-reward --workflow-types, --ingest --session-id --dataset-id, --stats, --health --gate, --json | Captured trajectories + ShareGPT JSONL export |
| Provider ABC | tools/finetune/provider.py | FineTuneProvider ABC and supporting dataclasses (FineTuneRequest, FineTuneStatus) — base interface for all fine-tune provider implementations | (library) | FineTuneProvider ABC |
| Provider Factory | tools/finetune/provider_factory.py | FineTuneProvider factory with GPU auto-detection; selects Unsloth (GPU), OpenAI/Bedrock/Azure (cloud), or CPU-only Ollama | (library) | get_provider() |
| OpenAI Provider | tools/finetune/openai_provider.py | OpenAI fine-tuning provider using /v1/fine_tuning/jobs API with long-running poll pattern | (library) | OpenAIFineTuneProvider |
| Bedrock Provider | tools/finetune/bedrock_provider.py | AWS Bedrock fine-tuning provider using create_model_customization_job API; enforces CUI boundary constraints on cloud training | (library) | BedrockFineTuneProvider |
| Evaluator | tools/finetune/evaluator.py | Automated model evaluator with BLEU, ROUGE-L, and perplexity scoring (pure Python, air-gap safe); optional LLM-as-judge | --evaluate, --model-version-id, --get, --eval-id, --list, --json | Evaluation scores |
| GovCon Pair Generator | tools/finetune/govcon_pair_generator.py | Deterministic Q&A pair templates for GovCon proposal sections; 3 pairs/section, no LLM call | (library) | list[{system_prompt, user_input, expected_output}] |
| GovCon FT Pipeline | tools/finetune/govcon_ft_pipeline.py | Local QLoRA fine-tuning on GovCon proposals (Qwen2.5-1.5B + 4-bit bitsandbytes); saves LoRA adapter | --dataset, --model-path, --output, --epochs, --dry-run, --json | Adapter + eval_metrics.json |
| Bedrock FT Pipeline | tools/finetune/bedrock_ft_pipeline.py | AWS Bedrock model customization; exports ft_dataset_examples as JSONL, uploads to S3, creates job, polls | --dataset, --base-model, --s3-bucket, --dry-run, --json | {status, job_arn, model_arn} |
| Model Weights Packager | tools/finetune/package_model_weights.py | Packages HuggingFace weights as pip wheel for air-gapped internal mirrors | --model, --output, --dry-run, --json | .whl file in dist/ |

