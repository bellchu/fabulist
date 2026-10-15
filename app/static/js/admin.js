// Admin dashboard: usage overview, AI provider profiles, generation settings, activity log.
import { h, mount, icon, api, guard, toast, modal, confirmBox, badge, statusTone, meter, fmt, duration, timeAgo, field, select, segmented, empty } from "./core.js";
import { state, setCrumbs } from "./main.js";

const rerender = () => window.dispatchEvent(new HashChangeEvent("hashchange"));
const TITLES = { overview: "Overview", providers: "AI providers", settings: "Generation settings", integrations: "Integrations", activity: "Activity" };

export async function renderAdmin(content, tab) {
  setCrumbs({ href: "#/admin/overview", label: "Administration" }, TITLES[tab] || "Overview");
  const view = { overview: viewOverview, providers: viewProviders, settings: viewSettings, integrations: viewIntegrations, activity: viewActivity }[tab] || viewOverview;
  mount(content, h("div", { class: "tabs" }, Object.entries(TITLES).map(([id, label]) =>
    h("a", { class: "tab" + (id === tab ? " active" : ""), href: "#/admin/" + id }, label))));
  await view(content);
}

function head(title, sub, action) {
  return h("div", { class: "page-head" }, h("div", { class: "grow" }, h("h1", {}, title), h("p", { class: "sub" }, sub)), action || null);
}

// ---- overview -----------------------------------------------------------

async function viewOverview(content) {
  const o = await api.get("/api/admin/overview");
  const w = o.last_30_days;
  const failRate = w.runs ? Math.round((100 * w.failed) / w.runs) : 0;
  const stat = (label, value, foot) => h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), foot ? h("div", { class: "foot" }, foot) : null);
  const dp = o.default_provider;
  mount(content, 
    head("Overview", "Platform health, usage and configuration at a glance."),
    o.encryption_configured ? null : h("div", { class: "callout warn", style: { marginBottom: "16px" } }, icon("alert"),
      "SETTINGS_ENCRYPTION_KEY is not set on the server, so API keys cannot be saved. Add it to the deployment secret."),
    h("div", { class: "stats", style: { marginBottom: "16px" } },
      stat("Projects", fmt(o.totals.projects)), stat("BRD documents", fmt(o.totals.documents), fmt(o.totals.chunks) + " indexed passages"),
      stat("Requirements", fmt(o.totals.requirements)), stat("Stories", fmt(o.totals.stories))),
    h("div", { class: "stats", style: { marginBottom: "16px" } },
      stat("Runs (30 days)", fmt(w.runs)),
      h("div", { class: "stat" }, h("div", { class: "label" }, "Failure rate (30 days)"), h("div", { class: "value" }, failRate + "%"), meter(100 - failRate)),
      stat("Tokens (30 days)", fmt(w.tokens_in + w.tokens_out), fmt(w.tokens_in) + " in · " + fmt(w.tokens_out) + " out"),
      stat("Avg run time", duration(w.avg_ms))),
    h("div", { class: "grid grid-2" },
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", { class: "grow" }, "Default AI provider"), h("a", { class: "btn btn-sm", href: "#/admin/providers" }, "Manage")),
        h("div", { class: "card-body" }, dp ? h("div", { class: "stack", style: { gap: "6px" } },
          h("div", { class: "row" }, h("b", {}, dp.name), badge(dp.kind_label, "accent")),
          dp.kind !== "mock" ? h("div", { class: "small muted mono" }, dp.base_url + " · " + dp.model) : h("div", { class: "small muted" }, "Offline heuristic: no AI calls are made. Add a provider for real generation."),
          dp.kind !== "mock" ? h("div", { class: "small" }, dp.has_key ? "API key saved (" + dp.key_hint + ")" : "No API key saved") : null)
          : h("p", { class: "muted" }, "No provider configured."))),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, "Usage by provider (30 days)")),
        o.by_provider.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
          h("thead", {}, h("tr", {}, ["Provider", "Model", "Runs", "Tokens"].map((x) => h("th", {}, x)))),
          h("tbody", {}, o.by_provider.map((r) => h("tr", {}, h("td", {}, r.provider), h("td", { class: "mono" }, r.model || "–"), h("td", {}, r.runs), h("td", {}, fmt(r.tokens)))))))
          : h("div", { class: "card-body muted" }, "No runs yet."))));
}

