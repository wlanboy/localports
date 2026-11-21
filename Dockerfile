ARG PYTHON_TAG=3.11-slim
ARG KUBECTL_VERSION=v1.37.2

# kubectl aus dem offiziellen Image (Multi-Arch, statisch gebaut)
FROM registry.k8s.io/kubectl:${KUBECTL_VERSION} AS kubectl

# Build stage: keine Laufzeit-Abhängigkeiten, nur Bytecode vorkompilieren
FROM python:${PYTHON_TAG} AS build

WORKDIR /app

COPY main.py /app/
COPY src/localports/ /app/localports/
RUN python -m compileall -q --invalidation-mode checked-hash /app

# Runtime stage
FROM python:${PYTHON_TAG}

ARG REVISION=unknown

LABEL org.opencontainers.image.title="localports" \
      org.opencontainers.image.description="Local port forwarding for kubernetes services" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.vendor="wlanboy" \
      org.opencontainers.image.source="https://github.com/wlanboy/localports" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="python:3.11-slim"

# HOME=/tmp: kubectl legt dort seinen Discovery-Cache ab, das Root-Dateisystem
# darf read-only sein. Die kubeconfig wird nach /kube/config gemountet.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    KUBECONFIG=/kube/config

# Create non-root user
RUN groupadd -r -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

WORKDIR /app

COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
COPY --from=build /app /app

USER appuser

ENTRYPOINT ["python", "/app/main.py"]
