# Dockerfile for AMD FPGA 2026 - RTL Multi-Agent V1
ARG BASE_IMAGE=fpgachina2026-base:latest
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV AGENT_MAX_ROUNDS=4
ENV AGENT_DEADLINE_S=360

WORKDIR /app

# Copy dependencies and install
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy complete agent and skills package
COPY manifest.json /app/
COPY model/ /app/model/
COPY agent/ /app/agent/
COPY skill/ /app/skill/
COPY serve/ /app/serve/
COPY baseline.py /app/
COPY run.sh /app/
COPY run_baseline.sh /app/
COPY REPORT.md /app/

RUN chmod +x /app/run.sh /app/run_baseline.sh /app/baseline.py

CMD ["python3", "-m", "serve.serve_api"]
