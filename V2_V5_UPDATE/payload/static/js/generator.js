(function () {
  const form = document.getElementById("gen-form");
  if (!form) return;
  const frame = document.getElementById("gen-preview"), state = document.getElementById("preview-state");
  const totals = document.getElementById("totals");
  const template = form.querySelector("[name=template]").value;
  let timer = null, seq = 0;

  function showErrors(errors) {
    form.querySelectorAll(".field-error").forEach(e => { e.textContent = ""; e.className = "field-error small text-danger"; });
    Object.entries(errors || {}).forEach(([k, v]) => { const el = form.querySelector('.field-error[data-for="' + k + '"]'); if (el) el.textContent = v; });
  }
  async function preview() {
    const mine = ++seq; state.textContent = "Updating...";
    try {
      const r = await fetch(form.dataset.preview, { method: "POST", body: new FormData(form), credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (r.status === 401 || r.redirected) { state.textContent = "Session expired: please log in again."; return; }
      const d = await r.json(); if (mine !== seq) return;
      if (d.ok) {
        frame.srcdoc = d.html; showErrors({}); state.textContent = "Up to date";
        if (totals && d.totals) totals.textContent = "Subtotal " + d.totals.subtotal + " | GST " + d.totals.tax + " | Total " + d.totals.total + " | " + d.totals.words;
      } else { showErrors(d.errors); state.textContent = "Fix the highlighted fields"; if (totals) totals.textContent = ""; }
    } catch (e) { state.textContent = "Preview unavailable"; }
  }
  const schedule = () => { clearTimeout(timer); timer = setTimeout(preview, 450); };
  form.addEventListener("input", schedule);
  form.addEventListener("change", schedule);

  const items = document.getElementById("items");
  if (items) {
    document.getElementById("add-item").addEventListener("click", () => {
      const row = items.querySelector(".item-row").cloneNode(true);
      row.querySelectorAll("input").forEach(i => { i.value = i.name === "item_qty" ? "1" : ""; });
      items.appendChild(row); row.querySelector("input").focus(); schedule();
    });
    items.addEventListener("click", e => {
      if (e.target.classList.contains("item-del") && items.querySelectorAll(".item-row").length > 1) { e.target.closest(".item-row").remove(); schedule(); }
    });
  }

  const pre = document.getElementById("prefill-select");
  if (pre) pre.addEventListener("change", async () => {
    if (!pre.value) return;
    const r = await fetch("/generator/prefill/" + pre.value + "/?template=" + template, { credentials: "same-origin" });
    if (!r.ok) return;
    const d = await r.json();
    Object.entries(d.fields || {}).forEach(([k, v]) => { const el = form.querySelector('[name="' + k + '"]'); if (el) el.value = v; });
    schedule();
  });
  form.addEventListener("submit", e => { const b = e.submitter; if (b) { b.classList.add("btn-spin"); setTimeout(() => b.classList.remove("btn-spin"), 2500); } });
  preview();
})();
