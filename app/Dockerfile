FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .[browser] || true

# Now the source and a real editable/normal install so the `scraper` script exists.
COPY . .
RUN pip install --no-cache-dir .[browser]

# The app is not a server: ofelia execs `scraper run-due` into this container.
# Keep it alive and idle.
CMD ["tail", "-f", "/dev/null"]
