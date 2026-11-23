const $ = (id) => document.getElementById(id);
let searchResults = [];
let shownResults = [];
let selected = null;
let videoShots = [];
let loadedVideoId = "";
let selectedEvaluation = "";
const sent = new Set();

async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body));
  }
  return body;
}

function shotKey(shot) {
  return shot.video_id + ":" + shot.shot_no;
}

function timeLabel(ms) {
  return (ms / 1000).toFixed(1) + "s";
}

function renderResults(items, title) {
  shownResults = items;
  $("resultsTitle").textContent = title + " (" + items.length + ")";
  $("results").replaceChildren();
  for (const item of items) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "card";
    if (selected && item.shot_no != null && shotKey(item) === shotKey(selected)) card.classList.add("selected");
    if (item.shot_no != null && sent.has(shotKey(item))) card.classList.add("sent");

    if (item.shot_no != null) {
      const image = document.createElement("img");
      image.className = "thumb";
      image.alt = "Thumbnail for shot " + item.shot_no;
      image.src = item.thumbnail_url || ("/thumbnail/" + item.video_id + "/" + item.shot_no);
      image.onerror = () => {
        const placeholder = document.createElement("div");
        placeholder.className = "thumb no-thumb";
        placeholder.textContent = "Thumbnail unavailable";
        image.replaceWith(placeholder);
      };
      card.append(image);
    }
    const body = document.createElement("div");
    body.className = "card-body";
    const name = document.createElement("strong");
    name.textContent = item.title || ("Video " + item.video_id + " · Shot " + item.shot_no);
    const detail = document.createElement("small");
    detail.textContent = item.shot_no == null
      ? "Video " + item.video_id
      : timeLabel(item.start_ms) + " – " + timeLabel(item.end_ms);
    body.append(name, detail);
    if (item.text || item.description) {
      const excerpt = document.createElement("p");
      excerpt.textContent = item.text || item.description;
      body.append(excerpt);
    }
    card.append(body);
    card.onclick = () => item.shot_no == null ? showVideoShots(item.video_id) : selectShot(item);
    $("results").append(card);
  }
}

async function loadVideoShots(videoId) {
  if (loadedVideoId === videoId) return videoShots;
  videoShots = await request("/video/" + encodeURIComponent(videoId) + "/shots");
  loadedVideoId = videoId;
  return videoShots;
}

async function showVideoShots(videoId) {
  try {
    const shots = await loadVideoShots(videoId);
    $("backButton").hidden = false;
    renderResults(shots, "Shots in video " + videoId);
    if (shots.length) await selectShot(shots[0]);
  } catch (error) {
    $("status").textContent = "Could not load shots: " + error.message;
  }
}

async function selectShot(shot) {
  selected = shot;
  $("selectedTitle").textContent = "Video " + shot.video_id + " · Shot " + shot.shot_no;
  $("startMs").value = shot.start_ms;
  $("endMs").value = shot.end_ms;
  $("playerStatus").textContent = "Loading video...";
  $("player").src = "/video/" + encodeURIComponent(shot.video_id);
  $("player").onloadedmetadata = () => {
    $("player").currentTime = shot.start_ms / 1000;
    $("playerStatus").textContent = "Check this shot before submitting.";
  };
  $("player").onerror = () => {
    $("playerStatus").textContent = "Local video unavailable or unsupported. Check videos_480p and browser H.265 playback.";
  };
  $("player").load();
  $("similar").disabled = false;
  $("setStart").disabled = false;
  $("setEnd").disabled = false;
  $("submit").disabled = !selectedEvaluation;
  try {
    const shots = await loadVideoShots(shot.video_id);
    const index = shots.findIndex((s) => s.shot_no === shot.shot_no);
    $("previous").disabled = index <= 0;
    $("next").disabled = index < 0 || index >= shots.length - 1;
  } catch (error) {
    $("previous").disabled = true;
    $("next").disabled = true;
  }
  renderResults(shownResults, $("resultsTitle").textContent.replace(/ \(\d+\)$/, ""));
}

