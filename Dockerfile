FROM python:3.12-slim
WORKDIR /src
COPY pyproject.toml README.md ./
COPY pg_ledger_sheets pg_ledger_sheets
RUN pip install --no-cache-dir . && rm -rf /src
WORKDIR /
USER 10001:10001
ENTRYPOINT ["pg-ledger-sheets"]
