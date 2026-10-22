// API reference: renders /openapi.json (served by the app, behind the same sign-in) without any external script or style.
const h = (tag, props, ...children) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v; else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
};

let spec = null;
const resolve = (schema) => {
  let s = schema, guard = 0;
  while (s && s.$ref && guard++ < 10) s = spec.components.schemas[s.$ref.split("/").pop()];
  return s || {};
};

// A short type label: string, integer, array of X, one of the enum values...
function typeLabel(schema) {
  const s = resolve(schema);
  if (s.anyOf || s.oneOf) {
    const parts = (s.anyOf || s.oneOf).map(typeLabel).filter((t) => t !== "null");
    return parts.join(" | ") || "any";
  }
  if (s.enum) return s.enum.map((v) => JSON.stringify(v)).join(" | ");
  if (s.type === "array") return "array of " + typeLabel(s.items || {});
  if (s.type === "object" && !s.properties) return s.additionalProperties ? "object (free-form)" : "object";
  if (s.type === "object") return "object";
  return s.type ? s.type + (s.format ? " (" + s.format + ")" : "") : "any";
}

// The fields of a body schema, flattened (objects nested up to three deep).
function rows(schema, depth = 0, prefix = "", seen = new Set()) {
  const s = resolve(schema);
  const props = s.properties || (s.items && resolve(s.items).properties) || null;
  if (!props || depth > 2) return [];
  const required = new Set(s.required || (s.items && resolve(s.items).required) || []);
  const out = [];
  for (const [name, def] of Object.entries(props)) {
    const r = resolve(def);
    out.push({ name: prefix + name, type: typeLabel(def), required: required.has(name), note: [def.description || r.description, def.default !== undefined ? "default " + JSON.stringify(def.default) : ""].filter(Boolean).join(" · ") });
    const target = (r.type === "array" && r.items ? resolve(r.items) : r);
    const key = def.$ref || (r.items && r.items.$ref) || "";
    if (target.properties && !seen.has(key)) out.push(...rows(target, depth + 1, prefix + name + ".", new Set([...seen, key])));
  }
  return out;
}

const table = (headers, body) => h("table", { class: "docs-schema" }, h("thead", {}, h("tr", {}, headers.map((x) => h("th", { scope: "col" }, x)))), h("tbody", {}, body));

function operation(path, method, op) {
  const params = (op.parameters || []).map((p) => ({ name: p.name, type: typeLabel(p.schema || {}), where: p.in, required: p.required, note: p.description || (p.schema && p.schema.default !== undefined ? "default " + JSON.stringify(p.schema.default) : "") }));
  const body = op.requestBody && op.requestBody.content && (op.requestBody.content["application/json"] || Object.values(op.requestBody.content)[0]);
  const fields = body ? rows(body.schema || {}) : [];
  const responses = Object.entries(op.responses || {});
  const text = (path + " " + method + " " + (op.summary || "") + " " + (op.description || "")).toLowerCase();
  const el = h("details", { class: "docs-op", "data-text": text },
    h("summary", {}, h("span", { class: "docs-method " + method }, method.toUpperCase()), h("span", { class: "docs-path" }, path), h("span", { class: "docs-sum" }, op.summary || "")),
    h("div", { class: "docs-body" },
      op.description ? h("p", {}, op.description) : null,
      params.length ? [h("h4", {}, "Parameters"), table(["Name", "In", "Type", "Notes"], params.map((p) => h("tr", {}, h("td", {}, p.name, p.required ? h("span", { class: "docs-req", title: "required" }, " *") : null), h("td", {}, p.where), h("td", {}, p.type), h("td", {}, p.note))))] : null,
      body ? [h("h4", {}, "Request body (JSON)"), fields.length
        ? table(["Field", "Type", "Notes"], fields.map((f) => h("tr", {}, h("td", {}, f.name, f.required ? h("span", { class: "docs-req", title: "required" }, " *") : null), h("td", {}, f.type), h("td", {}, f.note))))
        : h("p", { class: "muted small" }, "A " + typeLabel(body.schema || {}) + " (see the example in the introduction).")] : null,
      h("h4", {}, "Responses"),
      h("ul", { class: "docs-responses" }, responses.map(([code, r]) => h("li", {}, h("code", {}, code), " " + (r.description || ""))))));
  return el;
}

function render() {
  document.getElementById("intro").replaceChildren(...String(spec.info.description || "").split(/\n\s*\n/).map((para) =>
    h("p", {}, ...para.split(/(`[^`]+`|\*\*[^*]+\*\*)/).filter(Boolean).map((part) => part.startsWith("`") ? h("code", {}, part.slice(1, -1)) : part.startsWith("**") ? h("strong", {}, part.slice(2, -2)) : part))));
  const byTag = new Map((spec.tags || []).map((t) => [t.name, { desc: t.description, ops: [] }]));
  for (const [path, item] of Object.entries(spec.paths)) {
    for (const [method, op] of Object.entries(item)) {
      const tag = (op.tags && op.tags[0]) || "Other";
      if (!byTag.has(tag)) byTag.set(tag, { desc: "", ops: [] });
      byTag.get(tag).ops.push(operation(path, method, op));
    }
  }
  const groups = [...byTag.entries()].filter(([, g]) => g.ops.length).map(([name, g]) =>
    h("details", { class: "docs-group", "data-tag": name }, h("summary", {}, name, h("span", { class: "muted" }, g.ops.length + " operation" + (g.ops.length === 1 ? "" : "s") + (g.desc ? " · " + g.desc : ""))), g.ops));
  document.getElementById("reference").replaceChildren(...groups);
  applyFilter();
}

function applyFilter() {
  const words = document.getElementById("filter").value.toLowerCase().split(/\s+/).filter(Boolean);
  let shown = 0, total = 0;
  for (const group of document.querySelectorAll(".docs-group")) {
    let any = 0;
    for (const op of group.querySelectorAll(".docs-op")) {
      const hit = words.every((w) => op.dataset.text.includes(w) || group.dataset.tag.toLowerCase().includes(w));
      op.hidden = !hit;
      total++;
      if (hit) { any++; shown++; }
    }
    group.hidden = any === 0;
    if (words.length && any) group.open = true;  // a search shows its matches
  }
  document.getElementById("count").textContent = words.length ? shown + " of " + total + " operations" : total + " operations";
}

document.getElementById("filter").addEventListener("input", applyFilter);
fetch("/openapi.json", { credentials: "same-origin" })
  .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
  .then((s) => { spec = s; document.title = spec.info.title + " " + spec.info.version; render(); })
  .catch((e) => document.getElementById("reference").replaceChildren(h("p", { role: "alert" }, "Could not load the API description (" + e.message + "). Sign in and reload.")));
