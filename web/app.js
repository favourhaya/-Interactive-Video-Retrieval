const $ = (id) => document.getElementById(id);

const PER_VIDEO = { kist: 1, avs: 3, image: 3, similar: 3 };

//last search results 
let searchResults = [];
//results on screen and their title 
let shownResults = [];
let shownTitle = "Results";
// shot currently in the player
let selected = null; 
//name of the chosen DRES evluation
let selectedEvaluation = "";
//KIS-V screenshots in the current query
let images = [];  
//shots submitted in the current DRES task
const sent = new Set(); 
//shots that is ticked for batch submission
const batch = new Map();

//calls the server and return the JSON reply if failed throw error message
async function request(path, options) {
  const response = await fetch(path, options);
  const body = await response.json();
  if (!response.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body));
  }
  return body;
}

//unique key for a shot
const shotKey = (shot) => shot.video_id + ":" + shot.shot_no;
//format ms as seconds
const seconds = (ms) => (ms / 1000).toFixed(1) + "s";

//build the result grid, one card per shot with thumbnail, caption and tick box
function showResults(items, title) {
  shownResults = items;
  shownTitle = title;
  $("resultsTitle").textContent = title + " (" + items.length + ")";
  $("results").replaceChildren();
  for (const item of items) {
    const key = shotKey(item);
    const card = document.createElement("div");
    card.className = "card";
    //coloured borders for playing, ticked, already sent
    if (selected && key === shotKey(selected)) card.classList.add("selected");
    if (batch.has(key)) card.classList.add("picked");
    if (sent.has(key)) card.classList.add("sent");

    //clickable thumbnail and caption that plays the shot
    const open = document.createElement("button");
    open.type = "button";
    const image = document.createElement("img");
    image.loading = "lazy";
    image.alt = "Video " + item.video_id + " shot " + item.shot_no;
    image.src = item.thumbnail_url;
    // a palceholder when thumbnail is not available
    image.onerror = () => {
      const missing = document.createElement("div");
      missing.className = "no-thumb";
      missing.textContent = "No thumbnail";
      image.replaceWith(missing);
    };
    const caption = document.createElement("small");
    caption.textContent = item.video_id + " · shot " + item.shot_no + " · "
      + seconds(item.start_ms) + "–" + seconds(item.end_ms) + (sent.has(key) ? " · SENT" : "");
    open.append(image, caption);
    open.onclick = () => selectShot(item);

    //ticks box that adds or removes the shot from the batch
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.checked = batch.has(key);
    tick.setAttribute("aria-label", "Select " + item.video_id + " shot " + item.shot_no + " for batch");
    tick.onchange = () => {
      if (tick.checked) batch.set(key, item);
      else batch.delete(key);
      updateBatch();
      rerender();
    };
    card.append(open, tick);
    $("results").append(card);
  }
}

//redraws the current results
function rerender() {
  showResults(shownResults, shownTitle);
}

//fetched results from the server and show them
async function load(path, options, title) {
  $("status").textContent = "Searching...";
  try {
    const items = await request(path, options);
    const isSearch = title === "Results";
    if (isSearch) searchResults = items;
    $("backButton").hidden = isSearch;
    showResults(items, title);
    $("status").textContent = items.length + " result(s).";
  } catch (error) {
    $("status").textContent = "Search failed: " + error.message;
  }
}

//return to the last search result
$("backButton").onclick = () => {
  $("backButton").hidden = true;
  showResults(searchResults, "Results");
};

//Text search for KIS-T and AVS
$("searchForm").onsubmit = (event) => {
  event.preventDefault();
  const query = $("query").value.trim();
  if (!query) return;
  const mode = $("mode").value;
  const params = new URLSearchParams({ q: query, k: 200, per_video: PER_VIDEO[mode] });
  load((mode === "avs" ? "/search/avs?" : "/search?") + params, undefined, "Results");
};


