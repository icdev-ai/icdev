# CUI // SP-CTI
# The fuzz image for AI-assisted vulnerability discovery (icx-ent-01): the pinned base image plus atheris
# (Google's coverage-guided Python fuzzer, Apache-2.0), pinned by version AND wheel hash. Built with the network
# on the HOST; every run from it has none (SandboxExecutor.run_isolated). Optional: without it the built-in
# coverage-guided fuzzer runs in the plain base image.
#   docker build -t icdev-vuln-sandbox:atheris-3.1.0 deploy/vuln-sandbox
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt && rm /tmp/requirements.txt
USER 65534:65534
