"""Real ASGI app over stdio for browser tests; no TCP port or API fixtures.

Each JSON line contains an HTTP request. Responses carry the actual status,
headers, and base64 body returned by FastAPI's TestClient and worker thread.
"""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="autotuner-browser-") as directory:
        os.environ["AUTOTUNER_DATA_DIR"] = directory
        os.environ["AUTOTUNER_ORCHESTRATOR"] = "plain"
        # Explicit opt-in is required to spend OpenAI credits in browser QA.
        os.environ["AUTOTUNER_LLM_MODE"] = "openai" if "--live-openai" in sys.argv else "off"
        from fastapi.testclient import TestClient
        from autotuner.api import app
        with TestClient(app, raise_server_exceptions=False) as client:
            print(json.dumps({"ready": True}), flush=True)
            for line in sys.stdin:
                request = json.loads(line)
                response = client.request(request["method"], request["url"],
                                          content=base64.b64decode(request.get("body", "")),
                                          headers=request.get("headers", {}))
                headers = {key: value for key, value in response.headers.items()
                           if key.lower() not in {"content-length", "content-encoding", "transfer-encoding"}}
                print(json.dumps({"id": request["id"], "status": response.status_code,
                                  "headers": headers, "body": base64.b64encode(response.content).decode()}), flush=True)


if __name__ == "__main__":
    main()
