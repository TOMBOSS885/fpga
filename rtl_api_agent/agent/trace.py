from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


def digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".pending")
    with temp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(temp, path)


class Trace:
    def __init__(self, path: Path):
        self.fh = path.open("w", encoding="utf-8", newline="\n")
        self.seq = 0

    def log(self, **fields) -> None:
        self.seq += 1
        self.fh.write(json.dumps(dict(schema=1, seq=self.seq, ts=round(time.time(), 3), **fields), ensure_ascii=False) + "\n")
        self.fh.flush()

    def close(self):
        self.fh.close()
