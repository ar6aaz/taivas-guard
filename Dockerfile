FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir uv==0.13.0

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY ific_guard ./ific_guard
# Not under /app/data: compose mounts a volume there, which would hide the table.
COPY data/national/m4_bands.csv ./national/m4_bands.csv
ENV IFIC_M4_FILTER_PATH=/app/national/m4_bands.csv
COPY docs/challenge/Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx ./docs/challenge/Traficom_SinceAI_Frequencies_of_interest_v05112026.xlsx
COPY data/masks ./masks
ENV IFIC_MASK_DIR=/app/masks
# Outside /app/data, which compose mounts a volume over.
COPY data/refdocs ./refdocs
ENV IFIC_REFDOCS_DIR=/app/refdocs
RUN uv sync --frozen --no-dev

RUN useradd --create-home app && mkdir -p /app/data && chown -R app /app/data
USER app
ENV PATH="/app/.venv/bin:$PATH"
