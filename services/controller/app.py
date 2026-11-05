"""Demo controller: lets the Streamlit portal stop/start ONE service to stage an outage.

It talks to the Docker Engine API through the host socket and refuses anything outside ALLOWED.
Only reachable inside the Compose network (no published port).
"""
import json
import os

import httpx
from fastapi import FastAPI, HTTPException

ALLOWED = {"prediction_api"}
SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
app = FastAPI(title="Demo controller")


def docker():
    return httpx.Client(transport=httpx.HTTPTransport(uds=SOCKET), base_url="http://docker", timeout=30)


def container_id(client, service):
    if service not in ALLOWED:
        raise HTTPException(status_code=403, detail=f"{service} cannot be controlled")
    filters = json.dumps({"label": [f"com.docker.compose.service={service}"]})
    found = client.get("/containers/json", params={"all": 1, "filters": filters}).json()
    if not found:
        raise HTTPException(status_code=404, detail="container not found")
    return found[0]["Id"], found[0]["State"]


@app.get("/services/{service}")
def status(service: str):
    with docker() as client:
        _, state = container_id(client, service)
        return {"service": service, "state": state}


@app.post("/services/{service}/{action}")
def act(service: str, action: str):
    if action not in {"stop", "start"}:
        raise HTTPException(status_code=400, detail="action must be stop or start")
    with docker() as client:
        cid, _ = container_id(client, service)
        response = client.post(f"/containers/{cid}/{action}", params={"t": 2})
        if response.status_code not in (204, 304):
            raise HTTPException(status_code=502, detail=response.text)
        return {"service": service, "action": action, "ok": True}
