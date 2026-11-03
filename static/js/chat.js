(function () {
  const fab = document.getElementById("chat-fab");
  if (!fab) return;
  const cfg = JSON.parse(document.getElementById("chat-config").textContent);
  const panel = document.getElementById("chat-panel"), msgs = document.getElementById("chat-messages");
  const form = document.getElementById("chat-form"), input = document.getElementById("chat-input");
  const suggest = document.getElementById("chat-suggest"), clearBtn = document.getElementById("chat-clear");
  let busy = false;

  function open() { panel.classList.add("open"); panel.setAttribute("aria-hidden", "false"); setTimeout(() => input.focus(), 250); msgs.scrollTop = msgs.scrollHeight; }
  function close() { panel.classList.remove("open"); panel.setAttribute("aria-hidden", "true"); fab.focus(); }
  fab.addEventListener("click", () => panel.classList.contains("open") ? close() : open());
  document.getElementById("chat-close").addEventListener("click", close);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && panel.classList.contains("open")) close(); });

  function bubble(kind, text) {
    const wrap = document.createElement("div"); wrap.className = "msg " + kind;
    const b = document.createElement("div"); b.className = "bubble"; b.textContent = text;
    wrap.appendChild(b); msgs.appendChild(wrap); msgs.scrollTop = msgs.scrollHeight; return b;
  }
  function addSources(b, sources) {
    if (!sources || !sources.length) return;
    const d = document.createElement("details"); d.className = "sources"; d.open = true;
    const s = document.createElement("summary"); s.textContent = "Source" + (sources.length > 1 ? "s" : ""); d.appendChild(s);
    sources.forEach(src => { const i = document.createElement("div"); i.className = "source-item"; i.innerHTML = src.html; d.appendChild(i); });
    b.appendChild(d);
  }
  async function ask(q) {
    q = (q || "").trim(); if (!q || busy) return;
    busy = true; bubble("user", q); input.value = "";
    const typing = bubble("bot", ""); typing.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
    try {
      const r = await fetch(cfg.url, { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrf, "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify({ question: q }) });
      let data = {}; try { data = await r.json(); } catch (e) {}
      if (r.status === 401 || (r.status === 403 && data.code === "AUTH_REQUIRED")) {
        typing.textContent = "Your session has expired. Redirecting to the login page...";
        setTimeout(() => { window.location = cfg.loginUrl + "?next=" + encodeURIComponent(location.pathname); }, 1500); return;
      }
      if (!r.ok) { typing.textContent = data.error || "Something went wrong. Please try again."; return; }
      typing.textContent = data.answer; addSources(typing, data.sources);
    } catch (e) { typing.textContent = "Could not reach the server. Please try again."; }
    finally { busy = false; msgs.scrollTop = msgs.scrollHeight; }
  }
  form.addEventListener("submit", e => { e.preventDefault(); ask(input.value); });
  suggest.addEventListener("click", e => { if (e.target.classList.contains("chip-suggest")) { open(); ask(e.target.textContent); } });
  if (clearBtn) clearBtn.addEventListener("click", async () => {
    if (!confirm("Clear this conversation?")) return;
    const r = await fetch(cfg.url, { method: "DELETE", credentials: "same-origin", headers: { "X-CSRFToken": cfg.csrf } });
    if (r.ok) { msgs.innerHTML = ""; bubble("bot", "Conversation cleared. Ask me anything about this document."); }
  });
})();
