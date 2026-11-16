// Content script: يلتقط النص المحدد في الصفحة ويرسله إلى الـ Extension.

const MAX_LENGTH = 500; // حد أقصى لطول النص المحدد

function getSelectedText() {
  const sel = window.getSelection();
  let text = sel ? sel.toString() : "";

  // النص داخل input/textarea لا يظهر عبر getSelection
  const el = document.activeElement;
  if (!text && el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT")) {
    try {
      text = el.value.substring(el.selectionStart, el.selectionEnd);
    } catch (_) {
      /* بعض أنواع input لا تدعم selection */
    }
  }

  return text.replace(/\s+/g, " ").trim().slice(0, MAX_LENGTH);
}

// 1) الرد على طلب الـ popup
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "GET_SELECTION") {
    sendResponse({ text: getSelectedText() });
  }
  // رد متزامن، لا حاجة لإبقاء القناة مفتوحة
});

// 2) دفع التحديثات تلقائياً عند تغيّر التحديد (مع debounce)
let timer = null;
document.addEventListener("selectionchange", () => {
  clearTimeout(timer);
  timer = setTimeout(() => {
    const text = getSelectedText();
    // الرسالة تفشل بصمت إن لم يكن الـ popup مفتوحاً
    chrome.runtime
      .sendMessage({ type: "SELECTION_CHANGED", text })
      .catch(() => {});
  }, 150);
});
