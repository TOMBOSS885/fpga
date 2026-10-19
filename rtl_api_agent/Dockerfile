# Supply the actual official gfx1100/gfx1201 tag announced for your window.
# docker build --build-arg BASE_IMAGE=<official-image:tag> -t rtl-api-agent .
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
WORKDIR /opt/rtl-agent

# Runtime is stdlib-only; there are no pip/network installs at startup/build.
COPY agent/ agent/
COPY skill/ skill/
COPY serve/ serve/
COPY model/ model/
COPY run.sh run_baseline.sh baseline.py manifest.json REPORT.md README.md LICENSE ./
RUN python3 -c "import sys; assert sys.version_info >= (3,10)" \
    && chmod +x run.sh run_baseline.sh serve/start.sh \
    && python3 -c "import hashlib; assert hashlib.sha256(open('baseline.py','rb').read()).hexdigest() == '537783e39db22079c33c19af475dbdb760a29ff1656a48c481d434131f84fb51'"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LLM_BASE_URL=http://127.0.0.1:8000/v1 \
    LLM_CONTEXT_TOKENS=16384 \
    AGENT_DEADLINE_S=360 \
    RTL_PART=xczu3eg-sbva484-1-e \
    RTL_PERIOD_NS=5

# Inherit EDA PATH/XILINX_VIVADO/EDA_TMP from the official image.
# Mount model weights on local storage before the evaluation network is cut.
EXPOSE 7860
CMD ["/bin/bash"]
