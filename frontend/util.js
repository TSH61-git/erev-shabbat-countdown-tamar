// Every node is built with textContent, never innerHTML: replies come from the model and
// saved names come from what the model stored, so neither is trusted markup.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// SVG needs its own namespace; the icons are the design's own inline paths.
export function icon(width, height, attrs, ...shapes) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  for (const [k, v] of Object.entries({ width, height, viewBox: "0 0 24 24", fill: "none", "aria-hidden": "true", ...attrs })) {
    svg.setAttribute(k, v);
  }
  for (const [tag, shapeAttrs] of shapes) {
    const shape = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(shapeAttrs)) shape.setAttribute(k, v);
    svg.append(shape);
  }
  return svg;
}

export async function getJSON(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export async function postJSON(path, body = {}) {
  // JSON body on purpose: the backend refuses non-JSON writes (cross-site protection).
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
