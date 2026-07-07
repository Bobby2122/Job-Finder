from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .tracker import ApplicationTracker, REJECTION_REASONS, STATUSES


TRACKER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Application Tracker · JobFinder</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #17211c;
      --muted: #68746d;
      --paper: #f4f1e8;
      --card: #fffdf7;
      --line: #d9d4c7;
      --green: #22634d;
      --green-soft: #dcebe3;
      --amber: #9b5d16;
      --red: #963d35;
      --blue: #315e87;
      --shadow: 0 12px 32px rgba(35, 47, 40, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 8% 0%, #e4eee6 0, transparent 28rem),
        var(--paper);
      color: var(--ink);
      font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, sans-serif;
    }
    main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 42px 0 80px; }
    .eyebrow { color: var(--green); font-weight: 800; letter-spacing: .11em; text-transform: uppercase; font-size: 12px; }
    h1 { margin: 6px 0 8px; font: 700 clamp(32px, 5vw, 54px)/1.04 Georgia, serif; letter-spacing: -.035em; }
    .lede { margin: 0; max-width: 720px; color: var(--muted); font-size: 17px; }
    .summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 28px 0 20px; }
    .stat { background: rgba(255,253,247,.8); border: 1px solid var(--line); border-radius: 16px; padding: 16px; }
    .stat strong { display: block; font: 700 27px/1 Georgia, serif; }
    .stat span { color: var(--muted); font-size: 13px; }
    .toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 22px; }
    .manual {
      background: rgba(255,253,247,.82); border: 1px solid var(--line);
      border-radius: 18px; padding: 16px; margin: 20px 0 22px;
    }
    .manual h2 { font-size: 18px; margin-bottom: 10px; }
    .manual-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 9px; }
    .manual-grid input, .manual-grid select {
      width: 100%; border: 1px solid var(--line); border-radius: 10px;
      background: var(--card); padding: 9px 10px; font: inherit;
    }
    input[type=search] {
      min-width: min(330px, 100%); flex: 1; border: 1px solid var(--line);
      border-radius: 12px; background: var(--card); padding: 11px 13px; font: inherit;
    }
    .filters { display: flex; gap: 7px; flex-wrap: wrap; }
    button, select, .button {
      border: 1px solid var(--line); border-radius: 10px; background: var(--card);
      color: var(--ink); padding: 9px 11px; font: 700 13px/1.2 inherit; cursor: pointer;
    }
    button.active { border-color: var(--green); background: var(--green); color: white; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 16px; }
    .card {
      background: var(--card); border: 1px solid var(--line); border-radius: 19px;
      padding: 20px; box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 13px;
    }
    .card-head { display: flex; justify-content: space-between; gap: 14px; align-items: flex-start; }
    h2 { margin: 0; font: 700 21px/1.2 Georgia, serif; }
    .company { color: var(--green); font-weight: 800; margin-top: 3px; }
    .meta { display: flex; flex-wrap: wrap; gap: 7px; color: var(--muted); font-size: 13px; }
    .meta span { border-right: 1px solid var(--line); padding-right: 7px; }
    .meta span:last-child { border: 0; }
    .details { display: grid; gap: 7px; color: #2b3931; font-size: 13px; }
    .details p { margin: 0; }
    .details strong { color: var(--ink); }
    .badge { white-space: nowrap; border-radius: 99px; padding: 5px 9px; font-size: 12px; font-weight: 850; }
    .status-New { background: var(--green-soft); color: var(--green); }
    .status-Viewed { background: #e6edf4; color: var(--blue); }
    .status-Started { background: #fff0d5; color: var(--amber); }
    .status-Applied { background: #dce8f4; color: #244f78; }
    .status-Rejected, .status-Not-Interested { background: #f3dfdc; color: var(--red); }
    .status-Saved { background: #eee4fa; color: #674396; }
    .actions { display: grid; grid-template-columns: 1fr auto; gap: 9px; }
    .button { display: inline-flex; align-items: center; justify-content: center; text-decoration: none; background: var(--green); color: white; border-color: var(--green); }
    textarea {
      width: 100%; min-height: 72px; resize: vertical; border: 1px solid var(--line);
      border-radius: 11px; background: #fff; padding: 10px; font: inherit;
    }
    .note-row { display: flex; align-items: center; gap: 8px; }
    .reason-row { display: grid; grid-template-columns: 190px 1fr auto; gap: 8px; align-items: center; }
    .reason-row input { border: 1px solid var(--line); border-radius: 10px; padding: 9px 10px; font: inherit; }
    .saved { color: var(--green); font-size: 12px; min-height: 18px; }
    .empty { grid-column: 1/-1; text-align: center; padding: 64px 16px; color: var(--muted); }
    @media (max-width: 760px) {
      .summary { grid-template-columns: repeat(2, 1fr); }
      .grid { grid-template-columns: 1fr; }
      .manual-grid, .reason-row { grid-template-columns: 1fr; }
      main { width: min(100% - 20px, 1180px); padding-top: 24px; }
    }
  </style>
</head>
<body>
<main>
  <div class="eyebrow">Bobby’s JobFinder</div>
  <h1>Application Tracker</h1>
  <p class="lede">Opening an application marks it Viewed—never Applied. Application outcomes stay manual and honest.</p>
  <section class="summary" id="summary"></section>
  <section class="manual">
    <h2>Manually add an application-state record</h2>
    <form class="manual-grid" id="manualForm">
      <input name="company" placeholder="Company" required>
      <input name="title" placeholder="Role title" required>
      <input name="location" placeholder="Location">
      <input name="url" placeholder="Application URL" required>
      <select name="status" aria-label="Manual status"></select>
      <select name="reason_category" aria-label="Reason category"></select>
      <input name="manual_reason" placeholder="Optional reason / notes">
      <button type="submit">Add / update</button>
      <span class="saved" id="manualSaved"></span>
    </form>
  </section>
  <div class="toolbar">
    <input id="search" type="search" placeholder="Search company, title, location, or notes">
    <div class="filters" id="filters"></div>
  </div>
  <section class="grid" id="jobs" aria-live="polite"></section>
</main>
<script>
const statuses = __STATUSES__;
const rejectionReasons = __REJECTION_REASONS__;
let jobs = [];
let active = "All";
const escClass = value => value.replaceAll(" ", "-");

async function api(path, options={}) {
  const response = await fetch(path, {
    ...options,
    headers: {"Content-Type": "application/json", ...(options.headers || {})}
  });
  if (!response.ok) throw new Error((await response.json()).error || response.statusText);
  return response.json();
}

function counts() {
  const by = Object.fromEntries(statuses.map(status => [status, jobs.filter(job => job.status === status).length]));
  document.getElementById("summary").innerHTML = `
    <div class="stat"><strong>${jobs.length}</strong><span>tracked roles</span></div>
    <div class="stat"><strong>${by.Saved || 0}</strong><span>saved</span></div>
    <div class="stat"><strong>${by.Started || 0}</strong><span>in progress</span></div>
    <div class="stat"><strong>${by.Applied || 0}</strong><span>applied</span></div>`;
}

function makeFilters() {
  const root = document.getElementById("filters");
  root.replaceChildren();
  for (const status of ["All", "Saved", "New", "Viewed", "Started", "Applied", "Rejected", "Not Interested"]) {
    const button = document.createElement("button");
    button.textContent = status;
    button.className = status === active ? "active" : "";
    button.onclick = () => { active = status; makeFilters(); render(); };
    root.append(button);
  }
}

function fillSelect(select, values, selected="") {
  select.replaceChildren();
  for (const value of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value || "No reason";
    option.selected = value === selected;
    select.append(option);
  }
}

function card(job) {
  const article = document.createElement("article");
  article.className = "card";
  const head = document.createElement("div");
  head.className = "card-head";
  const titleWrap = document.createElement("div");
  const title = document.createElement("h2");
  title.textContent = job.title;
  const company = document.createElement("div");
  company.className = "company";
  company.textContent = job.company;
  titleWrap.append(title, company);
  const badge = document.createElement("span");
  badge.className = `badge status-${escClass(job.status)}`;
  badge.textContent = job.status;
  head.append(titleWrap, badge);

  const meta = document.createElement("div");
  meta.className = "meta";
  for (const value of [
    job.bucket || job.recommendation_tier,
    job.company_size,
    job.location,
    job.employment_type || "Employment not listed",
    `Score ${Number(job.score || 0).toFixed(1)}`
  ]) {
    const span = document.createElement("span");
    span.textContent = value;
    meta.append(span);
  }

  const actions = document.createElement("div");
  actions.className = "actions";
  const open = document.createElement("a");
  open.className = "button";
  open.href = `/go/${encodeURIComponent(job.id)}`;
  open.target = "_blank";
  open.rel = "noopener";
  open.textContent = "Open application ↗";
  open.onclick = () => {
    if (job.status === "New") {
      job.status = "Viewed";
      setTimeout(() => { counts(); makeFilters(); render(); }, 80);
    }
  };
  const select = document.createElement("select");
  select.setAttribute("aria-label", `Status for ${job.title}`);
  for (const status of statuses) {
    const option = document.createElement("option");
    option.value = status;
    option.textContent = status;
    option.selected = status === job.status;
    select.append(option);
  }
  select.onchange = async () => {
    try {
      const updated = await api(`/api/jobs/${encodeURIComponent(job.id)}/status`, {
        method: "POST", body: JSON.stringify({status: select.value})
      });
      Object.assign(job, updated.job);
      counts(); makeFilters(); render();
    } catch (error) { alert(error.message); select.value = job.status; }
  };
  actions.append(open, select);

  const details = document.createElement("div");
  details.className = "details";
  const keywordText = Array.isArray(job.matched_keywords) && job.matched_keywords.length
    ? job.matched_keywords.join(", ")
    : "None explicit; verify scope.";
  const concerns = Array.isArray(job.concerns) ? job.concerns.join("; ") : String(job.concerns || "");
  for (const [label, value] of [
    ["Why recommended", job.why_recommended || "Manual entry or no recommendation text stored yet."],
    ["AI/agentic relevance", job.ai_relevance || "Not recorded"],
    ["AI Engineer classifier", `${job.ai_engineer ? "Passed" : "Not passed"}${job.ai_classification_reason ? " — " + job.ai_classification_reason : ""}`],
    ["Matched keywords", keywordText],
    ["Concerns", concerns || "None recorded"],
    ["Pure SWE vs AI-focused", job.pure_swe_signal ? "Potential pure SWE concern" : (job.ai_relevance || "Not recorded")],
    ["Source", job.source || "Unknown"],
    ["Status updated", job.status_updated_at || job.updated_at || "Not recorded"]
  ]) {
    const p = document.createElement("p");
    p.innerHTML = `<strong>${label}:</strong> `;
    p.append(document.createTextNode(value));
    details.append(p);
  }

  const reasonRow = document.createElement("div");
  reasonRow.className = "reason-row";
  const reasonSelect = document.createElement("select");
  fillSelect(reasonSelect, ["", ...rejectionReasons], job.reason_category || "");
  const reasonInput = document.createElement("input");
  reasonInput.placeholder = "Optional rejection / not-interested reason";
  reasonInput.value = job.manual_reason || "";
  const reasonSave = document.createElement("button");
  reasonSave.textContent = "Save reason";
  reasonSave.onclick = async () => {
    try {
      const updated = await api(`/api/jobs/${encodeURIComponent(job.id)}/reason`, {
        method: "POST",
        body: JSON.stringify({
          reason_category: reasonSelect.value,
          manual_reason: reasonInput.value
        })
      });
      Object.assign(job, updated.job);
      saved.textContent = "Reason saved";
      setTimeout(() => saved.textContent = "", 1500);
    } catch (error) { saved.textContent = error.message; }
  };
  reasonRow.append(reasonSelect, reasonInput, reasonSave);

  const notes = document.createElement("textarea");
  notes.placeholder = "Notes — cover letter, sponsorship question, application details…";
  notes.value = job.notes || "";
  const noteRow = document.createElement("div");
  noteRow.className = "note-row";
  const save = document.createElement("button");
  save.textContent = "Save notes";
  const saved = document.createElement("span");
  saved.className = "saved";
  save.onclick = async () => {
    try {
      const updated = await api(`/api/jobs/${encodeURIComponent(job.id)}/notes`, {
        method: "POST", body: JSON.stringify({notes: notes.value})
      });
      Object.assign(job, updated.job);
      saved.textContent = "Saved";
      setTimeout(() => saved.textContent = "", 1500);
    } catch (error) { saved.textContent = error.message; }
  };
  noteRow.append(save, saved);
  article.append(head, meta, actions, details, reasonRow, notes, noteRow);
  return article;
}

function render() {
  const query = document.getElementById("search").value.trim().toLowerCase();
  const shown = jobs.filter(job => {
    if (active !== "All" && job.status !== active) return false;
    if (!query) return true;
    return [job.company, job.title, job.location, job.notes, job.bucket]
      .some(value => String(value || "").toLowerCase().includes(query));
  });
  const root = document.getElementById("jobs");
  root.replaceChildren();
  if (!shown.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No tracked jobs match this filter.";
    root.append(empty);
    return;
  }
  shown.forEach(job => root.append(card(job)));
}

async function load() {
  const payload = await api("/api/jobs");
  jobs = payload.jobs;
  counts(); makeFilters(); render();
}
document.getElementById("search").addEventListener("input", render);
const manualForm = document.getElementById("manualForm");
fillSelect(manualForm.elements.status, statuses, "Applied");
fillSelect(manualForm.elements.reason_category, ["", ...rejectionReasons], "");
manualForm.addEventListener("submit", async event => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(manualForm).entries());
  const target = document.getElementById("manualSaved");
  try {
    const updated = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    jobs = [updated.job, ...jobs.filter(job => job.id !== updated.job.id)];
    target.textContent = "Saved";
    manualForm.reset();
    fillSelect(manualForm.elements.status, statuses, "Applied");
    fillSelect(manualForm.elements.reason_category, ["", ...rejectionReasons], "");
    counts(); makeFilters(); render();
    setTimeout(() => target.textContent = "", 1500);
  } catch (error) { target.textContent = error.message; }
});
load().catch(error => {
  document.getElementById("jobs").innerHTML = `<div class="empty">${error.message}</div>`;
});
</script>
</body>
</html>
""".replace("__STATUSES__", json.dumps(STATUSES)).replace(
    "__REJECTION_REASONS__",
    json.dumps(REJECTION_REASONS),
)


def _handler(tracker: ApplicationTracker) -> type[BaseHTTPRequestHandler]:
    class TrackerHandler(BaseHTTPRequestHandler):
        server_version = "JobFinderTracker/1.0"

        def _json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Invalid Content-Length") from exc
            if length > 20_000:
                raise ValueError("Request body is too large")
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                raise ValueError("Request body must be valid JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            return payload

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path in {"/", "/tracker"}:
                body = TRACKER_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/jobs":
                status = parse_qs(parsed.query).get("status", [None])[0]
                try:
                    jobs = tracker.list_jobs(status)
                except ValueError as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json({"jobs": jobs, "statuses": STATUSES})
                return
            if parsed.path == "/health":
                self._json({"ok": True})
                return
            if parsed.path.startswith("/go/"):
                tracking_id = unquote(parsed.path.removeprefix("/go/"))
                try:
                    job = tracker.mark_viewed(tracking_id)
                except KeyError:
                    self._json({"error": "Unknown job"}, HTTPStatus.NOT_FOUND)
                    return
                destination = str(job.get("url", ""))
                if not destination.startswith(("https://", "http://")):
                    self._json(
                        {"error": "Stored application URL is invalid"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", destination)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            parts = [unquote(part) for part in parsed.path.split("/") if part]
            if parts == ["api", "jobs"]:
                try:
                    payload = self._body()
                    job = tracker.add_manual_job(
                        company=str(payload.get("company", "")),
                        title=str(payload.get("title", "")),
                        location=str(payload.get("location", "")),
                        url=str(payload.get("url", "")),
                        status=str(payload.get("status", "Applied")),
                        reason_category=str(payload.get("reason_category", "")),
                        manual_reason=str(payload.get("manual_reason", "")),
                        notes=str(payload.get("notes", "")),
                    )
                except ValueError as exc:
                    self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                    return
                self._json({"job": job}, HTTPStatus.CREATED)
                return
            if len(parts) != 4 or parts[:2] != ["api", "jobs"]:
                self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                return
            tracking_id, action = parts[2], parts[3]
            try:
                payload = self._body()
                if action == "status":
                    job = tracker.update_status(
                        tracking_id,
                        str(payload.get("status", "")),
                        str(payload.get("reason_category", "")),
                        str(payload.get("manual_reason", "")),
                    )
                elif action == "notes":
                    job = tracker.update_notes(
                        tracking_id,
                        str(payload.get("notes", "")),
                    )
                elif action == "reason":
                    job = tracker.update_reason(
                        tracking_id,
                        str(payload.get("reason_category", "")),
                        str(payload.get("manual_reason", "")),
                    )
                else:
                    self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                    return
            except KeyError:
                self._json({"error": "Unknown job"}, HTTPStatus.NOT_FOUND)
                return
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._json({"job": job})

        def log_message(self, format: str, *args: object) -> None:
            print(f"[TRACKER] {self.address_string()} - {format % args}")

    return TrackerHandler


def serve_tracker(
    data_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    tracker = ApplicationTracker(data_path)
    server = ThreadingHTTPServer((host, port), _handler(tracker))
    print(f"Application Tracker: http://{host}:{server.server_port}")
    print(f"Persistent data: {data_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTracker stopped.")
    finally:
        server.server_close()
