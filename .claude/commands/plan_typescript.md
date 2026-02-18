# CUI // SP-CTI
# Plan TypeScript Application — ICDEV Framework-Specific Build Command

Generate a comprehensive build plan for a TypeScript application with ICDEV compliance scaffolding.

## Application Name: $ARGUMENTS

## Project Structure
```
$ARGUMENTS/
├── src/
│   ├── index.ts                 # Express entry point
│   ├── config/                  # Environment configuration
│   ├── routes/                  # Express routers
│   ├── controllers/             # Request handlers
│   ├── services/                # Business logic
│   ├── models/                  # TypeORM/Prisma models
│   ├── middleware/              # Auth, logging, error
│   └── types/                   # TypeScript type definitions
├── tests/
│   ├── unit/                    # Jest unit tests
│   ├── integration/             # Supertest integration
│   └── features/                # cucumber-js BDD
│       ├── step_definitions/
│       └── *.feature
├── docker/
│   └── Dockerfile               # STIG-hardened (distroless)
├── k8s/
│   ├── deployment.yaml
│   └── service.yaml
├── .gitlab-ci.yml
├── package.json
├── tsconfig.json
├── jest.config.ts
└── README.md
```

## Technology Stack
- **Framework:** Express 4.x + TypeScript 5.x
- **Testing:** Jest + Supertest + cucumber-js (BDD)
- **Linting:** eslint + @typescript-eslint + eslint-plugin-security
- **SAST:** eslint-plugin-security + njsscan
- **Dependency Audit:** npm audit
- **Formatting:** prettier

## STIG-Hardened Dockerfile
```dockerfile
# CUI // SP-CTI
FROM node:20-slim AS build
WORKDIR /build
COPY package*.json ./
RUN npm ci --only=production
COPY tsconfig.json ./
COPY src/ src/
RUN npm run build

FROM gcr.io/distroless/nodejs20-debian12
COPY --from=build /build/dist /app/dist
COPY --from=build /build/node_modules /app/node_modules
WORKDIR /app
USER nonroot:nonroot
EXPOSE 8080
CMD ["dist/index.js"]
# CUI // SP-CTI
```

## CI/CD Pipeline Stages
1. **lint** — eslint src/ --ext .ts
2. **sast** — eslint-plugin-security + njsscan
3. **test** — jest --coverage
4. **bdd** — cucumber-js features/
5. **audit** — npm audit --audit-level=moderate
6. **sbom** — cyclonedx-npm
7. **build** — Multi-stage Docker (distroless)
8. **deploy** — K8s rolling update