//KIS-V adds screenshot to the quuery and search
function addImages(files) {
  const added = [...files].filter((file) => file.type.startsWith("image/"));
  if (!added.length) {
    $("status").textContent = "Only images can be used for KIS-V search.";
    return;
  }
  images.push(...added);
  $("imageCount").textContent = images.length + " image(s)";
  $("imageClear").disabled = false;
  const form = new FormData();
  images.forEach((file, i) => form.append("files", file, file.name || "screenshot-" + i + ".png"));
  load("/search_image?" + new URLSearchParams({ k: 200, per_video: PER_VIDEO.image }),
       { method: "POST", body: form }, "Results");
}

// anything pasted image on the website is added to KIS-V as a query
document.addEventListener("paste", (event) => {
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (!files.length) return;         
  event.preventDefault();
  addImages(files);
});
$("imageClear").onclick = () => {
  images = [];
  $("imageCount").textContent = "";
  $("imageClear").disabled = true;
};


//play a shot from its start and fills inthe submit range
async function selectShot(shot) {
  selected = shot;
  $("selectedTitle").textContent = "Video " + shot.video_id + " · shot " + shot.shot_no;
  $("startMs").value = shot.start_ms;
  $("endMs").value = shot.end_ms;
  const player = $("player");
  const src = "/video/" + encodeURIComponent(shot.video_id);
  player.onerror = () => { $("playerStatus").textContent = "Video unavailable or not playable in this browser."; };
  if (player.getAttribute("src") === src && player.readyState > 0) {
    //if same video just seek
    player.currentTime = shot.start_ms / 1000;
  } else {
    // new video, load it and then seek once 
    $("playerStatus").textContent = "Loading video...";
    player.onloadedmetadata = () => {
      player.currentTime = shot.start_ms / 1000;
      $("playerStatus").textContent = "";
    };
    player.src = src;
    player.load();
  }
  for (const id of ["allShots", "similar", "setStart", "setEnd"]) $(id).disabled = false;
  $("submit").disabled = !selectedEvaluation;
  rerender();
  //enables the arrow when ther is a shot before/after the current shot
  const shots = await shotsOf(shot.video_id);
  const index = shots.findIndex((s) => s.shot_no === shot.shot_no);
  $("previous").disabled = index <= 0;
  $("next").disabled = index < 0 || index >= shots.length - 1;
}

// all shots of a video in order
const shotLists = new Map();
async function shotsOf(videoId) {
  if (!shotLists.has(videoId)) {
    shotLists.set(videoId, await request("/video/" + encodeURIComponent(videoId) + "/shots"));
  }
  return shotLists.get(videoId);
}

//plays the shot just before or after the current one in the same video
async function stepShot(delta) {
  if (!selected) return;
  const shots = await shotsOf(selected.video_id);
  const index = shots.findIndex((s) => s.shot_no === selected.shot_no);
  const target = shots[index + delta];
  if (index >= 0 && target) selectShot(target);
}
$("previous").onclick = () => stepShot(-1);
$("next").onclick = () => stepShot(1);

//list all shot of the playing video and play the first shot
$("allShots").onclick = async () => {
  if (!selected) return;
  const videoId = selected.video_id;
  await load("/video/" + encodeURIComponent(videoId) + "/shots", undefined, "Shots in video " + videoId);
  if (shownResults.length) selectShot(shownResults[0]);
};

// shots that looks like the current playing shot
$("similar").onclick = () => {
  if (!selected) return;
  const params = new URLSearchParams({
    video_id: selected.video_id, shot_no: selected.shot_no, k: 200, per_video: PER_VIDEO.similar,
  });
  load("/similar?" + params, undefined, "More like video " + selected.video_id + " shot " + selected.shot_no);
};

//set the submit range from the player's current position
$("setStart").onclick = () => { $("startMs").value = Math.round($("player").currentTime * 1000); };
$("setEnd").onclick = () => { $("endMs").value = Math.round($("player").currentTime * 1000); };