$("searchForm").onsubmit = async (event) => {
  event.preventDefault();
  const query = $("query").value.trim();
  if (!query) return;
  const mode = $("mode").value;
  const params = new URLSearchParams({ q: query, k: mode === "visual" ? "200" : "20" });
  if (mode === "visual" && $("longQuery").checked) params.set("combine", "max");
  const path = mode === "visual" ? "/search" : "/search/" + mode;
  $("status").textContent = "Searching...";
  try {
    searchResults = await request(path + "?" + params);
    $("backButton").hidden = true;
    renderResults(searchResults, "Results");
    $("status").textContent = searchResults.length + " result(s).";
  } catch (error) {
    $("status").textContent = "Search failed: " + error.message;
  }
};

$("mode").onchange = () => {
  $("longQuery").disabled = $("mode").value !== "visual";
};
$("backButton").onclick = () => {
  $("backButton").hidden = true;
  renderResults(searchResults, "Results");
};
$("previous").onclick = () => {
  const index = videoShots.findIndex((s) => selected && s.shot_no === selected.shot_no);
  if (index > 0) selectShot(videoShots[index - 1]);
};
$("next").onclick = () => {
  const index = videoShots.findIndex((s) => selected && s.shot_no === selected.shot_no);
  if (index >= 0 && index < videoShots.length - 1) selectShot(videoShots[index + 1]);
};
$("similar").onclick = async () => {
  if (!selected) return;
  try {
    const params = new URLSearchParams({ video_id: selected.video_id, shot_no: selected.shot_no, k: 200 });
    const items = await request("/similar?" + params);
    $("backButton").hidden = false;
    renderResults(items, "Similar shots");
  } catch (error) {
    $("status").textContent = "Similar search failed: " + error.message;
  }
};
$("setStart").onclick = () => { $("startMs").value = Math.round($("player").currentTime * 1000); };
$("setEnd").onclick = () => { $("endMs").value = Math.round($("player").currentTime * 1000); };

$("connect").onclick = async () => {
  $("dresStatus").textContent = "Connecting...";
  try {
    const evaluations = await request("/dres/connect", { method: "POST" });
    $("evaluation").replaceChildren(new Option("Choose evaluation", ""));
    for (const evaluation of evaluations) {
      $("evaluation").add(new Option(evaluation.name || evaluation.id, evaluation.name));
    }
    $("evaluation").disabled = false;
    $("dresStatus").textContent = "Connected. Choose an evaluation.";
  } catch (error) {
    $("dresStatus").textContent = "DRES connection failed: " + error.message;
  }
};
$("evaluation").onchange = async () => {
  selectedEvaluation = "";
  $("submit").disabled = true;
  if (!$("evaluation").value) return;
  try {
    const params = new URLSearchParams({ name: $("evaluation").value });
    const result = await request("/dres/select?" + params, { method: "POST" });
    selectedEvaluation = result.evaluation;
    $("dresStatus").textContent = "Selected: " + selectedEvaluation;
    $("submit").disabled = !selected;
  } catch (error) {
    $("dresStatus").textContent = "Selection failed: " + error.message;
  }
};
$("submit").onclick = async () => {
  if (!selected || !selectedEvaluation) return;
  const start = Number($("startMs").value);
  const end = Number($("endMs").value);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) {
    $("reply").textContent = "Enter a valid start and end time in milliseconds.";
    return;
  }
  $("submit").disabled = true;
  try {
    const result = await request("/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ video_id: selected.video_id, start_ms: start, end_ms: end })
    });
    $("reply").textContent = JSON.stringify(result, null, 2);
    if (result.http_status >= 200 && result.http_status < 300) {
      sent.add(shotKey(selected));
      renderResults(shownResults, $("resultsTitle").textContent.replace(/ \(\d+\)$/, ""));
    }
  } catch (error) {
    $("reply").textContent = "Submission failed: " + error.message;
  } finally {
    $("submit").disabled = false;
  }
};
