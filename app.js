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
const noticeNumbers = { baseline: "25-395", expansion: "26-199" };
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
    const number = notice === notices.baseline ? noticeNumbers.baseline : noticeNumbers.expansion;
    item.append(link(result.source, "CPSC notice " + number + " ↗"));
    if (result.status === "covered" && !newlyCovered) {
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
  renderProof(data);
  renderWatch(data.watch);
  byId("actions").hidden = false;
  replay.hidden = !!data.expansion;
  live.hidden = !data.expansion;
}
function renderProof(data) {
  const proof = byId("proof");
  proof.hidden = !data.expansion;
  proof.replaceChildren();
  if (!data.expansion) return;

  const { model: savedModel, serial: savedSerial } = data.appliance;
  const states = {
    newly_covered: ["Newly covered", "January added your model and your code falls in the new range."],
    already_covered: ["The July recall still applies", "The January expansion does not replace your July result."],
    needs_serial: ["Serial code needed", "Check the rear label against the code format in the official notice."],
    no_new_match: ["No new match in this replay", "Review each notice below for its exact coverage."],
  };
  const [title, detail] = states[data.change];
  proof.className = "proof " + (data.newly_covered ? "proof-action" : "");
  const label = node("span", savedModel + (savedSerial ? " / " + savedSerial : ""), "proof-label");
  proof.append(label, node("strong", title), node("p", detail));

  if (data.control) {
    const comparison = node("div", "", "comparison");
    for (const [code, verdict, style] of [
      [savedSerial, verdictTitles[data.expansion.status], data.newly_covered ? "affected" : ""],
      ["A2401", verdictTitles[data.control.status], data.control.status === "covered" ? "affected" : ""],
    ]) {
      const tile = node("div", "", "comparison-label " + style);
      tile.append(node("span", code), node("strong", verdict));
      comparison.append(tile);
    }
    proof.append(comparison);
  }

  if (data.newly_covered) {
    proof.append(node("blockquote", "\u201c" + data.expansion.quote + "\u201d"));
    const copy = node("button", "Copy decision receipt", "quiet");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      const receipt = [
        "reacalcul — historical CPSC replay",
        "Saved label: " + savedModel + " / " + savedSerial,
        "July 24, 2025: " + data.baseline.reason,
        data.baseline.source,
        "January 15, 2026: " + data.expansion.reason,
        "Quoted rule: " + data.expansion.quote,
        data.expansion.source,
        "Manufacturer refund instructions: https://www.recallrtr.com/minifridge",
      ].join("\n");
      try {
        await navigator.clipboard.writeText(receipt);
        copy.textContent = "Decision receipt copied";
      } catch {
        message("Clipboard unavailable. The official source links are below.", true);
      }
    });
    const actions = node("div", "", "proof-actions");
    actions.append(copy, link("https://www.recallrtr.com/minifridge", "Open official refund instructions ↗"));
    proof.append(actions);
  }
}

function renderWatch(watch) {
  const box = byId("watch");
  box.hidden = !watch;
  box.replaceChildren();
  if (!watch) return;
  box.append(node("h3", "Current CPSC watch"));
  box.append(node("p", watch.summary));
  if (watch.checked_at) {
    const stamp = new Date(watch.checked_at);
    const date = new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(stamp);
    box.append(node("small", "Last successful daily scan: " + date + " UTC"));
  }
  if (watch.before_range || watch.after_range) {
    const prior = watch.before_range?.join("–") || "No listed range";
    const current = watch.after_range?.join("–") || "No listed range";
    box.append(node("small", "Previous range " + prior + " · Current range " + current, "watch-delta"));
  }
  if (watch.source) box.append(link(watch.source, (watch.title || "Read the newer notice") + " ↗"));
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
    if (result.appliance) {
      render(result);
      if (button.id === "run-demo" || matchMedia("(max-width: 780px)").matches) {
        const behavior = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
        byId("history-panel").scrollIntoView({ block: "start", behavior });
      }
    }
    if (success) success(result);
  } catch (error) {
    message(error.message, true);
  } finally {
    document.querySelectorAll("button").forEach((item) => { item.disabled = false; });
  }
}
byId("run-demo").addEventListener("click", () => run(byId("run-demo"), "/api/demo", {}));
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
  if (result.recorded) message("Update recorded for your saved fridge.");
}));
live.addEventListener("click", () => run(live, "/api/live", {}, (result) => {
  message("");
  renderWatch(result);
}));
api("/api/state").then(render).catch(() => message("Could not load your previous check. Start a new check to retry.", true));
