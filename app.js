const byId = (id) => document.getElementById(id);
const form = byId("sticker");
const model = byId("model");
const serial = byId("serial");
const replay = byId("replay");
const live = byId("live");
let exampleVisible = false;
let savedNotifications;
const pushSupported = Boolean(window.isSecureContext && navigator.serviceWorker && window.PushManager && window.Notification);
const worker = pushSupported ? navigator.serviceWorker.register("/sw.js").catch(() => null) : Promise.resolve(null);
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
  if (!data.preview) {
    model.value = data.appliance.model;
    serial.value = data.appliance.serial;
    byId("watch-label").textContent = [model.value, serial.value].filter(Boolean).join(" / ");
    byId("device-card").hidden = false;
    renderDevice(data.notifications);
    renderWatch(data.watch);
  }
  const timeline = node("ol", "", "timeline");
  timeline.append(entry(notices.baseline, data.baseline), entry(notices.expansion, data.expansion, data.newly_covered));
  byId("results").replaceChildren(timeline);
  byId("progress").textContent = data.preview ? "Sample replay" : data.expansion ? "Replay complete" : "July result saved";
  renderProof(data);
  byId("actions").hidden = data.preview || Boolean(data.expansion);
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

  if (data.alert_example) {
    proof.append(renderExample(data.alert_example));
  }
  if (data.preview) {
    const alternate = node("button", data.appliance.serial === "A2401" ? "Back to A2210" : "Try A2401 instead", "quiet");
    alternate.type = "button";
    alternate.addEventListener("click", () => run(alternate, "/api/demo", {
      serial: data.appliance.serial === "A2401" ? "A2210" : "A2401",
    }));
    proof.append(alternate);
  }
}

function renderExample(alert) {
  const box = node("div", "", "alert-preview");
  box.append(node("span", "Device alert preview", "alert-caption"), node("b", alert.title), node("p", alert.body));
  const send = node("button", "Send example to this device", "primary");
  const feedback = node("p", exampleVisible ? "Example shown on this device." : "", "message");
  feedback.id = "example-message";
  feedback.setAttribute("role", "status");
  send.type = "button";
  if (pushSupported) {
    send.addEventListener("click", () => pushAction("example", send, feedback));
    box.append(send);
  } else {
    feedback.textContent = "Device alerts need a browser with Web Push. On iPhone, open the site from your Home Screen.";
  }
  box.append(feedback);
  return box;
}

function renderDevice(state) {
  savedNotifications = state;
  const available = pushSupported && Notification.permission !== "denied";
  byId("enable-push").hidden = state.enabled || !available;
  byId("disable-push").hidden = !state.enabled;
  const text = state.enabled
    ? "This device is subscribed for newly covered serials."
    : !pushSupported
      ? "Device alerts need a browser with Web Push. On iPhone, open the site from your Home Screen."
      : !available
        ? "Allow notifications in your browser’s site settings to enable alerts."
        : "Get a device alert if an official notice adds your exact code, even after you close this tab.";
  byId("device-status").textContent = text;
  const latest = byId("latest-alert");
  latest.hidden = !state.last_alert;
  latest.replaceChildren();
  if (state.last_alert) {
    latest.className = "watch";
    latest.append(node("h3", "Latest alert"), node("p", state.last_alert.body), link(state.last_alert.notice, "Read official notice ↗"));
    if (state.last_alert.remedy) latest.append(link(state.last_alert.remedy, "Open official refund instructions ↗"));
  }
}

async function pushAction(action, button, feedback) {
  button.disabled = true;
  feedback.classList.remove("error");
  feedback.textContent = "";
  try {
    let subscription;
    if (action !== "disable") {
      let permission = Notification.permission;
      if (permission === "default") permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("Allow notifications in your browser to continue.");
      if (!(await worker)) throw new Error("This browser could not start device alerts.");
      const registration = await navigator.serviceWorker.ready;
      subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        const { key } = await api("/api/push/key");
        const publicKey = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/")), (character) => character.charCodeAt(0));
        subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: publicKey });
      }
    }
    const result = await api("/api/push", { action, subscription });
    if (action === "example") {
      feedback.textContent = exampleVisible ? "Example shown on this device." : "Accepted by your browser’s push service. Look for the device notification.";
    } else {
      renderDevice(result);
    }
  } catch (error) {
    if (error.expired) {
      const registration = await navigator.serviceWorker.getRegistration();
      const stale = await registration?.pushManager.getSubscription();
      if (stale) await stale.unsubscribe().catch(() => {});
    }
    if (savedNotifications) renderDevice(savedNotifications);
    feedback.textContent = error.message;
    feedback.classList.add("error");
  } finally {
    button.disabled = false;
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
    box.append(node("small", "Last successful CPSC scan: " + date + " UTC"));
  }
  if (watch.before_range || watch.after_range) {
    const prior = watch.before_range?.join("–") || "No listed range";
    const current = watch.after_range?.join("–") || "No listed range";
    box.append(node("small", "Previous range " + prior + " · Current range " + current, "watch-delta"));
  }
  if (watch.source) box.append(link(watch.source, (watch.title || "Read the newer notice") + " ↗"));
  if (watch.remedy) box.append(link(watch.remedy, "Open official refund instructions ↗"));
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
  if (!response.ok) throw Object.assign(new Error(data.error || "Could not check the recall. Try again."), { expired: data.expired });
  return data;
}
async function run(button, path, payload, success) {
  document.querySelectorAll("button").forEach((item) => { item.disabled = true; });
  try {
    message(button === live ? "Checking the official CPSC feed…" : "");
    const result = await api(path, payload);
    if (result.appliance) {
      render(result);
      if (button.id === "run-demo" || button.id === "replay") {
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
  run(byId("check"), "/api/check", { model: model.value, serial: serial.value, current: true });
});
replay.addEventListener("click", () => run(replay, "/api/replay", {}, (result) => {
  if (result.recorded) message("Update recorded for your saved fridge.");
}));
live.addEventListener("click", async () => {
  const feedback = byId("device-message");
  feedback.classList.remove("error");
  feedback.textContent = "Checking the official CPSC feed…";
  live.disabled = true;
  try {
    const result = await api("/api/live", {});
    renderWatch(result);
    feedback.textContent = "";
  } catch (error) {
    feedback.textContent = error.message;
    feedback.classList.add("error");
  } finally {
    live.disabled = false;
  }
});
for (const action of ["enable", "disable"]) {
  const button = byId(action + "-push");
  button.addEventListener("click", () => pushAction(action, button, byId("device-message")));
}
if (pushSupported) {
  navigator.serviceWorker.addEventListener("message", ({ data }) => {
    if (data?.type !== "recall-notification") return;
    if (data.example) {
      exampleVisible = true;
      const feedback = byId("example-message");
      if (feedback) feedback.textContent = "Example shown on this device.";
    } else {
      api("/api/state").then((state) => {
        if (state.notifications) renderDevice(state.notifications);
      }).catch(() => {});
    }
  });
}
async function restore() {
  try {
    const state = await api("/api/state");
    render(state);
    const alert = new URLSearchParams(location.search).get("alert");
    if (alert === "example") {
      await run(byId("run-demo"), "/api/demo", {});
    } else if (alert === "recall") {
      byId("device-watch").scrollIntoView({ block: "start" });
    }
  } catch {
    message("Could not load your previous check. Save a label to retry.", true);
  }
}
restore();
