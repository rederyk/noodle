FROM python:3.10-slim

# Install OpenSCAD + system deps for CadQuery
RUN apt-get update && apt-get install -y --no-install-recommends \
    openscad \
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

# Install build123d (bundles its matching cadquery-ocp / OCCT) + node engine deps.
# NOTE: cadquery 2.7.0 pins an OCP build incompatible with build123d, so build123d
# is now the B-Rep backend (per PLAN_NODE_CAD.md, it replaces cadquery).
RUN pip install --no-cache-dir --timeout 120 --retries 5 \
    build123d \
    numpy \
    mcp \
    fastapi==0.115.0 \
    uvicorn[standard]==0.30.0 \
    python-multipart

WORKDIR /app

COPY server.py .
COPY mcp_server.py .
COPY backends/ ./backends/
COPY cad_nodes/ ./cad_nodes/
COPY webui/ ./webui/

# Projects volume
VOLUME /app/projects

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8090"]
