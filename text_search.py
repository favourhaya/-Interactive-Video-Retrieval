import csv
import sqlite3
import re
from pathlib import Path
import html

ROOT = Path(__file__).parent
DATA = ROOT / "assignment2_data"

#Porter stemming and acent folding which is used by the both transcript and metadata tables
TOKENIZER = "porter unicode61 remove_diacritics 2"

#All of the common stop words 
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


#Builds the FTS5 query from non-stop words, OR-ed together
def fts_query(query):
    words = re.findall(r"\w+", query.lower())
    content = [w for w in words if w not in STOPWORDS] or words
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(content))
#changes to ms
def to_ms(timestamp):
    hours, minutes, seconds = timestamp.strip().split(":")
    sec, ms = seconds.split(",")

    return (int(hours) * 3600000 + int(minutes) * 60000 + int(sec) * 1000 + int(ms))

def load_shots():
    #shot information per video, keyed by video ID
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

#build the transcript full-text index, one row per subtitle segment
def build_index():
    #loads the shot table and video IDs in the sample
    shots = load_shots()
    video_ids = set(shots.keys())

    transcript_dir = DATA / "Transcript" / "V3C1" / "V3C1"
    db_path = DATA / "text_index.db"

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    #start from empty table
    cursor.execute("Drop Table IF EXISTS transcripts")

    #only the text is searchable
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
    #one .srt file per video
    for file in transcript_dir.rglob("*.srt"):
        video_id = file.stem

        #skips videos that are not in the sample
        if video_id not in video_ids:
            continue

        segments = parse_srt(file)

        #skips empty transcript
        if not segments:
            continue

        #insert every subtitle segment as its own row
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

    #save the index and closes the database
    conn.commit()
    conn.close()

    print("Total transcript files:", total_files)
    print("Total transcript segments", total_segments)
    print("Index saved to", db_path)

#searches transcripts and maps each matching segment to the shot it overlaps the most
def search_transcripts(query, k = 20):
    shots = load_shots()

    db_path = DATA / "text_index.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    #build the query, stop if there is nothing to search for
    search_query = fts_query(query)
    if not search_query or k <= 0:
        conn.close()
        return []

    #Best matching segment first using BM25
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
    #removes duplicate
    visited = set()

    for match in matches:
        video_id = match["video_id"]
        start_ms = int(match["start_ms"])
        end_ms = int(match["end_ms"])

        best_shot = None
        best_overlap = 0

        #finds the shot whose time range overlaps this segment the most
        for shot in shots.get(video_id, []):
            overlap = max(0, min(end_ms, shot["end_ms"]) - max(start_ms, shot["start_ms"]))

            if overlap > best_overlap:
                best_overlap = overlap
                best_shot = shot
        #if segment does not fit into any shots
        if best_shot is None:
            continue

        key = (video_id, best_shot["shot_no"])
        #skips shots that are already in the result
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
        #stop after k shots
        if len(results) >= k:
            break
    return results


#removes HTML tags from metadata
def clean_metadata_text(value):
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split())

#Build the metadata full-text index for te sample video
def build_metadata_index():
    video_ids = set(load_shots())
    db_path = DATA / "text_index.db"

    conn = sqlite3.connect(db_path)

    #start from empty table
    conn.execute("DROP TABLE IF EXISTS metadata")
    #full-text table with searchable title, description and tags
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
            #skips video that are not in sample
            if video_id not in video_ids:
                continue

            #insert the cleaned text
            conn.execute("INSERT INTO metadata (video_id, title, description, tags) VALUES (?,?,?,?)",
                         (video_id,
                          clean_metadata_text(row["title"]),
                          clean_metadata_text(row["description"]),
                          clean_metadata_text(row["tags"]).replace(";", " ")
                          )
                        )
            count += 1
        #save index and close the database
        conn.commit()
        conn.close()
        return count

#searches video metadata, weighting title highest, then tags then description
def search_metadata(query, k=20):
    #build the query and stop if there is nothing to search for
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

    # one result per video
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
    #build the two indexes
    build_index()
    build_metadata_index()