// ---- providers ----------------------------------------------------------

async function viewProviders(content) {
  const data = await api.get("/api/admin/providers");
  const list = h("div", { class: "card" });
  mount(content, 
    head("AI providers", "Endpoints, models and API keys used to extract requirements and write stories.",
      h("button", { class: "btn btn-primary", onclick: () => providerDialog(data, null) }, icon("plus"), "Add provider")),
    data.encryption_configured ? null : h("div", { class: "callout warn", style: { marginBottom: "16px" } }, icon("alert"),
      "API keys can't be stored until SETTINGS_ENCRYPTION_KEY is configured on the server."),
    list,
    h("p", { class: "muted small", style: { marginTop: "12px" } },
      "Keys are encrypted at rest and never returned to the browser. Text sent to external providers has obvious personal identifiers masked when redaction is on (Generation settings)."));
  list.append(...data.providers.map((p) => {
    const result = h("div", { class: "small", style: { flexBasis: "100%" } });
    return h("div", { class: "provider-row" },
      h("div", { class: "grow", style: { minWidth: "240px" } },
        h("div", { class: "row" }, h("b", {}, p.name), p.is_default ? badge("Default", "green") : null, badge(p.kind_label, "accent")),
        p.kind === "mock" ? h("div", { class: "small muted" }, "Rule-based extraction for demos and offline use. No data leaves the cluster.")
          : h("div", { class: "small muted mono" }, p.base_url + " · " + p.model),
        p.kind === "mock" ? null : h("div", { class: "small" }, p.has_key ? h("span", { class: "muted" }, "API key " + p.key_hint) : h("span", { style: { color: "var(--amber)" } }, "No API key"))),
      h("button", { class: "btn btn-sm", onclick: guard(async () => {
        result.replaceChildren(h("span", { class: "muted" }, "Testing…"));
        const r = await api.post("/api/admin/providers/" + p.id + "/test");
        result.replaceChildren(r.ok ? h("div", { class: "callout ok" }, icon("check"), "Connected in " + duration(r.latency_ms) + (r.reply ? " · replied “" + r.reply + "”" : ""))
          : h("div", { class: "callout error" }, icon("alert"), r.error));
      }) }, icon("play"), "Test"),
      h("button", { class: "btn btn-sm", onclick: () => providerDialog(data, p) }, icon("edit"), "Edit"),
      p.is_default ? null : h("button", { class: "btn btn-sm", onclick: guard(async () => {
        await api.post("/api/admin/providers/" + p.id + "/default"); state.meta = null; toast(p.name + " is now the default", "ok"); rerender();
      }) }, "Make default"),
      h("button", { class: "btn btn-sm btn-ghost btn-danger", "aria-label": "Delete " + p.name, onclick: guard(async () => {
        if (!(await confirmBox("Delete provider '" + p.name + "'? Its stored API key is removed.", { danger: true, okLabel: "Delete" }))) return;
        await api.del("/api/admin/providers/" + p.id); state.meta = null; rerender();
      }) }, icon("trash")),
      result);
  }));
}

