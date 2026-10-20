// Project workspace: Overview, Sources, Requirements, Stories, Traceability, Export.
import {
  h, mount, icon, api, guard, toast, modal, confirmBox, drawer, badge, statusTone, typeTone, scorePill, meter, issueIcon,
  issueList, empty, timeAgo, fmt, duration, field, requireValue, select, linesInput, readLines, tagsInput, segmented, download, historyList,
} from "./core.js";
import { state, setCrumbs } from "./main.js";

const TABS = [["overview", "Overview"], ["sources", "Sources"], ["requirements", "Requirements"],
  ["stories", "Stories"], ["trace", "Traceability"], ["export", "Export"]];
const TYPE_COLORS = { business: "#5867e8", functional: "#0176d3", "non-functional": "#dd7a01", constraint: "#ba0517", rule: "#06a59a" };

// Filters survive re-renders within the session.
const filters = { reqQ: "", reqType: "", reqStatus: "", reqFlagged: false, storyQ: "", storyStatus: "", storyView: "map", storyReady: "", traceGaps: false, exportScope: "all" };
let ctx = null;
let pollTimer = null;

const rerender = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
const base = () => "/api/projects/" + ctx.pid;

export async function renderProject(content, pid, tab) {
  clearTimeout(pollTimer);
  const project = await api.get("/api/projects/" + pid);
  ctx = { pid, project, tab };
  setCrumbs({ href: "#/", label: "Projects" }, project.name);
  const body = h("div");
  mount(content, header(project), stepper(project), tabBar(project, tab), body);
  const view = { overview: viewOverview, sources: viewSources, requirements: viewRequirements, stories: viewStories, trace: viewTrace, export: viewExport }[tab] || viewOverview;
  await view(body);
  revealCurrentStep(content);  // after the view: measuring a half-built (short) page resets its scroll
  schedulePoll();
}

// ---- chrome -------------------------------------------------------------

function header(p) {
  const kv = (label, value) => h("div", {}, h("div", { class: "object-label" }, label), h("div", {}, value));
  return h("div", { class: "page-head" },
    h("span", { class: "object-icon project" }, icon("folder")),
    h("div", { class: "grow" }, h("div", { class: "object-label" }, "Project"), h("h1", {}, p.name)),
    h("div", { class: "row" }, h("button", { class: "btn", onclick: editProjectDialog }, "Edit"),
      h("a", { class: "btn", href: "#/p/" + p.id + "/export" }, "Export")),
    h("div", { class: "row wrap", style: { flexBasis: "100%", gap: "32px", paddingLeft: "44px" } },
      kv("Target platform", state.meta.platforms[p.platform] || p.platform),
      kv("BRDs", String(p.documents)), kv("Requirements", String(p.requirements)), kv("Stories", String(p.stories)),
      kv("Coverage", p.coverage + "%"), kv("Description", p.description || "–")));
}

// On narrow screens the stepper scrolls sideways with a hidden scrollbar; centre the current
// step so users can see where they are. Not scrollIntoView, which can also scroll the page.
function revealCurrentStep(root) {
  const strip = root.querySelector(".stepper"), cur = strip && strip.querySelector(".current");
  if (!cur) return;
  const s = strip.getBoundingClientRect(), c = cur.getBoundingClientRect();
  strip.scrollLeft += c.left - s.left - (s.width - c.width) / 2;
}

function stepper(p) {
  const steps = [
    ["sources", "Sources", p.documents + " BRD" + (p.documents === 1 ? "" : "s"), p.documents > 0],
    ["requirements", "Review requirements", p.requirements ? p.requirements_approved + " of " + p.requirements + " approved" : "Not extracted", p.requirements > 0 && p.requirements_approved > 0],
    ["stories", "Refine stories", p.stories ? p.stories_ready + " of " + p.stories + " ready" : "Not generated", p.stories > 0 && p.stories_ready === p.stories],
    ["trace", "Traceability", p.requirements ? p.coverage + "% coverage" : "–", p.requirements > 0 && p.coverage === 100],
    ["export", "Export", p.stories_approved + " approved stories", p.stories_approved > 0],
  ];
  const current = steps.findIndex((s) => !s[3]);
  return h("div", { class: "path-wrap" },
    h("div", { class: "stepper", role: "list", "aria-label": "Workflow" }, steps.map(([tab, t, s, done], i) =>
      h("a", { class: "step" + (done ? " done" : "") + (i === current ? " current" : ""), href: "#/p/" + p.id + "/" + tab, role: "listitem", title: t + ": " + s,
        "aria-current": i === current ? "step" : null },
        h("span", { class: "num" }, icon("check")), h("span", { class: "t" }, t)))),
    h("div", { class: "path-detail" }, steps.map(([, t, s]) => h("div", {}, t, h("b", {}, s)))));
}

function tabBar(p, active) {
  const counts = { sources: p.documents, requirements: p.requirements, stories: p.stories };
  // Page links, not ARIA tabs: there are no tab panels or arrow-key switching.
  return h("nav", { class: "tabs", "aria-label": "Project sections" }, TABS.map(([id, label]) =>
    h("a", { class: "tab" + (id === active ? " active" : ""), href: "#/p/" + p.id + "/" + id, "aria-current": id === active ? "page" : null },
      label, counts[id] !== undefined ? h("span", { class: "count" }, counts[id]) : null)));
}

function editProjectDialog() {
  const p = ctx.project;
  const name = h("input", { type: "text", value: p.name, maxlength: "120" });
  const platform = select(Object.entries(state.meta.platforms), p.platform);
  const desc = h("textarea", { rows: 3, value: p.description });
  modal({
    title: "Project settings",
    body: h("div", { class: "stack" }, field("Name", name), field("Target platform", platform), field("Description", desc),
      h("div", { class: "callout warn small" }, h("div", { class: "grow" }, "Deleting a project removes its BRDs, requirements and stories."),
        h("button", { class: "btn btn-sm btn-danger", onclick: guard(async () => {
          if (!(await confirmBox("Delete '" + p.name + "' and everything in it?", { danger: true, okLabel: "Delete project" }))) return;
          await api.del(base()); location.hash = "#/"; toast("Project deleted");
        }) }, "Delete project"))),
    actions: [{ label: "Save", kind: "btn-primary", onClick: async () => {
      await api.patch(base(), { name: name.value, platform: platform.value, description: desc.value });
      toast("Project updated", "ok"); rerender();
    } }],
  });
}

// ---- runs ---------------------------------------------------------------

function runPanel(stage) {
  const run = ctx.project.runs[stage];
  const running = run && run.status === "running";
  const scope = select([["all", "Approved + draft requirements"], ["approved", "Approved requirements only"]], "all", { "aria-label": "Requirement scope" });
  const isReq = stage === "requirements";
  const start = guard(async () => {
    const hasData = isReq ? ctx.project.requirements > 0 || ctx.project.stories > 0 : ctx.project.stories > 0;
    if (hasData && !(await confirmBox(isReq
      ? "Re-extracting replaces all requirements and the current backlog, including your edits."
      : "Regenerating replaces the current stories, including your edits.", { okLabel: isReq ? "Re-extract" : "Regenerate", danger: true }))) return;
    await api.post(base() + "/runs", { stage, scope: scope.value });
    toast(isReq ? "Extraction started" : "Story generation started");
    rerender();
  });
  let statusLine = null;
  if (running) {
    statusLine = h("div", { class: "row small", style: { flexBasis: "100%" } }, badge("Running", "blue"),
      h("span", { class: "muted grow", id: "run-progress" }, run.progress || "Starting…"),
      h("button", { class: "btn btn-sm btn-ghost btn-danger", onclick: guard(async () => {
        await api.post(base() + "/runs/" + run.id + "/cancel"); toast("Cancelling after the current AI request…");
      }) }, "Cancel run"));
  } else if (run && run.status === "cancelled") {
    statusLine = h("div", { class: "callout warn small", style: { flexBasis: "100%" } }, icon("info"), "Last run was cancelled; existing data was kept.");
  } else if (run && run.status === "failed") {
    statusLine = h("div", { class: "callout error small", style: { flexBasis: "100%" } }, icon("alert"), "Last run failed: " + run.error);
  } else if (run) {
    statusLine = h("div", { class: "muted small", style: { flexBasis: "100%" } },
      "Last run " + timeAgo(run.created) + " · " +
      run.item_count + (isReq ? " requirements" : " stories") + " · " + duration(run.duration_ms) +
      (run.tokens_in + run.tokens_out ? " · " + fmt(run.tokens_in + run.tokens_out) + " tokens" : ""));
  }
  return h("div", { class: "card", style: { marginBottom: "16px" } },
    h("div", { class: "runbox" },
      h("div", { class: "grow" }, h("div", { class: "title" }, isReq ? "Extract requirements from BRDs" : "Generate user stories"),
        h("div", { class: "muted small" }, isReq
          ? "AI reads each BRD section and proposes atomic requirements with types and sources for your review."
          : "Builds epics, features and INVEST stories with Gherkin criteria, linked to requirement IDs.")),
      isReq ? null : scope,
      h("button", { class: "btn btn-primary", disabled: running, onclick: start }, icon("sparkles"), running ? "Running…" : isReq ? (ctx.project.requirements ? "Re-extract" : "Extract") : (ctx.project.stories ? "Regenerate" : "Generate")),
      running ? h("div", { class: "progress-line" }) : null, statusLine));
}

