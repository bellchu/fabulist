// Project workspace: Overview, Sources, Requirements, Stories, Traceability, Export.
import {
  h, mount, icon, api, guard, toast, modal, confirmBox, drawer, badge, statusTone, typeTone, scorePill, meter, issueIcon,
  issueList, copyText, empty, timeAgo, fmt, duration, debounce, field, requireValue, select, linesInput, readLines, tagsInput, segmented, download, historyList, accessPicker,
} from "./core.js";
import { state, setCrumbs, setTitle } from "./main.js";

const TABS = [["overview", "Overview"], ["sources", "Sources"], ["requirements", "Requirements"],
  ["stories", "Stories"], ["trace", "Traceability"], ["audit", "Audit"], ["export", "Export"]];
const TYPE_COLORS = { business: "#5867e8", functional: "#0176d3", "non-functional": "#dd7a01", constraint: "#ba0517", rule: "#06a59a" };

// Filters survive re-renders within the session.
const filters = { reqAssignee: "", storyAssignee: "", reqQ: "", reqType: "", reqTag: "", reqStatus: "", reqFlagged: false, reqSort: "", reqDir: 1, storySort: "", storyDir: 1, storyQ: "", storyStatus: "", storyTag: "", storyView: "map", storyReady: "", traceGaps: false, exportScope: "all" };
let ctx = null;
let pollTimer = null;

// From the home page's "Waiting for you": open the lists already filtered to what is assigned to the signed-in person.
export function showMyWork() { filters.reqAssignee = "me"; filters.storyAssignee = "me"; }

const rerender = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
// Reviewers (the viewer role) read and take part in discussions, and an archived project is frozen: the server refuses
// every other change, so the UI does not offer it.
const frozen = () => Boolean(ctx && ctx.project && ctx.project.archived);
const readOnly = () => state.me.role === "viewer" || frozen();
const writer = (el) => (readOnly() ? null : el);
const base = () => "/api/projects/" + ctx.pid;

export async function renderProject(content, pid, tab, key = "") {
  clearTimeout(pollTimer);
  const project = await api.get("/api/projects/" + pid);
  if (!content.isConnected) return;  // the user already navigated away: don't touch the new page's breadcrumb
  ctx = { pid, project, tab, key };
  setCrumbs({ href: "#/", label: "Projects" }, project.name);
  setTitle((TABS.find(([k]) => k === tab) || TABS[0])[1], project.name);
  const body = h("div");
  mount(content, header(project), archivedBanner(project), stepper(project), tabBar(project, tab), body);
  const view = { overview: viewOverview, sources: viewSources, requirements: viewRequirements, stories: viewStories, trace: viewTrace, audit: viewAudit, export: viewExport }[tab] || viewOverview;
  await view(body);
  revealCurrentStep(content);  // after the view: measuring a half-built (short) page resets its scroll
  schedulePoll();
}

// ---- chrome -------------------------------------------------------------

function header(p) {
  const kv = (label, value, cls = "") => h("div", { class: cls }, h("div", { class: "object-label" }, label), h("div", {}, value));
  return h("div", { class: "page-head" },
    h("span", { class: "object-icon project" }, icon("folder")),
    h("div", { class: "grow" }, h("div", { class: "object-label" }, "Project"), h("h1", {}, p.name),
      p.visibility === "members" ? h("div", { class: "small" }, badge("Private", "amber"), " ", h("span", { class: "muted" }, "Only chosen people and administrators can see this project.")) : null),
    h("div", { class: "row" }, writer(h("button", { class: "btn", onclick: guard(editProjectDialog) }, "Edit")),
      h("a", { class: "btn", href: "#/p/" + p.id + "/export" }, "Export")),
    h("div", { class: "row wrap", style: { flexBasis: "100%", gap: "32px", paddingLeft: "44px" } },
      kv("Target platform", state.meta.platforms[p.platform] || p.platform),
      // On phones the counts live in the tabs and the stepper, so the header keeps only what they don't show.
      kv("BRDs", String(p.documents), "hide-sm"), kv("Requirements", String(p.requirements), "hide-sm"), kv("Stories", String(p.stories), "hide-sm"),
      kv("Coverage", p.coverage + "%"), kv("Description", p.description || "–", "hide-sm")));
}

function archivedBanner(p) {
  if (!p.archived) return null;
  return h("div", { class: "callout warn", role: "status", style: { marginBottom: "12px" } },
    h("div", { class: "grow" }, h("b", {}, "This project is archived and read-only."), " Its audit trail is kept. Restore it to make changes."),
    h("button", { class: "btn btn-sm", onclick: guard(async () => { await api.post(base() + "/restore", {}); toast("Project restored", "ok"); rerender(); }) }, "Restore"),
    state.me.role === "admin" ? h("button", { class: "btn btn-sm btn-danger", onclick: guard(async () => {
      if (!(await confirmBox("Permanently delete '" + p.name + "', including its audit trail? This cannot be undone.", { danger: true, okLabel: "Delete permanently" }))) return;
      await api.del(base()); location.hash = "#/"; toast("Project deleted");
    }) }, "Delete permanently") : null);
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
    h("div", { class: "path-detail hide-sm" }, steps.map(([, t, s]) => h("div", {}, t, h("b", {}, s)))));
}

function tabBar(p, active) {
  const counts = { sources: p.documents, requirements: p.requirements, stories: p.stories };
  // Page links, not ARIA tabs: there are no tab panels or arrow-key switching.
  return h("nav", { class: "tabs", "aria-label": "Project sections" }, TABS.map(([id, label]) =>
    h("a", { class: "tab" + (id === active ? " active" : ""), href: "#/p/" + p.id + "/" + id, "aria-current": id === active ? "page" : null },
      label, counts[id] !== undefined ? h("span", { class: "count" }, counts[id]) : null)));
}

async function editProjectDialog() {
  const p = ctx.project;
  const [access, people] = await Promise.all([api.get(base() + "/access"), state.me.username === "anonymous" ? [] : api.get("/api/people")]);
  const picker = people.length ? accessPicker(people, access.visibility, access.members) : null;
  const name = h("input", { type: "text", value: p.name, maxlength: "120" });
  const platform = select(Object.entries(state.meta.platforms), p.platform);
  const desc = h("textarea", { rows: 3, value: p.description });
  const close = modal({
    title: "Project settings",
    body: h("div", { class: "stack" }, field("Name", name), field("Target platform", platform), field("Description", desc),
      picker ? field("Who can see this project", picker.el) : null,
      h("div", { class: "callout small" }, h("div", { class: "grow" }, h("b", {}, "Take your data with you. "),
        "A full archive holds the documents, requirements, stories, baselines, comments and audit history, and can be imported into any Fabulist."),
        h("button", { class: "btn btn-sm", onclick: () => download(base() + "/archive.json") }, icon("download"), "Download archive")),
      p.archived ? null : h("div", { class: "callout warn small" }, h("div", { class: "grow" }, "Archiving hides the project and makes it read-only. Its audit trail is kept and an administrator can delete it later."),
        h("button", { class: "btn btn-sm", onclick: guard(async () => {
          if (!(await confirmBox("Archive '" + p.name + "'? It becomes read-only and leaves the project list.", { okLabel: "Archive project" }))) return;
          await api.post(base() + "/archive", {}); close(); location.hash = "#/"; toast("Project archived");
        }) }, "Archive project"))),
    actions: [{ label: "Save", kind: "btn-primary", onClick: async () => {
      await api.patch(base(), { name: name.value, platform: platform.value, description: desc.value });
      if (picker) {
        const chosen = picker.value();
        const same = chosen.visibility === access.visibility && [...chosen.members].sort().join() === [...access.members].sort().join();
        if (!same) await api.put(base() + "/access", chosen);  // only when the choice changed
      }
      toast("Project updated", "ok"); rerender();
    } }],
  });
}

// ---- runs ---------------------------------------------------------------

// "Reading section 12 of 46: …" / "Writing stories 3 of 9: …": how far along the run is and, at the pace so far, how long is left.
// "section 12 of 46" means 11 are done. The start time is the server's, so a reload knows the pace too.
function runPace(run) {
  const m = /(\d+) of (\d+)/.exec(run.progress || "");
  if (!m || !Number(m[2])) return null;
  const done = Math.max(0, Number(m[1]) - 1), total = Number(m[2]);
  const elapsed = (Date.now() - new Date(run.started || run.created)) / 1000;
  const left = done >= 2 && elapsed > 5 ? Math.round((elapsed / done) * (total - done)) : null;
  return { done, total, pct: Math.round((100 * done) / total),
    text: done + " of " + total + " done" + (left == null ? "" : " · about " + (left >= 90 ? Math.round(left / 60) + " min" : left + " s") + " left") };
}

// Show the pace in the elements runPanel drew (called when it renders and on every poll).
function applyPace(run) {
  const pace = runPace(run), bar = document.getElementById("run-bar"), line = document.getElementById("run-line"), text = document.getElementById("run-pace");
  if (!bar || !line || !text) return;
  bar.hidden = !pace;
  line.hidden = Boolean(pace);  // the endless animation is only for when nothing is known yet
  if (!pace) { text.textContent = ""; return; }
  bar.setAttribute("aria-valuenow", String(pace.pct));
  bar.firstChild.style.width = pace.pct + "%";
  text.textContent = pace.text;
}

const LARGE_RUN = 20;  // from this many AI requests a run asks for confirmation, with the estimate