function providerDialog(data, p) {
  const isNew = !p;
  p = p || { name: "", kind: "openai", base_url: "", model: "", has_key: false, key_hint: "" };
  const name = h("input", { type: "text", value: p.name, placeholder: "e.g. DeepSeek production" });
  const kind = select(Object.entries(data.kinds), p.kind);
  const baseUrl = h("input", { type: "url", value: p.base_url, placeholder: "https://api.example.com/v1" });
  const model = h("input", { type: "text", value: p.model, placeholder: "Model ID" });
  const key = h("input", { type: "password", autocomplete: "new-password", placeholder: p.has_key ? "Saved (" + p.key_hint + "). Leave blank to keep." : "Paste API key" });
  const clearKey = h("input", { type: "checkbox" });
  const preset = select([["", "Start from a preset…"], ...data.presets.map((x, i) => [String(i), x.label])], "");
  preset.addEventListener("change", () => {
    const x = data.presets[Number(preset.value)];
    if (!x) return;
    kind.value = x.kind; baseUrl.value = x.base_url; if (x.model) model.value = x.model; if (!name.value) name.value = x.label;
    sync();
  });
  const remote = h("div", { class: "stack" },
    field("Endpoint (base URL)", baseUrl, "OpenAI-compatible: the URL before /chat/completions. Anthropic: https://api.anthropic.com"),
    field("Model", model),
    field("API key", key, data.encryption_configured ? "Stored encrypted. Never shown again after saving." : "Server has no encryption key configured; keys can't be saved."),
    p.has_key ? h("label", { class: "switch small" }, clearKey, "Remove the saved key") : null);
  const sync = () => remote.classList.toggle("hidden", kind.value === "mock");
  kind.addEventListener("change", sync);
  sync();
  modal({
    title: isNew ? "Add AI provider" : "Edit " + p.name, wide: true,
    body: h("div", { class: "stack" }, isNew ? preset : null, h("div", { class: "form-grid" }, field("Display name", name), field("Type", kind)), remote),
    actions: [{ label: isNew ? "Add provider" : "Save", kind: "btn-primary", onClick: async () => {
      const body = { name: name.value, kind: kind.value, base_url: baseUrl.value, model: model.value, api_key: key.value, clear_key: clearKey.checked };
      if (isNew) await api.post("/api/admin/providers", body);
      else await api.put("/api/admin/providers/" + p.id, body);
      state.meta = null; toast("Provider saved", "ok"); rerender();
    } }],
  });
}

// ---- settings -----------------------------------------------------------

async function viewSettings(content) {
  const [s, prov] = await Promise.all([api.get("/api/admin/settings"), api.get("/api/admin/providers")]);
  const v = s.values;
  const num = (key, step) => h("input", { type: "number", value: v[key], step: step || "1", min: s.limits[key][0], max: s.limits[key][1] });
  const inputs = {
    temperature: num("temperature", "0.05"), max_output_tokens: num("max_output_tokens"), timeout_seconds: num("timeout_seconds"),
    chunk_chars: num("chunk_chars", "500"), max_chunks_per_run: num("max_chunks_per_run"), rag_top_k: num("rag_top_k"),
    embed_model: h("input", { type: "text", value: v.embed_model, placeholder: "e.g. text-embedding-3-small" }),
  };
  let redact = v.redact_pii;
  const embedProvider = select([["", "Offline hashing (no external calls)"], ...prov.providers.filter((p) => p.kind === "openai").map((p) => [p.id, p.name + " · " + p.base_url])], v.embed_provider_id);
  const limit = (k) => "Range " + s.limits[k][0] + " – " + s.limits[k][1] + ". Default " + s.defaults[k] + ".";
  const card = (title, sub, ...children) => h("div", { class: "card", style: { marginBottom: "16px" } },
    h("div", { class: "card-head" }, h("div", {}, h("h3", {}, title), h("div", { class: "muted small" }, sub))), h("div", { class: "card-body" }, ...children));
  const save = guard(async () => {
    const body = { redact_pii: redact, embed_provider_id: embedProvider.value };
    for (const [k, el] of Object.entries(inputs)) body[k] = el.value;
    const r = await api.put("/api/admin/settings", body);
    toast("Settings saved", "ok");
    if (r.reindex_recommended && (await confirmBox("The embeddings configuration changed. Rebuild the search index for all documents now?", { okLabel: "Rebuild index" }))) {
      await api.post("/api/admin/reindex"); toast("Re-indexing started");
    }
  });
  mount(content, 
    head("Generation settings", "Parameters applied to every extraction and story-generation run.", h("button", { class: "btn btn-primary", onclick: save }, "Save settings")),
    card("Model parameters", "Sent with each AI request.",
      h("div", { class: "form-grid" },
        field("Temperature", inputs.temperature, "Lower is more deterministic. " + limit("temperature")),
        field("Max output tokens", inputs.max_output_tokens, limit("max_output_tokens")),
        field("Request timeout (seconds)", inputs.timeout_seconds, "Transient errors retry up to 3 times. " + limit("timeout_seconds")))),
    card("Document processing", "How BRDs are split before being sent to the model.",
      h("div", { class: "form-grid" },
        field("Section size (characters)", inputs.chunk_chars, "Sections follow BRD headings, then split at this size. " + limit("chunk_chars")),
        field("Max sections per run", inputs.max_chunks_per_run, "Guards cost on very large BRDs. " + limit("max_chunks_per_run")))),
    card("Retrieval (RAG)", "Related BRD passages from pgvector are added as context when writing stories.",
      h("div", { class: "form-grid" },
        field("Passages per request", inputs.rag_top_k, "0 disables retrieval. " + limit("rag_top_k")),
        field("Vector dimension", h("input", { type: "text", value: String(s.embed_dim), disabled: true }), "Fixed by the database (EMBED_DIM). The model must output this size."),
        field("Embeddings provider", embedProvider, "Only OpenAI-compatible providers expose /embeddings."),
        field("Embeddings model", inputs.embed_model)),
      h("div", { style: { marginTop: "12px" } }, h("button", { class: "btn btn-sm", onclick: guard(async () => {
        if (!(await confirmBox("Rebuild embeddings for every document? This calls the embeddings provider for all passages.", { okLabel: "Rebuild index" }))) return;
        await api.post("/api/admin/reindex"); toast("Re-indexing started");
      }) }, icon("refresh"), "Rebuild search index"))),
    card("Privacy", "Applies to text sent to external AI providers.",
      field("Mask personal identifiers", segmented([[true, "On"], [false, "Off"]], redact, (x) => (redact = x)),
        "Emails, phone numbers, SSNs and long account/card numbers are replaced with placeholders before leaving the cluster.")));
}