function schedulePoll() {
  const running = Object.values(ctx.project.runs || {}).some((r) => r && r.status === "running");
  if (!running) return;
  const pid = ctx.pid;
  pollTimer = setTimeout(guard(async () => {
    if (!ctx || ctx.pid !== pid) return;
    const runs = await api.get(base() + "/runs?limit=2");
    const active = runs.find((r) => r.status === "running");
    if (active) {
      const el = document.getElementById("run-progress");
      if (el) el.textContent = active.progress || "Working…";
      schedulePoll();
    } else {
      const last = runs[0];
      if (last && last.status === "failed") toast("Run failed: " + last.error, "error");
      else if (last && last.status === "cancelled") toast("Run cancelled");
      else toast("Run finished: " + (last ? last.item_count : 0) + " items", "ok");
      rerender();
    }
  }), 1500);
}

// ---- overview -----------------------------------------------------------

async function viewOverview(body) {
  const p = ctx.project;
  const [runs, changes] = await Promise.all([api.get(base() + "/runs?limit=8"), api.get(base() + "/history?limit=8")]);
  const stat = (label, value, foot, pct) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    foot ? h("div", { class: "foot" }, foot) : null, pct !== undefined ? meter(pct) : null);
  const next = !p.documents ? ["Upload BRDs", "Start by adding the business requirements documents for this application.", "sources"]
    : !p.requirements ? ["Extract requirements", "Let AI propose atomic requirements from your BRDs.", "requirements"]
    : !p.requirements_approved ? ["Review requirements", "Approve, edit or exclude extracted requirements before generating stories.", "requirements"]
    : !p.stories ? ["Generate stories", "Turn reviewed requirements into epics, features and user stories.", "stories"]
    : p.coverage < 100 ? ["Close traceability gaps", "Some requirements are not covered by any story.", "trace"]
    : p.stories_ready < p.stories ? ["Refine stories", "Resolve quality issues and open questions until stories meet the Definition of Ready.", "stories"]
    : ["Export the backlog", "Everything is covered. Export to Jira, Azure DevOps or Excel.", "export"];
  const total = Object.values(p.requirement_types).reduce((a, b) => a + b, 0);
  mount(body, 
    h("div", { class: "callout info", style: { marginBottom: "16px", alignItems: "center" } }, icon("sparkles"),
      h("div", { class: "grow" }, h("b", {}, "Next step: " + next[0]), h("div", { class: "small" }, next[1])),
      h("a", { class: "btn btn-sm btn-primary", href: "#/p/" + p.id + "/" + next[2] }, "Go")),
    h("div", { class: "stats", style: { marginBottom: "16px" } },
      stat("BRD documents", p.documents),
      stat("Requirements", p.requirements, p.requirements_approved + " approved · " + p.requirements_flagged + " flagged",
        p.requirements ? Math.round((100 * p.requirements_approved) / p.requirements) : 0),
      stat("Stories", p.stories, p.stories_ready + " meet Definition of Ready", p.stories ? Math.round((100 * p.stories_ready) / p.stories) : 0),
      stat("Coverage", p.coverage + "%", "Requirements linked to stories", p.coverage),
      stat("Requirement quality", p.requirement_quality || "–", "ISO 29148 checks", p.requirement_quality),
      stat("Story quality", p.story_quality || "–", "INVEST + Gherkin checks", p.story_quality)),
    h("div", { class: "grid grid-2" },
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Requirement mix")),
        h("div", { class: "card-body" }, total ? [
          h("div", { class: "typebar" }, Object.entries(p.requirement_types).filter(([, n]) => n).map(([t, n]) =>
            h("span", { title: t + ": " + n, style: { width: (100 * n) / total + "%", background: TYPE_COLORS[t] } }))),
          h("div", { class: "legend" }, Object.entries(p.requirement_types).map(([t, n]) => h("span", {}, h("i", { style: { background: TYPE_COLORS[t] } }), t + " " + n)))]
          : h("p", { class: "muted" }, "No requirements yet."))),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Recent runs")),
        runs.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
          h("thead", {}, h("tr", {}, ["Stage", "Status", "Items", "When"].map((x) => h("th", {}, x)))),
          h("tbody", {}, runs.map((r) => h("tr", {},
            h("td", {}, r.stage, r.actor ? h("div", { class: "muted small" }, "by " + r.actor) : null),
            h("td", {}, badge(r.status, statusTone[r.status])), h("td", {}, r.status === "done" ? r.item_count : "–"),
            h("td", { class: "muted small nowrap" }, timeAgo(r.created)))))))
          : h("div", { class: "card-body muted" }, "No runs yet."))),
    h("div", { class: "card", style: { marginTop: "16px" } }, h("div", { class: "card-head" }, h("h3", {}, "Recent changes"),
      h("span", { class: "muted small" }, "Audit trail of manual edits and reviews"),
      h("button", { class: "btn btn-sm right", title: "Download the complete audit trail as CSV", onclick: () => download(base() + "/history.csv") }, icon("download"), "Export trail")),
      h("div", { class: "card-body" }, historyList(changes, { showItem: true }))));
}

// ---- sources ------------------------------------------------------------

function repoDialog() {
  const url = h("input", { type: "url", placeholder: "https://github.com/org/brds.git" });
  const branch = h("input", { type: "text", placeholder: "Default branch" });
  const glob = h("input", { type: "text", value: "*", placeholder: "docs/brd/*.md, *.pdf" });
  const token = h("input", { type: "password", autocomplete: "new-password", placeholder: "Only for private repositories" });
  modal({
    title: "Connect BRD repository", wide: true,
    description: "Fabulist shallow-clones the repository over HTTPS on each sync and imports matching PDF, Word, Excel, CSV, Markdown and text files.",
    body: h("div", { class: "stack" }, field("Repository URL", url, "HTTPS clone URL (GitHub, GitLab, Bitbucket, Azure Repos…)."),
      h("div", { class: "form-grid" }, field("Branch", branch), field("Path filter", glob, "Comma-separated glob patterns relative to the repo root.")),
      field("Access token", token, "Read-only token. Stored encrypted; never shown again.")),
    actions: [{ label: "Connect and sync", kind: "btn-primary", onClick: async () => {
      const repo = await api.post(base() + "/repos", { url: url.value, branch: branch.value, path_glob: glob.value, token: token.value });
      await syncRepo(repo.id);
    } }],
  });
}

async function syncRepo(id) {
  toast("Syncing repository…");
  try {
    const r = await api.post(base() + "/repos/" + id + "/sync");
    toast("Synced " + r.commit.slice(0, 8) + ": " + r.added.length + " added, " + r.updated.length + " updated, " + r.removed.length + " removed" +
      (r.skipped.length ? ", " + r.skipped.length + " skipped" : ""), "ok");
  } finally { rerender(); }
}

