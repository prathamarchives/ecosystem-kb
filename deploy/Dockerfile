FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY ontology ./ontology
COPY contracts ./contracts
COPY src ./src
COPY migrations ./migrations
RUN pip install --no-cache-dir .
COPY deploy/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# Startup self-check: the production image MUST be able to import the
# asyncpg-backed engine and find its migrations (review P0 regression guard).
RUN python -c "import asyncpg, ecosystem_kb.adapters.postgres.migrations as m; assert m.load_migrations(), 'migrations missing from image'"
ENTRYPOINT ["/entrypoint.sh"]
