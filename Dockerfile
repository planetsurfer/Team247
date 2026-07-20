FROM python:3.12-slim

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole repo: app/ package + flat root modules + demo.html + data/ seeds.
COPY app/ /app/app/
COPY framework.py teamspec.py agent.py refine.py classify.py config.py assess.py battery.py candidate.py distill_enrichment.py run.py dashboard.py events.py gap.py smoke_test.py test_local.py /app/
COPY demo.html /app/demo.html
COPY data/ /app/data/

ENV PYTHONPATH=/app
# Read-only root FS in prod (docker-compose.prod.yml) means stray .pyc writes would
# just fail silently anyway; skip them outright, and give the non-root user a
# writable $HOME on the /tmp tmpfs so any incidental cache write has somewhere to go.
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/tmp

# Container hardening (PRODUCTION_ROADMAP.md P0 #3): non-root user, uid/gid 10001.
# /app/data is the bind-mount point for the host ./data dir (docker-compose.*.yml
# `volumes: ./data:/app/data`) — its on-disk contents here are only the seed data
# baked into the image for the plain `docker-compose.yml` (named-volume) path; the
# chown below covers that case. For the bind-mount case, the *host* dir's owner is
# what actually governs writability by uid 10001 inside the container:
#   - macOS Docker Desktop: bind-mount permissions are mapped loosely (any host
#     owner works) — no action needed for local verification.
#   - Amazon Linux 2023 EC2: run `sudo chown -R 10001:10001 ./data` once before the
#     first `docker compose up` (documented again in docker-compose.prod.yml and the
#     iteration-3 deploy docs/scripts).
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER app

EXPOSE 8000

# Run migrations, idempotently seed the catalog, then serve the FastAPI app on
# localhost. All three steps run fine as the non-root `app` user — none need root.
ENTRYPOINT ["sh", "-c", "alembic -c app/alembic/alembic.ini upgrade head && python -m app.seed_catalog --idempotent && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
