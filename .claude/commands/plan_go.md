# [TEMPLATE: CUI // SP-CTI]
# Plan Go Application — ICDEV Framework-Specific Build Command

Generate a comprehensive build plan for a Go application with ICDEV compliance scaffolding.

## Application Name: $ARGUMENTS

## Project Structure
```
$ARGUMENTS/
├── cmd/
│   └── server/
│       └── main.go              # Entry point
├── internal/
│   ├── config/                  # Configuration
│   ├── handler/                 # HTTP handlers
│   ├── middleware/               # Auth, logging, CORS
│   ├── model/                   # Data models
│   ├── repository/              # Data access
│   └── service/                 # Business logic
├── pkg/                         # Shared libraries
├── test/
│   ├── unit/                    # Unit tests
│   ├── integration/             # Integration tests
│   └── features/                # Godog BDD features
├── docker/
│   └── Dockerfile               # STIG-hardened (scratch)
├── k8s/
│   ├── deployment.yaml
│   └── service.yaml
├── .gitlab-ci.yml
├── go.mod
├── go.sum
├── Makefile
└── README.md
```

## Technology Stack
- **Framework:** net/http (stdlib) or Gin
- **Testing:** testing + testify + godog (BDD)
- **Linting:** golangci-lint
- **SAST:** gosec
- **Dependency Audit:** govulncheck
- **Formatting:** gofmt + goimports

## STIG-Hardened Dockerfile
```dockerfile
# CUI // SP-CTI
FROM golang:1.22-alpine AS build
RUN apk add --no-cache ca-certificates
WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /app ./cmd/server

FROM scratch
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=build /app /app
USER 65534:65534
EXPOSE 8080
ENTRYPOINT ["/app"]
# CUI // SP-CTI
```

## CI/CD Pipeline Stages
1. **lint** — golangci-lint run
2. **sast** — gosec ./...
3. **test** — go test ./... -cover -race
4. **bdd** — godog run test/features/
5. **audit** — govulncheck ./...
6. **sbom** — cyclonedx-gomod
7. **build** — Multi-stage Docker (scratch base)
8. **deploy** — K8s rolling update