//update the batch count and the submit selected button
function updateBatch() {
  const unsent = [...batch.values()].filter((shot) => !sent.has(shotKey(shot))).length;
  $("batchCount").textContent = batch.size;
  $("batchSubmit").disabled = !unsent || !selectedEvaluation;
  $("batchSubmit").textContent = unsent ? "Submit selected (" + unsent + ")" : "Submit selected";
}

//clears the sent marks and ticks for a new DRES task
function startNewTask() {
  sent.clear();
  batch.clear();
  updateBatch();
  rerender();
}
$("newTask").onclick = startNewTask;

//login to DRES and fills the evaluation dropdown
$("connect").onclick = async () => {
  $("dresStatus").textContent = "Connecting...";
  try {
    const evaluations = await request("/dres/connect", { method: "POST" });
    $("evaluation").replaceChildren(new Option("2. Choose evaluation", ""));
    for (const evaluation of evaluations) {
      $("evaluation").add(new Option(evaluation.name || evaluation.id, evaluation.name));
    }
    $("evaluation").disabled = false;
    $("dresStatus").textContent = "Connected. Choose an evaluation.";
  } catch (error) {
    $("dresStatus").textContent = "Connection failed: " + error.message;
  }
};

//choose the evaluation that submission will go to
$("evaluation").onchange = async () => {
  selectedEvaluation = "";
  startNewTask();
  $("submit").disabled = true;
  if (!$("evaluation").value) return;
  try {
    const params = new URLSearchParams({ name: $("evaluation").value });
    const result = await request("/dres/select?" + params, { method: "POST" });
    selectedEvaluation = result.evaluation;
    $("dresStatus").textContent = "Submitting to: " + selectedEvaluation;
    $("submit").disabled = !selected;
    updateBatch();
  } catch (error) {
    $("dresStatus").textContent = "Selection failed: " + error.message;
  }
};

//submit the playing shot with start/end range from the form
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
      body: JSON.stringify({ video_id: selected.video_id, start_ms: start, end_ms: end }),
    });
    $("reply").textContent = JSON.stringify(result, null, 2);
    //mark the shot as sent if DRES accepts it
    if (result.http_status >= 200 && result.http_status < 300) {
      sent.add(shotKey(selected));
      updateBatch();
      rerender();
    }
  } catch (error) {
    $("reply").textContent = "Submission failed: " + error.message;
  } finally {
    $("submit").disabled = false;
  }
};

//submits every ticked but not yet marked "sent" shots.
$("batchSubmit").onclick = async () => {
  const shots = [...batch.values()].filter((shot) => !sent.has(shotKey(shot)));
  if (!shots.length || !selectedEvaluation) return;
  if (!confirm("Submit " + shots.length + " shot(s) to \"" + selectedEvaluation + "\"?\n"
      + "Each is sent as its own shot range. Wrong ones cost points.")) return;
  $("batchSubmit").disabled = true;
  $("reply").textContent = "Submitting " + shots.length + " shot(s)...";
  try {
    const results = await request("/submit_batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        answers: shots.map((shot) => ({ video_id: shot.video_id, start_ms: shot.start_ms, end_ms: shot.end_ms })),
      }),
    });
    //shows the DRES results
    $("reply").textContent = results.map((result, i) => {
      const shot = shots[i];
      const ok = result.http_status >= 200 && result.http_status < 300;
      if (ok) {
        sent.add(shotKey(shot));
        batch.delete(shotKey(shot));
      }
      const verdict = (result.reply && (result.reply.submission || result.reply.description)) || JSON.stringify(result.reply);
      return shot.video_id + " shot " + shot.shot_no + ": " + (ok ? "" : "FAILED " + result.http_status + " ") + verdict;
    }).join("\n");
  } catch (error) {
    $("reply").textContent = "Batch submission failed: " + error.message;
  } finally {
    updateBatch();
    rerender();
  }
};