// ---- integrations -------------------------------------------------------

async function viewIntegrations(content) {
  const data = await api.get("/api/integrations");
  const list = h("div", { class: "card" });
  mount(content,
    head("Integrations", "Issue trackers that stories can be pushed to. Re-pushing updates the same issues instead of creating duplicates.",
      h("button", { class: "btn btn-primary", onclick: () => integrationDialog(data, null) }, icon("plus"), "Add integration")),
    list);
  if (!data.integrations.length) {
    list.append(empty({ iconName: "link", title: "No integrations yet", text: "Connect Jira or Azure DevOps to push approved stories directly." }));
    return;
  }
  list.append(...data.integrations.map((it) => {
    const result = h("div", { class: "small", style: { flexBasis: "100%" } });
    return h("div", { class: "provider-row" },
      h("div", { class: "grow", style: { minWidth: "240px" } },
        h("div", { class: "row" }, h("b", {}, it.name), badge(it.kind_label, "accent")),
        h("div", { class: "small muted mono" }, it.base_url + (it.target ? " · default " + it.target : "")),
        h("div", { class: "small" }, it.has_token ? h("span", { class: "muted" }, (it.username ? it.username + " · " : "") + "token " + it.key_hint)
          : h("span", { style: { color: "var(--amber)" } }, "No API token"))),
      h("button", { class: "btn btn-sm", onclick: guard(async () => {
        result.replaceChildren(h("span", { class: "muted" }, "Testing…"));
        const r = await api.post("/api/admin/integrations/" + it.id + "/test");
        result.replaceChildren(r.ok ? h("div", { class: "callout ok" }, icon("check"), r.message) : h("div", { class: "callout error" }, icon("alert"), r.error));
      }) }, icon("play"), "Test"),
      h("button", { class: "btn btn-sm", onclick: () => integrationDialog(data, it) }, icon("edit"), "Edit"),
      h("button", { class: "btn btn-sm btn-ghost btn-danger", "aria-label": "Delete " + it.name, onclick: guard(async () => {
        if (!(await confirmBox("Delete '" + it.name + "'? Links between stories and pushed issues are forgotten, so a later push creates new issues.", { danger: true, okLabel: "Delete" }))) return;
        await api.del("/api/admin/integrations/" + it.id); rerender();
      }) }, icon("trash")),
      result);
  }));
}

