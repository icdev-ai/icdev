# CUI // SP-CTI
# Plan Rust Application — ICDEV Framework-Specific Build Command

Generate a comprehensive build plan for a Rust application with ICDEV compliance scaffolding.

## Application Name: $ARGUMENTS

## Project Structure
```
$ARGUMENTS/
├── src/
│   ├── main.rs                  # Entry point
│   ├── config.rs                # Configuration
│   ├── routes/                  # Actix-web routes
│   ├── models/                  # Data models (serde)
│   ├── services/                # Business logic
│   └── middleware/              # Auth, logging
├── tests/
│   ├── unit/                    # Unit tests
│   ├── integration/             # Integration tests
│   └── features/                # cucumber-rs BDD
├── docker/
│   └── Dockerfile               # STIG-hardened (scratch)
├── k8s/
│   ├── deployment.yaml
│   └── service.yaml
├── .gitlab-ci.yml
├── Cargo.toml
├── Cargo.lock
└── README.md
```

## Technology Stack
- **Framework:** Actix-web 4.x
- **Testing:** cargo test + cucumber-rs (BDD)
- **Linting:** clippy
- **SAST:** cargo-audit
- **Dependency Audit:** cargo-audit
- **Formatting:** rustfmt

## STIG-Hardened Dockerfile
```dockerfile
# CUI // SP-CTI
FROM rust:1.77-slim AS build
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs && cargo build --release && rm -rf src
COPY src/ src/
RUN cargo build --release

FROM gcr.io/distroless/cc-debian12
COPY --from=build /build/target/release/$ARGUMENTS /app
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/app"]
# CUI // SP-CTI
```

## CI/CD Pipeline Stages
1. **lint** — cargo clippy -- -D warnings
2. **sast** — cargo audit
3. **test** — cargo test
4. **bdd** — cargo test --test cucumber
5. **format** — cargo fmt --check
6. **sbom** — cargo cyclonedx
7. **build** — Multi-stage Docker
8. **deploy** — K8s rolling update
