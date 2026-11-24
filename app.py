import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from search import (search_queries, search_similar, search_similar_many, split_sentences,
                    shots, search_frames, search_sequence, rank, row_of)
import numpy as np
from text_search import search_transcripts, search_metadata

import csv
import io
import json
import time
import os
import tempfile

import cv2
from fastapi import File, UploadFile
from PIL import Image
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
def search(q: str, k: int = 200, combine: str = "none", per_video: int = 0):
    # One query per line: each line is a separate phrasing, a shot scores its best line.
    # With "Long description" on, every line is also split into sentences.
    queries = [line.strip() for line in q.splitlines() if line.strip()]
    if not queries:
        return []
    if combine == "sequence":
        # KIS-T chaining: each line is one step of the described sequence, in order.
        return as_json(search_sequence(queries, k, per_video))
    if combine in ("max", "mean"):
        queries = [s for line in queries for s in (split_sentences(line) or [line])]
    method = "mean" if combine == "mean" else "max"
    return as_json(search_queries(queries, k, method, per_video))


@app.get("/similar")
def similar(video_id: str, shot_no: int, k: int = 200, per_video: int = 0):
    results = search_similar(video_id, shot_no, k, per_video)
    return as_json(results)


class ShotRef(BaseModel):
    video_id: str
    shot_no: int


class SimilarMany(BaseModel):
    shots: list[ShotRef]
    k: int = 200
    per_video: int = 0


@app.post("/similar_many")
def similar_many(body: SimilarMany):
    try:
        results = search_similar_many([(s.video_id, s.shot_no) for s in body.shots],
                                      body.k, body.per_video)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error))
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


AVS_VISUAL_POOL = 1000   # text may only re-order the visual top-N, never add shots
AVS_TEXT_WEIGHT = 0.3    # weight of transcript / metadata evidence relative to visual


@app.get("/search/avs")
def avs_search(q: str, k: int = 200, per_video: int = 0):
    """AVS combined search: visual ranking, re-ordered by transcript and metadata evidence.
    Every shot in the visual top-N gets 1/(60 + visual rank) (reciprocal rank fusion).
    A shot whose speech matches the query (transcript rank r) gets + w/(60 + r), and every
    candidate shot of a video whose title/description/tags match (metadata rank r) gets
    + w/(60 + r). Text can only move up shots that already *look* right: when text could
    add its own shots, "fish" pulled in kayakers and fly-tying, and "cooking" pulled in
    interviews and slides — wrong AVS submissions cost points."""
    queries = [line.strip() for line in q.splitlines() if line.strip()]
    if not queries:
        return []
    text = " ".join(queries)
    fused = np.full(len(shots), -1.0)                       # -1 = not a candidate

    visual = search_queries(queries, AVS_VISUAL_POOL, "max")   # each line = one phrasing
    for r, row in enumerate(visual.itertuples()):
        fused[row_of[(row.video_id, int(row.shot_no))]] = 1 / (60 + r)
    candidate = fused > 0

    for r, hit in enumerate(search_transcripts(text, 200)):
        i = row_of.get((hit["video_id"], int(hit["shot_no"])))
        if i is not None and candidate[i]:
            fused[i] += AVS_TEXT_WEIGHT / (60 + r)

    video_ids = shots.video_id.to_numpy()
    for r, hit in enumerate(search_metadata(text, 50)):
        fused[candidate & (video_ids == hit["video_id"])] += AVS_TEXT_WEIGHT / (60 + r)

    results = rank(fused, k, per_video)
    return as_json(results[results.score > 0])

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


class AnswerBatch(BaseModel):
    answers: list[Answer]


@app.post("/submit_batch")
def submit_batch(batch: AnswerBatch):
    """AVS: send several shots in one click. Each shot is its own DRES submission (so each
    is judged as its own segment), logged like a single submit. One failure doesn't stop
    the rest."""
    results = []
    for answer in batch.answers:
        try:
            outcome = submit(answer)
        except Exception as error:
            outcome = {"http_status": 0, "reply": str(error)}
        results.append({"video_id": answer.video_id, "start_ms": answer.start_ms,
                        "end_ms": answer.end_ms, **outcome})
    return results

web_folder = ROOT / "web"
web_folder.mkdir(exist_ok=True)
def sample_frames(path, per_second=1.0, max_frames=30):
    cap = cv2.VideoCapture(path)
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 25
    step = max(1, round(video_fps / per_second))
    frames, i = [], 0
    while len(frames) < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if i % step == 0:
            frames.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        i += 1
    cap.release()
    return frames


def frames_from_video(data, filename):
    suffix = Path(filename or "clip.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        return sample_frames(path)
    finally:
        os.remove(path)


@app.post("/search_image")
def search_by_images(files: list[UploadFile] = File(...), k: int = 200, per_video: int = 0):
    """KIS-V: search with pasted screenshots and/or a recorded clip. Images are used as-is,
    videos are sampled at 1 frame per second; every image/frame becomes one query."""
    images = []
    for file in files:
        data = file.file.read()
        if not (file.content_type or "").startswith("video/"):
            try:
                images.append(Image.open(io.BytesIO(data)).convert("RGB"))
                continue
            except Exception:
                pass                    # not an image: maybe a video with an unknown type
        frames = frames_from_video(data, file.filename)
        if not frames:
            raise HTTPException(status_code=400, detail=f"{file.filename}: not an image or video")
        images += frames
    if not images:
        raise HTTPException(status_code=400, detail="No usable images or video frames")
    return as_json(search_frames(images, k, per_video))

app.mount(
    "/",
    StaticFiles(directory=web_folder, html=True),
    name="web"
)