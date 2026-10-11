FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/nanomuse-cloud

COPY pyproject.toml README.md ./
COPY nanomuse_cloud ./nanomuse_cloud
# The relay runs as `relay`, uid 10001, not root. The uid is fixed so a bind-mounted data
# directory can be given to it on the host (chown -R 10001:10001) before the first start.
RUN pip install --no-cache-dir . \
 && groupadd --gid 10001 relay \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /srv/nanomuse-cloud --shell /usr/sbin/nologin relay \
 && mkdir -p /srv/nanomuse-cloud/data \
 && chown -R relay:relay /srv/nanomuse-cloud

ENV CLOUD_DB=/srv/nanomuse-cloud/data/cloud.db
VOLUME ["/srv/nanomuse-cloud/data"]
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).status == 200 else 1)"

USER relay
CMD ["nanomuse-cloud", "--host", "0.0.0.0", "--port", "8787"]
