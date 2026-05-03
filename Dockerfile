FROM python:3.10-slim

# Install OpenSCAD + system deps for CadQuery
RUN apt-get update && apt-get install -y --no-install-recommends \
    openscad \
    libgl1-mesa-glx \
    libglu1-mesa \
    libxmu6 \
    libxi6 \
    libxpm4 \
    libxcb1 \
    libxrender1 \
    libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Install CadQuery (bundles pythonocc + OCP)
RUN pip install --no-cache-dir \
    cadquery==2.7.0 \
    fastapi==0.115.0 \
    uvicorn[standard]==0.30.0 \
    python-multipart

WORKDIR /app

COPY server.py .
COPY backends/ ./backends/
COPY webui/ ./webui/

# Projects volume
VOLUME /app/projects

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health')"

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8090"]
