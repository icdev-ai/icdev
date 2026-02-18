# CUI // SP-CTI
# Plan C# Application — ICDEV Framework-Specific Build Command

Generate a comprehensive build plan for a C# application with ICDEV compliance scaffolding.

## Application Name: $ARGUMENTS

## Project Structure
```
$ARGUMENTS/
├── src/
│   ├── $ARGUMENTS.Api/
│   │   ├── Program.cs           # ASP.NET Core entry
│   │   ├── Controllers/         # API controllers
│   │   ├── Services/            # Business logic
│   │   ├── Models/              # Data models
│   │   ├── Data/                # EF Core DbContext
│   │   └── Middleware/          # Custom middleware
│   └── $ARGUMENTS.Core/
│       ├── Interfaces/          # Service interfaces
│       └── Entities/            # Domain entities
├── tests/
│   ├── $ARGUMENTS.UnitTests/    # xUnit unit tests
│   ├── $ARGUMENTS.IntTests/     # Integration tests
│   └── $ARGUMENTS.BddTests/    # SpecFlow BDD tests
│       └── Features/            # Gherkin features
├── docker/
│   └── Dockerfile               # STIG-hardened
├── k8s/
│   ├── deployment.yaml
│   └── service.yaml
├── .gitlab-ci.yml
├── $ARGUMENTS.sln
└── README.md
```

## Technology Stack
- **Framework:** ASP.NET Core 8.0
- **Testing:** xUnit + Moq + SpecFlow (BDD)
- **Linting:** dotnet analyzers + StyleCop
- **SAST:** SecurityCodeScan
- **Dependency Audit:** dotnet list package --vulnerable
- **Formatting:** dotnet format

## STIG-Hardened Dockerfile
```dockerfile
# CUI // SP-CTI
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /build
COPY *.sln ./
COPY src/**/*.csproj ./src/
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /publish

FROM mcr.microsoft.com/dotnet/aspnet:8.0-alpine
RUN addgroup -S appgroup && adduser -S appuser -G appgroup
COPY --from=build /publish /app
WORKDIR /app
USER appuser
EXPOSE 8080
ENTRYPOINT ["dotnet", "$ARGUMENTS.Api.dll"]
# CUI // SP-CTI
```

## CI/CD Pipeline Stages
1. **lint** — dotnet format --verify-no-changes
2. **sast** — SecurityCodeScan analysis
3. **test** — dotnet test (xUnit)
4. **bdd** — dotnet test --filter Category=BDD (SpecFlow)
5. **audit** — dotnet list package --vulnerable
6. **sbom** — CycloneDX dotnet plugin
7. **build** — Multi-stage Docker
8. **deploy** — K8s rolling update
