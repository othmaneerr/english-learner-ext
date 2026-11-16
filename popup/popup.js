// Popup (Phase 3): يعرض النص المحدد ويطلب الشرح من الـ Backend حسب المستوى.

const API_URL = "http://localhost:8000";
const REQUEST_TIMEOUT_MS = 40000;

const $ = (id) => document.getElementById(id);
const els = {
  box: $("selection-box"),
  text: $("selected-text"),
  meta: $("meta"),
  hint: $("hint"),
  level: $("level"),
  explain: $("explain"),
  refresh: $("refresh"),
  status: $("status"),
  result: $("result"),
  kind: $("kind"),
  meaning: $("meaning"),
  explanation: $("explanation"),
  idiomBox: $("idiom-box"),
  idiomNote: $("idiom-note"),
  examples: $("examples"),
};

const HINTS = {
  none: "لم تحدّد أي نص بعد. حدّد كلمة أو جملة إنجليزية في الصفحة، ثم افتح الإضافة أو اضغط «تحديث التحديد».",
  restricted: "لا يمكن قراءة النص في هذه الصفحة (صفحات المتصفح الداخلية أو متجر الإضافات). جرّب موقعاً عادياً.",
  error: "تعذّر قراءة التحديد. أعد تحميل الصفحة ثم حاول مرة أخرى.",
};

let current = { text: "", context: "" };

// ---------- قراءة التحديد ----------

// تنفَّذ داخل الصفحة (كل الإطارات) — يجب أن تكون مستقلة بذاتها
function pageSelection() {
  const norm = (s) => s.replace(/\s+/g, " ").trim();
  const sel = window.getSelection();
  let text = sel ? sel.toString() : "";
  let context = "";

  if (text && sel.rangeCount) {
    let node = sel.getRangeAt(0).commonAncestorContainer;
    if (node.nodeType === 3) node = node.parentElement;
    const clean = norm(text);
    // اصعد حتى نجد نصاً محيطاً أطول من التحديد بوضوح
    while (node && node !== document.body && norm(node.textContent || "").length < clean.length + 60) {
      node = node.parentElement;
    }
    const block = norm((node && node.textContent) || "");
    const i = block.indexOf(clean);
    if (i >= 0) {
      const before = Math.max(block.lastIndexOf(". ", i), block.lastIndexOf("? ", i), block.lastIndexOf("! ", i));
      const start = before < 0 ? 0 : before + 2;
      const afters = [". ", "? ", "! "].map((p) => block.indexOf(p, i + clean.length)).filter((x) => x >= 0);
      const end = afters.length ? Math.min(...afters) + 1 : block.length;
      context = block.slice(start, end);
      if (context.length > 600) context = context.slice(0, 600);
    }
  }

  const el = document.activeElement;
  if (!text && el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT")) {
    try { text = el.value.substring(el.selectionStart, el.selectionEnd); } catch (_) {}
  }
  return { text: norm(text).slice(0, 500), context };
}

async function readSelection() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !/^https?:/i.test(tab.url || "")) {
    return { text: "", context: "", hint: "restricted" };
  }
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: pageSelection,
    });
    const found = results.map((r) => r.result).find((r) => r && r.text);
    return { text: found ? found.text : "", context: found ? found.context : "", hint: "none" };
  } catch (_) {
    return { text: "", context: "", hint: "error" };
  }
}

// ---------- العرض ----------

function setStatus(msg, isError = false) {
  els.status.textContent = msg;
  els.status.classList.toggle("error", isError);
}

function clearResult() {
  els.result.hidden = true;
  setStatus("");
}

function renderSelection(text, hintKey) {
  const has = Boolean(text);
  els.box.classList.toggle("empty", !has);
  els.text.textContent = has ? text : "";
  els.explain.disabled = !has;

  if (has) {
    const words = text.split(/\s+/).length;
    els.meta.textContent = words === 1 ? "كلمة واحدة" : `جملة / عبارة (${words} كلمات)`;
    els.hint.textContent = "";
    els.hint.classList.remove("warn");
  } else {
    els.meta.textContent = "";
    els.hint.textContent = HINTS[hintKey || "none"];
    els.hint.classList.toggle("warn", hintKey === "restricted" || hintKey === "error");
  }
}

function renderResult(data) {
  els.kind.textContent = data.kind;
  els.meaning.textContent = data.meaning;
  els.explanation.textContent = data.explanation;

  const hasIdiom = Boolean(data.idiom_note);
  els.idiomBox.hidden = !hasIdiom;
  els.idiomNote.textContent = hasIdiom ? data.idiom_note : "";

  els.examples.replaceChildren(
    ...(data.examples || []).map((ex) => {
      const li = document.createElement("li");
      const s = document.createElement("span");
      s.className = "ex-sentence";
      s.textContent = ex.sentence;
      li.append(s);
      if (ex.note) {
        const n = document.createElement("span");
        n.className = "ex-note";
        n.textContent = ex.note;
        li.append(n);
      }
      return li;
    })
  );
  els.result.hidden = false;
}

// ---------- الشرح ----------

async function explain() {
  if (!current.text) return;
  els.explain.disabled = true;
  els.result.hidden = true;
  setStatus("جارٍ الشرح…");

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(`${API_URL}/explain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: current.text,
        context: current.context,
        level: els.level.value,
      }),
      signal: ctrl.signal,
    });
    if (!res.ok) {
      let detail = "";
      try { detail = (await res.json()).detail || ""; } catch (_) {}
      throw new Error(detail || `خطأ من الخادم (${res.status})`);
    }
    renderResult(await res.json());
    setStatus("");
  } catch (e) {
    if (e.name === "AbortError") {
      setStatus("انتهت المهلة. حاول مرة أخرى.", true);
    } else if (e instanceof TypeError) {
      setStatus("تعذّر الاتصال بالخادم. تأكد أن الـ Backend يعمل على localhost:8000.", true);
    } else {
      setStatus(e.message, true);
    }
  } finally {
    clearTimeout(timer);
    els.explain.disabled = !current.text;
  }
}

// ---------- التشغيل ----------

async function update() {
  const { text, context, hint } = await readSelection();
  if (text !== current.text) clearResult();
  current = { text, context };
  renderSelection(text, hint);
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "SELECTION_CHANGED") update();
});

els.level.addEventListener("change", () => {
  chrome.storage.local.set({ level: els.level.value });
});
els.explain.addEventListener("click", explain);
els.refresh.addEventListener("click", update);

(async () => {
  const { level } = await chrome.storage.local.get("level");
  if (level) els.level.value = level;
  update();
})();
