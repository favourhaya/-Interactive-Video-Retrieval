const $ = (id) => document.getElementById(id);
let searchResults = [];
let shownResults = [];
let shownTitle = "Results";
let selected = null;
let videoShots = [];
let loadedVideoId = "";
let selectedEvaluation = "";
const sent = new Set();          // shots already submitted (shown with a green frame)
const batch = new Map();         // shots ticked for "Submit selected" / "More like selected"
let imageQueue = [];             // screenshots / clips waiting to be used as a query

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

function perVideo() {
  return $("perVideo").value;
}

function isTyping(target) {
  return target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement;
}

// ---------- result grid ----------

function renderResults(items, title) {
  shownResults = items;
  shownTitle = title;
  $("resultsTitle").textContent = title + " (" + items.length + ")";
  $("results").replaceChildren();
  for (const item of items) {
    const key = shotKey(item);
    const card = document.createElement("div");
    card.className = "card";
    if (selected && key === shotKey(selected)) card.classList.add("selected");
    if (sent.has(key)) card.classList.add("sent");
    if (batch.has(key)) card.classList.add("picked");

    const main = document.createElement("button");
    main.type = "button";
    main.className = "card-main";
    const image = document.createElement("img");
    image.className = "thumb";
    image.loading = "lazy";
    image.alt = "Thumbnail for shot " + item.shot_no;
    image.src = item.thumbnail_url || ("/thumbnail/" + item.video_id + "/" + item.shot_no);
    image.onerror = () => {
      const placeholder = document.createElement("div");
      placeholder.className = "thumb no-thumb";
      placeholder.textContent = "Thumbnail unavailable";
      image.replaceWith(placeholder);
    };
    const body = document.createElement("div");
    body.className = "card-body";
    const name = document.createElement("strong");
    name.textContent = "Video " + item.video_id + " · Shot " + item.shot_no;
    const detail = document.createElement("small");
    detail.textContent = timeLabel(item.start_ms) + " – " + timeLabel(item.end_ms);
    body.append(name, detail);
    main.append(image, body);
    main.onclick = () => selectShot(item);
    card.append(main);

    // Tick box for the batch: separate from the card button so ticking doesn't load the video.
    const pick = document.createElement("label");
    pick.className = "pick";
    pick.title = "Select for batch";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = batch.has(key);
    box.setAttribute("aria-label", "Select video " + item.video_id + " shot " + item.shot_no + " for batch");
    box.onchange = () => toggleBatch(item, box.checked);
    pick.append(box);
    card.append(pick);
    if (sent.has(key)) {
      const tag = document.createElement("span");
      tag.className = "sent-tag";
      tag.textContent = "sent";
      card.append(tag);
    }
    $("results").append(card);
  }
}

function rerender() {
  renderResults(shownResults, shownTitle);
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
  const src = "/video/" + encodeURIComponent(shot.video_id);
  const player = $("player");
  player.onerror = () => {
    $("playerStatus").textContent = "Local video unavailable or unsupported. Check videos_480p and browser H.265 playback.";
  };
  if (player.getAttribute("src") === src && player.readyState > 0) {
    // Same video already loaded (e.g. stepping through its shots): just seek, no reload.
    player.currentTime = shot.start_ms / 1000;
    $("playerStatus").textContent = "Check this shot before submitting.";
  } else {
    player.onloadedmetadata = () => {
      player.currentTime = shot.start_ms / 1000;
      $("playerStatus").textContent = "Check this shot before submitting.";
    };
    player.src = src;
    player.load();
  }
  $("similar").disabled = false;
  $("allShots").disabled = false;
  $("toggleBatch").disabled = false;
  $("setStart").disabled = false;
  $("setEnd").disabled = false;
  $("submit").disabled = !selectedEvaluation;
  updateBatchControls();
  try {
    const shots = await loadVideoShots(shot.video_id);
    const index = shots.findIndex((s) => s.shot_no === shot.shot_no);
    $("previous").disabled = index <= 0;
    $("next").disabled = index < 0 || index >= shots.length - 1;
  } catch (error) {
    $("previous").disabled = true;
    $("next").disabled = true;
  }
  rerender();
}

// ---------- text search ----------

$("searchForm").onsubmit = async (event) => {
  event.preventDefault();
  const query = $("query").value.trim();
  if (!query) return;
  const mode = $("mode").value;
  const params = new URLSearchParams({ q: query, k: "200", per_video: perVideo() });
  if (mode === "kist") params.set("combine", $("kistMethod").value);   // "max" or "sequence"
  const path = mode === "avs" ? "/search/avs" : "/search";
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

// Enter searches, Shift+Enter adds another phrasing on a new line.
$("query").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("searchForm").requestSubmit();
  }
});

