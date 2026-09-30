/* LabelVerify client behaviour. No framework: a few declarative hooks.
   data-async            form posts via fetch and swaps HTML into data-target
   data-fetch            button POSTs to a URL and swaps HTML into data-target
   data-poll             element refreshes itself from a URL until a header condition is met
   data-toggle           button shows/hides a target
   data-confirm          confirm() before submit
   data-href             clickable table rows
   data-viewer           zoomable label image with panel thumbnails
   data-file-picker      multi-image chooser that accumulates files, with remove/reorder
   data-theme-toggle     light / dark switch
   #wizard               the three-step new application flow */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // ---------------------------------------------------------------- theme
  const theme = {
    current() {
      const set = document.documentElement.dataset.theme;
      if (set) return set;
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    },
    apply(name) {
      document.documentElement.dataset.theme = name;
      try { localStorage.setItem("lv-theme", name); } catch (e) { /* private mode */ }
    },
    toggle() { theme.apply(theme.current() === "dark" ? "light" : "dark"); },
  };

  // ---------------------------------------------------------------- helpers
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

  // ---------------------------------------------------------------- file picker
  // A native <input type="file"> forgets its previous selection every time the dialog
  // opens. The picker keeps its own list, syncs it back into the input (so plain form
  // posts still work), and renders thumbnails with remove and reorder controls.
  const PANEL_NAMES = ["Front", "Back", "Neck", "Panel 4", "Panel 5", "Panel 6"];

  function filePicker(root) {
    const input = $('input[type="file"]', root);
    const list = $("[data-file-list]", root);
    const hint = $("[data-file-hint]", root);
    const max = parseInt(root.dataset.max || "4", 10);
    let files = [];

    const key = (f) => `${f.name}|${f.size}|${f.lastModified}`;

    function sync() {
      const dt = new DataTransfer();
      files.forEach((f) => dt.items.add(f));
      input.files = dt.files;
      render();
      root.dispatchEvent(new CustomEvent("picker:change", { bubbles: true, detail: { count: files.length } }));
    }

    function render() {
      list.innerHTML = "";
      list.hidden = files.length === 0;
      files.forEach((file, i) => {
        const fig = document.createElement("figure");
        fig.className = "file-item";
        fig.innerHTML =
          `<img alt="">` +
          `<div class="file-move"><button type="button" title="Move earlier" ${i === 0 ? "disabled" : ""} data-move="-1">&larr;</button>` +
          `<button type="button" title="Move later" ${i === files.length - 1 ? "disabled" : ""} data-move="1">&rarr;</button></div>` +
          `<button type="button" class="file-remove" title="Remove this image"><svg class="icon"><use href="#i-x"></use></svg></button>` +
          `<figcaption><strong></strong> · <span></span></figcaption>`;
        fig.querySelector(".file-remove").setAttribute("aria-label", `Remove ${file.name}`);
        fig.querySelector("figcaption strong").textContent = PANEL_NAMES[i] || `Panel ${i + 1}`;
        fig.querySelector("figcaption span").textContent = file.name;
        const img = fig.querySelector("img");
        img.src = URL.createObjectURL(file);
        img.onload = () => URL.revokeObjectURL(img.src);
        fig.querySelector(".file-remove").addEventListener("click", () => { files.splice(i, 1); sync(); });
        $$("[data-move]", fig).forEach((btn) =>
          btn.addEventListener("click", () => {
            const j = i + parseInt(btn.dataset.move, 10);
            if (j < 0 || j >= files.length) return;
            [files[i], files[j]] = [files[j], files[i]];
            sync();
          })
        );
        list.appendChild(fig);
      });
      if (hint) {
        hint.textContent = files.length
          ? `${files.length} of ${max} images. The first is treated as the front label; use the arrows to reorder.`
          : "";
      }
    }

    input.addEventListener("change", () => {
      const picked = Array.from(input.files || []);
      const known = new Set(files.map(key));
      const fresh = picked.filter((f) => !known.has(key(f)));
      const room = Math.max(max - files.length, 0);
      if (fresh.length > room) {
        alert(`You can attach at most ${max} images. Only the first ${room} of the new ones were added.`);
      }
      files = files.concat(fresh.slice(0, room));
      sync();
    });

    const dropzone = $(".dropzone", root);
    if (dropzone) {
      ["dragenter", "dragover"].forEach((evt) => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("over"); }));
      ["dragleave", "drop"].forEach((evt) => dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("over"); }));
      dropzone.addEventListener("drop", (e) => {
        if (!e.dataTransfer.files.length) return;
        const dt = new DataTransfer();
        Array.from(e.dataTransfer.files).forEach((f) => dt.items.add(f));
        input.files = dt.files;
        input.dispatchEvent(new Event("change"));
      });
    }

    const api = {
      get files() { return files.slice(); },
      clear() { files = []; sync(); },
    };
    root.picker = api;
    return api;
  }

  // ---------------------------------------------------------------- wiring
  function wire(root) {
    root = root || document;

    $$("[data-theme-toggle]", root).forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", theme.toggle);
    });

    $$("[data-file-picker]", root).forEach((el) => {
      if (el.dataset.wired) return;
      el.dataset.wired = "1";
      filePicker(el);
    });

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
        // Expanding a thread counts as reading it: the "new" dot on the toggle goes away.
        if (btn.classList.contains("has-new")) {
          btn.classList.remove("has-new");
          $$(".unread-dot", btn).forEach((d) => d.remove());
          btn.title = "Comments on this field";
        }
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
        // The queue table sits inside the bulk-approve form, so only real controls opt out.
        if (e.target.closest("a, button, input, select, textarea, label, .check-col")) return;
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

  // ---------------------------------------------------------------- wizard
  function wizard() {
    const root = $("#wizard");
    if (!root) return;
    const uploadForm = $("#upload-form");
    const pickerEl = $("[data-file-picker]", uploadForm);
    const picker = pickerEl.picker;
    const sampleInput = $("#sample_id");
    const samplePreview = $("#sample-preview");
    const status = $("#upload-status");
    const detailsForm = $("#details-form");
    const submitForm = $("#submit-form");
    const submitBtn = $("#submit-btn");
    let applicationId = null;

    function pickSample(btn) {
      sampleInput.value = btn.dataset.sampleId;
      picker.clear();
      $$(".sample").forEach((b) => b.classList.toggle("selected", b === btn));
      samplePreview.hidden = false;
      $("img", samplePreview).src = btn.querySelector("img").src;
      $("span", samplePreview).textContent = `Sample: ${btn.textContent.trim()}`;
      const app = JSON.parse(btn.dataset.application || "{}");
      if (app.beverage_type) $("#beverage_type_1").value = app.beverage_type;
      status.textContent = "";
    }
    $$(".sample").forEach((btn) => btn.addEventListener("click", () => pickSample(btn)));

    pickerEl.addEventListener("picker:change", (e) => {
      if (e.detail.count > 0) {
        sampleInput.value = "";
        samplePreview.hidden = true;
        $$(".sample").forEach((b) => b.classList.remove("selected"));
      }
      status.textContent = "";
    });

    uploadForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!picker.files.length && !sampleInput.value) { status.textContent = "Choose a label image or a sample first."; return; }
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
        const n = (data.image_urls || []).length;
        status.textContent = `Read ${n > 1 ? n + " images" : "the label"} in ${secs} s · ${data.serial}`;
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
    const original = host.innerHTML; // the explanatory text shown before any draft exists
    const syncSend = () => { $("#send-correction").disabled = !$("#notice-host textarea"); };
    document.addEventListener("fetch:done", syncSend);
    // Discard a drafted notice: a mis-click or a change of mind puts the panel back exactly
    // as it was before generation, and the send button locks again.
    host.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-notice-discard]");
      if (!btn) return;
      const textarea = $("textarea", host);
      const edited = textarea && textarea.value !== textarea.defaultValue;
      if (edited && !window.confirm("Discard this draft, including your edits?")) return;
      host.innerHTML = original;
      wire(host);
      syncSend();
      const draftBtn = $('[data-fetch$="/notice"]');
      if (draftBtn) draftBtn.focus();
    });
  }

  // The sidebar badge counts inbox items the user has not read.
  // It refreshes quietly so a reply from the other side shows up without a reload.
  function unreadBadge() {
    const badges = $$("[data-unread-badge]");
    if (!badges.length) return;
    const baseTitle = document.title;
    const tick = async () => {
      try {
        const resp = await fetch("/me/unread", { headers: { Accept: "application/json" } });
        if (!resp.ok) return;
        const n = (await resp.json()).count || 0;
        badges.forEach((b) => { b.textContent = n; b.hidden = n === 0; });
        document.title = n ? `(${n}) ${baseTitle}` : baseTitle;
      } catch (err) { /* offline; try again next tick */ }
    };
    setInterval(tick, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  }

  document.addEventListener("DOMContentLoaded", () => { wire(document); wizard(); decisionPanel(); unreadBadge(); });
})();
