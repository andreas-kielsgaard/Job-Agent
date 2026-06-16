(() => {
  const COMPLETE_STATUSES = new Set(["completed", "complete", "pass", "success", "observed"]);
  const ACTIVE_STATUSES = new Set(["pending", "running", "warning", "empty", "skipped", "not_expected"]);
  const FAILED_STATUSES = new Set(["failed", "fail", "failing", "failure", "error"]);

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function statusClass(status) {
    const value = String(status || "").toLowerCase();
    if (COMPLETE_STATUSES.has(value)) return "complete";
    if (FAILED_STATUSES.has(value)) return "failed";
    if (ACTIVE_STATUSES.has(value)) return "active";
    return "";
  }

  function isExplicitRunning(value) {
    return value === true || value === "true" || value === 1 || value === "1";
  }

  function line(value, options = {}) {
    const running = isExplicitRunning(options.running);
    return `
      <span class="execution-line ${running ? "execution-running" : ""}">
        <span class="execution-spinner" aria-hidden="true"></span>
        <span>${escapeHtml(value)}</span>
      </span>
    `;
  }

  function stepClasses(status, options = {}) {
    return [statusClass(status), isExplicitRunning(options.running) ? "execution-running" : ""]
      .filter(Boolean)
      .join(" ");
  }

  function clearRunning(container) {
    const root = typeof container === "string" ? document.querySelector(container) : container;
    if (!root) return;
    root.querySelectorAll(".execution-running").forEach((element) => {
      element.classList.remove("execution-running");
    });
  }

  function setStepList(listSelector, options = {}) {
    const items = [...document.querySelectorAll(`${listSelector} li`)];
    const activeIndex = Number.isInteger(options.activeIndex) ? options.activeIndex : -1;
    const state = options.state || "";
    const running = isExplicitRunning(options.running);
    items.forEach((item, index) => {
      const classes = [];
      if (index < activeIndex) classes.push("complete");
      if (index === activeIndex && state) classes.push(state);
      if (index === activeIndex && running) classes.push("execution-running");
      item.className = classes.join(" ");
    });
  }

  function clearStepList(listSelector) {
    document.querySelectorAll(`${listSelector} li`).forEach((item) => {
      item.className = "";
    });
  }

  function finishStepList(listSelector, ok) {
    const items = [...document.querySelectorAll(`${listSelector} li`)];
    items.forEach((item, index) => {
      item.className = index === items.length - 1 ? (ok ? "complete" : "failed") : "complete";
    });
  }

  window.executionUi = {
    clearRunning,
    clearStepList,
    escapeHtml,
    finishStepList,
    line,
    setStepList,
    statusClass,
    stepClasses,
  };
})();