$("mode").onchange = () => {
  $("kistMethod").disabled = $("mode").value !== "kist";
};

// ---------- image / clip search (KIS-V) ----------

function isMedia(file) {
  return file && (file.type.startsWith("image/") || file.type.startsWith("video/")
    || /\.(png|jpe?g|webp|bmp|gif|mp4|mov|m4v|webm|mkv|avi)$/i.test(file.name || ""));
}

function renderQueue() {
  $("imageQueue").replaceChildren();
  imageQueue.forEach((file, index) => {
    const tile = document.createElement("div");
    tile.className = "queued";
    if (file.type.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = "Query image " + (index + 1);
      img.onload = () => URL.revokeObjectURL(img.src);
      tile.append(img);
    } else {
      tile.textContent = "🎞 " + (file.name || "clip");
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Remove";
    remove.onclick = () => {
      imageQueue.splice(index, 1);
      renderQueue();
      if (imageQueue.length) searchImages();
    };
    tile.append(remove);
    $("imageQueue").append(tile);
  });
  $("imageSearch").disabled = !imageQueue.length;
  $("imageClear").disabled = !imageQueue.length;
}

function addFiles(files) {
  const media = [...files].filter(isMedia);
  if (!media.length) {
    $("status").textContent = "Only images or video clips can be used as a query.";
    return;
  }
  imageQueue.push(...media);
  renderQueue();
  searchImages();              // search straight away: every second counts in KIS-V
}

async function searchImages() {
  if (!imageQueue.length) return;
  const form = new FormData();
  imageQueue.forEach((file, i) => form.append("files", file, file.name || ("pasted-" + i + ".png")));
  const params = new URLSearchParams({ k: "200", per_video: perVideo() });
  $("status").textContent = "Searching with " + imageQueue.length + " image/clip query item(s)...";
  try {
    searchResults = await request("/search_image?" + params, { method: "POST", body: form });
    $("backButton").hidden = true;
    renderResults(searchResults, "Image results");
    $("status").textContent = searchResults.length + " result(s) for " + imageQueue.length + " image/clip item(s).";
  } catch (error) {
    $("status").textContent = "Image search failed: " + error.message;
  }
}

// Ctrl+V anywhere on the page: images on the clipboard become a query. Text pastes
// (e.g. into the search box) are left alone.
document.addEventListener("paste", (event) => {
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter(isMedia);
  if (!files.length) return;
  event.preventDefault();
  addFiles(files);
});

const zone = $("dropZone");
zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); });
zone.addEventListener("dragleave", () => zone.classList.remove("over"));
zone.addEventListener("drop", (event) => {
  event.preventDefault();
  zone.classList.remove("over");
  addFiles(event.dataTransfer.files);
});
// A file dropped outside the zone would otherwise make the browser leave the page.
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());

$("chooseFiles").onclick = () => $("fileInput").click();
$("fileInput").onchange = () => {
  addFiles($("fileInput").files);
  $("fileInput").value = "";
};
$("imageSearch").onclick = searchImages;
$("imageClear").onclick = () => {
  imageQueue = [];
  renderQueue();
};

// ---------- batch selection (AVS) ----------

function toggleBatch(shot, on = !batch.has(shotKey(shot))) {
  if (on) batch.set(shotKey(shot), shot);
  else batch.delete(shotKey(shot));
  updateBatchControls();
  rerender();
}

function updateBatchControls() {
  const unsent = [...batch.values()].filter((s) => !sent.has(shotKey(s))).length;
  $("batchCount").textContent = batch.size;
  $("batchSimilar").disabled = !batch.size;
  $("batchClear").disabled = !batch.size;
  $("batchSubmit").disabled = !unsent || !selectedEvaluation;
  $("batchSubmit").textContent = unsent ? "Submit selected (" + unsent + ")" : "Submit selected";
  if (selected) $("toggleBatch").textContent = batch.has(shotKey(selected)) ? "Remove from batch" : "Add to batch";
}

$("toggleBatch").onclick = () => { if (selected) toggleBatch(selected); };
// "sent" marks belong to one DRES task. A new task (or another evaluation) starts with no
// marks, otherwise shots sent for an earlier task block "Submit selected".
function startNewTask() {
  sent.clear();
  batch.clear();
  updateBatchControls();
  rerender();
}
$("newTask").onclick = startNewTask;