function runPanel(stage) {
  const run = ctx.project.runs[stage];
  const running = run && run.status === "running";
  const queued = run && run.status === "queued";
  const scope = select([["all", "Approved + draft requirements"], ["approved", "Approved requirements only"]], "all", { "aria-label": "Requirement scope" });
  const isReq = stage === "requirements";
  // Which BRDs a run reads: the ones not read yet (added to what exists, in batches that fit the limit), or all of them starting over.
  const fresh = isReq ? ctx.project.documents_new : 0;
  const whichOptions = [];
  if (fresh > 0) whichOptions.push(["new", "New BRDs only (" + fresh + "): adds to your requirements"]);
  if (ctx.project.requirements > 0 || fresh === 0) whichOptions.push(["reset", "All BRDs: start over (replaces requirements)"]);
  // Which requirements a story run writes for: the ones no story covers yet (added to the stories that exist), or all of them starting over.
  const uncovered = isReq ? 0 : ctx.project.requirements_uncovered;
  const storyOptions = [];
  if (uncovered > 0) storyOptions.push(["uncovered", "Requirements without stories (" + uncovered + "): adds stories"]);
  if (ctx.project.stories > 0 || uncovered === 0) storyOptions.push(["reset", "All requirements: start over (replaces stories)"]);
  const which = readOnly() ? null
    : isReq && ctx.project.documents > 0 ? select(whichOptions, whichOptions[0][0], { "aria-label": "BRDs to read" })
    : !isReq && ctx.project.requirements > 0 ? select(storyOptions, storyOptions[0][0], { "aria-label": "Requirements to write stories for" }) : null;
  const mode = () => (which ? which.value : "all");
  const verb = () => (running ? "Running…" : queued ? "Queued…" : isReq ? (mode() === "new" ? "Extract new BRDs" : ctx.project.requirements ? "Re-extract" : "Extract")
    : (mode() === "uncovered" ? "Generate for new requirements" : ctx.project.stories ? "Regenerate" : "Generate"));
  const startLabel = h("span", {}, verb());
  if (which) which.addEventListener("change", () => { startLabel.textContent = verb(); });
  // Say why a run cannot start instead of failing after the click.
  const blocked = isReq ? (ctx.project.documents === 0 ? "Add a BRD on the Sources tab first." : "")
    : (ctx.project.requirements === 0 ? "Extract or add requirements first." : "");
  const start = guard(async () => {
    // Say what the run will ask of the AI provider before it does: a big project means many requests, hours and real cost.
    const est = await api.get(base() + "/runs/estimate?stage=" + stage + "&scope=" + (isReq ? "all" : scope.value) + (isReq ? "&documents=" : "&stories_for=") + mode());
    if (est.over_limit) {
      modal({ title: "Too large for one run",
        body: h("p", {}, est.too_big ? est.too_big : "This run needs " + est.requests + " AI requests, above the limit of " + est.limit + ". "
          + (isReq && mode() === "all" ? "Raise “Max sections per run” in Admin → Generation settings."
            : "Raise “Max sections per run” in Admin → Generation settings" + (isReq ? ", or split the documents." : ", or generate from approved requirements only (choose it next to the button)."))), actions: [] });
      return;
    }
    const addingStories = !isReq && mode() !== "all";
    const adding = isReq && mode() === "new", batched = isReq && mode() !== "all";
    const total = adding ? est.new_documents : est.batch_documents + est.remaining_documents;
    const storyBatch = addingStories ? "This run writes stories for " + est.batch_requirements + " of the " + (est.batch_requirements + est.remaining_requirements) + " requirements" + (mode() === "uncovered" ? " without a story" : "") + " ("
      + est.requests + " feature-area group" + (est.requests === 1 ? "" : "s") + ")" + (est.remaining_requirements ? "; " + est.remaining_requirements + " more will be left for the next run" : "")
      + ". " + (mode() === "uncovered" ? "Your existing stories are not changed. " : "") : "";
    const batch = addingStories ? storyBatch : batched ? "This run reads " + est.batch_documents + " of the " + total + (adding ? " new" : "") + " BRD" + (total === 1 ? "" : "s") + " ("
      + est.requests + " sections)" + (est.remaining_documents ? "; " + est.remaining_documents + " more will be left for the next run" : "") + ". "
      + (adding ? "Your existing requirements are not changed. " : "") : "";
    const big = est.requests >= LARGE_RUN;
    const cost = big ? "This run will make about " + est.requests + " AI requests"
      + (est.seconds ? " (roughly " + (est.seconds >= 90 ? Math.round(est.seconds / 60) + " minutes" : est.seconds + " seconds") + (est.tokens ? " and " + fmt(est.tokens) + " tokens" : "") + ")" : "")
      + ". " + est.basis + " " : "";
    const hasData = !adding && !(addingStories && mode() === "uncovered") && (isReq ? ctx.project.requirements > 0 || ctx.project.stories > 0 : ctx.project.stories > 0);
    const replace = hasData ? (isReq ? "Re-extracting replaces all requirements and the current backlog, including your edits."
      : "Regenerating replaces the current stories, including your edits.") : "";
    if ((big || hasData) && !(await confirmBox((batch + cost + replace).trim() + (big && !hasData ? " Continue?" : ""),
      { okLabel: hasData ? (isReq ? "Re-extract" : "Regenerate") : (isReq ? "Extract" : "Generate"), danger: hasData }))) return;
    await api.post(base() + "/runs", { stage, scope: scope.value, documents: isReq ? mode() : "all", stories_for: isReq ? "all" : mode() });
    toast(isReq ? "Extraction started" : "Story generation started");
    rerender();
  });
  let statusLine = null;
  const pace = running ? runPace(run) : null;
  if (running) {
    // A hung AI provider shows up as a run that stops reporting progress: say so, instead of an endless "Running".
    const idle = Math.floor((Date.now() - new Date(run.updated || run.created)) / 60000);
    statusLine = h("div", { class: "row small", style: { flexBasis: "100%" } }, badge("Running", "blue"),
      h("span", { class: "muted grow", id: "run-progress" }, run.progress || "Starting…",
        idle >= 3 ? h("span", { style: { color: "var(--amber)", marginLeft: "8px" } }, "No progress for " + idle + " min. The AI provider may not be responding; you can cancel this run.") : null),
      h("span", { class: "small", id: "run-pace" }, pace ? pace.text : ""),
      writer(h("button", { class: "btn btn-sm btn-ghost btn-danger", onclick: guard(async () => {
        await api.post(base() + "/runs/" + run.id + "/cancel"); toast("Cancelling after the current AI request…");
      }) }, "Cancel run")));
  } else if (queued) {
    // The server queues a run when every slot is busy; it starts by itself, in order.
    statusLine = h("div", { class: "row small", style: { flexBasis: "100%" } }, badge("Queued", "amber"),
      h("span", { class: "muted grow" }, "Waiting for a free slot: position " + (run.queue_position || 1) + " of " + (run.queue_length || 1) + ". It starts by itself; you can leave this page."),
      writer(h("button", { class: "btn btn-sm btn-ghost btn-danger", onclick: guard(async () => {
        await api.post(base() + "/runs/" + run.id + "/cancel"); toast("Removed from the queue"); rerender();
      }) }, "Remove from queue")));
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
          : "Builds epics, features and INVEST stories with Gherkin criteria, linked to requirement IDs."),
        blocked && !readOnly() ? h("div", { class: "small", id: "run-blocked-" + stage, style: { color: "var(--amber)", marginTop: "2px" } }, blocked) : null),
      isReq || readOnly() ? null : scope, which,
      writer(h("button", { class: "btn btn-primary", disabled: running || queued || !!blocked, title: blocked || null, "aria-describedby": blocked ? "run-blocked-" + stage : null, onclick: start }, icon("sparkles"), startLabel)),
      running ? h("div", { class: "progress-line", id: "run-line", hidden: Boolean(pace) }) : null,
      running ? h("div", { class: "progress-bar", id: "run-bar", role: "progressbar", "aria-label": "Run progress", "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-valuenow": String(pace ? pace.pct : 0), hidden: !pace }, h("span", { style: { width: (pace ? pace.pct : 0) + "%" } })) : null,
      statusLine));
}

