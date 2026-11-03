(function () {
  const cfg = JSON.parse(document.getElementById("status-config").textContent);
  const bar = document.getElementById("bar"), msg = document.getElementById("msg"), steps = document.getElementById("steps");
  const warn = document.getElementById("warn");
  let finished = false, ticks = 0;

  function render(p) {
    bar.style.width = p.progress_percent + "%";
    bar.textContent = p.progress_percent + "%";
    msg.textContent = p.progress_message || p.status_label;
    steps.innerHTML = p.steps.map(s => {
      const dot = s.state === "done" ? "&#10003;" : s.state === "active" ? "&#9679;" : s.state === "failed" ? "&#10007;" : "&#9675;";
      return '<div class="step ' + s.state + '"><span class="dot">' + dot + "</span>" + s.label + "</div>";
    }).join("");
    if (p.is_stuck) { warn.classList.remove("d-none"); }
  }
  function authExpired() {
    warn.className = "alert alert-warning";
    warn.textContent = "Your session has expired. Redirecting to the login page...";
    setTimeout(() => { window.location = cfg.loginUrl + "?next=" + encodeURIComponent(location.pathname); }, 1800);
  }
  async function poll() {
    if (finished) return;
    try {
      const r = await fetch(cfg.statusUrl, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (r.status === 401 || r.status === 403) return authExpired();
      if (r.ok) {
        const p = await r.json();
        render(p);
        if (p.is_terminal) { finished = true; setTimeout(() => { window.location = p.redirect_url; }, 600); return; }
      }
    } catch (e) {}
    ticks++;
    setTimeout(poll, 1000);
  }
  async function kick() {
    if (!cfg.canProcess) return;
    try {
      const r = await fetch(cfg.processUrl, { method: "POST", credentials: "same-origin",
        headers: { "X-CSRFToken": cfg.csrf, "X-Requested-With": "XMLHttpRequest" } });
      if (r.status === 401 || (r.status === 403)) return authExpired();
      if (r.ok) { const p = await r.json(); render(p); if (p.is_terminal) { finished = true; window.location = p.redirect_url; } }
    } catch (e) { /* the poller keeps going */ }
  }
  kick();
  poll();
})();
