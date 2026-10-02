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
    current() { return document.documentElement.dataset.theme === "light" ? "light" : "dark"; },
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
      if (resp.redirected) { location.href = resp.url; return; } // signed out: go where the server sent us
      if (target && !resp.ok) { target.innerHTML = (await resp.text()) + previous; wire(target); } // keep the form so the user can retry
      else if (target) await swap(target, resp);
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
          `<button type="button" class="file-remove" title="Remove this image"><svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg></button>` +
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
        const previous = target ? target.innerHTML : "";
        try {
          const resp = await fetch(btn.dataset.fetch, { method: "post", headers: { "X-Partial": "1" } });
          if (resp.redirected) { location.href = resp.url; return; }
          if (target) await swap(target, resp);
          btn.dispatchEvent(new CustomEvent("fetch:done", { bubbles: true, detail: { ok: resp.ok } }));
        } catch (err) {
          if (target) target.innerHTML = `<div class="alert alert-danger">Request failed: ${err}</div>` + previous;
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

    $$("[data-dismiss]", root).forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", () => btn.parentElement.remove());
    });

    $$("[data-check-all]", root).forEach((all) => {
      if (all.dataset.wired) return;
      all.dataset.wired = "1";
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
          if (resp.redirected) { location.href = resp.url; return; }
          const html = await resp.text();
          el.innerHTML = html;
          wire(el);
          if (!resp.ok) return; // the error partial is shown once; nothing to keep polling for
          if (header && resp.headers.get(header) === value) return;
        } catch (err) { /* keep polling */ }
        setTimeout(tick, interval);
      };
      setTimeout(tick, interval);
    });

    $$(".demo-user", root).forEach((btn) => {
      if (btn.dataset.wired) return;
      btn.dataset.wired = "1";
      btn.addEventListener("click", () => {
        $("#email").value = btn.dataset.email;
        $("#password").value = $(".auth-demo-title code").textContent;
        $("#password").focus();
      });
    });
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
    const precheckPlaceholder = $("#precheck-result") ? $("#precheck-result").innerHTML : "";

    function pickSample(btn) {
      sampleInput.value = btn.dataset.sampleId;
      picker.clear();
      $$(".sample").forEach((b) => { b.classList.toggle("selected", b === btn); b.setAttribute("aria-pressed", b === btn ? "true" : "false"); });
      samplePreview.hidden = false;
      $("img", samplePreview).src = btn.querySelector("img").src;
      $("span", samplePreview).textContent = `Sample: ${btn.textContent.trim()}`;
      status.textContent = "";
    }
    $$(".sample").forEach((btn) => btn.addEventListener("click", () => pickSample(btn)));

    pickerEl.addEventListener("picker:change", (e) => {
      if (e.detail.count > 0) {
        sampleInput.value = "";
        samplePreview.hidden = true;
        $$(".sample").forEach((b) => { b.classList.remove("selected"); b.setAttribute("aria-pressed", "false"); });
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
        const body = new FormData(uploadForm);
        if (applicationId) body.append("replace_draft_id", applicationId); // the earlier read's draft is replaced
        const resp = await fetch(uploadForm.action, { method: "post", body, headers: { Accept: "application/json" } });
        let data = null;
        try { data = await resp.json(); } catch (e) { data = null; } // a crash page is not JSON
        if (!resp.ok || !data) { status.textContent = (data && data.detail) || `Upload failed (${resp.status} ${resp.statusText}). Try again.`; return; }
        applicationId = data.id;
        applyRead(data, started);
      } catch (err) {
        status.textContent = `Upload failed: ${err}`;
      } finally {
        btn.disabled = false;
      }
    });

    // Fill step 2 from a read; after a timeout, offer to read the stored images again
    // rather than making the applicant upload them a second time.
    function applyRead(data, started) {
      const secs = ((performance.now() - started) / 1000).toFixed(1);
      const n = (data.image_urls || []).length;
      if (data.read_failed) {
        status.innerHTML = "";
        const note = document.createElement("span");
        note.textContent = `${data.warning}. `;
        const again = document.createElement("button");
        again.type = "button"; again.className = "link-btn"; again.textContent = "Read again";
        again.addEventListener("click", async () => {
          again.disabled = true;
          status.innerHTML = '<span class="spinner"></span> Reading the label again…';
          const t0 = performance.now();
          try {
            const resp = await fetch(`/applicant/applications/${applicationId}/read`, { method: "post", headers: { Accept: "application/json" } });
            const data = await resp.json();
            if (!resp.ok) { status.textContent = data.detail || "Read failed."; return; }
            applyRead(data, t0);
          } catch (err) { status.textContent = `Read failed: ${err}`; }
        });
        const orHand = document.createElement("span");
        orHand.className = "muted"; orHand.textContent = " or fill in the form by hand.";
        status.append(note, again, orHand);
      } else {
        const perImage = n > 1 ? ` (${(secs / n).toFixed(1)} s per image)` : "";
        status.textContent = `Read ${n > 1 ? n + " images" : "the label"} in ${secs} s${perImage} · ${data.serial}`;
      }
      {
        // A new label: nothing from the previous one may linger in steps 2 and 3.
        detailsForm.reset();
        $("#precheck-result").innerHTML = precheckPlaceholder;
        $("#step-2").classList.remove("step-done");
        $("#step-3").classList.remove("step-done");
        for (const [key, value] of Object.entries(data.prefill || {})) {
          const el = detailsForm.elements[key];
          if (el) el.value = value;
        }
        lastRead = data.label_read || {};
        showWizardViewer(data.image_urls || []);
        renderChecklist((data.prefill || {}).beverage_type ? "label" : "none");
        if (data.prefill && data.prefill.country_of_origin) $("#is_import").checked = true;
        $("#prefill-hint").textContent = data.warning ? data.warning : "Filled from the label. Check every value against your application.";
        detailsForm.action = `/applicant/applications/${applicationId}/precheck`;
        submitForm.action = `/applicant/applications/${applicationId}/submit`;
        $("#step-1").classList.add("step-done");
        for (const step of [$("#step-2"), $("#step-3")]) {
          step.classList.remove("step-locked");
          step.inert = false; // the template locks the steps with `inert`, which blocks every click and key
          step.removeAttribute("inert");
        }
        submitBtn.disabled = false;
        $("#step-2").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }

    // The uploaded panels beside steps 2 and 3, with the same zoomable viewer the
    // review page uses. Thumbnails switch panels when there is more than one.
    function showWizardViewer(urls) {
      const aside = $("#wizard-viewer");
      if (!aside || !urls.length) return;
      const img = $("[data-viewer-img]", aside);
      img.src = urls[0];
      $("[data-viewer-caption]", aside).textContent = "Front";
      $("[data-viewer]", aside).classList.remove("zoomed");
      const thumbs = $("[data-wizard-thumbs]", aside);
      thumbs.innerHTML = "";
      thumbs.hidden = urls.length < 2;
      urls.forEach((url, i) => {
        const label = PANEL_NAMES[i] || `Panel ${i + 1}`;
        const b = document.createElement("button");
        b.type = "button"; b.className = "viewer-thumb" + (i === 0 ? " active" : "");
        b.dataset.viewerSrc = url; b.dataset.viewerLabel = label; b.title = label;
        const t = document.createElement("img"); t.src = url; t.alt = ""; t.loading = "lazy";
        const span = document.createElement("span"); span.textContent = label[0];
        b.append(t, span); thumbs.appendChild(b);
        b.addEventListener("click", () => {  // the viewer itself was wired at page load
          img.src = url;
          $("[data-viewer-caption]", aside).textContent = label;
          $("[data-viewer]", aside).classList.remove("zoomed");
          $$(".viewer-thumb", thumbs).forEach((x) => x.classList.toggle("active", x === b));
        });
      });
      aside.hidden = false;
    }

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

  // The application form follows the class's own rules: a red mark on every field the
  // class requires, and a short "also read from the label" list for the items the
  // applicant never types (qualifying phrase, sulfites, vintage ...) with what the read
  // found, so they can be reviewed before the check. The class is detected from the
  // label and can be changed in the select, which re-applies the rules.
  const FORM_ITEMS = ["brand_name", "class_type", "alcohol_content", "net_contents",
    "producer_name", "producer_address", "country_of_origin"];
  let lastRead = {};
  let typeSource = "none";
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  let rulesCache; // parsed once; the attribute never changes after the page loads
  function formRules() {
    if (rulesCache !== undefined) return rulesCache;
    const grid = $("[data-form-rules]");
    if (!grid) return (rulesCache = null);
    try { rulesCache = JSON.parse(grid.getAttribute("data-form-rules") || "[]"); } catch (e) { rulesCache = null; }
    return rulesCache;
  }

  function applyClassRules() {
    const rules = formRules();
    const select = $("#beverage_type");
    if (!rules || !select) return;
    const chosen = rules.find((r) => r.beverage_type === select.value);
    const isImport = $("#is_import") && $("#is_import").checked;
    const byField = {};
    (chosen ? chosen.checklist : []).forEach((i) => { byField[i.field] = i; });

    // Required markers on the form fields.
    FORM_ITEMS.forEach((name) => {
      const input = document.getElementById(name);
      const field = input && input.closest(".field");
      if (!field) return;
      const rule = byField[name];
      let required = !!rule && rule.requirement === "required";
      if (!chosen) required = ["brand_name", "class_type"].includes(name);
      if (name === "country_of_origin") required = isImport; // whatever the class, imports name the country
      field.classList.toggle("required", required);
      field.classList.toggle("optional", !!rule && rule.requirement === "optional");
      const label = $("label", field);
      if (label && rule) label.title = `${rule.citation}${rule.note ? " · " + rule.note : ""}`;
    });

    // Where the type came from.
    const source = $("[data-type-source]");
    if (source) {
      source.hidden = typeSource === "none";
      source.textContent = typeSource === "label" ? "detected from the label" : typeSource === "user" ? "chosen by you" : "";
    }

    // Items printed on the label that are not entered here.
    const panel = $("#label-read");
    if (!panel) return;
    const items = (chosen ? chosen.checklist : [])
      .filter((i) => !FORM_ITEMS.includes(i.field) && i.checked_by === "engine")
      .sort((a, b) => (a.requirement === "required" ? 0 : 1) - (b.requirement === "required" ? 0 : 1));
    if (!chosen || !items.length || !Object.keys(lastRead).length) { panel.hidden = true; return; }
    $("[data-label-read]", panel).innerHTML = items.map((i) => {
      const value = lastRead[i.field];
      const mark = i.requirement === "required" ? '<span class="req" title="Required">*</span>' : "";
      const shown = value && value.length > 48 ? value.slice(0, 48).trimEnd() + "…" : value;
      const found = value
        ? `<span class="read-found" title="${esc(value)}">&#10003; ${esc(shown)}</span>`
        : i.requirement === "required"
          ? '<span class="read-missing">not found on the label</span>'
          : '<span class="muted">not on the label</span>';
      const tip = `${i.citation}${i.note ? " · " + i.note : ""}`;
      return `<li title="${esc(tip)}"><span class="label-read-name">${esc(i.label)}${mark}</span>${found}</li>`;
    }).join("");
    $("[data-label-read-note]", panel).innerHTML =
      `<span class="req">*</span> required for ${esc(chosen.name.toLowerCase())}. See <a href="/rules" target="_blank" rel="noopener">Reference</a> for the rule behind each item.`;
    panel.hidden = false;
  }

  function renderChecklist(source) {
    typeSource = source;
    applyClassRules();
  }

  function checklist() {
    const select = $("#beverage_type");
    if (!select || !formRules()) return;
    select.addEventListener("change", () => renderChecklist("user"));
    const imp = $("#is_import");
    if (imp) imp.addEventListener("change", applyClassRules);
    renderChecklist(select.value ? "preset" : "none");
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
      if (document.hidden) return; // a background tab polls again when it is shown
      try {
        const resp = await fetch("/me/unread", { headers: { Accept: "application/json" } });
        if (resp.status === 401) { clearInterval(timer); return; } // signed out: stop asking
        if (!resp.ok) return;
        const n = (await resp.json()).count || 0;
        badges.forEach((b) => {
          const grew = n > parseInt(b.textContent || "0", 10);
          b.textContent = n; b.hidden = n === 0;
          if (grew) { b.classList.remove("bump"); void b.offsetWidth; b.classList.add("bump"); }
        });
        document.title = n ? `(${n}) ${baseTitle}` : baseTitle;
      } catch (err) { /* offline; try again next tick */ }
    };
    const timer = setInterval(tick, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  }

  // Landing page: the sign-in form lives in a native <dialog>, opened from the bar or
  // the headline button, and reopened automatically after a failed attempt.
  function signin() {
    const dialog = $("#signin");
    if (!dialog) return;
    const open = () => { if (!dialog.open) dialog.showModal(); const first = $("#email", dialog); if (first) first.focus(); };
    $$("[data-open-signin]").forEach((btn) => btn.addEventListener("click", open));
    $$("[data-close-signin]", dialog).forEach((btn) => btn.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
    if (dialog.hasAttribute("data-open")) open();
  }

  document.addEventListener("DOMContentLoaded", () => { wire(document); wizard(); checklist(); decisionPanel(); unreadBadge(); signin(); });
})();
