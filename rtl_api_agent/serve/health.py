"""Readiness is measured, not assumed from an alive HTTP process."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from agent.client import request
from agent.config import Config
from agent.tools import Toolchain
from .measure_vram import measure


class Probe:
    def __init__(self):
        self.lock, self.cached, self.checked = threading.Lock(), {}, 0.0

    def check(self):
        with self.lock:
            if time.monotonic() - self.checked < 10:
                return self.cached
            result = dict(ready=False, track="rtl", model="unconfigured", vram_gb=0.0)
            try:
                config = Config.from_env()
                result["model"] = config.model
                response = request(config, "/models", None, 4)
                models = response.get("body", {}).get("data", [])
                # The immutable official baseline reports the first /models id.
                found = bool(models) and models[0].get("id") == config.model
                if found:
                    generated = request(config, "/chat/completions", dict(model=config.model,
                        messages=[{"role": "user", "content": "Reply OK."}], max_tokens=4,
                        temperature=0, stream=False), 5)
                    choices = generated.get("body", {}).get("choices", [])
                    found = generated.get("status") == 200 and bool(choices)
                missing = [k for k, v in Toolchain(config, Path("."), None).binaries.items() if not v]
                # Remote GPU memory must be provided by a trusted measurement
                # exporter on the model host, not invented by the agent host.
                measured = False
                try:
                    if os.environ.get("VRAM_METRICS_FILE"):
                        metrics = json.loads(Path(os.environ["VRAM_METRICS_FILE"]).read_text())
                    else:
                        metrics = measure(card=os.environ.get("GPU_DRM_CARD", ""))
                    age = time.time() - float(metrics["timestamp"])
                    memory = float(metrics["vram_gb"])
                    measured = 0 <= age <= 60 and 0 < memory <= 32
                    if measured:
                        result["vram_gb"] = memory
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                result.update(ready=found and not missing and measured,
                              model_responding=found, missing_tools=missing, vram_measured=measured)
            except Exception:
                result["detail"] = "Configuration, model connectivity or GPU metrics not ready"
            self.cached, self.checked = result, time.monotonic()
            return result
