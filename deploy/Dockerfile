FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY ontology ./ontology
COPY contracts ./contracts
COPY src ./src
RUN pip install --no-cache-dir .
COPY deploy/docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
