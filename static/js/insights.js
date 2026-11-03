(function () {
  const csrf = (document.getElementById("chat-config") ? JSON.parse(document.getElementById("chat-config").textContent).csrf : "") ||
    (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";
  const H = { "Content-Type": "application/json", "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" };
  function busy(btn, on, label) { btn.disabled = on; btn.classList.toggle("btn-spin", on); if (label) btn.firstChild.textContent = label; }

  const regen = document.getElementById("btn-regenerate");
  if (regen) regen.addEventListener("click", async () => {
    busy(regen, true);
    const r = await fetch(regen.dataset.url, { method: "POST", credentials: "same-origin", headers: H });
    if (r.ok) location.reload(); else { busy(regen, false); alert("Could not generate insights."); }
  });

  const tr = document.getElementById("btn-translate");
  if (tr) tr.addEventListener("click", async () => {
    const err = document.getElementById("translate-error"); err.classList.add("d-none");
    busy(tr, true);
    try {
      const r = await fetch(tr.dataset.url, { method: "POST", credentials: "same-origin", headers: H,
        body: JSON.stringify({ target: tr.dataset.target, force: tr.textContent.includes("again") }) });
      const d = await r.json();
      if (!r.ok) { err.textContent = d.error || "Translation failed."; err.classList.remove("d-none"); }
      else { document.getElementById("tr-source").textContent = d.source_text; document.getElementById("tr-target").textContent = d.text; tr.firstChild.textContent = "Translate again"; }
    } catch (e) { err.textContent = "Could not reach the server."; err.classList.remove("d-none"); }
    busy(tr, false);
  });

  const tagForm = document.getElementById("tag-form");
  if (tagForm) tagForm.addEventListener("submit", async e => {
    e.preventDefault(); const name = tagForm.name.value.trim(); if (!name) return;
    const r = await fetch(tagForm.dataset.url, { method: "POST", credentials: "same-origin", headers: H, body: JSON.stringify({ name }) });
    if (r.ok) location.reload();
  });
  document.querySelectorAll("[data-remove-tag]").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(b.dataset.removeTag, { method: "DELETE", credentials: "same-origin", headers: H });
    if (r.ok) b.closest(".chip").remove();
  }));
})();