function schedulePoll() {
  const waiting = (r) => r && (r.status === "running" || r.status === "queued");
  const before = Object.values(ctx.project.runs || {}).filter(waiting).map((r) => r.status).sort().join();
  if (!before) return;
  const pid = ctx.pid;
  pollTimer = setTimeout(guard(async () => {
    if (!ctx || ctx.pid !== pid) return;
    const runs = await api.get(base() + "/runs?limit=2");
    const active = runs.find(waiting);
    if (active && active.status === "queued" && before.includes("queued")) { schedulePoll(); return; }  // still in line
    if (active && !before.includes(active.status)) { rerender(); return; }  // it just left the queue: show the running state
    if (active) {
      const el = document.getElementById("run-progress");
      if (el) el.textContent = active.progress || "Working…";
      applyPace(active);
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
    h("div", { class: "grid grid-2", style: { alignItems: "start" } },
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Requirement mix")),
        h("div", { class: "card-body" }, total ? [
          h("div", { class: "typebar" }, Object.entries(p.requirement_types).filter(([, n]) => n).map(([t, n]) =>
            h("span", { title: t + ": " + n, style: { width: (100 * n) / total + "%", background: TYPE_COLORS[t] } }))),
          h("div", { class: "legend" }, Object.entries(p.requirement_types).filter(([, n]) => n).map(([t, n]) => h("span", {}, h("i", { style: { background: TYPE_COLORS[t] } }), t + " " + n)))]
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
      h("div", { class: "card-body" }, historyList(changes, { showItem: true, link: itemLink }))));
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
      writer(h("button", { class: "btn btn-sm", onclick: repoDialog }, icon("link"), "Connect repository"))),
    repoList.length ? repoList.map((r) => h("div", { class: "provider-row" },
      h("div", { class: "grow", style: { minWidth: "260px" } },
        h("div", { class: "row" }, h("b", { class: "mono" }, r.url), r.branch ? badge(r.branch) : null),
        h("div", { class: "small muted" }, "Path: " + r.path_glob + " · " + r.documents + " documents" + (r.has_token ? " · token " + r.key_hint : "")),
        r.last_error ? h("div", { class: "small", style: { color: "var(--red)" } }, r.last_error)
          : h("div", { class: "small muted" }, r.last_sync ? "Synced " + timeAgo(r.last_sync) + " at " + r.last_commit.slice(0, 8) : "Never synced")),
      writer(h("button", { class: "btn btn-sm", onclick: guard(() => syncRepo(r.id)) }, icon("refresh"), "Sync now")),
      writer(h("button", { class: "btn btn-sm btn-ghost btn-danger", "aria-label": "Disconnect " + r.url, onclick: guard(async () => {
        if (!(await confirmBox("Disconnect " + r.url + "? Its " + r.documents + " synced documents are removed; existing requirements are kept.", { danger: true, okLabel: "Disconnect" }))) return;
        await api.del(base() + "/repos/" + r.id); rerender();
      }) }, icon("trash")))))
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
      if (r.index_error) toast(r.filename + " is stored but not searchable yet: " + r.index_error + " Rebuild the search index in Admin > Generation settings once the provider works.", "error");
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
  const q = h("input", { type: "search", style: { flex: "1 1 220px" }, placeholder: "Ask a question, e.g. What is the return window for med-surg supplies?", "aria-label": "Question about the BRDs" });
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
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") (readOnly() ? search : ask)(); });

  // The documents are drawn a page at a time: a project can hold a thousand BRDs.
  let docLimit = PAGE;
  const docBox = h("div");
  const renderDocs = () => {
    const shown = docs.slice(0, docLimit);
    const row = (d) => h("tr", {},
      h("td", {}, h("div", { class: "row" }, icon("file"), h("b", {}, d.filename), d.extracted === null ? badge("New", "blue") : null)),
      h("td", { class: "hide-sm" }, d.source === "repo" ? badge("Git", "violet") : badge("Upload")),
      h("td", { class: "muted" }, fmt(Math.round(d.chars / 6)) + " words"),
      h("td", { class: "hide-sm" }, d.chunks), h("td", { class: "muted small" }, timeAgo(d.created)),
      h("td", { class: "nowrap", style: { textAlign: "right" } },
        h("button", { class: "btn btn-sm btn-ghost", onclick: guard(async () => {
          const full = await api.get(base() + "/documents/" + d.id);
          modal({ title: full.filename, wide: true, body: h("div", { class: "pre" }, full.text), actions: [] });
        }) }, "View"),
        writer(h("button", { class: "btn btn-sm btn-ghost btn-danger", onclick: guard(async () => {
          if (!(await confirmBox("Remove " + d.filename + "? Existing requirements are kept.", { danger: true, okLabel: "Remove" }))) return;
          await api.del(base() + "/documents/" + d.id); rerender();
        }) }, "Remove"))));
    docBox.replaceChildren(h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, [["Name", ""], ["Source", "hide-sm"], ["Size", ""], ["Indexed passages", "hide-sm"], ["Added", ""], ["", ""]].map(([x, c]) => h("th", { class: c }, x)))),
      h("tbody", {}, shown.map(row), pagerRow(docs.length, shown.length, 6, (n) => { docLimit = n; renderDocs(); })))));
  };
  renderDocs();

  mount(body, 
    writer(h("div", { class: "grid grid-2", style: { marginBottom: "16px" } },
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Upload BRDs")), h("div", { class: "card-body" }, drop, fileInput)),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Paste text")),
        h("div", { class: "card-body stack" }, field("Document name", pasteName), pasteText,
          h("div", {}, h("button", { class: "btn", onclick: guard(async () => {
            requireValue(pasteText, "Paste some text first");
            const added = await api.post(base() + "/documents/text", { name: pasteName.value, text: pasteText.value });
            if (added.index_error) toast(added.filename + " is stored but not searchable yet: " + added.index_error + " Rebuild the search index in Admin > Generation settings once the provider works.", "error");
            toast("Document added", "ok"); rerender();
          }) }, icon("plus"), "Add document")))))),
    reposCard(repoList),
    h("div", { class: "card", style: { marginBottom: "16px" } },
      h("div", { class: "card-head" }, h("h3", { class: "grow" }, "Documents"), h("span", { class: "muted small" }, docs.length + " total")),
      docs.length ? docBox
        : empty({ iconName: "file", title: "No BRDs yet", text: "Upload or paste at least one business requirements document." })),
    h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Ask the BRDs"),
      h("span", { class: "muted small" }, "Answers come only from retrieved BRD passages, with numbered citations")),
      h("div", { class: "card-body" }, h("div", { class: "row wrap" }, q, state.me.role === "viewer" ? null : h("button", { class: "btn btn-primary", onclick: ask }, icon("sparkles"), "Ask"),  // asking is allowed on an archived project (a read), not for viewers (AI budget)
        
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
            frozen() ? null : h("button", { class: "btn btn-sm btn-ghost", onclick: guard(async () => { await api.post(base() + "/comments/" + c.id + "/resolve"); load(); }) },
              c.resolved ? "Reopen" : "Resolve"),
            !readOnly() && (state.me.role === "admin" || state.me.username === c.author) ? h("button", { class: "btn btn-sm btn-ghost btn-danger", "aria-label": "Delete comment",
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
    frozen() ? h("div", { class: "muted small" }, "This project is archived: the discussion is closed.")
      : [body, h("div", { class: "row" }, h("button", { class: "btn btn-sm btn-primary", onclick: post }, "Post"), h("span", { class: "muted small" }, "Ctrl/⌘ + Enter to post"))]);
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

const PAGE = 300;  // rows drawn at a time in the long tables; filters, sorting and select-all still work on every row

// The "Showing 300 of 13,800 · Show more" line under a long table. `more()` is called with the number of rows now drawn.
function pagerRow(total, shown, cols, more) {
  if (total <= shown) return null;
  return h("tr", { class: "pager" }, h("td", { colspan: String(cols), style: { textAlign: "center", padding: "14px" } },
    h("span", { class: "muted small" }, "Showing " + shown.toLocaleString() + " of " + total.toLocaleString() + "  "),
    h("button", { type: "button", class: "btn btn-sm", onclick: () => more(shown + PAGE) }, "Show " + Math.min(PAGE, total - shown).toLocaleString() + " more"),
    " ", h("button", { type: "button", class: "btn btn-sm btn-ghost", onclick: () => more(total) }, "Show all")));
}

const BULK_CONFIRM_AT = 10;  // a bulk change this large is confirmed first: it is one click for hundreds of reviews

// A column header that sorts: ascending, descending, then back to the original order. `prefix` names the filters entry that remembers it.
function sortHeader(label, cls, key, prefix, redraw) {
  if (!key) return h("th", { class: cls }, label);
  const on = filters[prefix + "Sort"] === key, dir = filters[prefix + "Dir"];
  return h("th", { class: cls, "aria-sort": on ? (dir > 0 ? "ascending" : "descending") : "none" },
    h("button", { type: "button", class: "th-sort", "data-sort": prefix + ":" + key, onclick: () => {
      if (!on) { filters[prefix + "Sort"] = key; filters[prefix + "Dir"] = 1; } else if (dir > 0) filters[prefix + "Dir"] = -1; else filters[prefix + "Sort"] = "";
      redraw();
      // redrawing replaced this button: put focus back so the keyboard user can keep sorting
      document.querySelector('[data-sort="' + prefix + ":" + key + '"]')?.focus();
    } }, label, on ? (dir > 0 ? " ▲" : " ▼") : null));
}

const STATUS_RANK = { draft: 0, approved: 1, excluded: 2 };
const PRIORITY_RANK = { Critical: 0, High: 1, Medium: 2, Low: 3 };
const STORY_SORTS = {
  key: (a, b) => a.key.localeCompare(b.key, undefined, { numeric: true }),
  title: (a, b) => a.title.localeCompare(b.title),
  epic: (a, b) => (a.epic + a.feature).localeCompare(b.epic + b.feature, undefined, { numeric: true }),
  priority: (a, b) => (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9),
  points: (a, b) => (a.story_points || 0) - (b.story_points || 0),
  quality: (a, b) => a.quality_score - b.quality_score,
  status: (a, b) => a.status.localeCompare(b.status),
};
const SORTS = {
  key: (a, b) => a.key.localeCompare(b.key, undefined, { numeric: true }),
  type: (a, b) => a.type.localeCompare(b.type) || a.key.localeCompare(b.key, undefined, { numeric: true }),
  section: (a, b) => (a.section || "").localeCompare(b.section || "", undefined, { numeric: true }),
  quality: (a, b) => a.quality_score - b.quality_score,
  status: (a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status],
};

// Requirements that already exist in a spreadsheet: pick a .csv or .xlsx (a column called Requirement or Statement is needed).
function importFileButton(kind) {  // kind: "requirement" or "story"
  const plural = kind === "story" ? "stories" : "requirements";
  const file = h("input", { type: "file", accept: ".csv,.xlsx", class: "hidden", "aria-label": (kind === "story" ? "Stories" : "Requirements") + " file (CSV or Excel)" });
  file.addEventListener("change", guard(async () => {
    const chosen = file.files[0];
    if (!chosen) return;
    const fd = new FormData(); fd.append("file", chosen);
    const r = await api.upload(base() + "/" + plural + "/import-file", fd);
    toast("Imported " + r.created + " " + (r.created === 1 ? kind : plural) + (r.duplicates_skipped ? " (" + r.duplicates_skipped + " already there, skipped)" : "") + (r.statuses_not_recognised ? ". " + r.statuses_not_recognised + " had a status from another tool and came in as drafts" : ""), "ok");
    rerender();
  }));
  const hint = kind === "story"
    ? "Add stories from a CSV or Excel file (a Jira export works): a column called Title or Summary is needed; Narrative or Description, Epic, Feature, Persona, Priority, Story points, Acceptance criteria, Requirement IDs and Status are used when present"
    : "Add requirements from a CSV or Excel file: a column called Requirement or Statement is needed; Type, Epic, Section, Source ID, Actors and Status are used when present";
  return h("span", {}, file, h("button", { class: "btn", title: hint, onclick: () => { file.value = ""; file.click(); } }, icon("upload"), "Import file"));
}
const importRequirementsButton = () => importFileButton("requirement");

async function viewRequirements(body) {
  const [reqs, talk] = await Promise.all([api.get(base() + "/requirements"), api.get(base() + "/comments"), loadPeople()]);
  mount(body, runPanel("requirements"));
  if (!reqs.length) {
    mount(body, h("div", { class: "card" }, empty({ iconName: "list", title: "No requirements yet",
      text: ctx.project.documents ? "Extract requirements from your BRDs, or add them manually." : "Add BRDs on the Sources tab first.",
      action: writer(h("span", { class: "row" }, importRequirementsButton(), h("button", { class: "btn", onclick: () => reqDrawer(null) }, icon("plus"), "Add manually"))) })));
    return;
  }
  const selected = new Set();
  const tableBox = h("div");
  const bulk = h("div", { class: "bulk-sticky" });
  const q = h("input", { type: "search", class: "search", placeholder: "Search requirements", "aria-label": "Search requirements", value: filters.reqQ });
  const type = select([["", "All types"], ...state.meta.requirement_types.map((t) => [t, t])], filters.reqType, { "aria-label": "Type" });
  const status = select([["", "All statuses"], ["draft", "Draft"], ["approved", "Approved"], ["excluded", "Excluded"]], filters.reqStatus, { "aria-label": "Status" });
  const tagNames = [...new Set(reqs.flatMap((r) => r.compliance_tags || []))].sort();
  if (filters.reqTag && !tagNames.includes(filters.reqTag)) filters.reqTag = "";  // the tag was removed everywhere since the filter was set
  const tagFilter = tagNames.length ? select([["", "All compliance"], ...tagNames.map((t) => [t, t])], filters.reqTag, { "aria-label": "Compliance tag" }) : null;
  const flagged = h("input", { type: "checkbox", checked: filters.reqFlagged });

  const visible = () => reqs.filter((r) =>
    (!filters.reqQ || (r.key + " " + r.statement + " " + r.section).toLowerCase().includes(filters.reqQ.toLowerCase())) &&
    (!filters.reqType || r.type === filters.reqType) && (!filters.reqStatus || r.status === filters.reqStatus) && matchesAssignee(r, filters.reqAssignee) &&
    (!filters.reqTag || (r.compliance_tags || []).includes(filters.reqTag)) && (!filters.reqFlagged || r.issues.some((i) => i.severity !== "info")));
  const sorted = () => {
    const rows = visible(), by = SORTS[filters.reqSort];
    return by ? rows.sort((a, b) => filters.reqDir * by(a, b)) : rows;
  };

  const renderBulk = () => bulk.replaceChildren(...(selected.size ? [h("div", { class: "bulkbar" }, selected.size + " selected",
    h("span", { class: "grow" }),
    ...[["approved", "Approve"], ["draft", "Mark draft"], ["excluded", "Exclude"]].map(([s, l]) =>
      h("button", { class: "btn btn-sm", onclick: guard(async () => {
        if (selected.size >= BULK_CONFIRM_AT && !(await confirmBox(l + " " + selected.size + " requirements?", { okLabel: l, danger: s === "excluded" }))) return;
        await api.post(base() + "/requirements/bulk", { ids: [...selected], status: s }); toast(l + ": " + selected.size, "ok"); selected.clear(); await refresh();
      }) }, l)),
    bulkAssign("requirement", selected, refresh),
    bulkSet("requirement", selected, refresh),
    bulkTag("requirement", selected, refresh),
    selected.size >= 2 ? bulkMerge(reqs, selected, refresh) : null,
    h("button", { class: "btn btn-sm btn-ghost", onclick: () => { selected.clear(); renderTable(); renderBulk(); } }, "Clear"))] : []));

  let limit = PAGE;
  const renderTable = () => {
    const rows = sorted();  // every row matching the filters: select-all, Next/Prev and the counts use these
    const drawn = rows.slice(0, limit);
    const all = h("input", { type: "checkbox", "aria-label": rows.length > drawn.length ? "Select all " + rows.length.toLocaleString() + " matching" : "Select all", checked: rows.length > 0 && rows.every((r) => selected.has(r.id)),
      onchange: (e) => {
        rows.forEach((r) => (e.target.checked ? selected.add(r.id) : selected.delete(r.id)));
        // touch the rows, don't rebuild them: rebuilding thousands of rows makes every click lag
        tableBox.querySelectorAll("tbody tr.clickable").forEach((tr) => { tr.classList.toggle("selected", e.target.checked); tr.querySelector("input").checked = e.target.checked; });
        renderBulk();
      } });
    tableBox.replaceChildren(h("div", { class: "card table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, writer(h("th", { class: "w-check" }, all)), ...[["ID", "", "key"], ["Type", "hide-sm", "type"], ["Requirement", ""], ["Section", "hide-sm", "section"], ["Quality", "hide-sm", "quality"], ["Status", "", "status"]].map(([x, c, k]) => sortHeader(x, c, k, "req", () => restart())))),
      h("tbody", {}, rows.length ? drawn.map((r) => h("tr", { class: "clickable" + (selected.has(r.id) ? " selected" : ""), onclick: () => reqDrawer(r, nav) },
        writer(h("td", { onclick: (e) => e.stopPropagation() }, h("input", { type: "checkbox", "aria-label": "Select " + r.key, checked: selected.has(r.id),
          onchange: (e) => {
            e.target.checked ? selected.add(r.id) : selected.delete(r.id);
            e.target.closest("tr").classList.toggle("selected", e.target.checked);
            all.checked = rows.every((x) => selected.has(x.id));
            renderBulk();
          } }))),
        h("td", { class: "mono nowrap" }, rowOpener(r.key)),
        h("td", { class: "hide-sm" }, badge(r.type, typeTone[r.type])),
        h("td", { class: "statement" }, r.statement,
          r.issues.length ? h("div", { class: "issues" }, r.issues.map((i) => h("span", { class: "chip", title: i.message }, issueIcon(i.severity), i.code))) : null),
        h("td", { class: "small muted hide-sm" }, r.section),
        h("td", { class: "hide-sm" }, scorePill(r.quality_score)),
        h("td", {}, h("div", { class: "row", style: { flexWrap: "wrap" } }, badge(r.status, statusTone[r.status]), talkBadge(talk[r.id])), r.assignee ? h("div", { class: "small muted" }, "→ " + r.assignee) : null)))
        : h("tr", {}, h("td", { colspan: "7", class: "muted", style: { textAlign: "center", padding: "28px" } }, "No requirements match the filters.")),
        pagerRow(rows.length, drawn.length, 7, (n) => { const before = drawn.length; limit = n; renderTable(); tableBox.querySelectorAll("tbody tr.clickable .row-open")[before]?.focus(); })))));
  };
  // Reload the data but redraw only the table, so a reviewer keeps their place in a long list.
  const count = h("span", { class: "muted small right" });
  const refresh = async () => {
    reqs.splice(0, reqs.length, ...(await api.get(base() + "/requirements")));
    count.textContent = reqs.filter((r) => r.status === "approved").length + " of " + reqs.length + " approved";
    renderTable(); renderBulk();
  };
  const nav = { rows: sorted, refresh, find: (id) => reqs.find((r) => r.id === id) };
  const restart = () => { limit = PAGE; renderTable(); };  // a new filter or order starts again from the top
  q.addEventListener("input", debounce(() => { filters.reqQ = q.value; restart(); }));
  type.addEventListener("change", () => { filters.reqType = type.value; restart(); });
  status.addEventListener("change", () => { filters.reqStatus = status.value; restart(); });
  if (tagFilter) tagFilter.addEventListener("change", () => { filters.reqTag = tagFilter.value; restart(); });
  flagged.addEventListener("change", () => { filters.reqFlagged = flagged.checked; restart(); });

  count.textContent = reqs.filter((r) => r.status === "approved").length + " of " + reqs.length + " approved";
  const who = assigneeFilter(reqs, filters.reqAssignee, (v) => { filters.reqAssignee = v; restart(); });
  mount(body, h("div", { class: "toolbar" }, q, type, status, tagFilter, who, h("label", { class: "switch small" }, flagged, "Flagged only"), count,
    writer(importRequirementsButton()),
    writer(h("button", { class: "btn", onclick: () => reqDrawer(null) }, icon("plus"), "Add requirement"))), bulk, tableBox);
  renderTable();
  openLinkedItem(reqs, (r) => reqDrawer(r, nav));
}

// An address ending in an item's key (#/p/<id>/requirements/REQ-002) opens that item. The address then goes back to the tab, so closing the drawer
// or saving (which redraws the page) does not open it again.
// Who work can be assigned to: the named accounts (a picker), or, on installations without accounts, free text. Loaded once per session.
let peopleCache = null;
async function loadPeople() {
  if (!peopleCache) { try { peopleCache = await api.get("/api/people"); } catch (_) { peopleCache = []; } }
  return peopleCache;
}

function assigneeControl(current) {
  const people = peopleCache || [];
  if (!people.length) {
    const box = h("input", { type: "text", value: current || "", maxlength: "64", placeholder: "A name or a team (optional)", "aria-label": "Assignee" });
    return { el: box, value: () => box.value.trim() };
  }
  const names = people.map((p) => p.username);
  const options = [["", "Unassigned"], ...(current && !names.includes(current) ? [[current, current + " (no longer an account)"]] : []), ...names.map((n) => [n, n])];
  const box = select(options, current || "", { "aria-label": "Assignee" });
  return { el: box, value: () => box.value };
}

// "Assigned to" filter options for a list: everyone, me, nobody, then each person who has something.
function assigneeFilter(items, current, onChange) {
  const names = [...new Set([...(peopleCache || []).map((p) => p.username), ...items.map((x) => x.assignee).filter(Boolean)])].sort();
  if (!names.length) return null;
  const options = [["", "All assignees"], ...(state.me.named ? [["me", "Assigned to me"]] : []), ["none", "Unassigned"], ...names.map((n) => [n, n])];
  const box = select(options, current, { "aria-label": "Assignee filter" });
  box.addEventListener("change", () => onChange(box.value));
  return box;
}

// "Assign…" in a bulk bar: one person (or nobody) for everything selected.
function bulkAssign(kind, selected, refresh) {
  const plural = kind === "story" ? "stories" : "requirements";
  const who = assigneeControl("");
  return h("button", { class: "btn btn-sm", onclick: () => modal({
    title: "Assign " + selected.size + " " + (selected.size === 1 ? kind : plural), description: "Everything selected goes to the same person. Choose Unassigned to clear it.",
    body: field("Assignee", who.el),
    actions: [{ label: "Assign", kind: "btn-primary", onClick: async () => {
      const r = await api.post(base() + "/" + plural + "/assign", { ids: [...selected], assignee: who.value() });
      toast(r.updated + " " + (r.updated === 1 ? kind : plural) + " updated", "ok"); selected.clear(); await refresh();
    } }],
  }) }, "Assign…");
}

// "Set…" in a bulk bar: one value of a grouping or classification field for everything selected.
function bulkSet(kind, selected, refresh) {
  const plural = kind === "story" ? "stories" : "requirements";
  const fields = kind === "story" ? [["epic", "Epic"], ["feature", "Feature"], ["priority", "Priority"]] : [["epic", "Epic"], ["section", "Section / feature"], ["type", "Type"]];
  const which = select(fields, fields[0][0], { "aria-label": "Field" });
  const holder = h("div");
  let value = h("input", { type: "text", maxlength: "200", "aria-label": "Value", placeholder: "New value" });
  const draw = () => {
    const f = which.value;
    value = f === "type" ? select(state.meta.requirement_types.map((t) => [t, t]), "functional", { "aria-label": "Value" })
      : f === "priority" ? select(state.meta.priorities.map((t) => [t, t]), "Medium", { "aria-label": "Value" })
      : h("input", { type: "text", maxlength: "200", "aria-label": "Value", placeholder: "New value" });
    holder.replaceChildren(value);
  };
  which.addEventListener("change", draw);
  draw();
  return h("button", { class: "btn btn-sm", onclick: () => modal({
    title: "Change " + selected.size + " " + (selected.size === 1 ? kind : plural), description: "Everything selected gets the same value; each change is recorded in the audit trail.",
    body: h("div", { class: "stack" }, field("Field", which), field("Value", holder)),
    actions: [{ label: "Apply", kind: "btn-primary", onClick: async () => {
      const r = await api.post(base() + "/" + plural + "/set", { ids: [...selected], field: which.value, value: value.value });
      toast(r.updated + " " + (r.updated === 1 ? kind : plural) + " updated", "ok"); selected.clear(); await refresh();
    } }],
  }) }, "Set…");
}

// "Tag…" in the bulk bars: add a compliance tag to everything selected (requirements or stories), or take it off.
function bulkTag(kind, selected, refresh) {
  const plural = kind === "story" ? "stories" : "requirements";
  const tag = h("input", { type: "text", maxlength: "80", "aria-label": "Compliance tag", placeholder: "e.g. SOX" });
  const mode = select([["add", "Add to all"], ["remove", "Remove from all"]], "add", { "aria-label": "Action" });
  return h("button", { class: "btn btn-sm", title: "Add or remove a compliance tag", onclick: () => modal({
    title: "Tag " + selected.size + " " + (selected.size === 1 ? kind : plural), description: "Each change is recorded in the audit trail.",
    body: h("div", { class: "stack" }, field("Action", mode), field("Compliance tag", tag)),
    actions: [{ label: "Apply", kind: "btn-primary", onClick: async () => {
      const r = await api.post(base() + "/" + plural + "/tag", { ids: [...selected], tag: tag.value, remove: mode.value === "remove" });
      toast(r.updated + " " + (r.updated === 1 ? kind : plural) + " updated", "ok"); selected.clear(); await refresh();
    } }],
  }) }, "Tag…");
}

// "Merge…" in the requirements bulk bar (two or more selected): pick the one to keep; the others are retired into it.
function bulkMerge(reqs, selected, refresh) {
  const chosen = reqs.filter((r) => selected.has(r.id));
  let keep = chosen[0].id;
  const options = chosen.map((r, i) => h("label", { class: "row small", style: { alignItems: "flex-start", gap: "8px" } },
    h("input", { type: "radio", name: "keep", value: r.id, checked: i === 0, onchange: () => { keep = r.id; } }),
    h("span", {}, h("b", {}, r.key), " " + r.statement.slice(0, 140))));
  return h("button", { class: "btn btn-sm", title: "Merge duplicates into one requirement", onclick: () => modal({
    title: "Merge " + chosen.length + " requirements", description: "The one you keep gains where each was stated, the actors, product scope and compliance tags of the others; the others are excluded with a pointer to it, and stories that used them point at the one you keep.",
    body: h("fieldset", { class: "plain-fieldset stack" }, h("legend", { class: "small muted" }, "Keep"), ...options),
    actions: [{ label: "Merge", kind: "btn-primary", onClick: async () => {
      const r = await api.post(base() + "/requirements/merge", { keep, merge: chosen.map((x) => x.id).filter((id) => id !== keep) });
      toast("Merged " + r.merged.join(", ") + " into " + r.kept + (r.stories_relinked ? " (" + r.stories_relinked + " stories re-linked)" : ""), "ok"); selected.clear(); await refresh();
    } }],
  }) }, "Merge…");
}

const matchesAssignee = (item, want) => !want || (want === "none" ? !item.assignee : (want === "me" ? state.me.username : want) === item.assignee);

// A small "copy link" button for a drawer: the address that opens this very item.
const copyLinkButton = (tab, key) => h("button", { class: "btn btn-ghost icon-btn", "aria-label": "Copy link to " + key, title: "Copy a link to " + key,
  onclick: guard(async () => { await copyText(location.origin + "/#/p/" + ctx.pid + "/" + tab + "/" + key); toast("Link to " + key + " copied", "ok"); }) }, icon("link"));

const itemLink = (e) => (e.item_type === "requirement" ? "#/p/" + ctx.pid + "/requirements/" + e.item_key
  : e.item_type === "story" ? "#/p/" + ctx.pid + "/stories/" + e.item_key : null);

function openLinkedItem(items, open) {
  const key = ctx.key;
  if (!key) return;
  ctx.key = "";
  history.replaceState(null, "", "#/p/" + ctx.pid + "/" + ctx.tab);
  const item = items.find((x) => x.key === key);
  if (item) open(item);
}

function reqDrawer(r, nav) {
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
  const assignee = assigneeControl(r.assignee);
  const rows = nav && !isNew ? nav.rows() : [];
  const at = rows.findIndex((x) => x.id === r.id);
  const [prevId, nextId] = [rows[at - 1]?.id, rows[at + 1]?.id];  // neighbours as the list is now, before a save reorders or filters it
  let dirty = false;
  const save = (approveAndNext) => guard(async () => {
    if (approveAndNext) status = "approved";
    const payload = { statement: statement.value, type: type.value, status, epic: epic.value, section: section.value, source_id: sourceId.value,
      actors: actors.get(), product_scope: scope.get(), compliance_tags: compliance.get(), rationale: rationale.value, notes: notes.value, assignee: assignee.value() };
    if (isNew) await api.post(base() + "/requirements", Object.assign(payload, { source_ref: "Manual" }));
    else await api.patch(base() + "/requirements/" + r.id, Object.assign(payload, { rev: r.rev }));  // refused if someone saved first
    close(); toast(isNew ? "Requirement added" : "Saved " + r.key, "ok");
    if (!nav || isNew) return rerender();
    await nav.refresh();
    if (approveAndNext) go(nextId);
  });
  const go = (id) => { const n = id && nav.find(id); if (n) reqDrawer(n, nav); };
  const step = (id) => guard(async () => {
    if (dirty && !(await confirmBox("Discard your unsaved changes to " + r.key + "?", { okLabel: "Discard" }))) return;
    close(); go(id);
  });
  const keys = reviewKeys({ save: readOnly() ? () => {} : save(false), approve: readOnly() ? () => {} : nextId ? save(true) : save(false), prev: () => prevId && step(prevId)(), next: () => nextId && step(nextId)() });
  const remove = guard(async () => {
    if (!(await confirmBox("Delete " + r.key + "? Stories keep a dangling link until regenerated.", { danger: true, okLabel: "Delete" }))) return;
    await api.del(base() + "/requirements/" + r.id); close(); nav ? await nav.refresh() : rerender();
  });
  const close = drawer({
    subtitle: isNew ? "New requirement" : r.key + " · " + (r.source_ref || ""), title: isNew ? "Add requirement" : "Review requirement",
    headExtra: isNew ? null : copyLinkButton("requirements", r.key),
    body: h("div", { class: "stack", oninput: () => { dirty = true; } },
      isNew ? null : h("div", { class: "card card-body" }, h("div", { class: "row", style: { marginBottom: "10px" } }, h("b", { class: "grow" }, "Quality (ISO/IEC/IEEE 29148)"), scorePill(r.quality_score)), issueList(r.issues)),
      h("fieldset", { class: "plain-fieldset stack", disabled: readOnly() },  // a disabled fieldset makes every field below read-only
      field("Review status", segmented([["draft", "Draft"], ["approved", "Approved"], ["excluded", "Excluded"]], status, (v) => { status = v; dirty = true; })),
      field("Statement", statement, "One verifiable requirement. Avoid vague terms such as 'fast' or 'user-friendly'."),
      h("div", { class: "form-grid" }, field("Type", type), field("BRD reference ID", sourceId), field("Epic", epic), field("Section / feature", section), field("Assignee", assignee.el)),
      field("Actors", actors.el),
      field("Product classes", scope.el, "Leave empty if it applies to all products."),
      field("Compliance", compliance.el),
      field("Rationale", rationale), field("Notes", notes)),
      isNew ? null : discussionSection(r.id),
      isNew ? null : historySection(r.id)),
    onkeydown: keys,
    footer: [writer(h("button", { class: "btn btn-primary", onclick: save(false) }, "Save")),
      nav && !isNew && nextId ? writer(h("button", { class: "btn", onclick: save(true), title: "Mark approved, save, and open the next requirement (Ctrl+Shift+Enter)" }, "Approve & next")) : null,
      nav && !isNew ? h("button", { class: "btn btn-ghost", "aria-label": "Previous requirement", title: "Alt+Left", disabled: !prevId, onclick: step(prevId) }, "‹ Prev") : null,
      nav && !isNew ? h("button", { class: "btn btn-ghost", "aria-label": "Next requirement", title: "Alt+Right", disabled: !nextId, onclick: step(nextId) }, "Next ›") : null,
      isNew ? null : writer(h("button", { class: "btn", onclick: guard(() => refineRequirementDialog(r, close, nav)) }, icon("sparkles"), "Refine with AI")),
      isNew ? null : writer(h("button", { class: "btn", title: "Divide into several requirements by hand", onclick: () => splitRequirementDialog(r, close, nav) }, "Split…")),
      h("span", { class: "grow" }),
      isNew ? null : writer(h("button", { class: "btn btn-danger", onclick: remove }, icon("trash"), "Delete")),
      nav && !isNew && !readOnly() ? shortcutHint() : null],
  });
}

// Divide one requirement that says several things into parts by hand (no AI): the first part stays in place, the rest become new drafts.
function splitRequirementDialog(r, closeDrawer, nav) {
  const parts = h("div", { class: "stack" });
  const add = (text) => parts.append(h("textarea", { rows: "2", maxlength: "1000", "aria-label": "Part " + (parts.children.length + 1), value: text }));
  add(r.statement); add("");
  modal({
    title: "Split " + r.key, description: "The first part replaces " + r.key + "; the others are added as new draft requirements with the same epic, section, actors and source.",
    body: h("div", { class: "stack" }, parts, h("button", { class: "btn btn-sm", onclick: () => add("") }, "Add another part")),
    actions: [{ label: "Split", kind: "btn-primary", onClick: async () => {
      const statements = [...parts.children].map((t) => t.value.trim()).filter(Boolean);
      const res = await api.post(base() + "/requirements/" + r.id + "/split", { statements });
      closeDrawer(); toast(r.key + " split; new: " + res.created.join(", "), "ok"); nav ? await nav.refresh() : rerender();
    } }],
  });
}

async function refineRequirementDialog(r, closeDrawer, nav) {
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
      closeDrawer(); toast(split ? "Split into " + res.proposals.length + " requirements" : "Applied to " + r.key, "ok"); nav ? await nav.refresh() : rerender();
    } }],
  });
}