function integrationDialog(data, it) {
  const isNew = !it;
  it = it || { kind: "jira", name: "", base_url: "", username: "", target: "", has_token: false, key_hint: "" };
  const kind = select(Object.entries(data.kinds), it.kind);
  const name = h("input", { type: "text", value: it.name, placeholder: "e.g. Jira – order-to-cash" });
  const baseUrl = h("input", { type: "url", value: it.base_url });
  const username = h("input", { type: "text", value: it.username, placeholder: "you@company.com" });
  const target = h("input", { type: "text", value: it.target });
  const token = h("input", { type: "password", autocomplete: "new-password", placeholder: it.has_token ? "Saved (" + it.key_hint + "). Leave blank to keep." : "" });
  const userField = field("Account email", username, "Jira Cloud: your Atlassian email (with an API token). Data Center: leave blank and use a personal access token.");
  const hints = h("div", { class: "callout small" });
  const sync = () => {
    const jira = kind.value === "jira";
    userField.classList.toggle("hidden", !jira);
    baseUrl.placeholder = jira ? "https://your-site.atlassian.net" : "https://dev.azure.com/your-organization";
    target.placeholder = jira ? "Project key, e.g. OTC" : "Project name, e.g. Order to Cash";
    hints.textContent = jira
      ? "Creates an Epic per story-map epic and Stories under it (parent link). Needs permission to create issues in the project."
      : "Creates Epic and User Story work items with acceptance criteria, priority, story points and a parent link. Use a PAT with Work Items (read & write).";
  };
  kind.addEventListener("change", sync);
  sync();
  modal({
    title: isNew ? "Add integration" : "Edit " + it.name, wide: true,
    body: h("div", { class: "stack" }, hints, h("div", { class: "form-grid" }, field("Type", kind), field("Display name", name)),
      field("Base URL", baseUrl), userField, h("div", { class: "form-grid" }, field("Default target project", target), field("API token", token, "Stored encrypted; never shown again."))),
    actions: [{ label: isNew ? "Add integration" : "Save", kind: "btn-primary", onClick: async () => {
      const body = { kind: kind.value, name: name.value, base_url: baseUrl.value, username: username.value, target: target.value, token: token.value };
      if (isNew) await api.post("/api/admin/integrations", body);
      else await api.put("/api/admin/integrations/" + it.id, body);
      toast("Integration saved", "ok"); rerender();
    } }],
  });
}

// ---- activity -----------------------------------------------------------

async function viewActivity(content) {
  const runs = await api.get("/api/admin/runs?limit=100");
  mount(content, head("Activity", "Every extraction and generation run across projects."),
    runs.length ? h("div", { class: "card table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ["When", "Project", "Stage", "By", "Provider", "Status", "Items", "Tokens", "Duration"].map((x) => h("th", {}, x)))),
      h("tbody", {}, runs.map((r) => h("tr", {},
        h("td", { class: "small nowrap muted" }, timeAgo(r.created)),
        h("td", {}, r.project_name ? h("a", { href: "#/p/" + r.project_id }, r.project_name) : h("span", { class: "muted" }, "deleted")),
        h("td", {}, r.stage),
        h("td", { class: "small" }, r.actor || "–"),
        h("td", { class: "small" }, r.provider, r.model ? h("div", { class: "muted mono" }, r.model) : null),
        h("td", {}, badge(r.status, statusTone[r.status]), r.error ? h("div", { class: "small", style: { color: "var(--red)", maxWidth: "320px" } }, r.error) : null),
        h("td", {}, r.status === "done" ? r.item_count : "–"),
        h("td", { class: "nowrap" }, fmt(r.tokens_in + r.tokens_out)),
        h("td", { class: "nowrap" }, duration(r.duration_ms)))))))
      : h("div", { class: "card" }, empty({ iconName: "activity", title: "No activity yet", text: "Runs appear here once projects start extracting requirements." })));
}
