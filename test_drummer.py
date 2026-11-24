from search import search_text, search_long

description = """The first shot shows a drummer sitting behind a drum kit. He is wearing headphones and a black shirt that has "HI HOW ARE YOU?" written on it. The drummer throws a drumstick in the air and catches it again. The nest few shots show different musicians, one at a time. There is a text overlay for the first few seconds of the sequence which presumably lists the name of the band and the song they are about to play."""

versions = {
    "none": search_text(description, 200),
    "max": search_long(description, 200, "max"),
    "mean": search_long(description, 200, "mean"),
}

for name, results in versions.items():
    videos = list(results["video_id"])
    shot_nos = list(results["shot_no"])

    video_rank = videos.index("00031") + 1 if "00031" in videos else None

    opening_rank = None
    for i, (v, s) in enumerate(zip(videos, shot_nos)):
        if v == "00031" and s <= 8:            # one of the opening shots
            opening_rank = i + 1
            break

    print(f"{name:5}  video 00031 first at rank {video_rank}   opening shot first at rank {opening_rank}")