function reposCard(repoList) {
  return h("div", { class: "card", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("div", { class: "grow" }, h("h3", {}, "BRD repositories"),
      h("div", { class: "muted small" }, "Keep BRDs in Git; sync to pull new and changed documents.")),
      h("button", { class: "btn btn-sm", onclick: repoDialog }, icon("link"), "Connect repository")),
    repoList.length ? repoList.map((r) => h("div", { class: "provider-row" },
      h("div", { class: "grow", style: { minWidth: "260px" } },
        h("div", { class: "row" }, h("b", { class: "mono" }, r.url), r.branch ? badge(r.branch) : null),
        h("div", { class: "small muted" }, "Path: " + r.path_glob + " · " + r.documents + " documents" + (r.has_token ? " · token " + r.key_hint : "")),
        r.last_error ? h("div", { class: "small", style: { color: "var(--red)" } }, r.last_error)
          : h("div", { class: "small muted" }, r.last_sync ? "Synced " + timeAgo(r.last_sync) + " at " + r.last_commit.slice(0, 8) : "Never synced")),
      h("button", { class: "btn btn-sm", onclick: guard(() => syncRepo(r.id)) }, icon("refresh"), "Sync now"),
      h("button", { class: "btn btn-sm btn-ghost btn-danger", "aria-label": "Disconnect " + r.url, onclick: guard(async () => {
        if (!(await confirmBox("Disconnect " + r.url + "? Its " + r.documents + " synced documents are removed; existing requirements are kept.", { danger: true, okLabel: "Disconnect" }))) return;
        await api.del(base() + "/repos/" + r.id); rerender();
      }) }, icon("trash"))))
      : h("div", { class: "card-body muted small" }, "No repository connected. Upload files below, or connect a Git repository that holds your BRDs."));
}

async function viewSources(body) {
  const [docs, repoList] = await Promise.all([api.get(base() + "/documents"), api.get(base() + "/repos")]);
  const fileInput = h("input", { type: "file", multiple: true, accept: ".pdf,.docx,.xlsx,.csv,.md,.markdown,.txt", class: "hidden" });
  const uploadFiles = guard(async (files) => {
    for (const f of files) {
      const fd = new FormData(); fd.append("file", f);
      const r = await api.upload(base() + "/documents", fd);
      toast("Added " + r.filename + " (" + r.indexed_chunks + " passages indexed)", "ok");
    }
    rerender();
  });
  fileInput.addEventListener("change", () => uploadFiles([...fileInput.files]));
  const drop = h("div", { class: "dropzone", tabindex: "0", role: "button", onclick: () => fileInput.click(),
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") fileInput.click(); } },
    icon("upload"), h("div", { style: { fontWeight: 600, marginTop: "6px" } }, "Drop BRD files here or click to browse"),
    h("div", { class: "small" }, "PDF, Word, Excel, CSV, Markdown or text · up to 10 MB each"));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadFiles([...e.dataTransfer.files]); });

  const pasteName = h("input", { type: "text", value: "pasted-brd.md" });
  const pasteText = h("textarea", { rows: 6, placeholder: "Paste BRD text. Markdown headings (#, ##) become epics and features.", "aria-label": "BRD text" });
  const q = h("input", { type: "search", placeholder: "Ask a question, e.g. What is the return window for med-surg supplies?", "aria-label": "Question about the BRDs" });
  const results = h("div", { class: "stack", style: { marginTop: "12px" } });
  const search = guard(async () => {
    if (!q.value.trim()) return;
    const hits = await api.post(base() + "/search", { query: q.value });
    results.replaceChildren(...(hits.length ? hits.map((x) => h("div", { class: "callout" },
      h("div", { class: "grow" }, h("div", { class: "row small" }, h("b", {}, x.filename), h("span", { class: "muted right" }, "match " + Math.round(x.score * 100) + "%")),
        h("div", { class: "small", style: { marginTop: "4px" } }, x.text.slice(0, 320) + (x.text.length > 320 ? "…" : "")))))
      : [h("p", { class: "muted" }, "No matching passages.")]));
  });
  const passage = (x, n) => h("div", { class: "callout" },
    h("div", { class: "grow" }, h("div", { class: "row small" }, n ? h("b", {}, "[" + n + "]") : null, h("b", {}, x.filename),
      h("span", { class: "muted right" }, "match " + Math.round(x.score * 100) + "%")),
      h("div", { class: "small", style: { marginTop: "4px" } }, x.text.slice(0, 320) + (x.text.length > 320 ? "…" : ""))));
  const ask = guard(async () => {
    if (q.value.trim().length < 3) return;
    results.replaceChildren(h("div", { class: "muted" }, "Reading the BRDs…"));
    const r = await api.post(base() + "/ask", { question: q.value });
    results.replaceChildren(
      h("div", { class: "callout info" }, icon("sparkles"), h("div", { class: "grow" }, h("div", { class: "small muted" }, "Answer generated by AI from the BRD passages below"),
        h("div", { style: { whiteSpace: "pre-wrap", marginTop: "4px" } }, r.answer))),
      r.citations.length ? h("div", { class: "section-title" }, "Sources") : null,
      ...r.citations.map((c) => passage(c, c.n)));
  });
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });

  mount(body, 
    h("div", { class: "grid grid-2", style: { marginBottom: "16px" } },
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Upload BRDs")), h("div", { class: "card-body" }, drop, fileInput)),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Paste text")),
        h("div", { class: "card-body stack" }, field("Document name", pasteName), pasteText,
          h("div", {}, h("button", { class: "btn", onclick: guard(async () => {
            requireValue(pasteText, "Paste some text first");
            await api.post(base() + "/documents/text", { name: pasteName.value, text: pasteText.value });
            toast("Document added", "ok"); rerender();
          }) }, icon("plus"), "Add document"))))),
    reposCard(repoList),
    h("div", { class: "card", style: { marginBottom: "16px" } },
      h("div", { class: "card-head" }, h("h3", { class: "grow" }, "Documents"), h("span", { class: "muted small" }, docs.length + " total")),
      docs.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Name", "Source", "Size", "Indexed passages", "Added", ""].map((x) => h("th", {}, x)))),
        h("tbody", {}, docs.map((d) => h("tr", {},
          h("td", {}, h("div", { class: "row" }, icon("file"), h("b", {}, d.filename))),
          h("td", {}, d.source === "repo" ? badge("Git", "violet") : badge("Upload")),
          h("td", { class: "muted" }, fmt(Math.round(d.chars / 6)) + " words"),
          h("td", {}, d.chunks), h("td", { class: "muted small" }, timeAgo(d.created)),
          h("td", { class: "nowrap", style: { textAlign: "right" } },
            h("button", { class: "btn btn-sm btn-ghost", onclick: guard(async () => {
              const full = await api.get(base() + "/documents/" + d.id);
              modal({ title: full.filename, wide: true, body: h("div", { class: "pre" }, full.text), actions: [] });
            }) }, "View"),
            h("button", { class: "btn btn-sm btn-ghost btn-danger", onclick: guard(async () => {
              if (!(await confirmBox("Remove " + d.filename + "? Existing requirements are kept.", { danger: true, okLabel: "Remove" }))) return;
              await api.del(base() + "/documents/" + d.id); rerender();
            }) }, "Remove")))))))
        : empty({ iconName: "file", title: "No BRDs yet", text: "Upload or paste at least one business requirements document." })),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Ask the BRDs"),
      h("span", { class: "muted small" }, "Answers come only from retrieved BRD passages, with numbered citations")),
      h("div", { class: "card-body" }, h("div", { class: "row" }, q, h("button", { class: "btn btn-primary", onclick: ask }, icon("sparkles"), "Ask"),
        h("button", { class: "btn", onclick: search }, icon("search"), "Find passages")), results)));
}

