# Use Python 3.11 slim as the base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies, including Node.js and npm
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy python dependency files and install
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Download spaCy model
RUN python -m spacy download en_core_web_sm

# Copy frontend dependency files and install
COPY frontend/package.json frontend/package-lock.json* ./frontend/
RUN cd frontend && npm install

# Copy the rest of the application code
COPY . .

# Ensure data directory exists and initialize the database
RUN mkdir -p data && python -m scripts.init_db

# Build the frontend
RUN cd frontend && npm run build

# Expose ports
# Backend: 8000
# Frontend: 5173
EXPOSE 8000 5173

# Create a start script to run both backend and frontend
RUN echo '#!/bin/sh\n\
# Start the backend in the background\n\
echo "Starting Backend API on port 8000..."\n\
python -m uvicorn api.app:create_app --factory --host 0.0.0.0 --port 8000 &\n\
\n\
# Start the frontend\n\
echo "Starting Frontend on port 5173..."\n\
cd frontend && npm run preview -- --host 0.0.0.0 --port 5173\n\
' > /app/start.sh && chmod +x /app/start.sh

# Run the start script
CMD ["/app/start.sh"]
