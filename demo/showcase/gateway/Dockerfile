# The showcase gateway. Needs the Docker socket (it starts one container per visitor) and the
# docker CLI, taken from the official CLI image so nothing else comes along.
FROM docker:28-cli AS docker

FROM python:3.12-slim
LABEL org.opencontainers.image.licenses="GPL-3.0-or-later" \
      org.opencontainers.image.source="https://github.com/nano-muse/nanoMuse"

ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_INDEX_URL=${PIP_INDEX_URL}

COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker

WORKDIR /app
COPY pyproject.toml ./
COPY showcase_gateway ./showcase_gateway
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["python", "-m", "showcase_gateway"]
