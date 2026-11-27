import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from search import (search_queries, search_similar, shots, search_frames, search_sequence,
                    rank, row_of)
import numpy as np
from text_search import search_transcripts, search_metadata

import io

from fastapi import File, UploadFile
from PIL import Image
from pydantic import BaseModel

import dres

ROOT = Path(__file__).parent
DATA = ROOT / "assignment2_data"

#Map each video ID to its thumbnail zip
zips = {}
for file in (DATA / "thumbnails").rglob("*.zip"):
    zips[file.stem] = file
#Map each video ID to its 480p vieo file
videos = {}
for file in (DATA / "videos_480p").rglob("*.mp4"):
    videos[file.stem] = file


app = FastAPI()

#convert a table of shot into JSON list, the web page uses
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

#KIS-T sequence search
@app.get("/search")
def search(q: str, k: int = 200, per_video: int = 0):
    queries = [line.strip() for line in q.splitlines() if line.strip()]
    if not queries:
        return []
    return as_json(search_sequence(queries, k, per_video))

#more like implementation, returns similar shots
@app.get("/similar")
def similar(video_id: str, shot_no: int, k: int = 200, per_video: int = 0):
    results = search_similar(video_id, shot_no, k, per_video)
    return as_json(results)

#gets all of the shots in one video in order
@app.get("/video/{video_id}/shots")
def video_shots(video_id: str):
    results = shots[shots.video_id == video_id]
    return as_json(results)

#return the thumbnail image of one shot fro the video's zip
@app.get("/thumbnail/{video_id}/{shot_no}")
def thumbnail(video_id: str, shot_no: int):
    #error if no thumbnail
    if video_id not in zips:
        raise HTTPException(status_code=404)

    with zipfile.ZipFile(zips[video_id]) as archive:
        #finds the shot's image inside the zip
        for filename in archive.namelist():

            if filename.endswith(f"shot{video_id}_{shot_no}.png"):
                image = archive.read(filename)
                return Response(content=image, media_type="image/png")

    raise HTTPException(status_code=404)

#stream the 480p video file for the player
@app.get("/video/{video_id}")
def video(video_id: str):

    if video_id not in videos:
        raise HTTPException(status_code=404)

    return FileResponse(
        videos[video_id],
        media_type="video/mp4"
    )


AVS_VISUAL_POOL = 1000
AVS_TEXT_WEIGHT = 0.3

#AVS uses combined search: visual ranking, re-ordered by transcript and metadata matches
@app.get("/search/avs")
def avs_search(q: str, k: int = 200, per_video: int = 0):
    queries = [line.strip() for line in q.splitlines() if line.strip()]
    if not queries:
        return []
    text = " ".join(queries)
    #every shot starts at -1, default to not a candidate
    fused = np.full(len(shots), -1.0)                

    #visual search, each line is one phrasing: socre = 1 / (60 + rank)
    visual = search_queries(queries, AVS_VISUAL_POOL)
    for r, row in enumerate(visual.itertuples()):
        fused[row_of[(row.video_id, int(row.shot_no))]] = 1 / (60 + r)
    candidate = fused > 0

    #bonus for candidate shots whose speech matches the query
    for r, hit in enumerate(search_transcripts(text, 200)):
        i = row_of.get((hit["video_id"], int(hit["shot_no"])))
        if i is not None and candidate[i]:
            fused[i] += AVS_TEXT_WEIGHT / (60 + r)

    #bonus for candidate shots whose metadata matches the query
    video_ids = shots.video_id.to_numpy()
    for r, hit in enumerate(search_metadata(text, 50)):
        fused[candidate & (video_ids == hit["video_id"])] += AVS_TEXT_WEIGHT / (60 + r)

    #ranks the candidate and dops the non-candidates
    results = rank(fused, k, per_video)
    return as_json(results[results.score > 0])

#dres answer 
class Answer(BaseModel):
    video_id: str
    start_ms: int
    end_ms: int

#Login to dres and return the available evaluation
@app.post("/dres/connect")
def dres_connect():
    return dres.connect()  

#chooses the type of evaluation to submit
@app.post("/dres/select")
def dres_select(name: str):
    e = dres.select(name)
    return {"evaluation": e.get("name"), "id": e.get("id")}

#submit one answer to dres 
@app.post("/submit")
def submit(answer: Answer):
    return dres.submit(answer.video_id, answer.start_ms, answer.end_ms)

#a list of answers for batch submission
class AnswerBatch(BaseModel):
    answers: list[Answer]

#Batch submits, each shot is submitted as an individual dres submission
@app.post("/submit_batch")
def submit_batch(batch: AnswerBatch):
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

#KIS-V search with pasted screenshot of the clip
@app.post("/search_image")
def search_by_images(files: list[UploadFile] = File(...), k: int = 200, per_video: int = 0):
    images = []
    for file in files:
        try:
            images.append(Image.open(io.BytesIO(file.file.read())).convert("RGB"))
        except Exception:
            raise HTTPException(status_code=400, detail=f"{file.filename}: not an image")
    if not images:
        raise HTTPException(status_code=400, detail="No images received")
    return as_json(search_frames(images, k, per_video))

app.mount(
    "/",
    StaticFiles(directory=web_folder, html=True),
    name="web"
)