// ---- stories ------------------------------------------------------------

// Keyboard shortcuts shared by the review drawers: Ctrl/Cmd+Enter saves, Ctrl/Cmd+Shift+Enter approves and moves on, Alt+Left/Right steps.
const reviewKeys = ({ save, approve, prev, next }) => (e) => {
  const mod = e.ctrlKey || e.metaKey;
  const act = mod && e.key === "Enter" ? (e.shiftKey ? approve : save) : e.altKey && e.key === "ArrowRight" ? next : e.altKey && e.key === "ArrowLeft" ? prev : null;
  if (!act) return;
  e.preventDefault();
  act();
};

const shortcutHint = () => h("div", { class: "shortcut-hint small muted hide-sm" },
  h("kbd", {}, "Ctrl"), "+", h("kbd", {}, "Enter"), " save · ", h("kbd", {}, "Ctrl"), "+", h("kbd", {}, "Shift"), "+", h("kbd", {}, "Enter"), " approve & next · ",
  h("kbd", {}, "Alt"), "+", h("kbd", {}, "←"), h("kbd", {}, "→"), " previous / next");

let storyNav = null;  // list context for the story drawer: set by the Stories tab, never used from other tabs

async function viewStories(body) {
  const [stories, reqs, talk] = await Promise.all([api.get(base() + "/stories"), api.get(base() + "/requirements"), api.get(base() + "/comments"), loadPeople()]);
  ctx.reqs = reqs;
  ctx.talk = talk;
  mount(body, runPanel("stories"));
  if (!stories.length) {
    mount(body, h("div", { class: "card" }, empty({ iconName: "map", title: "No stories yet",
      text: reqs.length ? "Generate stories from your reviewed requirements." : "Extract and review requirements first.",
      action: writer(h("span", { class: "row" }, importFileButton("story"), h("button", { class: "btn", onclick: () => storyDrawer(null) }, icon("plus"), "Add manually"))) })));
    return;
  }
  const box = h("div");
  const q = h("input", { type: "search", class: "search", placeholder: "Search stories", "aria-label": "Search stories", value: filters.storyQ });
  const status = select([["", "All statuses"], ["draft", "Draft"], ["approved", "Approved"], ["rejected", "Rejected"]], filters.storyStatus, { "aria-label": "Status" });
  const tagNames = [...new Set(stories.flatMap((x) => x.compliance_tags || []))].sort();
  if (filters.storyTag && !tagNames.includes(filters.storyTag)) filters.storyTag = "";  // the tag was removed everywhere since the filter was set
  const tagFilter = tagNames.length ? select([["", "All compliance"], ...tagNames.map((t) => [t, t])], filters.storyTag, { "aria-label": "Compliance tag" }) : null;
  const ready = select([["", "Any readiness"], ["ready", "Ready"], ["not-ready", "Needs work"]], filters.storyReady, { "aria-label": "Readiness" });
  const visible = () => stories.filter((s) =>
    (!filters.storyQ || (s.key + " " + s.title + " " + s.narrative + " " + s.epic + " " + s.feature).toLowerCase().includes(filters.storyQ.toLowerCase())) &&
    (!filters.storyStatus || s.status === filters.storyStatus) && matchesAssignee(s, filters.storyAssignee) &&
    (!filters.storyTag || (s.compliance_tags || []).includes(filters.storyTag)) && (!filters.storyReady || (filters.storyReady === "ready") === s.ready));
  const sorted = () => { const rows = visible(), by = STORY_SORTS[filters.storySort]; return by ? rows.sort((a, b) => filters.storyDir * by(a, b)) : rows; };
  const selected = new Set();
  const bulk = h("div", { class: "bulk-sticky" });
  const renderBulk = () => bulk.replaceChildren(...(selected.size && filters.storyView === "list" ? [h("div", { class: "bulkbar" }, selected.size + " selected",
    h("span", { class: "grow" }),
    ...[["approved", "Approve"], ["draft", "Mark draft"], ["rejected", "Reject"]].map(([st, l]) =>
      h("button", { class: "btn btn-sm", onclick: guard(async () => {
        if (selected.size >= BULK_CONFIRM_AT && !(await confirmBox(l + " " + selected.size + " stories?", { okLabel: l, danger: st === "rejected" }))) return;
        await api.post(base() + "/stories/bulk", { ids: [...selected], status: st }); toast(l + ": " + selected.size, "ok"); selected.clear(); await refresh();
      }) }, l)),
    bulkAssign("story", selected, refresh),
    bulkSet("story", selected, refresh),
    bulkTag("story", selected, refresh),
    h("button", { class: "btn btn-sm btn-ghost", onclick: () => { selected.clear(); render(); } }, "Clear"))] : []));
  const paging = { limit: PAGE };  // rows of the list view drawn so far
  const render = () => {
    const rows = sorted();
    box.replaceChildren(filters.storyView === "map" ? storyMap(rows) : storyTable(rows, restart, { selected, changed: renderBulk, paging, render }));
    renderBulk();
  };
  const restart = () => { paging.limit = PAGE; render(); };  // a new filter, view or order starts again from the top
  q.addEventListener("input", debounce(() => { filters.storyQ = q.value; restart(); }));
  status.addEventListener("change", () => { filters.storyStatus = status.value; restart(); });
  if (tagFilter) tagFilter.addEventListener("change", () => { filters.storyTag = tagFilter.value; restart(); });
  ready.addEventListener("change", () => { filters.storyReady = ready.value; restart(); });
  const count = h("span", { class: "muted small right" });
  const tally = () => {
    const points = stories.filter((s) => s.status !== "rejected").reduce((a, s) => a + (s.story_points || 0), 0);
    count.textContent = stories.filter((s) => s.ready).length + " ready · " + points + " points";
  };
  // Reload the data but redraw only the list, so a reviewer keeps their place.
  const refresh = async () => { stories.splice(0, stories.length, ...(await api.get(base() + "/stories"))); tally(); render(); };
  storyNav = { rows: sorted, refresh, find: (id) => stories.find((x) => x.id === id) };
  tally();
  mount(body, h("div", { class: "toolbar" },
    segmented([["map", "Story map"], ["list", "List"]], filters.storyView, (v) => { filters.storyView = v; restart(); }),
    q, status, tagFilter, assigneeFilter(stories, filters.storyAssignee, (v) => { filters.storyAssignee = v; restart(); }), ready,
    count,
    writer(h("button", { class: "btn", onclick: guard(estimateDialog) }, icon("sparkles"), "Estimate with AI")),
    writer(importFileButton("story")),
    writer(h("button", { class: "btn", onclick: () => storyDrawer(null) }, icon("plus"), "Add story"))), bulk, box);
  render();
  openLinkedItem(stories, (s) => storyDrawer(s));
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
  const dropTarget = (c) => readOnly() ? {} : ({
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
        draggable: readOnly() ? null : "true", ondragstart: (e) => { e.dataTransfer.setData("text/fabulist-story", s.id); e.dataTransfer.effectAllowed = "move"; },
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

function storyTable(rows, redraw, pick) {
  const { selected, changed, paging, render } = pick;
  const drawn = rows.slice(0, paging.limit);  // `rows` is every story matching the filters: select-all and Next/Prev use these
  const all = h("input", { type: "checkbox", "aria-label": rows.length > drawn.length ? "Select all " + rows.length.toLocaleString() + " matching" : "Select all", checked: rows.length > 0 && rows.every((s) => selected.has(s.id)),
    onchange: (e) => {
      rows.forEach((s) => (e.target.checked ? selected.add(s.id) : selected.delete(s.id)));
      // touch the rows, don't rebuild them: rebuilding thousands of rows makes every click lag
      table.querySelectorAll("tbody tr.clickable").forEach((tr) => { tr.classList.toggle("selected", e.target.checked); tr.querySelector("input").checked = e.target.checked; });
      changed();
    } });
  const table = h("div", { class: "card table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, writer(h("th", { class: "w-check" }, all)), [["ID", "", "key"], ["Story", "", "title"], ["Epic / feature", "hide-sm", "epic"], ["Priority", "hide-sm", "priority"], ["Pts", "hide-sm", "points"], ["Requirements", "hide-sm"], ["Quality", "hide-sm", "quality"], ["Status", "", "status"]].map(([x, c, k]) => sortHeader(x, c, k, "story", redraw)))),
    h("tbody", {}, drawn.map((s) => h("tr", { class: "clickable" + (selected.has(s.id) ? " selected" : ""), onclick: () => storyDrawer(s) },
      writer(h("td", { onclick: (e) => e.stopPropagation() }, h("input", { type: "checkbox", "aria-label": "Select " + s.key, checked: selected.has(s.id),
        onchange: (e) => {
          e.target.checked ? selected.add(s.id) : selected.delete(s.id);
          e.target.closest("tr").classList.toggle("selected", e.target.checked);
          all.checked = rows.every((x) => selected.has(x.id));
          changed();
        } }))),
      h("td", { class: "mono nowrap" }, rowOpener(s.key)),
      h("td", {}, h("b", {}, s.title), h("div", { class: "small muted" }, s.persona)),
      h("td", { class: "small hide-sm" }, s.epic, h("div", { class: "muted" }, s.feature)),
      h("td", { class: "nowrap hide-sm" }, h("span", { class: "row" }, h("span", { class: "dot " + s.priority }), s.priority)),
      h("td", { class: "hide-sm" }, s.story_points || "–"),
      h("td", { class: "small mono hide-sm" }, s.requirement_ids.join(", ") || "–"),
      h("td", { class: "nowrap hide-sm" }, h("span", { class: "row" }, scorePill(s.quality_score), s.ready ? badge("Ready", "green") : null)),
      h("td", {}, h("div", { class: "row", style: { flexWrap: "wrap" } }, badge(s.status, statusTone[s.status]), talkBadge((ctx.talk || {})[s.id]))))),
      pagerRow(rows.length, drawn.length, 9, (n) => { const before = drawn.length; paging.limit = n; render(); document.querySelectorAll("#content tbody tr.clickable .row-open")[before]?.focus(); }))));
  return table;
}

function storyDrawer(s, nav = storyNav) {
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
  const assignee = assigneeControl(s.assignee);
  const linked = reqs.filter((r) => s.requirement_ids.includes(r.key));
  const rows = nav && !isNew ? nav.rows() : [];
  const at = rows.findIndex((x) => x.id === s.id);
  const [prevId, nextId] = [rows[at - 1]?.id, rows[at + 1]?.id];  // neighbours as the list is now, before a save reorders or filters it
  let dirty = false;
  const save = (approveAndNext) => guard(async () => {
    if (approveAndNext) status = "approved";
    const payload = { title: title.value, persona: persona.value, narrative: narrative.value, acceptance_criteria: readLines(ac),
      priority: priority.value, story_points: points.value ? Number(points.value) : null, status, epic: epic.value, feature: feature.value,
      requirement_ids: links.get(), labels: labels.get(), product_scope: scope.get(), compliance_tags: compliance.get(),
      variant_criteria: readLines(variants).map((line) => { const i = line.indexOf(":"); return i > 0 ? { scope: line.slice(0, i).trim(), criteria: line.slice(i + 1).trim() } : { scope: "", criteria: line }; }),
      open_questions: readLines(questions), notes: notes.value, assignee: assignee.value() };
    if (isNew) await api.post(base() + "/stories", payload);
    else await api.patch(base() + "/stories/" + s.id, Object.assign(payload, { rev: s.rev }));
    close(); toast(isNew ? "Story added" : "Saved " + s.key, "ok");
    if (!nav || isNew) return rerender();
    await nav.refresh();
    if (approveAndNext) go(nextId);
  });
  const go = (id) => { const n = id && nav.find(id); if (n) storyDrawer(n, nav); };
  const step = (id) => guard(async () => {
    if (dirty && !(await confirmBox("Discard your unsaved changes to " + s.key + "?", { okLabel: "Discard" }))) return;
    close(); go(id);
  });
  const keys = reviewKeys({ save: readOnly() ? () => {} : save(false), approve: readOnly() ? () => {} : nextId ? save(true) : save(false), prev: () => prevId && step(prevId)(), next: () => nextId && step(nextId)() });
  const remove = guard(async () => {
    if (!(await confirmBox("Delete " + s.key + "?", { danger: true, okLabel: "Delete" }))) return;
    await api.del(base() + "/stories/" + s.id); close(); nav ? await nav.refresh() : rerender();
  });
  const close = drawer({
    headExtra: isNew ? null : copyLinkButton("stories", s.key),
    subtitle: isNew ? "New story" : s.key + " · " + s.epic + " › " + s.feature, title: isNew ? "Add story" : s.title,
    body: h("div", { class: "stack", oninput: () => { dirty = true; } },
      (s.external || []).length ? h("div", { class: "row wrap small" }, h("span", { class: "muted" }, "In tracker:"),
        s.external.map((x) => h("a", { class: "chip", href: x.url, target: "_blank", rel: "noopener noreferrer" }, icon("link"), x.key))) : null,
      isNew ? null : h("div", { class: "card card-body" },
        h("div", { class: "row", style: { marginBottom: "10px" } }, h("b", { class: "grow" }, "Quality (INVEST, Definition of Ready)"),
          s.ready ? badge("Ready", "green") : badge("Needs work", "amber"), scorePill(s.quality_score)), issueList(s.issues)),
      h("fieldset", { class: "plain-fieldset stack", disabled: readOnly() },
      field("Review status", segmented([["draft", "Draft"], ["approved", "Approved"], ["rejected", "Rejected"]], status, (v) => { status = v; dirty = true; })),
      field("Title", title),
      h("div", { class: "form-grid" }, field("Persona", persona), field("Priority", priority), field("Epic", epic), field("Feature", feature), field("Assignee", assignee.el)),
      field("Narrative", narrative, "As a <role>, I want <goal>, so that <benefit>."),
      field("Acceptance criteria", ac, "One per line, in Given / When / Then form."),
      field("Product variants", variants, "One per line: 'Product class: criteria'. Variants avoid duplicate stories per product type."),
      h("div", { class: "form-grid" }, field("Estimate", points), field("Requirement links", links.el, null, "")),
      linked.length ? h("div", { class: "callout small" }, h("div", { class: "stack", style: { gap: "4px" } },
        linked.map((r) => h("div", {}, h("b", { class: "mono" }, r.key + " "), r.statement)))) : null,
      field("Labels", labels.el), field("Product classes", scope.el), field("Compliance", compliance.el),
      field("Open questions", questions, "Stories with open questions don't meet the Definition of Ready."), field("Notes", notes)),
      isNew ? null : discussionSection(s.id),
      isNew ? null : historySection(s.id)),
    onkeydown: keys,
    footer: [writer(h("button", { class: "btn btn-primary", onclick: save(false) }, "Save")),
      nav && !isNew && nextId ? writer(h("button", { class: "btn", onclick: save(true), title: "Mark approved, save, and open the next story (Ctrl+Shift+Enter)" }, "Approve & next")) : null,
      nav && !isNew ? h("button", { class: "btn btn-ghost", "aria-label": "Previous story", title: "Alt+Left", disabled: !prevId, onclick: step(prevId) }, "‹ Prev") : null,
      nav && !isNew ? h("button", { class: "btn btn-ghost", "aria-label": "Next story", title: "Alt+Right", disabled: !nextId, onclick: step(nextId) }, "Next ›") : null,
      isNew ? null : writer(h("button", { class: "btn", onclick: guard(() => refineDialog(s, close, nav)) }, icon("sparkles"), "Refine with AI")),
      h("span", { class: "grow" }),
      isNew ? null : writer(h("button", { class: "btn btn-danger", onclick: remove }, icon("trash"), "Delete")),
      nav && !isNew && !readOnly() ? shortcutHint() : null],
  });
}

const REFINE_FIELDS = [["title", "Title"], ["persona", "Persona"], ["narrative", "Narrative"], ["acceptance_criteria", "Acceptance criteria"],
  ["variant_criteria", "Product variants"], ["open_questions", "Open questions"], ["requirement_ids", "Requirement links"]];

function asText(key, v) {
  if (key === "variant_criteria") return (v || []).map((x) => x.scope + ": " + x.criteria).join("\n");
  return Array.isArray(v) ? v.join("\n") : String(v || "");
}

async function refineDialog(story, closeDrawer, nav) {
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
      closeDrawer(); toast("Applied refinement to " + story.key, "ok"); nav ? await nav.refresh() : rerender();
    } }] : [],
  });
}

