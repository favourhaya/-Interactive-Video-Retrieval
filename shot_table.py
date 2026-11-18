import pandas as pd
from pathlib import Path
import io, zipfile
import numpy as np
import open_clip
import torch
from PIL import Image
import io, shutil, zipfile
import sys



ROOT = Path(__file__).parent
DATA = ROOT / "assignment2_data"
OUT = ROOT / "embeddings"           
OUT.mkdir(exist_ok=True)
DATA = Path(__file__).parent / "assignment2_data"
rows = []     

 
def shotTable():
    for tsv_file in sorted((DATA / "msb").rglob("*.tsv")):
        video_id = tsv_file.stem                        
        data = pd.read_csv(tsv_file, sep="\t")

        c = 1
        i = 0

        #for shot_no, shot in enumerate(data.itertuples()):   

        while i < len(data):
            shot = data.iloc[i]

            rows.append({ "video_id": video_id, "shot_no": c,
                "start_ms": round(shot.starttime * 1000),
                "end_ms": round(shot.endtime * 1000),
                "start_frame": shot.startframe,
                "end_frame": shot.endframe,
                "thumbnail": f"shot{video_id}_{c}.png",
            })

          #  c,i += 1 
            c += 1
            i += 1

    shots = pd.DataFrame(rows)
    shots.to_csv("shots.csv", index=False)
    print(len(shots), "shots from", shots["video_id"].nunique(), "videos")




def embed():
    shots = pd.read_csv(ROOT / "shots.csv", dtype={"video_id": str})
# datacomp_s13m_b4k laion400m_e32
    model, _, preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")

    model.eval()

    zips = {p.stem: p for p in (DATA / "thumbnails").rglob("*.zip")}

    total = shots.video_id.nunique()

    for i, (video_id, group) in enumerate(shots.groupby("video_id", sort=False), 1):
        out_file = OUT / f"{video_id}.npy"

        if out_file.exists():
            continue

        thumbs = group.thumbnail.tolist()
        vectors = []

        with zipfile.ZipFile(zips[video_id]) as z, torch.no_grad():
            names = {Path(n).name: n for n in z.namelist()}

            for j in range(0, len(thumbs), 64):
                batch = torch.stack([
                    preprocess(
                        Image.open(io.BytesIO(z.read(names[t]))).convert("RGB")
                    )
                    for t in thumbs[j:j + 64]
                ])

                v = model.encode_image(batch)
                v = v / v.norm(dim=-1, keepdim=True)
                vectors.append(v.numpy())

        tmp = out_file.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            np.save(f, np.concatenate(vectors).astype(np.float32))
        tmp.replace(out_file)             
     #   print(f"{i}/{total}  {video_id}: {len(thumbs)} shots", flush=True)

    all_vectors = np.concatenate([np.load(OUT / f"{v}.npy") for v in shots["video_id"].unique()])

    np.save(ROOT / "embeddings.npy", all_vectors)
  #  print("Done:", all_vectors.shape)


def load_index():
    shots = pd.read_csv(ROOT / "shots.csv", dtype={"video_id": str})
    full = ROOT / "embeddings.npy"

    if full.exists():
        return shots, np.load(full)

    done = [v for v in shots.video_id.unique()
            if (ROOT / "embeddings" / f"{v}.npy").exists()]

    vectors = np.concatenate([np.load(ROOT / "embeddings" / f"{v}.npy") for v in done])
    shots = shots[shots.video_id.isin(done)].reset_index(drop=True)
    return shots, vectors
shots, vectors = load_index()

row_of = {
    (v, n): i
    for i, (v, n) in enumerate(zip(shots.video_id, shots.shot_no))
}

model, _, _ = open_clip.create_model_and_transforms(
    "ViT-B-32", pretrained="laion2b_s34b_b79k"
)

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



if __name__ == "__main__":
    #shotTable() remove comments to build and embed shi
    #    embed()
    # dog running is a good example
    results = search_text(" ".join(sys.argv[1:]))
    print(results.to_string(index=False))

    out = ROOT / "results"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()

    zips = {p.stem: p for p in (ROOT / "assignment2_data" / "thumbnails").rglob("*.zip")}

    for rank, r in enumerate(results.itertuples(), 1):
        with zipfile.ZipFile(zips[r.video_id]) as z:
            name = next(n for n in z.namelist() if n.endswith(r.thumbnail))
            (out / f"{rank:02d}_{r.thumbnail}").write_bytes(z.read(name))