function discussionSection(itemId) {
  const list = h("div", { class: "stack", style: { gap: "8px" } });
  const body = h("textarea", { rows: 2, placeholder: "Ask a question or leave review feedback…", "aria-label": "New comment" });
  const load = guard(async () => {
    const items = await api.get(base() + "/comments?item_id=" + encodeURIComponent(itemId));
    list.replaceChildren(...(items.length ? items.map((c) => h("div", { class: "callout" + (c.resolved ? "" : " info"), style: { opacity: c.resolved ? ".65" : "1" } },
      h("span", { class: "avatar", style: { width: "24px", height: "24px", fontSize: "10px", flex: "none" } }, c.author.slice(0, 2)),
      h("div", { class: "grow" },
        h("div", { class: "row small" }, h("b", {}, c.author), h("span", { class: "muted" }, timeAgo(c.created)),
          c.resolved ? badge("Resolved" + (c.resolved_by ? " by " + c.resolved_by : ""), "green") : null,
          h("span", { class: "right row" },
            h("button", { class: "btn btn-sm btn-ghost", onclick: guard(async () => { await api.post(base() + "/comments/" + c.id + "/resolve"); load(); }) },
              c.resolved ? "Reopen" : "Resolve"),
            state.me.role === "admin" || state.me.username === c.author ? h("button", { class: "btn btn-sm btn-ghost btn-danger", "aria-label": "Delete comment",
              onclick: guard(async () => { await api.del(base() + "/comments/" + c.id); load(); }) }, icon("trash")) : null)),
        h("div", { style: { whiteSpace: "pre-wrap", marginTop: "2px" } }, c.body))))
      : [h("p", { class: "muted small" }, "No discussion yet.")]));
  });
  const post = guard(async () => {
    if (!body.value.trim()) return;
    await api.post(base() + "/comments", { item_id: itemId, body: body.value });
    body.value = ""; load();
  });
  body.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) post(); });
  load();
  return h("div", { class: "card card-body stack", style: { gap: "8px" } }, h("div", { class: "row" }, icon("chat"), h("b", {}, "Discussion")), list,
    body, h("div", { class: "row" }, h("button", { class: "btn btn-sm btn-primary", onclick: post }, "Post"), h("span", { class: "muted small" }, "Ctrl/⌘ + Enter to post")));
}

function historySection(itemId) {
  const box = h("div", { class: "muted small" }, "Loading…");
  const details = h("details", { class: "card card-body" }, h("summary", { style: { cursor: "pointer", fontWeight: 600 } }, "Change history"), box);
  details.addEventListener("toggle", guard(async () => {
    if (!details.open) return;
    box.replaceChildren(historyList(await api.get(base() + "/history?item_id=" + encodeURIComponent(itemId))));
  }), { once: true });
  return details;
}

// ---- requirements -------------------------------------------------------

const talkBadge = (n) => (n ? h("span", { class: "chip", title: n + " open discussion" + (n === 1 ? "" : "s") }, icon("chat"), String(n)) : null);

async function viewRequirements(body) {
  const [reqs, talk] = await Promise.all([api.get(base() + "/requirements"), api.get(base() + "/comments")]);
  mount(body, runPanel("requirements"));
  if (!reqs.length) {
    mount(body, h("div", { class: "card" }, empty({ iconName: "list", title: "No requirements yet",
      text: ctx.project.documents ? "Extract requirements from your BRDs, or add them manually." : "Add BRDs on the Sources tab first.",
      action: h("button", { class: "btn", onclick: () => reqDrawer(null) }, icon("plus"), "Add manually") })));
    return;
  }
  const selected = new Set();
  const tableBox = h("div");
  const bulk = h("div");
  const q = h("input", { type: "search", class: "search", placeholder: "Search requirements", "aria-label": "Search requirements", value: filters.reqQ });
  const type = select([["", "All types"], ...state.meta.requirement_types.map((t) => [t, t])], filters.reqType, { "aria-label": "Type" });
  const status = select([["", "All statuses"], ["draft", "Draft"], ["approved", "Approved"], ["excluded", "Excluded"]], filters.reqStatus, { "aria-label": "Status" });
  const flagged = h("input", { type: "checkbox", checked: filters.reqFlagged });

  const visible = () => reqs.filter((r) =>
    (!filters.reqQ || (r.key + " " + r.statement + " " + r.section).toLowerCase().includes(filters.reqQ.toLowerCase())) &&
    (!filters.reqType || r.type === filters.reqType) && (!filters.reqStatus || r.status === filters.reqStatus) &&
    (!filters.reqFlagged || r.issues.some((i) => i.severity !== "info")));

  const renderBulk = () => bulk.replaceChildren(...(selected.size ? [h("div", { class: "bulkbar" }, selected.size + " selected",
    h("span", { class: "grow" }),
    ...[["approved", "Approve"], ["draft", "Mark draft"], ["excluded", "Exclude"]].map(([s, l]) =>
      h("button", { class: "btn btn-sm", onclick: guard(async () => {
        await api.post(base() + "/requirements/bulk", { ids: [...selected], status: s }); toast(l + ": " + selected.size, "ok"); rerender();
      }) }, l)),
    h("button", { class: "btn btn-sm btn-ghost", onclick: () => { selected.clear(); renderTable(); renderBulk(); } }, "Clear"))] : []));

  const renderTable = () => {
    const rows = visible();
    const all = h("input", { type: "checkbox", "aria-label": "Select all", checked: rows.length > 0 && rows.every((r) => selected.has(r.id)),
      onchange: (e) => { rows.forEach((r) => (e.target.checked ? selected.add(r.id) : selected.delete(r.id))); renderTable(); renderBulk(); } });
    tableBox.replaceChildren(h("div", { class: "card table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, h("th", { class: "w-check" }, all), ...["ID", "Type", "Requirement", "Section", "Quality", "Status"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows.length ? rows.map((r) => h("tr", { class: "clickable" + (selected.has(r.id) ? " selected" : ""), onclick: () => reqDrawer(r) },
        h("td", { onclick: (e) => e.stopPropagation() }, h("input", { type: "checkbox", "aria-label": "Select " + r.key, checked: selected.has(r.id),
          onchange: (e) => { e.target.checked ? selected.add(r.id) : selected.delete(r.id); renderTable(); renderBulk(); } })),
        h("td", { class: "mono nowrap" }, rowOpener(r.key)),
        h("td", {}, badge(r.type, typeTone[r.type])),
        h("td", { class: "statement" }, r.statement,
          r.issues.length ? h("div", { class: "issues" }, r.issues.map((i) => h("span", { class: "chip", title: i.message }, issueIcon(i.severity), i.code))) : null),
        h("td", { class: "small muted" }, r.section),
        h("td", {}, scorePill(r.quality_score)),
        h("td", {}, h("div", { class: "row" }, badge(r.status, statusTone[r.status]), talkBadge(talk[r.id])))))
        : h("tr", {}, h("td", { colspan: "7", class: "muted", style: { textAlign: "center", padding: "28px" } }, "No requirements match the filters."))))));
  };
  q.addEventListener("input", () => { filters.reqQ = q.value; renderTable(); });
  type.addEventListener("change", () => { filters.reqType = type.value; renderTable(); });
  status.addEventListener("change", () => { filters.reqStatus = status.value; renderTable(); });
  flagged.addEventListener("change", () => { filters.reqFlagged = flagged.checked; renderTable(); });

  const approved = reqs.filter((r) => r.status === "approved").length;
  mount(body, h("div", { class: "toolbar" }, q, type, status, h("label", { class: "switch small" }, flagged, "Flagged only"),
    h("span", { class: "muted small right" }, approved + " of " + reqs.length + " approved"),
    h("button", { class: "btn", onclick: () => reqDrawer(null) }, icon("plus"), "Add requirement")), bulk, tableBox);
  renderTable();
}