// ---- traceability -------------------------------------------------------

const AUDIT_PAGE = 50;

async function viewAudit(body) {
  const facets = await api.get(base() + "/history/filters");
  const actionSel = select([["", "All actions"], ...facets.actions.map((a) => [a, a])], "", { "aria-label": "Filter by action", style: { width: "auto", minWidth: "180px" } });
  const actorSel = select([["", "All users"], ...facets.actors.map((a) => [a, a])], "", { "aria-label": "Filter by user", style: { width: "auto", minWidth: "180px" } });
  const list = h("div", { class: "card-body" });
  const more = h("button", { class: "btn", style: { margin: "0 16px 16px" } }, "Load more");
  const status = h("span", { class: "muted small" });
  let rows = [];
  const load = async (reset) => {
    if (reset) rows = [];
    const q = new URLSearchParams({ limit: AUDIT_PAGE });
    if (actionSel.value) q.set("action", actionSel.value);
    if (actorSel.value) q.set("actor", actorSel.value);
    if (rows.length) q.set("before", rows[rows.length - 1].n);
    const page = await api.get(base() + "/history?" + q);
    rows = rows.concat(page);
    list.replaceChildren(historyList(rows, { showItem: true, group: false, link: itemLink }));
    const last = page.length < AUDIT_PAGE;
    more.style.display = last ? "none" : "";  // `hidden` loses to .btn's display rule
    status.textContent = rows.length + " entr" + (rows.length === 1 ? "y" : "ies") + (last ? "" : " shown");
  };
  more.onclick = guard(() => load(false));
  actionSel.onchange = actorSel.onchange = guard(() => load(true));
  mount(body, h("div", { class: "card" },
    h("div", { class: "card-head" }, h("h3", {}, "Audit trail"), status,
      h("button", { class: "btn btn-sm right", onclick: () => download(base() + "/history.csv") }, icon("download"), "Export CSV")),
    h("div", { class: "row wrap", style: { padding: "12px 16px 0" } }, actionSel, actorSel),
    list, more));
  await load(true);
}

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
        h("td", {}, h("div", { class: "row wrap" }, r.stories.map((s) => h("button", { class: "chip", style: { cursor: "pointer" }, title: s.title, onclick: () => storyDrawer(byId[s.id], null) }, s.key)))),
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
        h("span", { class: "row wrap", style: { display: "inline-flex" } }, t.orphans.map((o) => h("button", { class: "chip", style: { cursor: "pointer" }, onclick: () => storyDrawer(byId[o.id], null) }, o.key + " " + o.title))))) : null,
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
      writer(h("button", { class: "btn btn-sm", onclick: create }, icon("plus"), "Create baseline"))),
    list.length ? h("div", { class: "table-wrap", style: { marginTop: "8px" } }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, [["Version", ""], ["Name", ""], ["Contents", "hide-sm"], ["Created", "hide-sm"], ["", ""]].map(([x, c]) => h("th", { class: c }, x)))),
      h("tbody", {}, list.map((b) => {
        const fmtSel = select(state.meta.formats.map((f) => [f.id, f.label]), "xlsx", { "aria-label": "Format", class: "fmt-select", style: { width: "auto" } });
        return h("tr", {},
          h("td", {}, badge("v" + b.version, "accent")),
          h("td", {}, h("b", {}, b.name), b.note ? h("div", { class: "muted small" }, b.note) : null,
            h("div", { class: "muted small show-sm" }, b.requirements + " req · " + b.stories + " stories")),  // the Contents column is hidden on phones
          h("td", { class: "small hide-sm" }, b.requirements + " requirements · " + b.stories + " stories", h("div", { class: "muted" }, b.scope === "approved" ? "Approved only" : "Approved + draft")),
          h("td", { class: "small hide-sm" }, timeAgo(b.created), h("div", { class: "muted" }, "by " + (b.actor || "–"))),
          h("td", { style: { textAlign: "right" } },
            h("span", { class: "row wrap", style: { justifyContent: "flex-end" } }, fmtSel,
              h("button", { class: "btn btn-sm", onclick: () => download(base() + "/baselines/" + b.id + "/export?format=" + encodeURIComponent(fmtSel.value)) }, icon("download"), "Export"),
              h("button", { class: "btn btn-sm", title: "Compare with the current backlog", "aria-label": "Compare " + b.name + " with the current backlog", onclick: () => compare(b) }, "Compare"))));
      }))))
      : h("div", { class: "card-body muted small" }, "No baselines yet. Create one when the backlog is signed off."));
}

