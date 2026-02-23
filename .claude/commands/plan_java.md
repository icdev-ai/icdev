# [TEMPLATE: CUI // SP-CTI]
# Plan Java Application — ICDEV Framework-Specific Build Command

Generate a comprehensive build plan for a Java application with ICDEV compliance scaffolding.

## Application Name: $ARGUMENTS

## Project Structure
```
$ARGUMENTS/
├── src/main/java/gov/icdev/
│   ├── Application.java         # Spring Boot entry point
│   ├── config/                  # Configuration classes
│   ├── controller/              # REST controllers
│   ├── service/                 # Business logic
│   ├── repository/              # Data access (JPA)
│   ├── model/                   # Entity/DTO classes
│   └── security/                # Security configuration
├── src/main/resources/
│   ├── application.yml          # Spring config
│   └── db/migration/            # Flyway migrations
├── src/test/java/gov/icdev/
│   ├── unit/                    # JUnit 5 unit tests
│   └── integration/             # Spring Boot integration tests
├── src/test/resources/
│   └── features/                # Cucumber-JVM BDD features
├── docker/
│   └── Dockerfile               # STIG-hardened (distroless)
├── k8s/
│   ├── deployment.yaml
│   └── service.yaml
├── .gitlab-ci.yml
├── pom.xml                      # Maven build
└── README.md
```

## Technology Stack
- **Framework:** Spring Boot 3.x
- **Testing:** JUnit 5 + Mockito + Cucumber-JVM (BDD)
- **Linting:** checkstyle + PMD
- **SAST:** SpotBugs + Find Security Bugs
- **Dependency Audit:** OWASP Dependency-Check
- **Formatting:** google-java-format
- **Build:** Maven 3.9+

## STIG-Hardened Dockerfile
```dockerfile
# CUI // SP-CTI
FROM eclipse-temurin:17-jdk AS build
WORKDIR /build
COPY pom.xml .
RUN mvn dependency:go-offline
COPY src/ src/
RUN mvn package -DskipTests

FROM gcr.io/distroless/java17-debian12
COPY --from=build /build/target/*.jar /app/app.jar
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
# CUI // SP-CTI
```

## CI/CD Pipeline Stages
1. **lint** — checkstyle + PMD
2. **sast** — SpotBugs + Find Security Bugs
3. **test** — mvn test (JUnit 5)
4. **bdd** — mvn verify -Pcucumber
5. **audit** — OWASP Dependency-Check
6. **sbom** — CycloneDX Maven plugin
7. **build** — Docker multi-stage build
8. **deploy** — K8s rolling update
