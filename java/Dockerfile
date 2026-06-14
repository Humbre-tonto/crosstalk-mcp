# ---- build ----
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn -q -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -q -DskipTests package

# ---- run ----
FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /app/target/crosstalk-mcp.jar app.jar
# Persist the SQLite db on a volume; set RELAY_TOKEN to require auth.
ENV PORT=8765 \
    RELAY_DB=/data/relay.db
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["java", "-jar", "app.jar"]
