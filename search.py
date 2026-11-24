import re
import shutil
import sys
import zipfile
from pathlib import Path

import numpy as np
import open_clip
import pandas as pd
import torch


ROOT = Path(__file__).parent
DATA = ROOT / "assignment2_data"


def shotTable():
    """Build shots.csv from the master shot boundaries (msb/<video>.tsv, unzipped).
    Shot n of a video is the n-th data row of its TSV. Run once; the app only reads shots.csv."""
    rows = []
    for tsv_file in sorted((DATA / "msb").rglob("*.tsv")):
        video_id = tsv_file.stem
        data = pd.read_csv(tsv_file, sep="	")
        for c, shot in enumerate(data.itertuples(), 1):
            rows.append({"video_id": video_id, "shot_no": c,
                "start_ms": round(shot.starttime * 1000),
                "end_ms": round(shot.endtime * 1000),
                "start_frame": shot.startframe,
                "end_frame": shot.endframe,
                "thumbnail": f"shot{video_id}_{c}.png",
            })

    shots = pd.DataFrame(rows)
    shots.to_csv("shots.csv", index=False)
    print(len(shots), "shots from", shots["video_id"].nunique(), "videos")


def load_index():
    """shots.csv plus one CLIP embedding per shot (built by embed_keyframes.py), same row order."""
    shots = pd.read_csv(ROOT / "shots.csv", dtype={"video_id": str})
    vectors = np.load(ROOT / "embeddings_keyframes.npy")
    assert len(vectors) == len(shots), "embeddings don't match shots.csv"
    return shots, vectors

shots, vectors = load_index()

row_of = {
    (v, n): i
    for i, (v, n) in enumerate(zip(shots.video_id, shots.shot_no))
}

model, _, preprocess = open_clip.create_model_and_transforms(
    "ViT-B-32", pretrained="laion2b_s34b_b79k"
)

tokenizer = open_clip.get_tokenizer("ViT-B-32")
model.eval()


# Each shot's video as a small integer, so the per-video cap can count with a plain array.
video_code, _ = pd.factorize(shots.video_id)
# shots.csv lists each video's shots together, so every video is one block of rows;
# video_start holds the first row of each block (used for per-video maxima).
assert np.all(np.diff(video_code) >= 0), "shots.csv must keep each video's shots together"
video_start = np.r_[0, np.flatnonzero(np.diff(video_code)) + 1]


def rank(scores, k, per_video=0):
    """Top-k shots by score. With per_video > 0, keep at most that many shots from any
    one video, so the list covers more distinct videos (AVS rewards diversity)."""
    order = np.argsort(-scores)
    if per_video > 0:
        taken = np.zeros(video_code.max() + 1, dtype=int)
        keep = []
        for i in order:                       # walk down the ranking, skipping full videos
            v = video_code[i]
            if taken[v] < per_video:
                taken[v] += 1
                keep.append(i)
                if len(keep) == k:
                    break
        best = np.array(keep, dtype=int)
    else:
        best = order[:k]
    result = shots.iloc[best].copy()
    result["score"] = scores[best]
    return result


def top_k(query_vector, k, per_video=0):
    return rank(vectors @ query_vector, k, per_video)


def encode_texts(texts):
    with torch.no_grad():
        q = model.encode_text(tokenizer(texts))
        q = q / q.norm(dim=-1, keepdim=True)
    return q.numpy()


def search_queries(queries, k=20, combine="max", per_video=0):
    """Several text queries at once (e.g. one phrasing per line). Each shot gets its best
    score over the queries ("max": matches any phrasing) or the average ("mean")."""
    per_query = vectors @ encode_texts(queries).T      # (shots, queries)
    scores = per_query.max(axis=1) if combine == "max" else per_query.mean(axis=1)
    return rank(scores, k, per_video)


def search_text(query, k=20, per_video=0):
    return search_queries([query], k, per_video=per_video)


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if len(p) > 3]


def search_long(text, k=20, combine="max", per_video=0):
    sentences = split_sentences(text)
    if len(sentences) <= 1:
        return search_text(text, k, per_video)
    return search_queries(sentences, k, combine, per_video)


def ahead_max(col, window):
    """out[s] = max of col over shots s .. s+window that belong to the same video as s."""
    out = col.copy()
    n = len(col)
    for d in range(1, window + 1):
        ahead = np.full(n, -np.inf, dtype=col.dtype)
        ahead[:n - d] = col[d:]
        same_video = np.zeros(n, dtype=bool)
        same_video[:n - d] = video_code[d:] == video_code[:n - d]
        out = np.maximum(out, np.where(same_video, ahead, -np.inf))
    return out


