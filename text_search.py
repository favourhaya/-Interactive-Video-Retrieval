import csv
import sqlite3
import re
from pathlib import Path
import html

ROOT = Path(__file__).parent
DATA = ROOT / "assignment2_data"

# FTS5 tokenizer for both tables: Porter stemming ("drums"/"drumming" -> "drum"),
# case folding and accent removal ("Cúria" -> "curia"). Queries pass through the
# same tokenizer, so they are stemmed the same way. Rebuild both indexes after changing this.
TOKENIZER = "porter unicode61 remove_diacritics 2"

# Common English words that carry no topic. Left in, they match almost every transcript
# line and title ("in", "a", "the") and push unrelated videos up, which adds noise to
# the AVS combined search.
STOPWORDS = set("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers herself him himself his how i if in into is it
its itself just me more most my myself no nor not now of off on once one only or other our
ours ourselves out over own same she should so some such than that the their theirs them
themselves then there these they this those through to too under until up very was we were
what when where which while who whom why will with would you your yours yourself yourselves
shot shots show shows showing video scene first next
""".split())


def fts_query(query):
    """Turn free text into an FTS5 query: every non-stopword, OR-ed together.
    If the query is nothing but stopwords, fall back to all of its words."""
    words = re.findall(r"\w+", query.lower())
    content = [w for w in words if w not in STOPWORDS] or words
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(content))   # dedupe, keep order

def to_ms(timestamp):
    hours, minutes, seconds = timestamp.strip().split(":")
    sec, ms = seconds.split(",")

    return (int(hours) * 3600000 + int(minutes) * 60000 + int(sec) * 1000 + int(ms))

def load_shots():
    #dictionary to store shot information keyed by shot shot ID
    shots = {}

    with open(ROOT / "shots.csv", "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        #loops through the csv
        for row in reader:
            video_id = row["video_id"]

            #store the information for one shot
            shot = {
                "shot_no" : int(row["shot_no"]),
                "start_ms" : int(row["start_ms"]),
                "end_ms" : int(row["end_ms"]),
                "start_frame" : int(row["start_frame"]),
                "end_frame" : int(row["end_frame"]),
                "thumbnail" : row["thumbnail"]
            }
            #create an empty list for a new video
            if video_id not in shots:
                shots[video_id] = []
            #add the current shot to that video's list
            shots[video_id].append(shot)

        return shots


def parse_srt(file_path):
    # a list to store all of the segments
    segements = []

    file_path = Path(file_path)
    video_Id = file_path.stem

    with open(file_path, "r", encoding="utf-8-sig") as file:
        content = file.read()

    if not content.strip():
        return []

    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().splitlines()

        start, end = lines[1].split("-->", 1)

        start_ms = to_ms(start)
        end_ms = to_ms(end)

        text = " ".join(line.strip() for line in lines[2:])

        segements.append({
            "video_id" : video_Id,
            "start_ms" : start_ms,
            "end_ms" : end_ms,
            "text" : text
        })

    return segements

def build_index():
    shots = load_shots()
    video_ids = set(shots.keys())

    transcript_dir = DATA / "Transcript" / "V3C1" / "V3C1"
    db_path = DATA / "text_index.db"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("Drop Table IF EXISTS transcripts")

    cursor.execute(f"""
        CREATE VIRTUAL TABLE transcripts USING fts5(
            video_id UNINDEXED,
            start_ms UNINDEXED,
            end_ms UNINDEXED,
            text,
            tokenize = '{TOKENIZER}'
        )
    """)

    total_files = 0
    total_segments = 0

    for file in transcript_dir.rglob("*.srt"):
        video_id = file.stem

        if video_id not in video_ids:
            continue

        segments = parse_srt(file)

        if not segments:
            continue

        for segment in segments:
            cursor.execute("""
            INSERT INTO transcripts (video_id, start_ms, end_ms, text)
            VALUES (?, ?, ?, ?)
            """, (
            segment["video_id"],
            segment["start_ms"],
            segment["end_ms"],
            segment["text"]
        ))
        total_files += 1
        total_segments += len(segments)

    conn.commit()
    conn.close()

    print("Total transcript files:", total_files)
    print("Total transcript segments", total_segments)
    print("Index saved to", db_path)

def search_transcripts(query, k = 20):
    shots = load_shots()

    db_path = DATA / "text_index.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    search_query = fts_query(query)
    if not search_query or k <= 0:
        conn.close()
        return []

    cursor.execute("""
        SELECT video_id, start_ms, end_ms, text, bm25(transcripts) AS rank
        From transcripts
        Where transcripts MATCH ?
        ORDER BY rank
        LIMIT ?
        """, (search_query, max(k * 20, 100)))

    matches = cursor.fetchall()
    conn.close()

    results = []
    visited = set()

    for match in matches:
        video_id = match["video_id"]
        start_ms = int(match["start_ms"])
        end_ms = int(match["end_ms"])

        best_shot = None
        best_overlap = 0

        for shot in shots.get(video_id, []):
            overlap = max(0, min(end_ms, shot["end_ms"]) - max(start_ms, shot["start_ms"]))

            if overlap > best_overlap:
                best_overlap = overlap
                best_shot = shot

        if best_shot is None:
            continue

        key = (video_id, best_shot["shot_no"])

        if key in visited:
            continue

        visited.add(key)

        results.append({
            "video_id": video_id,
            "shot_no": best_shot["shot_no"],
            "start_ms": best_shot["start_ms"],
            "end_ms" : best_shot["end_ms"],
            "thumbnail" : best_shot["thumbnail"],
            "score" : -match["rank"],
            "text" : match["text"]
        })

        if len(results) >= k:
            break
    return results



def clean_metadata_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())

def build_metadata_index():
    video_ids = set(load_shots())
    db_path = DATA / "text_index.db"

    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS metadata")
    conn.execute(f"""
        CREATE VIRTUAL TABLE metadata USING fts5(
            video_id UNINDEXED,
            title,
            description,
            tags,
            tokenize = '{TOKENIZER}'
            )
        """)

    count = 0

    with open(DATA / "videos_metadata.csv", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            video_id = row["v3cId"]
            if video_id not in video_ids:
                continue

            conn.execute("INSERT INTO metadata (video_id, title, description, tags) VALUES (?,?,?,?)",
                         (video_id,
                          clean_metadata_text(row["title"]),
                          clean_metadata_text(row["description"]),
                          clean_metadata_text(row["tags"]).replace(";", " ")
                          )
                        )
            count += 1

        conn.commit()
        conn.close()
        return count


def search_metadata(query, k=20):
    match = fts_query(query)
    if not match or k <= 0:
        return []

    conn = sqlite3.connect(DATA / "text_index.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT video_id, title, description, tags, bm25(metadata, 0.0, 4.0, 1.0, 3.0) AS rank
        FROM metadata
        Where metadata MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (match, k)
    ).fetchall()
    conn.close()


    return [
        {
            "video_id": row["video_id"],
            "title" : row["title"],
            "description" : row["description"],
            "tags" : row["tags"],
            "score": -row["rank"]
        } 
        for row in rows
    ]

if __name__ == "__main__":
    # Rebuild both full-text indexes (needed after changing TOKENIZER or the source data).
    build_index()
    print("Indexed videos:", build_metadata_index())
