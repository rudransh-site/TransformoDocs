(function () {
  const cfg = JSON.parse(document.getElementById("upload-config").textContent);
  const zone = document.getElementById("dropzone"), input = document.getElementById("file-input");
  const form = document.getElementById("upload-form"), btn = document.getElementById("upload-btn");
  const info = document.getElementById("file-info"), alertBox = document.getElementById("upload-alert");
  const csrf = form.querySelector("[name=csrfmiddlewaretoken]").value;
  let chosen = null;

  function show(html, kind) {
    alertBox.className = "alert alert-" + kind;
    alertBox.innerHTML = html;
    alertBox.classList.remove("d-none");
  }
  function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  function validate(file) {
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!cfg.extensions.includes(ext)) return "Unsupported file type. Please upload PDF, DOCX, PNG or JPG.";
    if (file.size === 0) return "This file is empty.";
    if (file.size > cfg.maxBytes) return "File is " + (file.size / 1048576).toFixed(1) + " MB. Maximum is " + cfg.maxMb + " MB.";
    return null;
  }
  function pick(file) {
    alertBox.classList.add("d-none");
    const err = file ? validate(file) : null;
    if (err) { chosen = null; btn.disabled = true; info.textContent = ""; show(esc(err), "danger"); return; }
    chosen = file;
    btn.disabled = !file;
    info.textContent = file ? file.name + " (" + (file.size / 1024).toFixed(0) + " KB)" : "";
  }
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", () => pick(input.files[0]));
  ["dragenter", "dragover"].forEach(e => zone.addEventListener(e, ev => { ev.preventDefault(); zone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach(e => zone.addEventListener(e, ev => { ev.preventDefault(); zone.classList.remove("dragover"); }));
  zone.addEventListener("drop", ev => { if (ev.dataTransfer.files.length) pick(ev.dataTransfer.files[0]); });

  async function send(force) {
    if (!chosen) return;
    btn.disabled = true; btn.textContent = "Uploading...";
    const fd = new FormData();
    fd.append("file", chosen);
    if (force) fd.append("force", "1");
    const lang = document.getElementById("ocr-language"), hw = document.getElementById("handwriting");
    if (lang) fd.append("ocr_language", lang.value);
    if (hw && hw.checked) fd.append("handwriting", "1");
    try {
      const resp = await fetch(cfg.apiUrl, { method: "POST", body: fd, credentials: "same-origin",
        headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest" } });
      let data = {};
      try { data = await resp.json(); } catch (e) {}
      if (resp.status === 201) { window.location = data.status_url; return; }
      if (resp.status === 401 || (resp.status === 403 && data.code === "AUTH_REQUIRED")) {
        show("Your session has expired. Redirecting to the login page...", "warning");
        setTimeout(() => { window.location = cfg.loginUrl + "?next=" + encodeURIComponent(location.pathname); }, 1800);
        return;
      }
      if (resp.status === 409 && data.code === "DUPLICATE") {
        let html = esc(data.error) + ' <a href="' + data.existing.detail_url + '">Open existing</a>';
        if (data.can_retry) html += ' &middot; the previous attempt failed: open it and press Retry.';
        if (data.can_force) html += ' &middot; <a href="#" id="force-link">Upload as a new copy</a>';
        show(html, "warning");
        const fl = document.getElementById("force-link");
        if (fl) fl.addEventListener("click", e => { e.preventDefault(); send(true); });
      } else if (resp.status === 503) {
        show(esc(data.error || "The system is temporarily unavailable. Please try again in a minute."), "danger");
      } else {
        show(esc(data.error || "Upload failed. Please try again."), "danger");
      }
    } catch (e) {
      show("Could not reach the server. Check that it is running and try again.", "danger");
    }
    btn.disabled = false; btn.textContent = "Upload and process";
  }
  form.addEventListener("submit", ev => { ev.preventDefault(); send(false); });
})();
