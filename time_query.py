import time
from search import search_text, search_long
from text_search import search_transcripts

query = "a drummer with headphones behind a drum kit"
long_query = "A man plays the drums. He wears headphones. Other musicians play guitar."

tests = {
    "visual": lambda: search_text(query, 200),
    "long description": lambda: search_long(long_query, 200, "max"),
    "transcript": lambda: search_transcripts("hello", 200),
}

for name, run in tests.items():
    run()                               
    times = []
    for _ in range(10):
        t = time.perf_counter()
        run()
        times.append((time.perf_counter() - t) * 1000)
    print(f"{name:17} {sum(times) / len(times):7.1f} ms on average")



import zipfile
from pathlib import Path
from PIL import Image
import io
from search import search_frames

zip_path = next(Path("assignment2_data/thumbnails").rglob("00031.zip"))
with zipfile.ZipFile(zip_path) as z:
    names = sorted(z.namelist())[:30]
    frames = [Image.open(io.BytesIO(z.read(n))).convert("RGB") for n in names]

for n in (1, 10):                      
    search_frames(frames[:n], 200)        
    t = time.perf_counter()
    for _ in range(5):
        search_frames(frames[:n], 200)
    print(f"image search, {n:2} frames {(time.perf_counter() - t) / 5 * 1000:7.1f} ms on average")