$("batchClear").onclick = () => {
  batch.clear();
  updateBatchControls();
  rerender();
};

$("batchSimilar").onclick = async () => {
  if (!batch.size) return;
  try {
    const body = {
      shots: [...batch.values()].map((s) => ({ video_id: s.video_id, shot_no: s.shot_no })),
      k: 200,
      per_video: Number(perVideo()),
    };
    const items = await request("/similar_many", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("backButton").hidden = false;
    renderResults(items, "More like " + batch.size + " selected shot(s)");
  } catch (error) {
    $("status").textContent = "Similar search failed: " + error.message;
  }
};

$("batchSubmit").onclick = async () => {
  const shots = [...batch.values()].filter((s) => !sent.has(shotKey(s)));
  if (!shots.length || !selectedEvaluation) return;
  // Wrong submissions cost points, so make the operator confirm the whole batch.
  if (!confirm("Submit " + shots.length + " shot(s) to \"" + selectedEvaluation + "\"?\n"
      + "Each is sent as its own shot range. Wrong ones cost points.")) return;
  $("batchSubmit").disabled = true;
  $("reply").textContent = "Submitting " + shots.length + " shot(s)...";
  try {
    const results = await request("/submit_batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answers: shots.map((s) => ({ video_id: s.video_id, start_ms: s.start_ms, end_ms: s.end_ms })),
      }),
    });
    const lines = [];
    results.forEach((result, i) => {
      const shot = shots[i];
      const ok = result.http_status >= 200 && result.http_status < 300;
      if (ok) {
        sent.add(shotKey(shot));
        batch.delete(shotKey(shot));
      }
      const verdict = (result.reply && (result.reply.submission || result.reply.description)) || JSON.stringify(result.reply);
      lines.push(shot.video_id + " shot " + shot.shot_no + ": " + (ok ? "" : "FAILED " + result.http_status + " ") + verdict);
    });
    $("reply").textContent = lines.join("\n");
  } catch (error) {
    $("reply").textContent = "Batch submission failed: " + error.message;
  } finally {
    updateBatchControls();
    rerender();
  }
};

// ---------- player navigation ----------

$("backButton").onclick = () => {
  $("backButton").hidden = true;
  renderResults(searchResults, "Results");
};
function stepShot(delta) {
  const index = videoShots.findIndex((s) => selected && s.shot_no === selected.shot_no);
  const target = videoShots[index + delta];
  if (index >= 0 && target) selectShot(target);
}
// Open the playing shot's whole video as a shot list, starting at its first shot
// (KIS-T: Sequence finds the video, the described shot is often near its start).
$("allShots").onclick = () => { if (selected) showVideoShots(selected.video_id); };
$("previous").onclick = () => stepShot(-1);
$("next").onclick = () => stepShot(1);
$("similar").onclick = async () => {
  if (!selected) return;
  try {
    const params = new URLSearchParams({
      video_id: selected.video_id, shot_no: selected.shot_no, k: 200, per_video: perVideo(),
    });
    const items = await request("/similar?" + params);
    $("backButton").hidden = false;
    renderResults(items, "Similar shots");
  } catch (error) {
    $("status").textContent = "Similar search failed: " + error.message;
  }
};
$("setStart").onclick = () => { $("startMs").value = Math.round($("player").currentTime * 1000); };
$("setEnd").onclick = () => { $("endMs").value = Math.round($("player").currentTime * 1000); };

// Keyboard shortcuts, only when not typing in a field.
document.addEventListener("keydown", (event) => {
  if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "ArrowLeft" && !$("previous").disabled) { event.preventDefault(); stepShot(-1); }
  else if (event.key === "ArrowRight" && !$("next").disabled) { event.preventDefault(); stepShot(1); }
  else if ((event.key === "b" || event.key === "B") && selected) { event.preventDefault(); toggleBatch(selected); }
});

// ---------- DRES ----------

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
  startNewTask();
  $("submit").disabled = true;
  updateBatchControls();
  if (!$("evaluation").value) return;
  try {
    const params = new URLSearchParams({ name: $("evaluation").value });
    const result = await request("/dres/select?" + params, { method: "POST" });
    selectedEvaluation = result.evaluation;
    $("dresStatus").textContent = "Selected: " + selectedEvaluation;
    $("submit").disabled = !selected;
    updateBatchControls();
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
      updateBatchControls();
      rerender();
    }
  } catch (error) {
    $("reply").textContent = "Submission failed: " + error.message;
  } finally {
    $("submit").disabled = false;
  }
};
