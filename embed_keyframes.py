import io
import re
import sys
import zipfile
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import open_clip
from PIL import Image


BASE = Path(__file__).parent
DATA = BASE / "assignment2_data"
CACHE = BASE / "embeddings_kf"

BATCH_SIZE = 32
CHUNK_SIZE = 50

PATTERN = re.compile(r"_(\d+)(?:_RKF)?\.(?:png|jpg)$", re.IGNORECASE)


def find_zip(folder, video):
    path = DATA / folder
    return next(path.rglob(f"{video}.zip"), None) if path.exists() else None


def save_embeddings(path, embeddings):
    temp = path.with_suffix(".tmp")

    with open(temp, "wb") as file:
        np.save(file, embeddings)

    temp.replace(path)


def main(limit=None):

    CACHE.mkdir(exist_ok=True)

    # Read CSV
    df = pd.read_csv(BASE / "shots.csv", dtype={"video_id": str})

    videos = {
        video: group["shot_no"].tolist()
        for video, group in df.groupby("video_id", sort=False)
    }

    # Find videos without embeddings
    pending = [
        video for video in videos
        if not (CACHE / f"{video}.npy").exists()
    ]

    if limit is not None:
        pending = pending[:limit]

    # Load CLIP
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32",
        pretrained="laion2b_s34b_b79k"
    )

    model.eval()

    # Process videos in chunks
    for start in range(0, len(pending), CHUNK_SIZE):

        batch = pending[start:start + CHUNK_SIZE]

        # Download keyframes
        video_list = ",".join(str(int(v)) for v in batch)

        subprocess.run(
            [
                sys.executable,
                "student_download.py",
                "--packages",
                "keyframes",
                "--videos",
                video_list
            ],
            cwd=BASE,
            check=True
        )

        for video in batch:

            zip_path = find_zip("keyframes", video)
            thumb_path = find_zip("thumbnails", video)

            if zip_path is None:
                print(f"{video}: No keyframes found")
                continue

            # Read keyframes from ZIP
            keyframes = {}

            with zipfile.ZipFile(zip_path) as archive:

                for filename in archive.namelist():

                    match = PATTERN.search(filename)

                    if match:
                        shot = int(match.group(1))
                        keyframes[shot] = archive.read(filename)

            # Read thumbnails from ZIP
            thumbnails = {}

            if thumb_path is not None:

                with zipfile.ZipFile(thumb_path) as archive:

                    for filename in archive.namelist():

                        match = PATTERN.search(filename)

                        if match:
                            shot = int(match.group(1))
                            thumbnails[shot] = archive.read(filename)

            # Generate embeddings
            embeddings = []
            missing = []
            thumb_count = 0

            shots = videos[video]

            for i in range(0, len(shots), BATCH_SIZE):

                batch_shots = shots[i:i + BATCH_SIZE]
                images = []

                for shot in batch_shots:

                    if shot in keyframes:
                        image_bytes = keyframes[shot]

                    elif shot in thumbnails:
                        image_bytes = thumbnails[shot]
                        thumb_count += 1

                    else:
                        missing.append(shot)
                        continue

                    # Open and resize image
                    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                    image.thumbnail((640, 640))

                    images.append(preprocess(image))

                if images:

                    with torch.inference_mode():

                        features = model.encode_image(torch.stack(images))

                        features = features / features.norm(dim=-1, keepdim=True)

                    embeddings.append(features.cpu().numpy())

            if missing:
                raise ValueError(f"{video}: Missing shots {missing[:10]}")

            if embeddings:
                embeddings = np.concatenate(embeddings).astype(np.float32)
            else:
                embeddings = np.empty((0, 0), dtype=np.float32)

            # Save embeddings
            save_embeddings(CACHE / f"{video}.npy", embeddings)

            # Remove downloaded keyframes
            zip_path.unlink(missing_ok=True)

            print(f"{video}: {len(embeddings)} embeddings, {thumb_count} thumbnails")

    files = [CACHE / f"{video}.npy" for video in videos]

    if not all(path.exists() for path in files):
        print("Some embeddings are missing")
        return

    combined = np.concatenate([np.load(path) for path in files], axis=0)

    assert len(combined) == len(df), "Incorrect embedding count"

    np.save(BASE / "embeddings_keyframes.npy", combined)

    print("Finished:", combined.shape)


if __name__ == "__main__":

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    main(limit)