ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --prefix=/install .

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv && addgroup --system --gid 10001 bot && adduser --system --uid 10001 --ingroup bot --home /nonexistent --no-create-home bot
COPY --from=builder /install/ /opt/venv/
COPY --chmod=0555 deployment/production/app-entrypoint.sh /usr/local/bin/app-entrypoint
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/app-entrypoint"]