function reqDrawer(r) {
  const isNew = !r;
  r = r || { statement: "", type: "functional", status: "draft", epic: "", section: "", actors: [], product_scope: [], compliance_tags: [], issues: [], quality_score: 100 };
  let status = r.status;
  const statement = h("textarea", { rows: 4, value: r.statement, placeholder: "The system shall …" });
  const type = select(state.meta.requirement_types.map((t) => [t, t]), r.type);
  const epic = h("input", { type: "text", value: r.epic || "" });
  const section = h("input", { type: "text", value: r.section || "" });
  const sourceId = h("input", { type: "text", value: r.source_id || "", placeholder: "e.g. BR-12" });
  const actors = tagsInput(r.actors, []);
  const scope = tagsInput(r.product_scope, state.meta.product_classes);
  const compliance = tagsInput(r.compliance_tags, state.meta.compliance_tags);
  const rationale = h("textarea", { rows: 2, value: r.rationale || "" });
  const notes = h("textarea", { rows: 2, value: r.notes || "", placeholder: "Reviewer notes, decisions, clarifications" });
  const save = guard(async () => {
    const payload = { statement: statement.value, type: type.value, status, epic: epic.value, section: section.value, source_id: sourceId.value,
      actors: actors.get(), product_scope: scope.get(), compliance_tags: compliance.get(), rationale: rationale.value, notes: notes.value };
    if (isNew) await api.post(base() + "/requirements", Object.assign(payload, { source_ref: "Manual" }));
    else await api.patch(base() + "/requirements/" + r.id, payload);
    close(); toast(isNew ? "Requirement added" : "Saved " + r.key, "ok"); rerender();
  });
  const remove = guard(async () => {
    if (!(await confirmBox("Delete " + r.key + "? Stories keep a dangling link until regenerated.", { danger: true, okLabel: "Delete" }))) return;
    await api.del(base() + "/requirements/" + r.id); close(); rerender();
  });
  const close = drawer({
    subtitle: isNew ? "New requirement" : r.key + " · " + (r.source_ref || ""), title: isNew ? "Add requirement" : "Review requirement",
    body: h("div", { class: "stack" },
      isNew ? null : h("div", { class: "card card-body" }, h("div", { class: "row", style: { marginBottom: "10px" } }, h("b", { class: "grow" }, "Quality (ISO/IEC/IEEE 29148)"), scorePill(r.quality_score)), issueList(r.issues)),
      field("Review status", segmented([["draft", "Draft"], ["approved", "Approved"], ["excluded", "Excluded"]], status, (v) => (status = v))),
      field("Statement", statement, "One verifiable requirement. Avoid vague terms such as 'fast' or 'user-friendly'."),
      h("div", { class: "form-grid" }, field("Type", type), field("BRD reference ID", sourceId), field("Epic", epic), field("Section / feature", section)),
      field("Actors", actors.el),
      field("Product classes", scope.el, "Leave empty if it applies to all products."),
      field("Compliance", compliance.el),
      field("Rationale", rationale), field("Notes", notes),
      isNew ? null : discussionSection(r.id),
      isNew ? null : historySection(r.id)),
    footer: [h("button", { class: "btn btn-primary", onclick: save }, "Save"),
      isNew ? null : h("button", { class: "btn", onclick: guard(() => refineRequirementDialog(r, close)) }, icon("sparkles"), "Refine with AI"),
      h("span", { class: "grow" }),
      isNew ? null : h("button", { class: "btn btn-danger", onclick: remove }, icon("trash"), "Delete")],
  });
}

async function refineRequirementDialog(r, closeDrawer) {
  toast("Asking AI to improve " + r.key + "…");
  const res = await api.post(base() + "/requirements/" + r.id + "/refine", {});
  const split = res.proposals.length > 1;
  modal({
    title: (split ? "Split " : "Refine ") + r.key, wide: true,
    description: "Generated by AI. " + (split ? "The first item replaces " + r.key + "; the others are added as new draft requirements. " : "") + "Nothing is saved until you apply it.",
    body: h("div", { class: "stack" },
      h("div", { class: "stack", style: { gap: "4px" } }, h("div", { class: "row" }, h("b", { class: "grow" }, "Current"), scorePill(res.before_score)),
        h("div", { class: "pre", style: { background: "var(--red-soft)" } }, r.statement)),
      res.proposals.map((p, i) => h("div", { class: "stack", style: { gap: "4px" } },
        h("div", { class: "row" }, h("b", { class: "grow" }, split ? (i === 0 ? "Replaces " + r.key : "New requirement " + i) : "Proposed"),
          badge(p.requirement.type, typeTone[p.requirement.type]), scorePill(p.quality_score)),
        h("div", { class: "pre", style: { background: "var(--green-soft)" } }, p.requirement.statement),
        p.requirement.notes ? h("div", { class: "callout warn small" }, icon("info"), p.requirement.notes) : null))),
    actions: [{ label: split ? "Apply split" : "Apply", kind: "btn-primary", onClick: async () => {
      const [first, ...rest] = res.proposals.map((p) => p.requirement);
      await api.patch(base() + "/requirements/" + r.id, { statement: first.statement, type: first.type, actors: first.actors, notes: first.notes });
      for (const extra of rest) await api.post(base() + "/requirements", extra);
      closeDrawer(); toast(split ? "Split into " + res.proposals.length + " requirements" : "Applied to " + r.key, "ok"); rerender();
    } }],
  });
}

// ---- stories ------------------------------------------------------------

async function viewStories(body) {
  const [stories, reqs, talk] = await Promise.all([api.get(base() + "/stories"), api.get(base() + "/requirements"), api.get(base() + "/comments")]);
  ctx.reqs = reqs;
  ctx.talk = talk;
  mount(body, runPanel("stories"));
  if (!stories.length) {
    mount(body, h("div", { class: "card" }, empty({ iconName: "map", title: "No stories yet",
      text: reqs.length ? "Generate stories from your reviewed requirements." : "Extract and review requirements first.",
      action: h("button", { class: "btn", onclick: () => storyDrawer(null) }, icon("plus"), "Add manually") })));
    return;
  }
  const box = h("div");
  const q = h("input", { type: "search", class: "search", placeholder: "Search stories", "aria-label": "Search stories", value: filters.storyQ });
  const status = select([["", "All statuses"], ["draft", "Draft"], ["approved", "Approved"], ["rejected", "Rejected"]], filters.storyStatus, { "aria-label": "Status" });
  const ready = select([["", "Any readiness"], ["ready", "Ready"], ["not-ready", "Needs work"]], filters.storyReady, { "aria-label": "Readiness" });
  const visible = () => stories.filter((s) =>
    (!filters.storyQ || (s.key + " " + s.title + " " + s.narrative + " " + s.epic + " " + s.feature).toLowerCase().includes(filters.storyQ.toLowerCase())) &&
    (!filters.storyStatus || s.status === filters.storyStatus) &&
    (!filters.storyReady || (filters.storyReady === "ready") === s.ready));
  const render = () => { const rows = visible(); box.replaceChildren(filters.storyView === "map" ? storyMap(rows) : storyTable(rows)); };
  q.addEventListener("input", () => { filters.storyQ = q.value; render(); });
  status.addEventListener("change", () => { filters.storyStatus = status.value; render(); });
  ready.addEventListener("change", () => { filters.storyReady = ready.value; render(); });
  const points = stories.filter((s) => s.status !== "rejected").reduce((a, s) => a + (s.story_points || 0), 0);
  mount(body, h("div", { class: "toolbar" },
    segmented([["map", "Story map"], ["list", "List"]], filters.storyView, (v) => { filters.storyView = v; render(); }),
    q, status, ready,
    h("span", { class: "muted small right" }, stories.filter((s) => s.ready).length + " ready · " + points + " points"),
    h("button", { class: "btn", onclick: guard(estimateDialog) }, icon("sparkles"), "Estimate with AI"),
    h("button", { class: "btn", onclick: () => storyDrawer(null) }, icon("plus"), "Add story")), box);
  render();
}

