# BYOC container for Prometheus-2 7B GGUF on SageMaker
# Uses llama.cpp for efficient CPU inference
# Based on: https://github.com/aws-samples/deploy-gguf-model-to-sagemaker

FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    python3 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Build llama.cpp server
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp.git /opt/llama.cpp \
    && cd /opt/llama.cpp \
    && cmake -B build \
    && cmake --build build --config Release -j$(nproc) \
    && cp build/bin/llama-server /usr/local/bin/

# Install Python deps for SageMaker wrapper
RUN pip3 install flask requests boto3

# Copy SageMaker wrapper
COPY serve /opt/program/serve
COPY main.py /opt/program/main.py
COPY server.sh /opt/program/server.sh

RUN chmod +x /opt/program/serve /opt/program/server.sh

# SageMaker expects model at /opt/ml/model
ENV MODEL_DIR=/opt/ml/model
ENV MODEL_PATH=/opt/ml/model/prometheus-7b-v2.0.Q4_K_M.gguf
ENV N_CTX=4096
ENV N_THREADS=4
ENV PORT=8081

WORKDIR /opt/program

EXPOSE 8080

ENTRYPOINT ["python3", "/opt/program/serve"]
