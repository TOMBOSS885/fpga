"""Preflight the actual model API without starting or installing a model server."""
import json
from agent.client import Client, request
from agent.config import Config
from agent.process import Deadline


class Quiet:
    def log(self, **fields):
        pass


def main():
    config = Config.from_env()
    response = request(config, "/models", None, 10)
    ids = [m.get("id") for m in response.get("body", {}).get("data", [])]
    if not ids or config.model != ids[0]:
        raise SystemExit("Configured model must be the first /models id for the official baseline metadata")
    reply = Client(config, Deadline(30), Quiet()).chat("Reply briefly.", "Reply OK.", "preflight", max_tokens=32)
    print(json.dumps(dict(model=config.model, connected=True, content_present=bool(reply.text),
                          stream=config.stream), ensure_ascii=False))


if __name__ == "__main__":
    main()
