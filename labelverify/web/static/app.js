(function () {
  const form = document.getElementById("verify-form");
  if (!form) return;

  const samples = JSON.parse(document.getElementById("samples-data").textContent || "[]");
  const byId = Object.fromEntries(samples.map((s) => [s.id, s]));
  const sampleInput = document.getElementById("sample_id");
  const fileInput = document.getElementById("image");
  const preview = document.getElementById("preview");
  const dropHint = document.getElementById("drop-hint");
  const dropzone = document.getElementById("dropzone");
  const result = document.getElementById("result");
  const submitBtn = document.getElementById("submit-btn");
  const timer = document.getElementById("timer");

  function showPreview(src, label) {
    preview.src = src;
    preview.hidden = false;
    dropHint.textContent = label;
  }

  function fillForm(app) {
    for (const [key, value] of Object.entries(app)) {
      const el = form.elements[key];
      if (!el) continue;
      if (el.type === "checkbox") el.checked = Boolean(value);
      else el.value = value == null ? "" : value;
    }
  }

  document.querySelectorAll(".sample").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.sampleId;
      const sample = byId[id];
      if (!sample) return;
      sampleInput.value = id;
      fileInput.value = "";
      fillForm(sample.application);
      showPreview(`/samples/${id}/image`, `Sample: ${sample.title}. Choose a file to use your own image instead.`);
      document.querySelectorAll(".sample").forEach((b) => b.classList.toggle("selected", b === btn));
      form.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    sampleInput.value = "";
    document.querySelectorAll(".sample").forEach((b) => b.classList.remove("selected"));
    showPreview(URL.createObjectURL(file), file.name);
  });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("over"); })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("over"); })
  );
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change"));
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const started = performance.now();
    submitBtn.disabled = true;
    timer.hidden = false;
    result.innerHTML = '<div class="placeholder">Reading the label…</div>';
    const tick = setInterval(() => {
      timer.textContent = `${((performance.now() - started) / 1000).toFixed(1)} s`;
    }, 100);
    try {
      const resp = await fetch(form.action, { method: "POST", body: new FormData(form) });
      result.innerHTML = await resp.text();
    } catch (err) {
      result.innerHTML = `<div class="banner banner-error"><div class="banner-title">Request failed</div><div class="banner-meta">${err}</div></div>`;
    } finally {
      clearInterval(tick);
      timer.textContent = `${((performance.now() - started) / 1000).toFixed(1)} s`;
      submitBtn.disabled = false;
      result.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
})();
