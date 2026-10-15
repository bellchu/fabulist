"use strict";
const $ = (id) => document.getElementById(id);
let cfg = null, current = null, pollTimer = null;

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") n.className = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const kid of kids) n.append(kid);
  return n;
}
function toast(msg) { const t = $("toast"); t.textContent = msg; t.hidden = false; setTimeout(() => (t.hidden = true), 5000); }
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let m = r.statusText;
    try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : m; } catch (e) { /* non-JSON error */ }
    throw new Error(m);
  }
  return r.status === 204 ? null : r;
}
const json = async (path, opts) => (await api(path, opts)).json();
const post = (path, body) => json(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message); } };

function fill(sel, entries) { sel.replaceChildren(...entries.map(([v, l]) => el("option", { value: v }, l))); }

async function loadProjects() {
  const list = await json("/api/projects");
  $("projects").replaceChildren(...list.map((p) =>
    el("li", { class: current && current.id === p.id ? "active" : "", onclick: guard(() => openProject(p)) }, p.name)));
}
async function openProject(p) {
  current = p; $("workspace").hidden = false; $("title").textContent = p.name + " · " + cfg.platforms[p.platform];
  await Promise.all([loadProjects(), loadDocs(), loadStories(), pollRun()]);
}
async function loadDocs() {
  const docs = await json("/api/projects/" + current.id + "/documents");
  $("docs").replaceChildren(...docs.map((d) => el("li", {}, d.filename + " (" + d.chars + " chars) ",
    el("button", { class: "ghost", onclick: guard(async () => { await api("/api/projects/" + current.id + "/documents/" + d.id, { method: "DELETE" }); loadDocs(); }) }, "remove"))));
}
async function loadStories() {
  const stories = await json("/api/projects/" + current.id + "/stories");
  const box = $("stories");
  if (!stories.length) { box.replaceChildren(el("p", { class: "meta" }, "No stories yet.")); return; }
  box.replaceChildren(...stories.map(storyCard));
}
function tags(arr) { return arr.map((t) => el("span", { class: "tag" }, t)); }
function storyCard(s) {
  const setStatus = (status) => guard(async () => {
    await json("/api/projects/" + current.id + "/stories/" + s.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status }) });
    loadStories();
  });
  const edit = guard(async () => {
    const title = prompt("Title", s.title); if (title === null) return;
    const narrative = prompt("Narrative", s.narrative); if (narrative === null) return;
    await json("/api/projects/" + current.id + "/stories/" + s.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title, narrative }) });
    loadStories();
  });
  return el("div", { class: "story " + s.status },
    el("h4", {}, s.title),
    el("div", { class: "meta" }, s.epic + " · " + s.persona + " · " + s.priority + " · " + s.status),
    el("p", {}, s.narrative),
    el("ul", {}, ...s.acceptance_criteria.map((a) => el("li", {}, a))),
    ...(s.variant_criteria.length ? [el("div", { class: "meta" }, "Product variants"), el("ul", {}, ...s.variant_criteria.map((v) => el("li", {}, v.scope + ": " + v.criteria)))] : []),
    el("div", {}, ...tags(s.product_scope), ...tags(s.compliance_tags), ...tags(s.labels)),
    el("div", { class: "meta" }, "Source: " + s.source_ref),
    ...(s.open_questions.length ? [el("div", { class: "meta" }, "Open questions: " + s.open_questions.join("; "))] : []),
    el("div", { class: "row" },
      el("button", { class: "ghost", onclick: setStatus("approved") }, "Approve"),
      el("button", { class: "ghost", onclick: setStatus("rejected") }, "Reject"),
      el("button", { class: "ghost", onclick: setStatus("draft") }, "Reset"),
      el("button", { class: "ghost", onclick: edit }, "Edit")));
}
async function pollRun() {
  clearTimeout(pollTimer);
  const run = await json("/api/projects/" + current.id + "/runs/latest");
  const st = $("run-status");
  if (!run.id) { st.textContent = ""; return; }
  if (run.status === "running") {
    st.textContent = "Running… " + run.progress; $("generate").disabled = true;
    pollTimer = setTimeout(guard(async () => { await pollRun(); }), 1500);
  } else {
    $("generate").disabled = false;
    st.textContent = run.status === "failed" ? "Failed: " + run.error : run.story_count + " stories generated";
    await loadStories();
  }
}

$("new-project").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const p = await post("/api/projects", { name: $("p-name").value, platform: $("p-platform").value });
  $("p-name").value = ""; await openProject(p);
}));
$("upload").addEventListener("click", guard(async () => {
  const f = $("file").files[0]; if (!f) return toast("Choose a file first");
  const fd = new FormData(); fd.append("file", f);
  await json("/api/projects/" + current.id + "/documents", { method: "POST", body: fd });
  $("file").value = ""; loadDocs();
}));
$("add-text").addEventListener("click", guard(async () => {
  await post("/api/projects/" + current.id + "/documents/text", { name: $("t-name").value, text: $("t-text").value });
  $("t-text").value = ""; loadDocs();
}));
$("generate").addEventListener("click", guard(async () => {
  await post("/api/projects/" + current.id + "/runs", { provider: $("provider").value, model: $("model").value || null });
  await pollRun();
}));
$("export").addEventListener("click", () => {
  window.location = "/api/projects/" + current.id + "/export?format=" + encodeURIComponent($("format").value);
});
$("search").addEventListener("click", guard(async () => {
  const hits = await post("/api/projects/" + current.id + "/search", { query: $("q").value });
  $("hits").replaceChildren(...hits.map((h) => el("li", {}, "[" + h.filename + "] " + h.text.slice(0, 240))));
}));

(async () => {
  cfg = await json("/api/config");
  fill($("p-platform"), Object.entries(cfg.platforms));
  fill($("provider"), cfg.providers.map((p) => [p, p]));
  $("provider").value = cfg.default_provider;
  fill($("format"), cfg.formats.map((f) => [f, f]));
  await loadProjects();
})().catch((e) => toast(e.message));
