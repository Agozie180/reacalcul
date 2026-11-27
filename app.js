const byId = (id) => document.getElementById(id);
const form = byId("sticker");
const model = byId("model");
const serial = byId("serial");
const replay = byId("replay");
const live = byId("live");
const notices = {
  baseline: { date: "Jul 24, 2025", label: "Initial recall", description: "Four model numbers with limited serial ranges." },
  expansion: { date: "Jan 15, 2026", label: "Recall expansion", description: "About 330,000 more minifridges included." },
};
const verdictTitles = {
  outside_model: "Model outside this notice", needs_serial: "Enter the serial to check",
  outside_serial: "Outside this notice’s serial range", covered: "Covered by this notice",
};
function node(tag, content, css) {
  const el = document.createElement(tag);
  if (content) el.textContent = content;
  if (css) el.className = css;
  return el;
}
function link(url, label, css) {
  const el = node("a", label, css);
  Object.assign(el, { href: url, target: "_blank", rel: "noopener" });
  return el;
}
function entry(notice, result, newlyCovered = false) {
  const item = node("li", "", "event " + (result ? result.status === "covered" ? "match" : "" : "pending"));
  item.append(node("span", notice.date, "date"));
  item.append(node("h3", result ? newlyCovered ? "Newly covered" : verdictTitles[result.status] : notice.label));
  item.append(node("p", result?.reason || notice.description));
  if (result?.evidence) item.append(node("p", result.evidence, "evidence"));
  if (result) {
    item.append(link(result.source, "Read the CPSC notice ↗"));
    if (result.status === "covered") {
      item.append(node("br"));
      const refund = link("https://www.recallrtr.com/minifridge", "Open official refund instructions ↗", "remedy");
      item.append(refund);
    }
  }
  return item;
}
function render(data) {
  if (!data.appliance) return;
  model.value = data.appliance.model;
  serial.value = data.appliance.serial;
  const timeline = node("ol", "", "timeline");
  timeline.append(entry(notices.baseline, data.baseline), entry(notices.expansion, data.expansion, data.newly_covered));
  byId("results").replaceChildren(timeline);
  byId("progress").textContent = data.expansion ? "Replay complete" : "July result saved";
  byId("actions").hidden = false;
  replay.hidden = !!data.expansion;
  live.hidden = !data.expansion;
}
function message(text, error = false) {
  const el = byId("message");
  el.textContent = text;
  el.classList.toggle("error", error);
}
async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Could not check the recall. Try again.");
  return data;
}
async function run(button, path, payload, success) {
  document.querySelectorAll("button").forEach((item) => { item.disabled = true; });
  try {
    message(button === live ? "Checking the official CPSC feed…" : "");
    const result = await api(path, payload);
    if (result.appliance) render(result);
    if (success) success(result);
  } catch (error) {
    message(error.message, true);
  } finally {
    document.querySelectorAll("button").forEach((item) => { item.disabled = false; });
  }
}
form.addEventListener("submit", (event) => {
  event.preventDefault();
  run(byId("check"), "/api/check", { model: model.value, serial: serial.value });
});
function useSample(value) {
  model.value = "EFMIS121";
  serial.value = value;
  form.requestSubmit();
}
byId("sample").addEventListener("click", () => useSample("A2210"));
byId("other-sample").addEventListener("click", () => useSample("A2401"));
replay.addEventListener("click", () => run(replay, "/api/replay", {}, (result) => {
  if (result.newly_covered) message("Update recorded for your saved fridge.");
}));
live.addEventListener("click", () => run(live, "/api/live", {}, (result) => {
  message(result.summary);
  if (result.match) {
    byId("message").append(document.createTextNode(" "), link(result.match.source, "Open newer notice ↗"));
  }
}));
api("/api/state").then(render).catch(() => message("Could not load your previous check. Start a new check to retry.", true));
