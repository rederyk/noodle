FROM python:3.10-slim

# System libs OCCT/build123d needs at runtime (OpenGL/X stack for cadquery-ocp)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libegl1 \
    libglu1-mesa \
    libxmu6 \
    libxi6 \
    libxpm4 \
    libxcb1 \
    libxrender1 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Install runtime deps from requirements.txt (single source of truth — keeps the
# host venv and the image identical). build123d bundles its matching
# cadquery-ocp / OCCT; cadquery 2.7.0 pins an incompatible OCP build, so
# build123d is the B-Rep backend (per PLAN_NODE_CAD.md, it replaces cadquery).
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt

COPY server.py .
COPY mcp_server.py .
COPY cad_nodes/ ./cad_nodes/
COPY webui/ ./webui/

# Run as a non-root user. UID/GID 1000 match the typical host user so the
# bind-mounted ./projects and ./feedback stay editable from the host without
# sudo (chown existing dirs once: `sudo chown -R 1000:1000 projects feedback`).
RUN useradd --uid 1000 --user-group --create-home noodle \
    && mkdir -p /app/projects /app/feedback \
    && chown -R noodle:noodle /app
USER noodle

# Projects volume
VOLUME /app/projects

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8090"]