async function estimateDialog() {
  toast("Asking AI to estimate stories…");
  const { proposals } = await api.post(base() + "/stories/estimate", { scope: "unestimated" });
  if (!proposals.length) { toast("Every story already has an estimate", "ok"); return; }
  const picks = {};
  const rows = proposals.map((p) => {
    const sel = select(state.meta.points.map((n) => [n, String(n)]), p.points, { "aria-label": "Points for " + p.key, style: { width: "80px" } });
    picks[p.id] = sel;
    return h("tr", {}, h("td", { class: "mono nowrap" }, p.key), h("td", {}, h("b", {}, p.title), h("div", { class: "muted small" }, p.rationale)), h("td", {}, sel));
  });
  const total = proposals.reduce((a, p) => a + p.points, 0);
  modal({
    title: "Proposed estimates", wide: true,
    description: "Generated by AI using relative story points. Adjust any value; nothing is saved until you apply it.",
    body: h("div", { class: "stack" }, h("div", { class: "muted small" }, proposals.length + " unestimated stories · " + total + " points proposed"),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Story", "Summary", "Points"].map((x) => h("th", {}, x)))), h("tbody", {}, rows)))),
    actions: [{ label: "Apply estimates", kind: "btn-primary", onClick: async () => {
      const points = Object.fromEntries(Object.entries(picks).map(([id, el]) => [id, Number(el.value)]));
      const r = await api.post(base() + "/stories/points", { points });
      toast(r.updated + " stories estimated", "ok"); rerender();
    } }],
  });
}

function storyMap(rows) {
  if (!rows.length) return h("p", { class: "muted" }, "No stories match the filters.");
  // Backbone: one column per feature (user activity), labelled with its epic; stories stack by priority.
  const order = { High: 0, Medium: 1, Low: 2 };
  const cols = new Map();
  for (const s of rows) {
    const k = s.epic + "\u0000" + s.feature;
    if (!cols.has(k)) cols.set(k, { epic: s.epic, feature: s.feature, items: [] });
    cols.get(k).items.push(s);
  }
  // Drag a card onto another column to move the story to that epic/feature (saved and audited).
  const dropTarget = (c) => ({
    ondragover: (e) => { if (e.dataTransfer.types.includes("text/fabulist-story")) { e.preventDefault(); e.currentTarget.classList.add("drop"); } },
    ondragleave: (e) => e.currentTarget.classList.remove("drop"),
    ondrop: guard(async (e) => {
      e.preventDefault(); e.currentTarget.classList.remove("drop");
      const id = e.dataTransfer.getData("text/fabulist-story");
      const story = rows.find((x) => x.id === id);
      if (!story || (story.epic === c.epic && story.feature === c.feature)) return;
      await api.patch(base() + "/stories/" + id, { epic: c.epic, feature: c.feature });
      toast(story.key + " moved to " + c.feature, "ok"); rerender();
    }),
  });
  // Columns follow the BRD's section order (via requirements), so moving a card never reshuffles them.
  const sectionOrder = new Map();
  (ctx.reqs || []).forEach((r, i) => { const k = r.epic + "\u0000" + r.section; if (!sectionOrder.has(k)) sectionOrder.set(k, i); });
  const colOrder = (c) => (sectionOrder.has(c.epic + "\u0000" + c.feature) ? sectionOrder.get(c.epic + "\u0000" + c.feature) : 1e6);
  const ordered = [...cols.values()].sort((a, b) => colOrder(a) - colOrder(b) || a.epic.localeCompare(b.epic) || a.feature.localeCompare(b.feature));
  return h("div", { class: "map" }, ordered.map((c) =>
    h("div", Object.assign({ class: "map-col", "aria-label": c.epic + " / " + c.feature }, dropTarget(c)),
      h("div", { class: "map-col-head" }, h("div", { class: "grow" }, h("div", { class: "map-feature", style: { padding: 0 } }, c.epic), h("h3", {}, c.feature)), badge(String(c.items.length))),
      c.items.sort((a, b) => order[a.priority] - order[b.priority]).map((s) => h("div", { class: "story-card " + s.status, tabindex: "0", role: "button", onclick: () => storyDrawer(s),
        draggable: "true", ondragstart: (e) => { e.dataTransfer.setData("text/fabulist-story", s.id); e.dataTransfer.effectAllowed = "move"; },
        onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); storyDrawer(s); } } },
        h("div", { class: "row" }, h("span", { class: "dot " + s.priority, title: s.priority + " priority" }), h("span", { class: "k" }, s.key),
          h("span", { class: "right" }, s.status !== "draft" ? badge(s.status, statusTone[s.status]) : null)),
        h("div", { class: "t" }, s.title),
        h("div", { class: "row small muted" }, h("span", { class: "grow" }, s.persona),
          s.story_points ? badge(s.story_points + " pt") : null,
          talkBadge((ctx.talk || {})[s.id]),
          s.ready ? h("span", { title: "Meets Definition of Ready", style: { color: "var(--green)", display: "inline-flex" } }, icon("check")) : null,
          scorePill(s.quality_score)))))));
}

// Keyboard access to a clickable table row: the button's click (also fired by Enter/Space)
// bubbles to the row's onclick, so it needs no handler of its own unless the row has none.
function rowOpener(key, onclick) {
  return h("button", { type: "button", class: "row-open", "aria-label": "Open " + key, onclick }, key);
}

function storyTable(rows) {
  return h("div", { class: "card table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, ["ID", "Story", "Epic / feature", "Priority", "Pts", "Requirements", "Quality", "Status"].map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((s) => h("tr", { class: "clickable", onclick: () => storyDrawer(s) },
      h("td", { class: "mono nowrap" }, rowOpener(s.key)),
      h("td", {}, h("b", {}, s.title), h("div", { class: "small muted" }, s.persona)),
      h("td", { class: "small" }, s.epic, h("div", { class: "muted" }, s.feature)),
      h("td", { class: "nowrap" }, h("span", { class: "row" }, h("span", { class: "dot " + s.priority }), s.priority)),
      h("td", {}, s.story_points || "–"),
      h("td", { class: "small mono" }, s.requirement_ids.join(", ") || "–"),
      h("td", { class: "nowrap" }, h("span", { class: "row" }, scorePill(s.quality_score), s.ready ? badge("Ready", "green") : null)),
      h("td", {}, h("div", { class: "row" }, badge(s.status, statusTone[s.status]), talkBadge((ctx.talk || {})[s.id]))))))));
}

function storyDrawer(s) {
  const isNew = !s;
  s = s || { title: "", persona: "", narrative: "As a , I want , so that .", acceptance_criteria: [], priority: "Medium", story_points: null, status: "draft",
    epic: "", feature: "", requirement_ids: [], labels: [], product_scope: [], compliance_tags: [], variant_criteria: [], open_questions: [], issues: [], quality_score: 0 };
  let status = s.status;
  const reqs = ctx.reqs || [];
  const title = h("input", { type: "text", value: s.title });
  const persona = h("input", { type: "text", value: s.persona });
  const narrative = h("textarea", { rows: 3, value: s.narrative });
  const ac = linesInput(s.acceptance_criteria, 6, "Given … when … then …");
  const priority = select(state.meta.priorities.map((p) => [p, p]), s.priority);
  const points = select([["", "Not estimated"], ...state.meta.points.map((p) => [p, p + " points"])], s.story_points || "");
  const epic = h("input", { type: "text", value: s.epic });
  const feature = h("input", { type: "text", value: s.feature });
  const links = tagsInput(s.requirement_ids, reqs.map((r) => r.key));
  const labels = tagsInput(s.labels, []);
  const scope = tagsInput(s.product_scope, state.meta.product_classes);
  const compliance = tagsInput(s.compliance_tags, state.meta.compliance_tags);
  const variants = linesInput((s.variant_criteria || []).map((v) => v.scope + ": " + v.criteria), 3, "Product class: criteria");
  const questions = linesInput(s.open_questions, 2, "One question per line");
  const notes = h("textarea", { rows: 2, value: s.notes || "" });
  const linked = reqs.filter((r) => s.requirement_ids.includes(r.key));
  const save = guard(async () => {
    const payload = { title: title.value, persona: persona.value, narrative: narrative.value, acceptance_criteria: readLines(ac),
      priority: priority.value, story_points: points.value ? Number(points.value) : null, status, epic: epic.value, feature: feature.value,
      requirement_ids: links.get(), labels: labels.get(), product_scope: scope.get(), compliance_tags: compliance.get(),
      variant_criteria: readLines(variants).map((line) => { const i = line.indexOf(":"); return i > 0 ? { scope: line.slice(0, i).trim(), criteria: line.slice(i + 1).trim() } : { scope: "", criteria: line }; }),
      open_questions: readLines(questions), notes: notes.value };
    if (isNew) await api.post(base() + "/stories", payload);
    else await api.patch(base() + "/stories/" + s.id, payload);
    close(); toast(isNew ? "Story added" : "Saved " + s.key, "ok"); rerender();
  });
  const remove = guard(async () => {
    if (!(await confirmBox("Delete " + s.key + "?", { danger: true, okLabel: "Delete" }))) return;
    await api.del(base() + "/stories/" + s.id); close(); rerender();
  });
  const close = drawer({
    subtitle: isNew ? "New story" : s.key + " · " + s.epic + " › " + s.feature, title: isNew ? "Add story" : s.title,
    body: h("div", { class: "stack" },
      (s.external || []).length ? h("div", { class: "row wrap small" }, h("span", { class: "muted" }, "In tracker:"),
        s.external.map((x) => h("a", { class: "chip", href: x.url, target: "_blank", rel: "noopener noreferrer" }, icon("link"), x.key))) : null,
      isNew ? null : h("div", { class: "card card-body" },
        h("div", { class: "row", style: { marginBottom: "10px" } }, h("b", { class: "grow" }, "Quality (INVEST, Definition of Ready)"),
          s.ready ? badge("Ready", "green") : badge("Needs work", "amber"), scorePill(s.quality_score)), issueList(s.issues)),
      field("Review status", segmented([["draft", "Draft"], ["approved", "Approved"], ["rejected", "Rejected"]], status, (v) => (status = v))),
      field("Title", title),
      h("div", { class: "form-grid" }, field("Persona", persona), field("Priority", priority), field("Epic", epic), field("Feature", feature)),
      field("Narrative", narrative, "As a <role>, I want <goal>, so that <benefit>."),
      field("Acceptance criteria", ac, "One per line, in Given / When / Then form."),
      field("Product variants", variants, "One per line: 'Product class: criteria'. Variants avoid duplicate stories per product type."),
      h("div", { class: "form-grid" }, field("Estimate", points), field("Requirement links", links.el, null, "")),
      linked.length ? h("div", { class: "callout small" }, h("div", { class: "stack", style: { gap: "4px" } },
        linked.map((r) => h("div", {}, h("b", { class: "mono" }, r.key + " "), r.statement)))) : null,
      field("Labels", labels.el), field("Product classes", scope.el), field("Compliance", compliance.el),
      field("Open questions", questions, "Stories with open questions don't meet the Definition of Ready."), field("Notes", notes),
      isNew ? null : discussionSection(s.id),
      isNew ? null : historySection(s.id)),
    footer: [h("button", { class: "btn btn-primary", onclick: save }, "Save"),
      isNew ? null : h("button", { class: "btn", onclick: guard(() => refineDialog(s, close)) }, icon("sparkles"), "Refine with AI"),
      h("span", { class: "grow" }),
      isNew ? null : h("button", { class: "btn btn-danger", onclick: remove }, icon("trash"), "Delete")],
  });
}

