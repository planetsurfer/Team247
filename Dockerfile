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

EXPOSE 8000

# Run migrations, idempotently seed the catalog, then serve the FastAPI app on localhost.
ENTRYPOINT ["sh", "-c", "alembic -c app/alembic/alembic.ini upgrade head && python -m app.seed_catalog --idempotent && uvicorn app.main:app --host 127.0.0.1 --port 8000"]
