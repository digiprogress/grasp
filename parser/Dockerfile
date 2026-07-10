# Grasp Parser — tree-sitter code analysis pipeline (local HTTP daemon).

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git build-essential && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY handler.py .
COPY local_entry.py .
COPY run_local.py .
COPY modules/ ./modules/

CMD ["python", "-u", "local_entry.py"]
