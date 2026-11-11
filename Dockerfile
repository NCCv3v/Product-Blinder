FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd -r blinder && useradd -r -g blinder -m blinder
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY examples ./examples
RUN pip install . && mkdir -p data && chown -R blinder:blinder /app
USER blinder
# CLI engine image only. Native desktop runs on the host; no web port/server.
ENTRYPOINT ["product-blinder"]
CMD ["--help"]