const REFINE_FIELDS = [["title", "Title"], ["persona", "Persona"], ["narrative", "Narrative"], ["acceptance_criteria", "Acceptance criteria"],
  ["variant_criteria", "Product variants"], ["open_questions", "Open questions"], ["requirement_ids", "Requirement links"]];

function asText(key, v) {
  if (key === "variant_criteria") return (v || []).map((x) => x.scope + ": " + x.criteria).join("\n");
  return Array.isArray(v) ? v.join("\n") : String(v || "");
}

async function refineDialog(story, closeDrawer) {
  toast("Asking AI for an improved version…");
  const r = await api.post(base() + "/stories/" + story.id + "/refine", {});
  const changed = REFINE_FIELDS.filter(([k]) => asText(k, story[k]) !== asText(k, r.story[k]));
  const col = (title, text, tone) => h("div", { class: "stack", style: { gap: "4px" } }, h("div", { class: "muted small" }, title),
    h("div", { class: "pre", style: { background: tone, maxHeight: "220px" } }, text || "∅"));
  modal({
    title: "Proposed refinement for " + story.key, wide: true,
    description: "Generated by AI. Nothing is saved until you apply it.",
    body: h("div", { class: "stack" },
      h("div", { class: "row" }, h("span", { class: "muted small" }, "Quality"), scorePill(r.before_score), "→", scorePill(r.quality_score),
        r.ready ? badge("Ready", "green") : badge("Needs work", "amber")),
      changed.length ? changed.map(([k, label]) => h("div", { class: "stack", style: { gap: "6px" } }, h("b", {}, label),
        h("div", { class: "grid grid-2" }, col("Current", asText(k, story[k]), "var(--red-soft)"), col("Proposed", asText(k, r.story[k]), "var(--green-soft)"))))
        : h("div", { class: "callout" }, "No changes proposed."),
      r.issues.length ? h("div", { class: "stack", style: { gap: "6px" } }, h("b", {}, "Remaining issues"), issueList(r.issues)) : null),
    actions: changed.length ? [{ label: "Apply changes", kind: "btn-primary", onClick: async () => {
      await api.patch(base() + "/stories/" + story.id, r.story);
      closeDrawer(); toast("Applied refinement to " + story.key, "ok"); rerender();
    } }] : [],
  });
}

// ---- traceability -------------------------------------------------------

async function viewTrace(body) {
  const [t, stories, reqs] = await Promise.all([api.get(base() + "/traceability"), api.get(base() + "/stories"), api.get(base() + "/requirements")]);
  ctx.reqs = reqs;
  if (!t.total) {
    mount(body, h("div", { class: "card" }, empty({ iconName: "link", title: "Nothing to trace yet", text: "Extract requirements and generate stories to build the traceability matrix." })));
    return;
  }
  const byId = Object.fromEntries(stories.map((s) => [s.id, s]));
  const reqByKey = Object.fromEntries(reqs.map((r) => [r.key, r]));
  const box = h("div");
  const gapsOnly = h("input", { type: "checkbox", checked: filters.traceGaps });
  const render = () => {
    const rows = t.rows.filter((r) => !filters.traceGaps || !r.stories.length);
    box.replaceChildren(h("div", { class: "card table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ["Requirement", "Type", "Statement", "Covered by", "Coverage"].map((x) => h("th", {}, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", { class: "mono nowrap" }, rowOpener(r.key, () => reqDrawer(reqByKey[r.key]))), h("td", {}, badge(r.type, typeTone[r.type])),
        h("td", { class: "statement" }, r.statement, h("div", { class: "small muted" }, r.section)),
        h("td", {}, h("div", { class: "row wrap" }, r.stories.map((s) => h("button", { class: "chip", style: { cursor: "pointer" }, title: s.title, onclick: () => storyDrawer(byId[s.id]) }, s.key)))),
        h("td", {}, r.stories.length ? badge("Covered", "green") : badge("Gap", "red"))))))));
  };
  gapsOnly.addEventListener("change", () => { filters.traceGaps = gapsOnly.checked; render(); });
  mount(body, 
    h("div", { class: "stats", style: { marginBottom: "16px" } },
      h("div", { class: "stat" }, h("div", { class: "label" }, "Requirement coverage"), h("div", { class: "value" }, t.coverage + "%"), meter(t.coverage)),
      h("div", { class: "stat" }, h("div", { class: "label" }, "Covered requirements"), h("div", { class: "value" }, t.covered + " / " + t.total)),
      h("div", { class: "stat" }, h("div", { class: "label" }, "Gaps"), h("div", { class: "value", style: { color: t.total - t.covered ? "var(--red)" : "" } }, t.total - t.covered), h("div", { class: "foot" }, "Requirements without stories")),
      h("div", { class: "stat" }, h("div", { class: "label" }, "Orphan stories"), h("div", { class: "value" }, t.orphans.length), h("div", { class: "foot" }, "Stories without requirements"))),
    t.products.length ? h("div", { class: "card", style: { marginBottom: "16px" } },
      h("div", { class: "card-head" }, h("div", { class: "grow" }, h("h3", {}, "Product-class coverage"),
        h("div", { class: "muted small" }, "Do the stories linked to each requirement address the product classes it names?"))),
      h("div", { class: "table-wrap", style: { marginTop: "8px" } }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Product class", "Requirements", "Addressed", "Gaps"].map((x) => h("th", {}, x)))),
        h("tbody", {}, t.products.map((p) => {
          const pct = Math.round((100 * p.covered) / p.requirements);
          return h("tr", {},
            h("td", {}, h("b", {}, p.product_class)),
            h("td", {}, String(p.requirements)),
            h("td", { style: { minWidth: "140px" } }, p.covered + " of " + p.requirements + " (" + pct + "%)", meter(pct)),
            h("td", {}, p.gaps.length ? h("div", { class: "row wrap" }, p.gaps.map((g) =>
              h("button", { class: "chip", style: { cursor: "pointer" }, title: g.statement + " (" + g.reason + ")", "aria-label": g.key + ": " + g.reason,
                onclick: () => reqDrawer(reqByKey[g.key]) }, issueIcon("warning"), g.key))) : badge("Covered", "green")));
        }))))) : null,
    t.orphans.length ? h("div", { class: "callout warn", style: { marginBottom: "16px" } }, icon("alert"),
      h("div", {}, h("b", {}, "Stories not linked to any requirement: "),
        h("span", { class: "row wrap", style: { display: "inline-flex" } }, t.orphans.map((o) => h("button", { class: "chip", style: { cursor: "pointer" }, onclick: () => storyDrawer(byId[o.id]) }, o.key + " " + o.title))))) : null,
    h("div", { class: "toolbar" }, h("label", { class: "switch small" }, gapsOnly, "Show gaps only"),
      h("a", { class: "btn btn-sm right", href: base() + "/export?format=rtm-csv&scope=all" }, icon("download"), "Download matrix")),
    box);
  render();
}

