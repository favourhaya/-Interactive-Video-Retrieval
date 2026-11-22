import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from search import search_text, search_similar, search_long, shots
import csv
import json
import time

from pydantic import BaseModel

import dres

ROOT = Path(__file__).parent
DATA = ROOT / "assignment2_data"

zips = {}
for file in (DATA / "thumbnails").rglob("*.zip"):
    zips[file.stem] = file
videos = {}
for file in (DATA / "videos_480p").rglob("*.mp4"):
    videos[file.stem] = file


app = FastAPI()


def as_json(df):
    results = []

    for row in df.itertuples():
        results.append({
            "video_id": row.video_id,
            "shot_no": int(row.shot_no),
            "start_ms": int(row.start_ms),
            "end_ms": int(row.end_ms),
            "score": float(getattr(row, "score", 0)),
            "thumbnail_url": f"/thumbnail/{row.video_id}/{row.shot_no}"
        })

    return results


@app.get("/search")
def search(q: str, k: int = 200, combine: str = "none"):
    if combine in ("max", "mean"):
        results = search_long(q, k, combine)
    else:
        results = search_text(q, k)
    return as_json(results)


@app.get("/similar")
def similar(video_id: str, shot_no: int, k: int = 200):
    results = search_similar(video_id, shot_no, k)
    return as_json(results)


@app.get("/video/{video_id}/shots")
def video_shots(video_id: str):
    results = shots[shots.video_id == video_id]
    return as_json(results)


@app.get("/thumbnail/{video_id}/{shot_no}")
def thumbnail(video_id: str, shot_no: int):

    if video_id not in zips:
        raise HTTPException(status_code=404)

    with zipfile.ZipFile(zips[video_id]) as archive:

        for filename in archive.namelist():

            if filename.endswith(f"shot{video_id}_{shot_no}.png"):
                image = archive.read(filename)
                return Response(content=image, media_type="image/png")

    raise HTTPException(status_code=404)


@app.get("/video/{video_id}")
def video(video_id: str):

    if video_id not in videos:
        raise HTTPException(status_code=404)

    return FileResponse(
        videos[video_id],
        media_type="video/mp4"
    )

class Answer(BaseModel):
    video_id: str
    start_ms: int
    end_ms: int


@app.post("/dres/connect")
def dres_connect():
    return dres.connect()          


@app.post("/dres/select")
def dres_select(name: str):
    e = dres.select(name)
    return {"evaluation": e.get("name"), "id": e.get("id")}


@app.post("/submit")
def submit(answer: Answer):
    result = dres.submit(answer.video_id, answer.start_ms, answer.end_ms)

    with open(ROOT / "submissions.csv", "a", newline="") as f:
        csv.writer(f).writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            dres.evaluation["name"] if dres.evaluation else "",
            answer.video_id, answer.start_ms, answer.end_ms,
            result["http_status"], json.dumps(result["reply"]),
        ])

    return result

web_folder = ROOT / "web"
web_folder.mkdir(exist_ok=True)

app.mount(
    "/",
    StaticFiles(directory=web_folder, html=True),
    name="web"
)