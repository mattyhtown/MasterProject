FROM python:3.13-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY apex_sharpe/ apex_sharpe/
COPY .env .env

# Verify the package imports
RUN python -c "from apex_sharpe.config import load_config; print('Config OK')"

# Default: run full scan+monitor pipeline
CMD ["python", "-m", "apex_sharpe", "full"]
