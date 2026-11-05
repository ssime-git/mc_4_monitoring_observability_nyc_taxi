"""Fake notification receiver for the demo (stands in for Slack / PagerDuty)."""
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, Request

app = FastAPI(title="Demo alert receiver")
notifications: deque = deque(maxlen=200)


@app.post("/alerts")
async def receive(request: Request):
    body = await request.json()
    for alert in body.get("alerts", []):
        notifications.appendleft({
            "received_at": datetime.now(timezone.utc).isoformat(),
            "status": alert["status"],
            "alertname": alert["labels"].get("alertname"),
            "severity": alert["labels"].get("severity"),
            "layer": alert["labels"].get("layer"),
            "summary": alert["annotations"].get("summary"),
            "action": alert["annotations"].get("action"),
        })
    return {"received": len(body.get("alerts", []))}


@app.get("/alerts")
def list_notifications():
    return list(notifications)


@app.delete("/alerts")
def clear():
    notifications.clear()
    return {"cleared": True}


@app.get("/health")
def health():
    return {"status": "ok"}
