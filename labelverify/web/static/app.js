/* LabelVerify client behaviour. No framework: a few declarative hooks.
   data-async            form posts via fetch and swaps HTML into data-target
   data-fetch            button POSTs to a URL and swaps HTML into data-target
   data-poll             element refreshes itself from a URL until a header condition is met
   data-toggle           button shows/hides a target
   data-confirm          confirm() before submit
   data-href             clickable table rows
   data-viewer           zoomable label image
   #wizard               the three-step new application flow */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  function busy(target, text) {
    target.innerHTML = `<div class="busy-note"><span class="spinner"></span><span>${text || "Working…"}</span></div>`;
  }

  async function swap(target, response) {
    const html = await response.text();
    target.innerHTML = html;
    wire(target);
    return response;
  }

  async function asyncForm(form) {
    const target = $(form.dataset.target);
    const submitBtn = form.querySelector('[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    const previous = target ? target.innerHTML : "";
    if (target && form.dataset.busy) busy(target, form.dataset.busy);
    try {
      const resp = await fetch(form.action, { method: form.method || "post", body: new FormData(form), headers: { "X-Partial": "1" } });
      if (target) await swap(target, resp);
      form.dispatchEvent(new CustomEvent("async:done", { bubbles: true, detail: { ok: resp.ok, status: resp.status } }));
      if (resp.ok && form.matches(".comment-form")) form.reset();
    } catch (err) {
      if (target) target.innerHTML = `<div class="alert alert-danger">Request failed: ${err}</div>` + previous;
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  function wire(root) {
    root = root || document;

    $$("form[data-async]", root).forEach((form) => {
      if (form.dataset.wired) return;
      form.dataset.wired = "1";
      form.addEventListener("submit", (e) => { e.preventDefault(); asyncForm(form); });
    });

    $$("[data-fetch]", root).forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", async () => {
        const target = $(btn.dataset.target);
        btn.disabled = true;
        if (target && btn.dataset.busy) busy(target, btn.dataset.busy);
        try {
          const resp = await fetch(btn.dataset.fetch, { method: "post", headers: { "X-Partial": "1" } });
          if (target) await swap(target, resp);
          btn.dispatchEvent(new CustomEvent("fetch:done", { bubbles: true, detail: { ok: resp.ok } }));
        } finally {
          btn.disabled = false;
        }
      });
    });

    $$("[data-toggle]", root).forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", () => {
        const target = $(btn.dataset.toggle);
        if (target) target.hidden = !target.hidden;
      });
    });

    $$("[data-confirm]", root).forEach((el) => {
      if (el.dataset.wired) return;
      el.dataset.wired = "1";
      el.addEventListener("click", (e) => { if (!window.confirm(el.dataset.confirm)) e.preventDefault(); });
    });

    $$("tr[data-href]", root).forEach((row) => {
      if (row.dataset.wired) return;
      row.dataset.wired = "1";
      row.addEventListener("click", (e) => {
        if (e.target.closest("a, button, input, label, form")) return;
        window.location.href = row.dataset.href;
      });
    });

    $$("[data-dismiss]", root).forEach((btn) => btn.addEventListener("click", () => btn.parentElement.remove()));

    $$("[data-check-all]", root).forEach((all) => {
      const form = all.closest("form");
      const boxes = () => $$('input[name="ids"]', form);
      const update = () => {
        const n = boxes().filter((b) => b.checked).length;
        $$("[data-check-count]", form).forEach((el) => (el.textContent = n));
        $$("[data-needs-selection]", form).forEach((el) => (el.disabled = n === 0));
      };
      all.addEventListener("change", () => { boxes().forEach((b) => (b.checked = all.checked)); update(); });
      boxes().forEach((b) => b.addEventListener("change", update));
      update();
    });

    $$("[data-viewer]", root).forEach((viewer) => {
      if (viewer.dataset.wired) return;
      viewer.dataset.wired = "1";
      const img = $("[data-viewer-img]", viewer);
      if (!img) return;
      const toggle = () => viewer.classList.toggle("zoomed");
      img.addEventListener("click", toggle);
      const zoomBtn = $("[data-viewer-toggle]", viewer);
      if (zoomBtn) zoomBtn.addEventListener("click", toggle);
      viewer.addEventListener("mousemove", (e) => {
        if (!viewer.classList.contains("zoomed")) return;
        const r = viewer.getBoundingClientRect();
        img.style.transformOrigin = `${((e.clientX - r.left) / r.width) * 100}% ${((e.clientY - r.top) / r.height) * 100}%`;
      });
      const caption = $("[data-viewer-caption]", viewer);
      $$("[data-viewer-src]").forEach((thumb) =>
        thumb.addEventListener("click", () => {
          img.src = thumb.dataset.viewerSrc;
          viewer.classList.remove("zoomed");
          if (caption && thumb.dataset.viewerLabel) caption.textContent = thumb.dataset.viewerLabel;
          $$("[data-viewer-src]").forEach((t) => t.classList.toggle("active", t === thumb));
        })
      );
    });

    $$("[data-poll]", root).forEach((el) => {
      if (el.dataset.wired) return;
      el.dataset.wired = "1";
      const [header, value] = (el.dataset.pollUntil || "").split(":").map((s) => s.trim());
      const interval = parseInt(el.dataset.pollInterval || "2000", 10);
      const tick = async () => {
        try {
          const resp = await fetch(el.dataset.poll, { headers: { "X-Partial": "1" } });
          const html = await resp.text();
          el.innerHTML = html;
          wire(el);
          if (header && resp.headers.get(header) === value) return;
        } catch (err) { /* keep polling */ }
        setTimeout(tick, interval);
      };
      setTimeout(tick, interval);
    });

    $$(".demo-user", root).forEach((btn) =>
      btn.addEventListener("click", () => {
        $("#email").value = btn.dataset.email;
        $("#password").value = $(".auth-demo-title code").textContent;
        $("#password").focus();
      })
    );
  }

  function wizard() {
    const root = $("#wizard");
    if (!root) return;
    const uploadForm = $("#upload-form");
    const fileInput = $("#image");
    const dropzone = $("#dropzone");
    const preview = $("#upload-preview");
    const sampleInput = $("#sample_id");
    const status = $("#upload-status");
    const detailsForm = $("#details-form");
    const submitForm = $("#submit-form");
    const submitBtn = $("#submit-btn");
    let applicationId = null;

    function showPreviews(items) {
      preview.innerHTML = "";
      items.forEach(({ src, label }) => {
        const fig = document.createElement("figure");
        fig.innerHTML = `<img alt=""><figcaption></figcaption>`;
        fig.querySelector("img").src = src;
        fig.querySelector("figcaption").textContent = label;
        preview.appendChild(fig);
      });
      preview.hidden = items.length === 0;
    }
    const PANELS = ["Front", "Back", "Neck", "Panel 4"];

    function pickSample(btn) {
      sampleInput.value = btn.dataset.sampleId;
      fileInput.value = "";
      $$(".sample").forEach((b) => b.classList.toggle("selected", b === btn));
      showPreviews([{ src: btn.querySelector("img").src, label: "Sample" }]);
      const app = JSON.parse(btn.dataset.application || "{}");
      if (app.beverage_type) $("#beverage_type_1").value = app.beverage_type;
      status.textContent = `Sample selected: ${btn.textContent.trim()}`;
    }
    $$(".sample").forEach((btn) => btn.addEventListener("click", () => pickSample(btn)));

    fileInput.addEventListener("change", () => {
      const files = Array.from(fileInput.files || []);
      if (!files.length) return;
      if (files.length > 4) { status.textContent = "Choose at most 4 images."; fileInput.value = ""; return; }
      sampleInput.value = "";
      $$(".sample").forEach((b) => b.classList.remove("selected"));
      showPreviews(files.map((f, i) => ({ src: URL.createObjectURL(f), label: `${PANELS[i]} · ${f.name}` })));
      status.textContent = files.length === 1 ? files[0].name : `${files.length} images selected`;
    });
    ["dragenter", "dragover"].forEach((evt) => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("over"); }));
    ["dragleave", "drop"].forEach((evt) => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("over"); }));
    dropzone.addEventListener("drop", (e) => {
      if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; fileInput.dispatchEvent(new Event("change")); }
    });

    uploadForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!fileInput.files.length && !sampleInput.value) { status.textContent = "Choose a label image or a sample first."; return; }
      const btn = $("#upload-btn");
      btn.disabled = true;
      status.innerHTML = '<span class="spinner"></span> Reading the label…';
      const started = performance.now();
      try {
        const resp = await fetch(uploadForm.action, { method: "post", body: new FormData(uploadForm), headers: { Accept: "application/json" } });
        const data = await resp.json();
        if (!resp.ok) { status.textContent = data.detail || "Upload failed."; return; }
        applicationId = data.id;
        const secs = ((performance.now() - started) / 1000).toFixed(1);
        status.textContent = `Read in ${secs} s · ${data.serial}`;
        // Fill step 2.
        $("#beverage_type").value = $("#beverage_type_1").value;
        for (const [key, value] of Object.entries(data.prefill || {})) {
          const el = detailsForm.elements[key];
          if (el) el.value = value;
        }
        if (data.prefill && data.prefill.country_of_origin) $("#is_import").checked = true;
        $("#prefill-hint").textContent = data.warning ? data.warning : "Filled from the label. Check every value against your application.";
        detailsForm.action = `/applicant/applications/${applicationId}/precheck`;
        submitForm.action = `/applicant/applications/${applicationId}/submit`;
        $("#step-1").classList.add("step-done");
        $("#step-2").classList.remove("step-locked");
        $("#step-3").classList.remove("step-locked");
        submitBtn.disabled = false;
        $("#step-2").scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (err) {
        status.textContent = `Upload failed: ${err}`;
      } finally {
        btn.disabled = false;
      }
    });

    detailsForm.addEventListener("async:done", (e) => {
      if (e.detail.ok) {
        $("#step-2").classList.add("step-done");
        $("#step-3").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });

    submitForm.addEventListener("submit", () => {
      // Carry the latest field values along with the submit.
      new FormData(detailsForm).forEach((value, key) => {
        if (submitForm.elements[key]) return;
        const input = document.createElement("input");
        input.type = "hidden"; input.name = key; input.value = value;
        submitForm.appendChild(input);
      });
    });
  }

  function decisionPanel() {
    const host = $("#notice-host");
    if (!host) return;
    document.addEventListener("fetch:done", () => {
      const ready = !!$("#notice-host textarea");
      $("#send-correction").disabled = !ready;
    });
  }

  document.addEventListener("DOMContentLoaded", () => { wire(document); wizard(); decisionPanel(); });
})();
