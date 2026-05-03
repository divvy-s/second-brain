# Use Python 3.11 slim as the base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
# Render provides the PORT environment variable at runtime (defaults to 10000)
ENV PORT=10000

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy the rest of the application code first so pyproject.toml can find the packages
COPY . .

# Install python dependencies
RUN pip install --no-cache-dir .

# Download spaCy model
RUN python -m spacy download en_core_web_sm

# Ensure data directory exists and initialize the database
RUN mkdir -p data && python -m scripts.init_db

# Expose port
EXPOSE 10000

# Start the backend using the PORT environment variable provided by Render
CMD ["sh", "-c", "python -m uvicorn api.app:create_app --factory --host 0.0.0.0 --port ${PORT}"]
# Use Python 3.11 slim as the base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive
# Render provides the PORT environment variable at runtime (defaults to 10000)
ENV PORT=10000

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy python dependency files and install
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Download spaCy model
RUN python -m spacy download en_core_web_sm

# Copy the rest of the application code
COPY . .

# Ensure data directory exists and initialize the database
RUN mkdir -p data && python -m scripts.init_db

# Expose port
EXPOSE 10000

# Start the backend using the PORT environment variable provided by Render
CMD ["sh", "-c", "python -m uvicorn api.app:create_app --factory --host 0.0.0.0 --port ${PORT}"]