// ---- export -------------------------------------------------------------

async function viewExport(body) {
  const p = ctx.project;
  const grid = h("div", { class: "grid grid-3" });
  const note = h("div", { class: "muted small" });
  const render = () => {
    const approvedOnly = filters.exportScope === "approved";
    const n = approvedOnly ? p.stories_approved : p.stories;
    note.textContent = approvedOnly
      ? p.stories_approved + " approved stories and " + p.requirements_approved + " approved requirements will be exported."
      : p.stories + " stories (excluding rejected) and " + p.requirements + " requirements (excluding excluded) will be exported.";
    grid.replaceChildren(...state.meta.formats.map((f) => h("div", { class: "card card-body stack", style: { gap: "8px" } },
      h("div", { class: "row" }, icon(f.ext === "xlsx" ? "chart" : "file"), h("h3", { class: "grow" }, f.label), badge("." + f.ext)),
      h("p", { class: "muted small grow" }, f.description),
      h("div", { class: "row" }, h("button", { class: "btn btn-sm", disabled: !n && !(approvedOnly ? p.requirements_approved : p.requirements),
        onclick: () => download(base() + "/export?format=" + encodeURIComponent(f.id) + "&scope=" + (approvedOnly ? "approved" : "all")) }, icon("download"), "Download"),
        f.id === "html" ? h("a", { class: "btn btn-sm btn-ghost", target: "_blank", rel: "noopener",
          href: base() + "/export?format=html&inline=true&scope=" + (approvedOnly ? "approved" : "all") }, "Preview") : null))));
  };
  mount(body, 
    h("div", { class: "card card-body", style: { marginBottom: "16px" } },
      h("div", { class: "row wrap" }, h("div", { class: "grow" }, h("h3", {}, "What to export"), note),
        segmented([["approved", "Approved only"], ["all", "Everything in review"]], filters.exportScope, (v) => { filters.exportScope = v; render(); }))),
    grid,
    await baselinesCard(),
    await pushCard());
  render();
}

async function baselinesCard() {
  const list = await api.get(base() + "/baselines");
  const create = guard(async () => {
    const name = h("input", { type: "text", placeholder: "e.g. Release 1 sign-off", maxlength: "120" });
    const note = h("textarea", { rows: 2, placeholder: "Approvers, meeting, scope notes (optional)" });
    let scope = filters.exportScope === "approved" ? "approved" : "all";
    modal({
      title: "Create baseline", description: "Freezes a numbered, read-only copy of the backlog that can be exported and compared later.",
      body: h("div", { class: "stack" }, field("Name", name), field("Contents", segmented([["approved", "Approved items only"], ["all", "Approved + draft"]], scope, (v) => (scope = v))),
        field("Note", note)),
      actions: [{ label: "Create baseline", kind: "btn-primary", onClick: async () => {
        requireValue(name, "Name is required");
        const r = await api.post(base() + "/baselines", { name: name.value, note: note.value, scope });
        toast("Baseline v" + r.version + " created", "ok"); rerender();
      } }],
    });
  });
  const compare = guard(async (b) => {
    const c = await api.get(base() + "/baselines/" + b.id + "/compare");
    const section = (title, d) => h("div", { class: "stack", style: { gap: "6px" } }, h("b", {}, title),
      !d.added.length && !d.removed.length && !d.changed.length ? h("div", { class: "muted small" }, "No changes.") : null,
      d.added.map((x) => h("div", { class: "row small" }, badge("Added", "green"), h("span", { class: "mono" }, x.key), x.title)),
      d.removed.map((x) => h("div", { class: "row small" }, badge("Removed", "red"), h("span", { class: "mono" }, x.key), x.title)),
      d.changed.map((x) => h("div", { class: "row small" }, badge("Changed", "amber"), h("span", { class: "mono" }, x.key), x.title,
        h("span", { class: "muted" }, "(" + x.fields.map((f) => f.replace(/_/g, " ")).join(", ") + ")"))));
    modal({ title: "Changes since " + c.from, description: "Compared with the " + c.to + ".", wide: true,
      body: h("div", { class: "stack" }, section("Stories", c.stories), section("Requirements", c.requirements)), actions: [] });
  });
  return h("div", { class: "card", style: { marginTop: "12px" } },
    h("div", { class: "card-head" }, h("div", { class: "grow" }, h("h3", {}, "Baselines"),
      h("div", { class: "muted small" }, "Versioned sign-off snapshots of the backlog for audit and release planning.")),
      h("button", { class: "btn btn-sm", onclick: create }, icon("plus"), "Create baseline")),
    list.length ? h("div", { class: "table-wrap", style: { marginTop: "8px" } }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ["Version", "Name", "Contents", "Created", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, list.map((b) => {
        const fmtSel = select(state.meta.formats.map((f) => [f.id, f.label]), "xlsx", { "aria-label": "Format", style: { width: "auto" } });
        return h("tr", {},
          h("td", {}, badge("v" + b.version, "accent")),
          h("td", {}, h("b", {}, b.name), b.note ? h("div", { class: "muted small" }, b.note) : null),
          h("td", { class: "small" }, b.requirements + " requirements · " + b.stories + " stories", h("div", { class: "muted" }, b.scope === "approved" ? "Approved only" : "Approved + draft")),
          h("td", { class: "small" }, timeAgo(b.created), h("div", { class: "muted" }, "by " + (b.actor || "–"))),
          h("td", { class: "nowrap", style: { textAlign: "right" } },
            h("span", { class: "row", style: { justifyContent: "flex-end" } }, fmtSel,
              h("button", { class: "btn btn-sm", onclick: () => download(base() + "/baselines/" + b.id + "/export?format=" + encodeURIComponent(fmtSel.value)) }, icon("download"), "Export"),
              h("button", { class: "btn btn-sm", onclick: () => compare(b) }, "Compare with current"))));
      }))))
      : h("div", { class: "card-body muted small" }, "No baselines yet. Create one when the backlog is signed off."));
}

async function pushCard() {
  const { integrations } = await api.get("/api/integrations");
  const card = h("div", { class: "card", style: { marginTop: "16px" } },
    h("div", { class: "card-head" }, h("div", { class: "grow" }, h("h3", {}, "Push to issue tracker"),
      h("div", { class: "muted small" }, "Jira, Azure DevOps or GitHub issues, a Confluence page, or a webhook. Pushing again updates the same items."))));
  if (!integrations.length) {
    card.append(h("div", { class: "card-body muted small" }, "No tracker connected. An administrator can add Jira or Azure DevOps under ",
      h("a", { href: "#/admin/integrations" }, "Admin → Integrations"), "."));
    return card;
  }
  const conn = select(integrations.map((i) => [i.id, i.name + " · " + i.kind_label]), integrations[0].id, { "aria-label": "Integration" });
  const target = h("input", { type: "text", value: integrations[0].target, placeholder: "Target project", style: { maxWidth: "220px" } });
  conn.addEventListener("change", () => { target.value = (integrations.find((i) => i.id === conn.value) || {}).target || ""; });
  const result = h("div", { style: { flexBasis: "100%" } });
  const push = guard(async () => {
    const approvedOnly = filters.exportScope === "approved";
    if (!(await confirmBox("Push " + (approvedOnly ? "approved" : "all non-rejected") + " stories to " + conn.selectedOptions[0].textContent + " (" + target.value + ")?", { okLabel: "Push" }))) return;
    btn.disabled = true; btn.textContent = "Pushing…";
    try {
      const r = await api.post(base() + "/push", { integration_id: conn.value, target: target.value, scope: approvedOnly ? "approved" : "all" });
      result.replaceChildren(h("div", { class: "callout " + (r.failed.length ? "warn" : "ok") }, h("div", { class: "stack", style: { gap: "4px" } },
        h("b", {}, r.created + " created, " + r.updated + " updated" + (r.failed.length ? ", " + r.failed.length + " failed" : "")),
        r.failed.map((f) => h("div", { class: "small" }, f.item + ": " + f.error)))));
    } finally { btn.disabled = false; btn.textContent = "Push stories"; }
  });
  const btn = h("button", { class: "btn btn-primary", onclick: push }, "Push stories");
  card.append(h("div", { class: "runbox" }, conn, target, h("span", { class: "muted small grow" }, "Uses the export scope selected above."), btn, result));
  return card;
}
