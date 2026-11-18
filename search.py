import sys
from pathlib import Path

import numpy as np
import open_clip
import pandas as pd
import torch
import io, shutil, zipfile

ROOT = Path(__file__).parent


def load_index():
    shots = pd.read_csv(ROOT / "shots.csv", dtype={"video_id": str})
    full = ROOT / "embeddings.npy"
    if full.exists():
        return shots, np.load(full)
    # Run still going: use only the videos finished so far
    done = [v for v in shots["video_id"].unique()
            if (ROOT / "embeddings" / f"{v}.npy").exists()]
    vectors = np.concatenate([np.load(ROOT / "embeddings" / f"{v}.npy") for v in done])
    shots = shots[shots["video_id"].isin(done)].reset_index(drop=True)
    return shots, vectors


shots, vectors = load_index()
row_of = {(v, n): i for i, (v, n) in enumerate(zip(shots["video_id"], shots["shot_no"]))}

model, _, _ = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
tokenizer = open_clip.get_tokenizer("ViT-B-32")
model.eval()


def top_k(query_vector, k):
    scores = vectors @ query_vector
    best = np.argsort(-scores)[:k]
    result = shots.iloc[best].copy()
    result["score"] = scores[best]
    return result


def search_text(query, k=20):
    with torch.no_grad():
        q = model.encode_text(tokenizer([query]))
        q = q / q.norm(dim=-1, keepdim=True)
    return top_k(q[0].numpy(), k)


def search_similar(video_id, shot_no, k=20):
    return top_k(vectors[row_of[(video_id, shot_no)]], k)



results = search_text(" ".join(sys.argv[1:]))
out = ROOT / "results"
shutil.rmtree(out, ignore_errors=True)
out.mkdir()
zips = {p.stem: p for p in (ROOT / "assignment2_data" / "thumbnails").rglob("*.zip")}
for rank, r in enumerate(results.itertuples(), start=1):
    with zipfile.ZipFile(zips[r.video_id]) as z:
        name = next(n for n in z.namelist() if n.endswith(r.thumbnail))
        (out / f"{rank:02d}_{r.thumbnail}").write_bytes(z.read(name))