#!/bin/bash
# Start llama.cpp server on port 8081
MODEL_PATH="${MODEL_PATH:-/opt/ml/model/prometheus-7b-v2.0.Q4_K_M.gguf}"
N_CTX="${N_CTX:-4096}"
N_THREADS="${N_THREADS:-4}"

echo "Starting llama.cpp server with model: ${MODEL_PATH}"
exec llama-server \
  --model "${MODEL_PATH}" \
  --port 8081 \
  --host 0.0.0.0 \
  --ctx-size "${N_CTX}" \
  --threads "${N_THREADS}" \
  --log-disable