def search_sequence(lines, k=20, per_video=0, window=3):
    """KIS-T temporal chaining. lines = short visual descriptions in the order they happen.
    score(s) = z(line 1, s) + best score of the rest of the chain starting at one of the
    shots s .. s+window in the same video (computed from the last line backwards).
    z = each line's similarities standardised (mean 0, spread 1) so lines weigh equally.
    A shot ranks high when the lines are matched one after another in its video."""
    per_line = vectors @ encode_texts(lines).T                 # (shots, lines)
    z = (per_line - per_line.mean(axis=0)) / per_line.std(axis=0)
    chain = z[:, -1]
    for i in range(len(lines) - 2, -1, -1):
        chain = z[:, i] + ahead_max(chain, window)
    return rank(chain, k, per_video)


def search_similar(video_id, shot_no, k=20, per_video=0):
    return top_k(vectors[row_of[(video_id, shot_no)]], k, per_video)


def search_similar_many(keys, k=20, per_video=0):
    """"More like these": average the embeddings of several chosen shots and search with
    that. Shots that agree on what matters (e.g. 'fish underwater') pull the query that way."""
    rows = [row_of[(v, int(n))] for v, n in keys if (v, int(n)) in row_of]
    if not rows:
        raise KeyError("none of the given shots exist")
    q = vectors[rows].mean(axis=0)
    return top_k(q / np.linalg.norm(q), k, per_video)


def trim_borders(image, threshold=18):
    """Cut black bars (letterboxing, dark player background) off a screenshot, so CLIP
    sees the video content rather than a frame of black. Keeps the image if trimming
    would leave almost nothing."""
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    rows = np.where(gray.mean(axis=1) > threshold)[0]
    cols = np.where(gray.mean(axis=0) > threshold)[0]
    if len(rows) < gray.shape[0] * 0.2 or len(cols) < gray.shape[1] * 0.2:
        return image
    return image.crop((cols[0], rows[0], cols[-1] + 1, rows[-1] + 1))


def is_informative(image, min_std=12):
    """False for near-uniform frames (black fades, blank title backgrounds): they look
    like thousands of shots and only add noise."""
    return np.asarray(image.convert("L"), dtype=np.float32).std() > min_std


def search_frames(images, k=20, per_video=0):
    """Image query: screenshots, or frames sampled from a clip (KIS-V).

    score(shot) = best z-score of the shot against any image
                + how well the shot's whole video matches the clip
                  (per image: best z-score among the video's shots, averaged over images).
    The z-scores (per image: subtract its mean over all shots, divide by the spread) stop
    a generic image that is similar to everything from dominating. The video term rewards
    the video that matches *many* of the frames, which is what a real clip does.
    On 25 simulated 20 s clips this put the target shot at rank 1 in 80% of cases
    (64% with a plain max) and the target video in the top 10 in all of them."""
    images = [trim_borders(im.convert("RGB")) for im in images]
    images = [im for im in images if is_informative(im)] or images
    with torch.no_grad():
        batch = torch.stack([preprocess(im) for im in images])
        v = model.encode_image(batch)
        v = v / v.norm(dim=-1, keepdim=True)
    per_frame = vectors @ v.numpy().T                       # (shots, images)
    z = (per_frame - per_frame.mean(axis=0)) / per_frame.std(axis=0)
    video_fit = np.maximum.reduceat(z, video_start, axis=0).mean(axis=1)
    return rank(z.max(axis=1) + video_fit[video_code], k, per_video)

if __name__ == "__main__":
    # Command-line search for quick checks: python search.py dog running on a beach
    # (saves the top thumbnails to results/). Build shots.csv first with shotTable() if missing.
    results = search_text(" ".join(sys.argv[1:]))
    print(results.to_string(index=False))

    out = ROOT / "results"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()

    zips = {p.stem: p for p in (DATA / "thumbnails").rglob("*.zip")}

    for position, r in enumerate(results.itertuples(), 1):
        with zipfile.ZipFile(zips[r.video_id]) as z:
            name = next(n for n in z.namelist() if n.endswith(r.thumbnail))
            (out / f"{position:02d}_{r.thumbnail}").write_bytes(z.read(name))