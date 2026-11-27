import json
from pathlib import Path

import requests

CONFIG = json.loads((Path(__file__).parent / "dres_config.json").read_text())
BASE = CONFIG["url"].rstrip("/")

http = requests.Session()       
session_id = None
evaluation = None               

def login():
    global session_id
    r = http.post(f"{BASE}/api/v2/login", timeout=10,
                  json={"username": CONFIG["username"], "password": CONFIG["password"]})
    r.raise_for_status()                     
    session_id = r.json()["sessionId"]


def list_evaluations():
    r = http.get(f"{BASE}/api/v2/client/evaluation/list",
                 params={"session": session_id}, timeout=10)
    r.raise_for_status()
    return r.json()


def connect():
    """Log in and return the evaluations you can submit to."""
    login()
    return [{"id": e["id"], "name": e.get("name")} for e in list_evaluations()]


def select(name):
    """Choose which evaluation the next submissions go to."""
    global evaluation
    matches = [e for e in list_evaluations() if e.get("name") == name]
    if not matches:
        raise RuntimeError(f"No evaluation called {name!r}")
    evaluation = matches[0]
    return evaluation


def _post(video_id, start_ms, end_ms):
    body = {"answerSets": [{"answers": [{
        "mediaItemName": video_id, "start": int(start_ms), "end": int(end_ms)}]}]}
    return http.post(f"{BASE}/api/v2/submit/{evaluation['id']}",
                     params={"session": session_id}, json=body, timeout=10)


def submit(video_id, start_ms, end_ms):
    if evaluation is None:
        raise RuntimeError("No evaluation selected: call select() first")
    r = _post(video_id, start_ms, end_ms)
    if r.status_code == 401:                  # session expired: log in again, retry once
        connect()
        r = _post(video_id, start_ms, end_ms)
    try:
        reply = r.json()
    except ValueError:
        reply = r.text
    return {"http_status": r.status_code, "reply": reply}


if __name__ == "__main__":
    login()
    print("Logged in. Evaluations visible to you:")
    for e in list_evaluations():
        print(e)