async function pushCard() {
  if (readOnly()) return null;  // pushing writes to an external tracker
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
    btn.disabled = true; btn.textContent = label() === "Publish page" ? "Publishing…" : "Pushing…";
    result.replaceChildren();
    try {
      const r = await api.post(base() + "/push", { integration_id: conn.value, target: target.value, scope: approvedOnly ? "approved" : "all" });
      const partial = r.failed.length || r.remaining;
      const shown = r.failed.slice(0, 10);
      result.replaceChildren(h("div", { class: "callout " + (partial ? "warn" : "ok"), role: partial ? "alert" : "status" }, h("div", { class: "stack", style: { gap: "4px" } },
        h("b", {}, r.created + " created, " + r.updated + " updated" + (r.failed.length ? ", " + r.failed.length + " failed" : "")),
        r.remaining ? h("div", {}, r.remaining + " " + (r.remaining === 1 ? "story was" : "stories were") + " not pushed because the time limit was reached. Push again to continue: stories already pushed are updated, not duplicated.") : null,
        shown.map((f) => h("div", { class: "small" }, f.item + ": " + f.error)),
        r.failed.length > shown.length ? h("div", { class: "small muted" }, "…and " + (r.failed.length - shown.length) + " more. The count is recorded in the audit trail.") : null)));
    } catch (e) {
      // Keep the failure in the card (a toast fades); the same message is announced to screen readers.
      result.replaceChildren(h("div", { class: "callout error", role: "alert" }, e.message));
    } finally { btn.disabled = false; btn.textContent = label(); }
  });
  const label = () => (integrations.find((i) => i.id === conn.value) || {}).kind === "confluence" ? "Publish page" : "Push stories";
  const btn = h("button", { class: "btn btn-primary", onclick: push }, label());
  conn.addEventListener("change", () => { btn.textContent = label(); });
  card.append(h("div", { class: "runbox" }, conn, target, h("span", { class: "muted small grow" }, "Uses the export scope selected above."), btn, result));
  return card;
}
