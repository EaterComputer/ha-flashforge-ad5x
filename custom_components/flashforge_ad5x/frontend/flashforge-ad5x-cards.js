/**
 * FlashForge AD5X — Bundled Custom Lovelace Cards & Visual Config Editors
 * Automatically served and registered by `custom_components/flashforge_ad5x`.
 *
 * Included Cards:
 *  1. custom:flashforge-ad5x-card          (Complete All-in-One Card)
 *  2. custom:flashforge-ad5x-camera-card   (Camera, Obico ML & Timelapse Card)
 *  3. custom:flashforge-ad5x-ifs-card      (4-Slot IFS & Spoolman Station Card)
 *  4. custom:flashforge-ad5x-controls-card (Power, Temps, Tuning, Jog & G-Code Card)
 *  5. custom:flashforge-ad5x-files-card    (Files, Multi-Color Tool Mapping, Upload & OTA Card)
 */

const AD5X_CARD_VERSION = "1.0.0";
const AD5X_API_BASE = "/api/flashforge_ad5x";

console.info(
  `%c FLASHFORGE-AD5X-CARDS %c v${AD5X_CARD_VERSION} `,
  "background:#2563eb;color:#fff;font-weight:700;padding:2px 6px;border-radius:4px 0 0 4px;",
  "background:#1e293b;color:#93c5fd;font-weight:600;padding:2px 6px;border-radius:0 4px 4px 0;"
);

/* -------------------------------------------------------------------------- */
/* Shared Utilities & Auto-Discovery                                          */
/* -------------------------------------------------------------------------- */

function escHtml(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function discoverAD5XPrinters(hass) {
  if (!hass || !hass.states) return [];
  const map = new Map();
  for (const st of Object.values(hass.states)) {
    const entryId = st.attributes && st.attributes.ad5x_entry_id;
    const key = st.attributes && st.attributes.ad5x_key;
    if (!entryId || !key) continue;
    if (!map.has(entryId)) {
      map.set(entryId, { entryId, name: entryId, byKey: {} });
    }
    const rec = map.get(entryId);
    rec.byKey[key] = st;
    if (key === "status" && st.attributes.printer_name) {
      rec.name = st.attributes.printer_name;
    }
  }
  return Array.from(map.values());
}

function resolveAD5XContext(hass, preferredEntryId) {
  const printers = discoverAD5XPrinters(hass);
  if (!printers.length) return null;
  const chosen =
    (preferredEntryId && printers.find((p) => p.entryId === preferredEntryId)) ||
    printers[0];
  const byKey = chosen.byKey;
  const statusSt = byKey.status || null;
  const attrs = (statusSt && statusSt.attributes) || {};
  const mod = attrs.module_config || {};

  const externalCamEntity =
    mod.external_camera_entity ||
    (byKey.external_camera && byKey.external_camera.entity_id) ||
    "";
  const nativeCamEntity =
    (byKey.native_camera && byKey.native_camera.entity_id) || "";
  const obicoCamEntity = mod.obico_camera_entity || "";
  const thumbEntity = (byKey.thumbnail && byKey.thumbnail.entity_id) || "";

  return {
    entryId: chosen.entryId,
    printerName: attrs.printer_name || chosen.name || "FlashForge AD5X",
    online: Boolean(attrs.online),
    rawStatus: attrs.raw_status || "offline",
    statusLabel: statusSt ? statusSt.state : "Offline",
    statusAttrs: attrs,
    mod,
    byKey,
    cameras: {
      external: externalCamEntity && hass.states[externalCamEntity] ? externalCamEntity : "",
      native: nativeCamEntity && hass.states[nativeCamEntity] ? nativeCamEntity : "",
      obico: obicoCamEntity && hass.states[obicoCamEntity] ? obicoCamEntity : "",
      thumbnail: thumbEntity && hass.states[thumbEntity] ? thumbEntity : "",
    },
  };
}

function computeContextSignature(hass, ctx) {
  if (!ctx) return "empty";
  const parts = [ctx.entryId];
  for (const [k, st] of Object.entries(ctx.byKey)) {
    parts.push(`${k}:${st.state}:${st.last_updated}`);
  }
  const extraIds = [
    ctx.mod.external_camera_entity,
    ctx.mod.external_camera_switch,
    ctx.mod.smart_plug_entity,
    ctx.mod.obico_camera_entity,
    ctx.mod.obico_switch_entity,
    ctx.mod.obico_error_binary_sensor,
    ctx.mod.obico_confidence_sensor,
  ];
  for (const eid of extraIds) {
    if (eid && hass.states[eid]) {
      const s = hass.states[eid];
      parts.push(`${eid}:${s.state}:${s.last_updated}`);
    }
  }
  return parts.join("|");
}

function statusBadgeColor(rawStatus, online) {
  if (!online || rawStatus === "offline") return "#64748b";
  if (rawStatus === "printing") return "#3b82f6";
  if (rawStatus === "heating" || rawStatus === "calibrating" || rawStatus === "busy") return "#f59e0b";
  if (rawStatus === "paused" || rawStatus === "pausing") return "#eab308";
  if (rawStatus === "completed") return "#10b981";
  if (rawStatus === "error" || rawStatus === "cancel" || rawStatus === "cancelled") return "#ef4444";
  return "#10b981";
}

/* -------------------------------------------------------------------------- */
/* Shared CSS Styles                                                          */
/* -------------------------------------------------------------------------- */

const SHARED_STYLES = `
  :host {
    display: block;
    --ad5x-bg: var(--ha-card-background, var(--card-background-color, #1c1f26));
    --ad5x-surface: rgba(127, 137, 158, 0.09);
    --ad5x-surface-hover: rgba(127, 137, 158, 0.16);
    --ad5x-border: var(--divider-color, rgba(148, 163, 184, 0.18));
    --ad5x-text: var(--primary-text-color, #f1f5f9);
    --ad5x-muted: var(--secondary-text-color, #94a3b8);
    --ad5x-accent: var(--primary-color, #3b82f6);
    --ad5x-ok: #10b981;
    --ad5x-warn: #f59e0b;
    --ad5x-danger: #ef4444;
  }
  ha-card {
    padding: 16px;
    color: var(--ad5x-text);
    overflow: hidden;
  }
  .hdr {
    display: flex; align-items: center; justify-content: space-between;
    gap: 10px; margin-bottom: 12px; flex-wrap: wrap;
  }
  .hdr-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .hdr-title { font-size: 1.08rem; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .badge {
    font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
    padding: 3px 9px; border-radius: 999px; color: #fff; white-space: nowrap;
  }
  .hdr-actions { display: flex; gap: 6px; align-items: center; }
  .icon-btn {
    border: 1px solid var(--ad5x-border); background: var(--ad5x-surface);
    color: var(--ad5x-text); border-radius: 10px; padding: 6px 10px;
    font-size: 0.82rem; font-weight: 600; cursor: pointer; display: inline-flex;
    align-items: center; gap: 5px; transition: 0.15s ease;
  }
  .icon-btn:hover { background: var(--ad5x-surface-hover); }
  .icon-btn.on { background: rgba(59, 130, 246, 0.2); border-color: var(--ad5x-accent); color: var(--ad5x-accent); }
  .icon-btn.ok { background: rgba(16, 185, 129, 0.18); border-color: var(--ad5x-ok); }
  .icon-btn.danger { background: rgba(239, 68, 68, 0.15); border-color: var(--ad5x-danger); color: #fca5a5; }
  .icon-btn.primary { background: var(--ad5x-accent); border-color: var(--ad5x-accent); color: #fff; }
  .icon-btn:disabled { opacity: 0.45; cursor: not-allowed; }

  .section {
    background: var(--ad5x-surface); border: 1px solid var(--ad5x-border);
    border-radius: 12px; padding: 12px; margin-bottom: 12px;
  }
  .section:last-child { margin-bottom: 0; }
  .sec-title {
    font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--ad5x-muted); margin-bottom: 8px;
    display: flex; justify-content: space-between; align-items: center; gap: 8px;
  }

  .progress-wrap { margin-bottom: 12px; }
  .progress-top { display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 5px; font-weight: 600; }
  .progress-bar { height: 8px; background: var(--ad5x-surface-hover); border-radius: 999px; overflow: hidden; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, #3b82f6, #10b981); transition: width 0.35s ease; }
  .metrics-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 8px; margin-top: 8px;
  }
  .metric {
    background: rgba(0, 0, 0, 0.14); border: 1px solid var(--ad5x-border);
    border-radius: 9px; padding: 7px 9px;
  }
  .metric-lbl { font-size: 0.7rem; color: var(--ad5x-muted); }
  .metric-val { font-size: 0.9rem; font-weight: 700; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  /* Tabs */
  .tabs { display: flex; gap: 6px; margin-bottom: 12px; overflow-x: auto; padding-bottom: 2px; }
  .tab {
    flex: 1; text-align: center; padding: 7px 10px; border-radius: 9px;
    border: 1px solid var(--ad5x-border); background: var(--ad5x-surface);
    color: var(--ad5x-muted); font-size: 0.82rem; font-weight: 600; cursor: pointer; white-space: nowrap;
  }
  .tab.active { background: var(--ad5x-accent); border-color: var(--ad5x-accent); color: #fff; }

  /* Camera Box */
  .cam-shell {
    position: relative; border-radius: 12px; overflow: hidden;
    background: #0b0e14; border: 1px solid var(--ad5x-border); margin-bottom: 10px;
  }
  .cam-viewport { width: 100%; aspect-ratio: 16 / 9; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .cam-viewport.ratio-4-3 { aspect-ratio: 4 / 3; }
  .cam-viewport img, .cam-viewport ha-camera-stream { width: 100%; height: 100%; object-fit: contain; display: block; }
  .cam-pills {
    display: flex; gap: 6px; flex-wrap: wrap; padding: 8px;
    background: rgba(15, 23, 42, 0.78); border-bottom: 1px solid var(--ad5x-border);
  }
  .cam-pill {
    font-size: 0.75rem; font-weight: 600; padding: 4px 9px; border-radius: 999px;
    border: 1px solid var(--ad5x-border); background: transparent; color: #cbd5e1; cursor: pointer;
  }
  .cam-pill.active { background: var(--ad5x-accent); border-color: var(--ad5x-accent); color: #fff; }

  /* 4-Slot IFS Grid */
  .ifs-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
  @media (min-width: 520px) {
    .ifs-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  }
  .slot-card {
    border: 1px solid var(--ad5x-border); border-radius: 11px; padding: 9px;
    background: rgba(0, 0, 0, 0.14); display: flex; flex-direction: column; gap: 6px; position: relative;
  }
  .slot-card.active-nozzle { border-color: var(--ad5x-ok); box-shadow: 0 0 0 1px var(--ad5x-ok); }
  .slot-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  .swatch {
    width: 22px; height: 22px; border-radius: 50%; flex-shrink: 0;
    border: 2px solid rgba(255, 255, 255, 0.35);
  }
  .slot-num { font-size: 0.8rem; font-weight: 700; }
  .slot-mat { font-size: 0.82rem; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .slot-sub { font-size: 0.72rem; color: var(--ad5x-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-height: 1em; }
  .slot-btns { display: flex; gap: 4px; flex-wrap: wrap; margin-top: auto; }
  .slot-btns button {
    flex: 1; padding: 4px 6px; font-size: 0.72rem; border-radius: 7px;
    border: 1px solid var(--ad5x-border); background: var(--ad5x-surface);
    color: var(--ad5x-text); cursor: pointer; font-weight: 600;
  }
  .slot-btns button:hover { background: var(--ad5x-surface-hover); }

  /* Controls & Inputs */
  .row { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .row + .row { margin-top: 8px; }
  input[type="number"], input[type="text"], select {
    background: rgba(0, 0, 0, 0.22); color: var(--ad5x-text);
    border: 1px solid var(--ad5x-border); border-radius: 8px;
    padding: 6px 8px; font-size: 0.85rem;
  }
  input[type="range"] { flex: 1; accent-color: var(--ad5x-accent); }

  /* XYZ Jog Pad */
  .jog-layout { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; }
  .xy-pad {
    display: grid; grid-template-columns: repeat(3, 42px); grid-template-rows: repeat(3, 38px);
    gap: 5px; justify-content: center;
  }
  .z-pad { display: flex; flex-direction: column; gap: 5px; align-items: center; }
  .jog-btn {
    border: 1px solid var(--ad5x-border); background: var(--ad5x-surface);
    color: var(--ad5x-text); border-radius: 8px; font-weight: 700; font-size: 0.78rem;
    cursor: pointer; display: flex; align-items: center; justify-content: center;
    min-width: 42px; height: 38px;
  }
  .jog-btn:hover { background: var(--ad5x-surface-hover); }

  /* Inline Spoolman Picker Modal */
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.72);
    z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 14px;
  }
  .modal-card {
    background: var(--ha-card-background, #1b1f27); color: var(--ad5x-text);
    border: 1px solid var(--ad5x-border); border-radius: 16px;
    width: 100%; max-width: 500px; max-height: 90vh; overflow-y: auto; padding: 16px;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
  }
  .spool-list { max-height: 240px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; margin: 8px 0; }
  .spool-row {
    display: flex; align-items: center; gap: 10px; padding: 8px 10px;
    border: 1px solid var(--ad5x-border); border-radius: 9px; cursor: pointer;
    background: rgba(0, 0, 0, 0.16);
  }
  .spool-row.selected { border-color: var(--ad5x-accent); background: rgba(59, 130, 246, 0.18); }
  .banner { padding: 8px 10px; border-radius: 8px; font-size: 0.8rem; margin-bottom: 8px; }
  .banner.info { background: rgba(59, 130, 246, 0.16); border: 1px solid var(--ad5x-accent); }
  .banner.ok { background: rgba(16, 185, 129, 0.16); border: 1px solid var(--ad5x-ok); }
  .banner.warn { background: rgba(245, 158, 11, 0.16); border: 1px solid var(--ad5x-warn); }
  .banner.err { background: rgba(239, 68, 68, 0.16); border: 1px solid var(--ad5x-danger); }
`;

/* -------------------------------------------------------------------------- */
/* Reusable Section Renderers & Camera / Picker Controllers                   */
/* -------------------------------------------------------------------------- */

class AD5XCardBase extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._lastSig = "";
    this._selectedFeed = "";
    this._mountedCamKey = "";
    this._mountedCamEl = null;
    this._activeTab = "overview";
    this._toolMappings = {};
    this._pickerOpen = false;
    this._pickerSlot = 1;
    this._pickerState = null;
    this._pickerSelectedSpool = null;
    this._pickerSearch = "";
    this._pickerBindUid = "";
    this._pickerBanner = null;
    this._qrStream = null;
    this._qrRaf = null;
    this._pickerPollTimer = null;
    this._lastSeenTagTs = 0;
    this._uploadStatus = "";
    this._shellInitialized = false;
  }

  disconnectedCallback() {
    this._stopQrScanner();
    if (this._pickerPollTimer) {
      clearInterval(this._pickerPollTimer);
      this._pickerPollTimer = null;
    }
  }

  _ensureShell() {
    if (this._shellInitialized) return;
    this.shadowRoot.innerHTML = `
      <style>${SHARED_STYLES}</style>
      <ha-card>
        <div id="topMount"></div>
        <div id="cameraShell" class="cam-shell" style="display:none;">
          <div id="cameraPills" class="cam-pills" style="display:none;"></div>
          <div id="cameraViewport" class="cam-viewport"></div>
        </div>
        <div id="bodyMount"></div>
      </ha-card>
      <div id="modalMount"></div>
    `;
    this._shellInitialized = true;
  }

  set hass(hass) {
    this._hass = hass;
    this._ensureShell();
    const ctx = resolveAD5XContext(hass, this._config.entry_id);

    // Always keep live camera element's `hass` & `stateObj` fresh without remounting
    this._syncMountedCameraProps(ctx);

    const activeEl = this.shadowRoot.activeElement;
    const bodyMount = this.shadowRoot.getElementById("bodyMount");
    if (activeEl && bodyMount && bodyMount.contains(activeEl)) {
      const tag = activeEl.tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") {
        return;
      }
    }

    const sig = computeContextSignature(hass, ctx);
    if (sig !== this._lastSig) {
      this._lastSig = sig;
      this._renderCard(ctx);
    }
  }

  _forceRender() {
    if (!this._hass) return;
    const ctx = resolveAD5XContext(this._hass, this._config.entry_id);
    this._lastSig = computeContextSignature(this._hass, ctx);
    this._renderCard(ctx);
  }

  _availableCameraFeeds(ctx) {
    if (!ctx) return [];
    const feeds = [];
    if (ctx.cameras.external) feeds.push({ id: "external", label: "📷 External", entityId: ctx.cameras.external, kind: "camera" });
    if (ctx.cameras.native) feeds.push({ id: "native", label: "🎥 Native", entityId: ctx.cameras.native, kind: "camera" });
    if (ctx.cameras.obico) feeds.push({ id: "obico", label: "🧠 Obico ML", entityId: ctx.cameras.obico, kind: "camera" });
    if (ctx.cameras.thumbnail) feeds.push({ id: "thumbnail", label: "🧊 3D Preview", entityId: ctx.cameras.thumbnail, kind: "image" });
    return feeds;
  }

  _resolveActiveFeed(ctx, defaultFeedPref = "auto") {
    const feeds = this._availableCameraFeeds(ctx);
    if (!feeds.length) return null;
    if (this._selectedFeed && feeds.some((f) => f.id === this._selectedFeed)) {
      return feeds.find((f) => f.id === this._selectedFeed);
    }
    if (defaultFeedPref && defaultFeedPref !== "auto") {
      const match = feeds.find((f) => f.id === defaultFeedPref);
      if (match) return match;
    }
    return feeds[0];
  }

  _updateCameraSection(ctx, { visible, showPills = true, defaultFeed = "auto", aspect43 = false } = {}) {
    const shell = this.shadowRoot.getElementById("cameraShell");
    const pillsEl = this.shadowRoot.getElementById("cameraPills");
    const viewport = this.shadowRoot.getElementById("cameraViewport");
    if (!shell || !viewport) return;

    const feeds = this._availableCameraFeeds(ctx);
    const active = visible ? this._resolveActiveFeed(ctx, defaultFeed) : null;
    if (!visible || !active) {
      shell.style.display = "none";
      viewport.innerHTML = "";
      this._mountedCamKey = "";
      this._mountedCamEl = null;
      return;
    }

    shell.style.display = "block";
    viewport.classList.toggle("ratio-4-3", Boolean(aspect43));

    if (showPills && feeds.length > 1) {
      pillsEl.style.display = "flex";
      pillsEl.innerHTML = feeds
        .map(
          (f) =>
            `<button type="button" class="cam-pill ${f.id === active.id ? "active" : ""}" data-feed="${escHtml(f.id)}">${escHtml(f.label)}</button>`
        )
        .join("");
      pillsEl.querySelectorAll("[data-feed]").forEach((btn) => {
        btn.onclick = () => {
          this._selectedFeed = btn.dataset.feed;
          this._updateCameraSection(ctx, { visible, showPills, defaultFeed, aspect43 });
        };
      });
    } else {
      pillsEl.style.display = "none";
    }

    const mountKey = `${active.kind}:${active.entityId}`;
    const stObj = this._hass.states[active.entityId];
    if (!stObj) return;

    if (this._mountedCamKey !== mountKey) {
      viewport.innerHTML = "";
      this._mountedCamKey = mountKey;
      if (active.kind === "camera" && customElements.get("ha-camera-stream")) {
        const el = document.createElement("ha-camera-stream");
        el.muted = true;
        el.controls = false;
        el.allowExoPlayer = true;
        el.hass = this._hass;
        el.stateObj = stObj;
        viewport.appendChild(el);
        this._mountedCamEl = el;
      } else {
        const img = document.createElement("img");
        img.alt = active.label;
        img.src = this._entityImageUrl(active, stObj);
        viewport.appendChild(img);
        this._mountedCamEl = img;
      }
    } else {
      this._syncMountedCameraProps(ctx);
    }
  }

  _entityImageUrl(feed, stObj) {
    if (feed.kind === "camera") {
      const tok = stObj.attributes && stObj.attributes.access_token;
      return tok
        ? `/api/camera_proxy_stream/${feed.entityId}?token=${encodeURIComponent(tok)}`
        : stObj.attributes.entity_picture || "";
    }
    return (stObj.attributes && stObj.attributes.entity_picture) || "";
  }

  _syncMountedCameraProps(ctx) {
    if (!this._mountedCamEl || !this._mountedCamKey || !this._hass) return;
    const [kind, entityId] = this._mountedCamKey.split(":");
    const stObj = this._hass.states[entityId];
    if (!stObj) return;
    if (kind === "camera" && this._mountedCamEl.tagName === "HA-CAMERA-STREAM") {
      this._mountedCamEl.hass = this._hass;
      this._mountedCamEl.stateObj = stObj;
    } else if (kind === "image" && this._mountedCamEl.tagName === "IMG") {
      const pic = stObj.attributes && stObj.attributes.entity_picture;
      if (pic && this._mountedCamEl.getAttribute("src") !== pic) {
        this._mountedCamEl.setAttribute("src", pic);
      }
    }
  }

  /* ---- HA Entity Action Helpers ---- */

  _pressButton(ctx, key) {
    const st = ctx.byKey[key];
    if (st) {
      this._hass.callService("button", "press", { entity_id: st.entity_id });
    }
  }

  _toggleSwitch(ctx, key) {
    const st = ctx.byKey[key];
    if (st) {
      this._hass.callService("switch", "toggle", { entity_id: st.entity_id });
    }
  }

  _setNumber(ctx, key, value) {
    const st = ctx.byKey[key];
    if (st && !Number.isNaN(Number(value))) {
      this._hass.callService("number", "set_value", {
        entity_id: st.entity_id,
        value: Number(value),
      });
    }
  }

  _selectOption(ctx, key, option) {
    const st = ctx.byKey[key];
    if (st && option) {
      this._hass.callService("select", "select_option", {
        entity_id: st.entity_id,
        option,
      });
    }
  }

  /* ---- Reusable HTML Builders ---- */

  _htmlHeader(ctx, titleOverride, showPowerAndLight = true) {
    const title = titleOverride || ctx.printerName;
    const badgeBg = statusBadgeColor(ctx.rawStatus, ctx.online);
    const lightSt = ctx.byKey.light;
    const powerSt = ctx.byKey.safe_power;
    const lightOn = lightSt && lightSt.state === "on";
    const powerOn = powerSt && powerSt.state === "on";

    return `
      <div class="hdr">
        <div class="hdr-left">
          <div class="hdr-title">${escHtml(title)}</div>
          <span class="badge" style="background:${badgeBg}">${escHtml(ctx.statusLabel)}</span>
        </div>
        ${
          showPowerAndLight
            ? `<div class="hdr-actions">
                ${
                  lightSt
                    ? `<button type="button" class="icon-btn ${lightOn ? "on" : ""}" data-act="toggle-switch" data-key="light" ${!ctx.online ? "disabled" : ""} title="Chamber LED">💡 ${lightOn ? "On" : "Off"}</button>`
                    : ""
                }
                ${
                  powerSt
                    ? `<button type="button" class="icon-btn ${powerOn ? "ok" : "danger"}" data-act="toggle-switch" data-key="safe_power" title="Safe Smart Plug Power">🔌 ${powerOn ? "Powered" : "Off"}</button>`
                    : ""
                }
              </div>`
            : ""
        }
      </div>
    `;
  }

  _htmlProgress(ctx) {
    const prog = Number((ctx.byKey.print_progress && ctx.byKey.print_progress.state) || 0);
    const file = (ctx.byKey.current_file && ctx.byKey.current_file.state) || "Idle";
    const layers = (ctx.byKey.layer_progress && ctx.byKey.layer_progress.state) || "-- / --";
    const ptime = (ctx.byKey.print_time && ctx.byKey.print_time.state) || "-- / --";
    const speed = (ctx.byKey.print_speed && ctx.byKey.print_speed.state) || "0";
    const ext = ctx.byKey.extruder_temp;
    const bed = ctx.byKey.bed_temp;
    const extStr =
      ext && ext.state !== "unavailable" && ext.state !== "unknown"
        ? `${ext.state}°C / ${ext.attributes.target_temp ?? 0}°C`
        : "--";
    const bedStr =
      bed && bed.state !== "unavailable" && bed.state !== "unknown"
        ? `${bed.state}°C / ${bed.attributes.target_temp ?? 0}°C`
        : "--";

    const showJobBar =
      ctx.online && ["printing", "paused", "pausing", "heating", "completed"].includes(ctx.rawStatus);

    return `
      <div class="section">
        ${
          showJobBar
            ? `<div class="progress-wrap">
                <div class="progress-top">
                  <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">📄 ${escHtml(file)}</span>
                  <span>${prog}%</span>
                </div>
                <div class="progress-bar"><div class="progress-fill" style="width:${Math.min(100, Math.max(0, prog))}%"></div></div>
              </div>
              <div class="row" style="margin-bottom:8px;">
                ${
                  ctx.rawStatus === "paused"
                    ? `<button type="button" class="icon-btn primary" data-act="press-btn" data-key="resume_print">▶️ Resume</button>`
                    : `<button type="button" class="icon-btn" data-act="press-btn" data-key="pause_print">⏸️ Pause</button>`
                }
                <button type="button" class="icon-btn danger" data-act="press-btn" data-key="cancel_print">⏹️ Cancel</button>
                ${
                  ctx.rawStatus === "completed"
                    ? `<button type="button" class="icon-btn ok" data-act="press-btn" data-key="clear_bed">🧹 Clear Plate</button>`
                    : ""
                }
              </div>`
            : ""
        }
        <div class="metrics-grid">
          <div class="metric"><div class="metric-lbl">Hotend</div><div class="metric-val">🔥 ${escHtml(extStr)}</div></div>
          <div class="metric"><div class="metric-lbl">Build Plate</div><div class="metric-val">♨️ ${escHtml(bedStr)}</div></div>
          <div class="metric"><div class="metric-lbl">Layers</div><div class="metric-val">${escHtml(layers)}</div></div>
          <div class="metric"><div class="metric-lbl">Time</div><div class="metric-val">${escHtml(ptime)}</div></div>
          <div class="metric"><div class="metric-lbl">Speed</div><div class="metric-val">${escHtml(speed)} mm/s</div></div>
        </div>
      </div>
    `;
  }

  _htmlObicoBar(ctx) {
    if (!ctx.mod.obico) return "";
    const swId = ctx.mod.obico_switch_entity;
    const errId = ctx.mod.obico_error_binary_sensor;
    const confId = ctx.mod.obico_confidence_sensor;
    const swSt = swId ? this._hass.states[swId] : null;
    const errSt = errId ? this._hass.states[errId] : null;
    const confSt = confId ? this._hass.states[confId] : null;
    const notifySw = ctx.byKey.obico_notify;
    const pauseSw = ctx.byKey.obico_auto_pause;
    const threshNum = ctx.byKey.obico_confidence_threshold;

    const alertActive = errSt && errSt.state === "on";
    const confLabel = confSt && !["unknown", "unavailable"].includes(confSt.state) ? `${confSt.state}%` : "--";

    return `
      <div class="section">
        <div class="sec-title">
          <span>🧠 Obico AI Guard</span>
          <span style="color:${alertActive ? "var(--ad5x-danger)" : "var(--ad5x-ok)"};">
            ${alertActive ? "⚠️ ISSUE DETECTED" : `Confidence: ${escHtml(confLabel)}`}
          </span>
        </div>
        <div class="row">
          ${
            swSt
              ? `<button type="button" class="icon-btn ${swSt.state === "on" ? "on" : ""}" data-act="toggle-ext-switch" data-entity="${escHtml(swId)}">📡 Detection: ${swSt.state === "on" ? "Active" : "Idle"}</button>`
              : ""
          }
          ${
            notifySw
              ? `<button type="button" class="icon-btn ${notifySw.state === "on" ? "on" : ""}" data-act="toggle-switch" data-key="obico_notify">🔔 Notify</button>`
              : ""
          }
          ${
            pauseSw
              ? `<button type="button" class="icon-btn ${pauseSw.state === "on" ? "on" : ""}" data-act="toggle-switch" data-key="obico_auto_pause">⏸️ Auto-Pause</button>`
              : ""
          }
          ${
            threshNum
              ? `<span style="font-size:0.78rem;color:var(--ad5x-muted);">Alert ≥ <input type="number" min="10" max="100" step="5" style="width:58px;" value="${escHtml(threshNum.state)}" data-act="set-num" data-key="obico_confidence_threshold" />%</span>`
              : ""
          }
        </div>
      </div>
    `;
  }

  _htmlTimelapseBar(ctx) {
    if (!ctx.mod.timelapse) return "";
    const channels = ["external", "native"].filter(
      (ch) => ctx.byKey[`timelapse_summary_${ch}`]
    );
    if (!channels.length) return "";

    return `
      <div class="section">
        <div class="sec-title"><span>🎬 Timelapse Studio</span></div>
        ${channels
          .map((ch) => {
            const sum = ctx.byKey[`timelapse_summary_${ch}`];
            const sel = ctx.byKey[`timelapse_mode_${ch}`];
            const opts = (sel && sel.attributes.options) || ["Disabled", "By Layer", "Every 15s", "Every 30s", "Every 60s"];
            const label = channels.length > 1 ? `${ch.toUpperCase()}: ` : "";
            return `
              <div class="row" style="margin-bottom:6px;">
                <span style="font-size:0.82rem;font-weight:600;">${escHtml(label)}${escHtml(sum ? sum.state : "0 frames")}</span>
                <div style="display:flex;gap:5px;align-items:center;flex-wrap:wrap;">
                  ${
                    sel
                      ? `<select data-act="set-select" data-key="timelapse_mode_${ch}">
                          ${opts
                            .map(
                              (o) =>
                                `<option value="${escHtml(o)}" ${sel.state === o ? "selected" : ""}>${escHtml(o)}</option>`
                            )
                            .join("")}
                        </select>`
                      : ""
                  }
                  <button type="button" class="icon-btn" data-act="press-btn" data-key="timelapse_snapshot_${ch}" title="Capture Snapshot">📸</button>
                  <button type="button" class="icon-btn primary" data-act="press-btn" data-key="timelapse_compile_${ch}" title="Compile MP4 Video">🎞️ MP4</button>
                  <button type="button" class="icon-btn danger" data-act="press-btn" data-key="timelapse_clear_${ch}" title="Clear Frames">🗑️</button>
                </div>
              </div>
            `;
          })
          .join("")}
      </div>
    `;
  }

  _htmlIfsSection(ctx, { showStatus = true, showLoadUnload = true, showWeights = true, showPurge = false, showPalette = false } = {}) {
    const ifsAction = ctx.statusAttrs.ifs_action || "Idle";
    const ifsStep = ctx.statusAttrs.ifs_step || "Idle";
    const spoolmanOn = Boolean(ctx.mod.spoolman);

    const cards = [1, 2, 3, 4]
      .map((slot) => {
        const st = ctx.byKey[`ifs_slot_${slot}`];
        const a = (st && st.attributes) || {};
        const color = a.display_color || "#475569";
        const activeNozzle = Boolean(a.active_in_extruder);
        const hasFil = Boolean(a.has_filament);
        const title = a.spool_id
          ? `#${a.spool_id} ${a.material || ""}`
          : hasFil
          ? a.printer_material || "Loaded"
          : "Empty";
        const sub = a.spool_id
          ? `${a.vendor || ""} ${showWeights && a.remaining_weight_g != null ? `• ${a.remaining_weight_g}g` : ""}`
          : hasFil
          ? "No spool assigned"
          : "Insert filament";

        return `
          <div class="slot-card ${activeNozzle ? "active-nozzle" : ""}">
            <div class="slot-top">
              <span class="slot-num">Slot ${slot}${activeNozzle ? " ⚡" : ""}</span>
              <span class="swatch" style="background:${escHtml(color)}"></span>
            </div>
            <div class="slot-mat" title="${escHtml(title)}">${escHtml(title)}</div>
            <div class="slot-sub" title="${escHtml(sub)}">${escHtml(sub)}</div>
            <div class="slot-btns">
              ${
                spoolmanOn
                  ? `<button type="button" data-act="open-picker" data-slot="${slot}">🧵 Spool</button>`
                  : ""
              }
              ${
                showLoadUnload
                  ? `<button type="button" data-act="press-btn" data-key="ifs_slot_${slot}_load" ${!ctx.online ? "disabled" : ""} title="Load into Extruder">⬇️</button>
                     <button type="button" data-act="press-btn" data-key="ifs_slot_${slot}_unload" ${!ctx.online ? "disabled" : ""} title="Unload Slot">⬆️</button>`
                  : ""
              }
            </div>
          </div>
        `;
      })
      .join("");

    const purgeNum = ctx.byKey.purge_mm_per_swap;
    const snapSw = ctx.byKey.snap_printer_colors;

    return `
      <div class="section">
        <div class="sec-title">
          <span>🎨 IFS 4-Slot Filament Station</span>
          ${showStatus ? `<span>${escHtml(ifsAction)}${ifsStep !== "Idle" ? ` • ${escHtml(ifsStep)}` : ""}</span>` : ""}
        </div>
        <div class="ifs-grid">${cards}</div>
        ${
          spoolmanOn && (showPurge || showPalette || ctx.statusAttrs.spoolman_create_url)
            ? `<div class="row" style="margin-top:10px;font-size:0.78rem;color:var(--ad5x-muted);">
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                  ${
                    ctx.statusAttrs.spoolman_create_url
                      ? `<a href="${escHtml(ctx.statusAttrs.spoolman_create_url)}" target="_blank" rel="noopener" style="color:var(--ad5x-accent);text-decoration:none;font-weight:600;">➕ New Spool in Spoolman ↗</a>`
                      : ""
                  }
                  <button type="button" class="icon-btn" style="padding:4px 8px;font-size:0.74rem;" data-act="press-btn" data-key="refresh_spoolman">🔄 Sync Spoolman</button>
                </div>
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                  ${
                    showPurge && purgeNum
                      ? `<span>Purge/swap: <input type="number" min="0" max="1500" step="10" style="width:62px;" value="${escHtml(purgeNum.state)}" data-act="set-num" data-key="purge_mm_per_swap" /> mm</span>`
                      : ""
                  }
                  ${
                    showPalette && snapSw
                      ? `<button type="button" class="icon-btn ${snapSw.state === "on" ? "on" : ""}" style="padding:4px 8px;font-size:0.74rem;" data-act="toggle-switch" data-key="snap_printer_colors">🎨 24-Color Snap</button>`
                      : ""
                  }
                </div>
              </div>`
            : ""
        }
      </div>
    `;
  }

  _htmlTemperaturesAndTuning(ctx, { showTemps = true, showTuning = true, showPower = true } = {}) {
    const extTarget = ctx.byKey.target_extruder_temp;
    const bedTarget = ctx.byKey.target_bed_temp;
    const speedNum = ctx.byKey.print_speed_pct;
    const fanNum = ctx.byKey.cooling_fan_pct;
    const zNum = ctx.byKey.z_offset;
    const autoOff = ctx.byKey.auto_power_off;
    const coolNum = ctx.byKey.cooldown_temp;

    return `
      ${
        showTemps
          ? `<div class="section">
              <div class="sec-title"><span>🌡️ Temperature Controls</span></div>
              <div class="row">
                <span style="font-size:0.84rem;">🔥 Nozzle Target (°C)</span>
                <div style="display:flex;gap:5px;align-items:center;">
                  <input type="number" min="0" max="330" style="width:70px;" value="${escHtml(extTarget ? extTarget.state : 0)}" data-act="set-num" data-key="target_extruder_temp" ${!ctx.online ? "disabled" : ""} />
                  <button type="button" class="icon-btn" data-act="quick-num" data-key="target_extruder_temp" data-val="0" ${!ctx.online ? "disabled" : ""}>Off</button>
                  <button type="button" class="icon-btn" data-act="quick-num" data-key="target_extruder_temp" data-val="215" ${!ctx.online ? "disabled" : ""}>PLA</button>
                  <button type="button" class="icon-btn" data-act="quick-num" data-key="target_extruder_temp" data-val="240" ${!ctx.online ? "disabled" : ""}>PETG</button>
                </div>
              </div>
              <div class="row">
                <span style="font-size:0.84rem;">♨️ Bed Target (°C)</span>
                <div style="display:flex;gap:5px;align-items:center;">
                  <input type="number" min="0" max="130" style="width:70px;" value="${escHtml(bedTarget ? bedTarget.state : 0)}" data-act="set-num" data-key="target_bed_temp" ${!ctx.online ? "disabled" : ""} />
                  <button type="button" class="icon-btn" data-act="quick-num" data-key="target_bed_temp" data-val="0" ${!ctx.online ? "disabled" : ""}>Off</button>
                  <button type="button" class="icon-btn" data-act="quick-num" data-key="target_bed_temp" data-val="60" ${!ctx.online ? "disabled" : ""}>60°</button>
                  <button type="button" class="icon-btn" data-act="quick-num" data-key="target_bed_temp" data-val="80" ${!ctx.online ? "disabled" : ""}>80°</button>
                </div>
              </div>
              ${
                showPower && autoOff
                  ? `<div class="row" style="padding-top:8px;border-top:1px solid var(--ad5x-border);">
                      <button type="button" class="icon-btn ${autoOff.state === "on" ? "on" : ""}" data-act="toggle-switch" data-key="auto_power_off">
                        ⏲️ Auto Power-Off After Cooldown: ${autoOff.state === "on" ? "Armed" : "Off"}
                      </button>
                      ${
                        coolNum
                          ? `<span style="font-size:0.78rem;color:var(--ad5x-muted);">Cutoff &lt; <input type="number" min="30" max="80" style="width:55px;" value="${escHtml(coolNum.state)}" data-act="set-num" data-key="cooldown_temp" />°C</span>`
                          : ""
                      }
                    </div>`
                  : ""
              }
            </div>`
          : ""
      }
      ${
        showTuning
          ? `<div class="section">
              <div class="sec-title"><span>🎛️ Live Print Tuning</span></div>
              <div class="row">
                <span style="font-size:0.84rem;min-width:110px;">⚡ Speed (${escHtml(speedNum ? speedNum.state : 100)}%)</span>
                <input type="range" min="10" max="200" step="5" value="${escHtml(speedNum ? speedNum.state : 100)}" data-act="set-num" data-key="print_speed_pct" ${!ctx.online ? "disabled" : ""} />
              </div>
              <div class="row">
                <span style="font-size:0.84rem;min-width:110px;">🌀 Fan (${escHtml(fanNum ? fanNum.state : 0)}%)</span>
                <input type="range" min="0" max="100" step="5" value="${escHtml(fanNum ? fanNum.state : 0)}" data-act="set-num" data-key="cooling_fan_pct" ${!ctx.online ? "disabled" : ""} />
              </div>
              <div class="row">
                <span style="font-size:0.84rem;">📏 Z-Offset (mm)</span>
                <input type="number" min="-2" max="2" step="0.005" style="width:88px;" value="${escHtml(zNum ? zNum.state : 0)}" data-act="set-num" data-key="z_offset" ${!ctx.online ? "disabled" : ""} />
              </div>
            </div>`
          : ""
      }
    `;
  }

  _htmlJogAndCalibration(ctx, { showJog = true, showCalibration = true, showConsole = false } = {}) {
    const jogSel = ctx.byKey.jog_step;
    const stepMm = (jogSel && jogSel.attributes.step_mm) || 10;
    const coords = (ctx.byKey.coordinates && ctx.byKey.coordinates.state) || "X:0 Y:0 Z:0";
    const gcodeResp = (ctx.byKey.gcode_response && ctx.byKey.gcode_response.state) || "";

    return `
      ${
        showJog
          ? `<div class="section">
              <div class="sec-title">
                <span>🕹️ XYZ Motion & Homing</span>
                <span>${escHtml(coords)}</span>
              </div>
              <div class="jog-layout">
                <div class="xy-pad">
                  <div></div>
                  <button type="button" class="jog-btn" data-act="jog" data-axis="y" data-dir="1" ${!ctx.online ? "disabled" : ""}>Y+</button>
                  <div></div>
                  <button type="button" class="jog-btn" data-act="jog" data-axis="x" data-dir="-1" ${!ctx.online ? "disabled" : ""}>X-</button>
                  <button type="button" class="jog-btn" data-act="press-btn" data-key="home_axes" ${!ctx.online ? "disabled" : ""} title="Home XYZ">🏠</button>
                  <button type="button" class="jog-btn" data-act="jog" data-axis="x" data-dir="1" ${!ctx.online ? "disabled" : ""}>X+</button>
                  <div></div>
                  <button type="button" class="jog-btn" data-act="jog" data-axis="y" data-dir="-1" ${!ctx.online ? "disabled" : ""}>Y-</button>
                  <div></div>
                </div>
                <div class="z-pad">
                  <button type="button" class="jog-btn" data-act="jog" data-axis="z" data-dir="-1" ${!ctx.online ? "disabled" : ""}>Z▲</button>
                  ${
                    jogSel
                      ? `<select data-act="set-select" data-key="jog_step" style="width:74px;text-align:center;">
                          ${(jogSel.attributes.options || ["1 mm", "10 mm", "50 mm", "100 mm"])
                            .map(
                              (o) =>
                                `<option value="${escHtml(o)}" ${jogSel.state === o ? "selected" : ""}>${escHtml(o)}</option>`
                            )
                            .join("")}
                        </select>`
                      : `<span>${stepMm}mm</span>`
                  }
                  <button type="button" class="jog-btn" data-act="jog" data-axis="z" data-dir="1" ${!ctx.online ? "disabled" : ""}>Z▼</button>
                </div>
              </div>
              ${
                showCalibration
                  ? `<div class="row" style="margin-top:10px;padding-top:8px;border-top:1px solid var(--ad5x-border);">
                      <button type="button" class="icon-btn" data-act="press-btn" data-key="calibrate_bed" ${!ctx.online ? "disabled" : ""}>📐 Bed Leveling</button>
                      <button type="button" class="icon-btn" data-act="press-btn" data-key="calibrate_vibration" ${!ctx.online ? "disabled" : ""}>〰️ Vibration Cal</button>
                    </div>`
                  : ""
              }
            </div>`
          : ""
      }
      ${
        showConsole && ctx.mod.gcode_console
          ? `<div class="section">
              <div class="sec-title"><span>💻 Raw TCP G-Code Console (:8899)</span></div>
              <div class="row">
                <input type="text" id="gcodeCmdInput" placeholder="e.g. M115 or M105" style="flex:1;" ${!ctx.online ? "disabled" : ""} />
                <button type="button" class="icon-btn primary" data-act="send-gcode" ${!ctx.online ? "disabled" : ""}>Send</button>
              </div>
              ${
                gcodeResp && gcodeResp !== "Idle"
                  ? `<pre style="margin:8px 0 0;padding:8px;border-radius:8px;background:rgba(0,0,0,0.3);font-size:0.75rem;overflow-x:auto;white-space:pre-wrap;">${escHtml(gcodeResp)}</pre>`
                  : ""
              }
            </div>`
          : ""
      }
    `;
  }

  _htmlFilesAndUpdates(ctx, { showToolMapping = true, showPrintOptions = true, showUpload = true, showFirmware = true, showThumbnail = false } = {}) {
    const fileSel = ctx.byKey.selected_gcode_file;
    const selectedFile = (fileSel && fileSel.state) || ctx.statusAttrs.selected_file || "";
    const details = ctx.statusAttrs.gcode_details || [];
    const matchedDetail = details.find((d) => d.gcodeFileName === selectedFile);
    const toolDatas = (matchedDetail && matchedDetail.gcodeToolDatas) || [];

    const lvlSw = ctx.byKey.print_leveling;
    const flowSw = ctx.byKey.print_flow;
    const inspSw = ctx.byKey.print_inspection;
    const fwSt = ctx.byKey.firmware_status;
    const thumbSt = ctx.byKey.thumbnail;

    return `
      ${
        ctx.mod.files
          ? `<div class="section">
              <div class="sec-title"><span>📂 Print Files & Multi-Color Mapping</span></div>
              ${
                showThumbnail && thumbSt && thumbSt.attributes.entity_picture
                  ? `<div style="text-align:center;margin-bottom:8px;">
                      <img src="${escHtml(thumbSt.attributes.entity_picture)}" alt="Preview" style="max-height:130px;border-radius:10px;border:1px solid var(--ad5x-border);" />
                    </div>`
                  : ""
              }
              <div class="row">
                ${
                  fileSel
                    ? `<select data-act="set-select" data-key="selected_gcode_file" style="flex:1;min-width:160px;">
                        ${(fileSel.attributes.options || ["No files"])
                          .map(
                            (f) =>
                              `<option value="${escHtml(f)}" ${fileSel.state === f ? "selected" : ""}>${escHtml(f)}</option>`
                          )
                          .join("")}
                      </select>`
                    : `<span>No files available</span>`
                }
                <button type="button" class="icon-btn primary" data-act="start-print" ${!ctx.online || !selectedFile || selectedFile === "No files" ? "disabled" : ""}>🚀 Start Print</button>
              </div>
              ${
                showToolMapping && toolDatas.length > 0
                  ? `<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--ad5x-border);">
                      <div style="font-size:0.75rem;color:var(--ad5x-muted);margin-bottom:6px;">File Extruder Tool → IFS Slot Mapping:</div>
                      ${toolDatas
                        .map((td, idx) => {
                          const tid = td.toolId ?? idx;
                          const defSlot = this._toolMappings[tid] || Math.min(4, (idx % 4) + 1);
                          return `
                            <div class="row" style="margin-bottom:4px;font-size:0.82rem;">
                              <span style="display:flex;align-items:center;gap:6px;">
                                <span style="width:14px;height:14px;border-radius:50%;background:${escHtml(td.materialColor || "#fff")};display:inline-block;border:1px solid rgba(255,255,255,0.4);"></span>
                                Tool ${tid + 1} (${escHtml(td.materialName || "Filament")})
                              </span>
                              <select data-act="map-tool" data-tool="${tid}">
                                ${[1, 2, 3, 4]
                                  .map(
                                    (s) =>
                                      `<option value="${s}" ${Number(defSlot) === s ? "selected" : ""}>IFS Slot ${s}</option>`
                                  )
                                  .join("")}
                              </select>
                            </div>
                          `;
                        })
                        .join("")}
                    </div>`
                  : ""
              }
              ${
                showPrintOptions
                  ? `<div class="row" style="margin-top:10px;">
                      ${lvlSw ? `<button type="button" class="icon-btn ${lvlSw.state === "on" ? "on" : ""}" data-act="toggle-switch" data-key="print_leveling">📐 Leveling</button>` : ""}
                      ${flowSw ? `<button type="button" class="icon-btn ${flowSw.state === "on" ? "on" : ""}" data-act="toggle-switch" data-key="print_flow">💧 Flow Cal</button>` : ""}
                      ${inspSw ? `<button type="button" class="icon-btn ${inspSw.state === "on" ? "on" : ""}" data-act="toggle-switch" data-key="print_inspection">🔍 1st Layer</button>` : ""}
                    </div>`
                  : ""
              }
              ${
                showUpload
                  ? `<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--ad5x-border);">
                      <div class="row">
                        <input type="file" id="uploadFileInput" accept=".gcode,.3mf,.gx" style="font-size:0.78rem;flex:1;" ${!ctx.online ? "disabled" : ""} />
                        <button type="button" class="icon-btn" data-act="upload-browser-file" data-print="0" ${!ctx.online ? "disabled" : ""}>⬆️ Upload</button>
                        <button type="button" class="icon-btn primary" data-act="upload-browser-file" data-print="1" ${!ctx.online ? "disabled" : ""}>⬆️ & Print</button>
                      </div>
                      ${this._uploadStatus ? `<div style="font-size:0.76rem;color:var(--ad5x-muted);margin-top:4px;">${escHtml(this._uploadStatus)}</div>` : ""}
                    </div>`
                  : ""
              }
            </div>`
          : ""
      }
      ${
        showFirmware && ctx.mod.firmware_check && fwSt
          ? `<div class="section">
              <div class="sec-title">
                <span>⬆️ OTA Firmware Updates</span>
                <span>v${escHtml(fwSt.attributes.installed_version || "?")}</span>
              </div>
              <div class="row">
                <span style="font-size:0.84rem;font-weight:600;">${escHtml(fwSt.state)}</span>
                <button type="button" class="icon-btn" data-act="press-btn" data-key="check_firmware">🔄 Check</button>
              </div>
              ${Object.values(fwSt.attributes.packages || {})
                .map(
                  (pkg) => `
                  <div class="row" style="margin-top:6px;font-size:0.8rem;">
                    <span>📦 ${escHtml(pkg.name)} v${escHtml(pkg.version)}</span>
                    ${
                      pkg.url
                        ? `<button type="button" class="icon-btn primary" data-act="download-fw" data-url="${escHtml(pkg.url)}" data-fn="${escHtml(pkg.filename || "")}">⬇️ Download to HA</button>`
                        : ""
                    }
                  </div>`
                )
                .join("")}
            </div>`
          : ""
      }
    `;
  }

  _htmlLifetimeStats(ctx) {
    const hrs = ctx.byKey.total_print_time;
    const fil = ctx.byKey.total_filament;
    const disk = ctx.byKey.free_disk_space;
    const noz = ctx.byKey.nozzle_model;
    return `
      <div class="section">
        <div class="sec-title"><span>📊 Machine Telemetry</span></div>
        <div class="metrics-grid">
          <div class="metric"><div class="metric-lbl">Lifetime Hours</div><div class="metric-val">${escHtml(hrs ? `${hrs.state} h` : "--")}</div></div>
          <div class="metric"><div class="metric-lbl">Lifetime Filament</div><div class="metric-val">${escHtml(fil ? `${fil.state} m` : "--")}</div></div>
          <div class="metric"><div class="metric-lbl">Free Storage</div><div class="metric-val">${escHtml(disk ? `${disk.state} GB` : "--")}</div></div>
          <div class="metric"><div class="metric-lbl">Nozzle</div><div class="metric-val">${escHtml(noz ? noz.state : "--")}</div></div>
        </div>
      </div>
    `;
  }

  /* ---- Event Delegation Binding ---- */

  _bindActions(rootEl, ctx) {
    if (!rootEl || !ctx) return;

    rootEl.querySelectorAll("[data-act]").forEach((el) => {
      const act = el.dataset.act;

      if (act === "press-btn") {
        el.onclick = () => this._pressButton(ctx, el.dataset.key);
      } else if (act === "toggle-switch") {
        el.onclick = () => this._toggleSwitch(ctx, el.dataset.key);
      } else if (act === "toggle-ext-switch") {
        el.onclick = () => {
          const eid = el.dataset.entity;
          if (eid) this._hass.callService("switch", "toggle", { entity_id: eid });
        };
      } else if (act === "quick-num") {
        el.onclick = () => this._setNumber(ctx, el.dataset.key, Number(el.dataset.val));
      } else if (act === "set-num") {
        el.onchange = () => this._setNumber(ctx, el.dataset.key, Number(el.value));
      } else if (act === "set-select") {
        el.onchange = () => this._selectOption(ctx, el.dataset.key, el.value);
      } else if (act === "jog") {
        el.onclick = () => {
          const axis = el.dataset.axis;
          const dir = Number(el.dataset.dir || 1);
          const step = Number((ctx.byKey.jog_step && ctx.byKey.jog_step.attributes.step_mm) || 10);
          this._hass.callService("flashforge_ad5x", "jog", {
            entry_id: ctx.entryId,
            axis,
            distance: step * dir,
          });
        };
      } else if (act === "send-gcode") {
        el.onclick = () => {
          const inp = rootEl.querySelector("#gcodeCmdInput");
          if (inp && inp.value.trim()) {
            this._hass.callService("flashforge_ad5x", "send_gcode", {
              entry_id: ctx.entryId,
              command: inp.value.trim(),
            });
            inp.value = "";
          }
        };
      } else if (act === "map-tool") {
        el.onchange = () => {
          this._toolMappings[Number(el.dataset.tool)] = Number(el.value);
        };
      } else if (act === "start-print") {
        el.onclick = () => {
          const fileSel = ctx.byKey.selected_gcode_file;
          const filename = (fileSel && fileSel.state) || ctx.statusAttrs.selected_file;
          const payload = { entry_id: ctx.entryId, filename };
          if (Object.keys(this._toolMappings).length > 0) {
            payload.tool_mappings = this._toolMappings;
          }
          this._hass.callService("flashforge_ad5x", "start_print", payload);
        };
      } else if (act === "upload-browser-file") {
        el.onclick = async () => {
          const inp = rootEl.querySelector("#uploadFileInput");
          const file = inp && inp.files && inp.files[0];
          if (!file) {
            this._uploadStatus = "Select a .gcode or .3mf file first.";
            this._forceRender();
            return;
          }
          this._uploadStatus = `Uploading ${file.name}...`;
          this._forceRender();
          const fd = new FormData();
          fd.append("entry_id", ctx.entryId);
          fd.append("print_now", el.dataset.print === "1" ? "true" : "false");
          fd.append("file", file, file.name);
          try {
            const resp = await this._hass.fetchWithAuth(`${AD5X_API_BASE}/upload`, {
              method: "POST",
              body: fd,
            });
            const data = await resp.json();
            this._uploadStatus = data.ok
              ? `✅ Uploaded ${file.name}${el.dataset.print === "1" ? " & started print!" : "!"}`
              : `❌ ${data.error || "Upload failed"}`;
          } catch (err) {
            this._uploadStatus = `❌ Upload error: ${err}`;
          }
          this._forceRender();
        };
      } else if (act === "download-fw") {
        el.onclick = () => {
          this._hass.callService("flashforge_ad5x", "download_firmware", {
            entry_id: ctx.entryId,
            url: el.dataset.url,
            filename: el.dataset.fn || undefined,
          });
        };
      } else if (act === "open-picker") {
        el.onclick = () => this._openSpoolPickerModal(ctx, Number(el.dataset.slot || 1));
      } else if (act === "switch-tab") {
        el.onclick = () => {
          this._activeTab = el.dataset.tab;
          this._forceRender();
        };
      }
    });
  }

  /* ---- Inline Spoolman Picker Modal (Dropdown + QR + NFC + Create Link) ---- */

  async _openSpoolPickerModal(ctx, slot) {
    this._pickerOpen = true;
    this._pickerSlot = slot;
    this._pickerBindUid = "";
    this._pickerBanner = { kind: "info", text: "Loading Spoolman inventory..." };
    this._renderPickerModal(ctx);

    try {
      const resp = await this._hass.fetchWithAuth(
        `${AD5X_API_BASE}/spool_action?entry_id=${encodeURIComponent(ctx.entryId)}&refresh=1&active_slot=${slot}`
      );
      this._pickerState = await resp.json();
      this._lastSeenTagTs =
        (this._pickerState.last_scanned_tag && this._pickerState.last_scanned_tag.timestamp) || 0;
      const curSlotObj = (this._pickerState.slots || []).find((s) => s.slot === slot);
      this._pickerSelectedSpool =
        curSlotObj && curSlotObj.assigned_spool ? curSlotObj.assigned_spool.id : null;
      this._pickerBanner = null;
    } catch (err) {
      this._pickerBanner = { kind: "err", text: `Failed loading Spoolman state: ${err}` };
    }
    this._renderPickerModal(ctx);

    if (this._pickerPollTimer) clearInterval(this._pickerPollTimer);
    this._pickerPollTimer = setInterval(async () => {
      if (!this._pickerOpen) return;
      try {
        const r = await this._hass.fetchWithAuth(
          `${AD5X_API_BASE}/spool_action?entry_id=${encodeURIComponent(ctx.entryId)}&active_slot=${this._pickerSlot}`
        );
        if (!r.ok) return;
        const fresh = await r.json();
        if (fresh.last_scanned_tag && fresh.last_scanned_tag.timestamp > this._lastSeenTagTs) {
          this._lastSeenTagTs = fresh.last_scanned_tag.timestamp;
          this._pickerState = fresh;
          const scan = fresh.last_scanned_tag;
          if (scan.matched_spool) {
            this._pickerSelectedSpool = scan.matched_spool.id;
            this._pickerBanner = {
              kind: "ok",
              text: `NFC Tag ${scan.uid} matched Spool #${scan.matched_spool.id} (${scan.matched_spool.vendor} ${scan.matched_spool.material})!`,
            };
          } else {
            this._pickerBindUid = scan.uid;
            this._pickerBanner = {
              kind: "warn",
              text: `Unrecognized NFC Tag (${scan.uid}). Select a spool below (or create one in Spoolman) and tap Bind & Assign!`,
            };
          }
          this._renderPickerModal(ctx);
        }
      } catch (_e) {}
    }, 2500);
  }

  _closeSpoolPickerModal() {
    this._pickerOpen = false;
    this._stopQrScanner();
    if (this._pickerPollTimer) {
      clearInterval(this._pickerPollTimer);
      this._pickerPollTimer = null;
    }
    const modalMount = this.shadowRoot.getElementById("modalMount");
    if (modalMount) modalMount.innerHTML = "";
  }

  async _pickerPost(ctx, payload) {
    const resp = await this._hass.fetchWithAuth(`${AD5X_API_BASE}/spool_action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry_id: ctx.entryId, ...payload }),
    });
    return await resp.json();
  }

  _stopQrScanner() {
    if (this._qrRaf) cancelAnimationFrame(this._qrRaf);
    this._qrRaf = null;
    if (this._qrStream) {
      this._qrStream.getTracks().forEach((t) => t.stop());
      this._qrStream = null;
    }
  }

  _renderPickerModal(ctx) {
    const mount = this.shadowRoot.getElementById("modalMount");
    if (!mount) return;
    if (!this._pickerOpen) {
      mount.innerHTML = "";
      return;
    }

    const st = this._pickerState || { slots: [], spools: [] };
    const q = this._pickerSearch.trim().toLowerCase();
    const filtered = (st.spools || []).filter((sp) => {
      if (!q) return true;
      return `#${sp.id} ${sp.vendor} ${sp.material} ${sp.name} ${sp.location}`.toLowerCase().includes(q);
    });

    mount.innerHTML = `
      <div class="modal-backdrop" id="pickerBackdrop">
        <div class="modal-card">
          <div class="hdr" style="margin-bottom:8px;">
            <div class="hdr-title">🧵 IFS Slot ${this._pickerSlot} — Spoolman</div>
            <button type="button" class="icon-btn" id="pickerCloseBtn">✕</button>
          </div>

          <div class="tabs">
            ${[1, 2, 3, 4]
              .map(
                (s) =>
                  `<button type="button" class="tab ${this._pickerSlot === s ? "active" : ""}" data-pslot="${s}">Slot ${s}</button>`
              )
              .join("")}
          </div>

          ${
            this._pickerBanner
              ? `<div class="banner ${escHtml(this._pickerBanner.kind)}">${escHtml(this._pickerBanner.text)}</div>`
              : ""
          }

          <div class="row" style="margin-bottom:8px;">
            <button type="button" class="icon-btn" id="pickerQrBtn" style="flex:1;">📷 Scan QR</button>
            <button type="button" class="icon-btn" id="pickerNfcBtn" style="flex:1;">📶 Scan NFC</button>
            <button type="button" class="icon-btn" id="pickerRefreshBtn">🔄 Refresh</button>
          </div>

          <div id="pickerQrArea" style="display:none;margin-bottom:8px;">
            <video id="pickerQrVideo" playsinline muted style="width:100%;max-height:210px;border-radius:10px;background:#000;object-fit:cover;"></video>
            <div class="row" style="margin-top:6px;">
              <input type="text" id="pickerManualQr" placeholder="Or paste WEB+SPOOLMAN:S-12" style="flex:1;" />
              <button type="button" class="icon-btn primary" id="pickerManualQrBtn">Match</button>
            </div>
          </div>

          <div id="pickerNfcArea" style="display:none;margin-bottom:8px;" class="banner info">
            <div id="pickerNfcHint">Tap an NFC tag with HA Companion App / reader, or enter UID:</div>
            <div class="row" style="margin-top:6px;">
              <input type="text" id="pickerManualUid" placeholder="NFC UID (e.g. 04:A1:B2...)" style="flex:1;" />
              <button type="button" class="icon-btn primary" id="pickerManualUidBtn">Lookup</button>
            </div>
          </div>

          <input type="text" id="pickerSearchInp" placeholder="🔍 Search by #ID, vendor, material, color name..." value="${escHtml(this._pickerSearch)}" style="width:100%;box-sizing:border-box;" />

          <div class="spool-list">
            ${
              filtered.length
                ? filtered
                    .map(
                      (sp) => `
                    <div class="spool-row ${this._pickerSelectedSpool === sp.id ? "selected" : ""}" data-spoolid="${sp.id}">
                      <span class="swatch" style="background:${escHtml(sp.color_hex || "#fff")}"></span>
                      <div style="flex:1;min-width:0;">
                        <div style="font-weight:700;font-size:0.86rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">
                          #${sp.id} — ${escHtml(sp.vendor)} ${escHtml(sp.material)} (${escHtml(sp.name)})
                        </div>
                        <div style="font-size:0.74rem;color:var(--ad5x-muted);">
                          ⚖️ ${sp.remaining_weight != null ? `${sp.remaining_weight}g` : "N/A"}
                          ${sp.remaining_length_m != null ? `(${sp.remaining_length_m}m)` : ""}
                          ${sp.location ? ` • 📍 ${escHtml(sp.location)}` : ""}
                        </div>
                      </div>
                    </div>`
                    )
                    .join("")
                : `<div style="padding:14px;text-align:center;color:var(--ad5x-muted);font-size:0.84rem;">No matching spools in Spoolman.</div>`
            }
          </div>

          ${
            st.spoolman_create_url
              ? `<div class="row" style="font-size:0.8rem;margin-bottom:10px;">
                  <span style="color:var(--ad5x-muted);">Spool not in library yet?</span>
                  <a href="${escHtml(st.spoolman_create_url)}" target="_blank" rel="noopener" style="color:var(--ad5x-accent);font-weight:700;text-decoration:none;">➕ Create in Spoolman ↗</a>
                </div>`
              : ""
          }

          <div class="row">
            <button type="button" class="icon-btn primary" id="pickerAssignBtn" style="flex:1;justify-content:center;padding:10px;">
              ${this._pickerBindUid ? `🔗 Bind NFC & Assign to Slot ${this._pickerSlot}` : `✅ Assign to IFS Slot ${this._pickerSlot}`}
            </button>
            <button type="button" class="icon-btn danger" id="pickerClearBtn" style="padding:10px;">Clear</button>
          </div>
        </div>
      </div>
    `;

    mount.querySelector("#pickerBackdrop").onclick = (ev) => {
      if (ev.target.id === "pickerBackdrop") this._closeSpoolPickerModal();
    };
    mount.querySelector("#pickerCloseBtn").onclick = () => this._closeSpoolPickerModal();

    mount.querySelectorAll("[data-pslot]").forEach((btn) => {
      btn.onclick = () => {
        this._pickerSlot = Number(btn.dataset.pslot);
        this._renderPickerModal(ctx);
      };
    });

    mount.querySelectorAll("[data-spoolid]").forEach((row) => {
      row.onclick = () => {
        this._pickerSelectedSpool = Number(row.dataset.spoolid);
        this._renderPickerModal(ctx);
      };
    });

    const searchInp = mount.querySelector("#pickerSearchInp");
    searchInp.oninput = () => {
      this._pickerSearch = searchInp.value;
      const pos = searchInp.selectionStart;
      this._renderPickerModal(ctx);
      const nextInp = mount.querySelector("#pickerSearchInp");
      if (nextInp) {
        nextInp.focus();
        nextInp.setSelectionRange(pos, pos);
      }
    };

    mount.querySelector("#pickerRefreshBtn").onclick = async () => {
      this._pickerBanner = { kind: "info", text: "Refreshing Spoolman..." };
      this._renderPickerModal(ctx);
      const res = await this._pickerPost(ctx, { action: "refresh" });
      if (res.state) this._pickerState = res.state;
      this._pickerBanner = { kind: "ok", text: "Spoolman list updated!" };
      this._renderPickerModal(ctx);
    };

    mount.querySelector("#pickerAssignBtn").onclick = async () => {
      if (!this._pickerSelectedSpool) {
        this._pickerBanner = { kind: "warn", text: "Select a spool first." };
        this._renderPickerModal(ctx);
        return;
      }
      const res = await this._pickerPost(ctx, {
        action: "assign",
        slot: this._pickerSlot,
        spool_id: this._pickerSelectedSpool,
        bind_uid: this._pickerBindUid || undefined,
      });
      if (res.ok) {
        this._closeSpoolPickerModal();
      } else {
        this._pickerBanner = { kind: "err", text: res.error || "Failed assigning spool." };
        this._renderPickerModal(ctx);
      }
    };

    mount.querySelector("#pickerClearBtn").onclick = async () => {
      await this._pickerPost(ctx, { action: "unassign", slot: this._pickerSlot });
      this._closeSpoolPickerModal();
    };

    const handleQrText = async (rawText) => {
      this._stopQrScanner();
      const res = await this._pickerPost(ctx, { action: "parse_qr", qr: rawText });
      if (res.ok && res.spool) {
        this._pickerSelectedSpool = res.spool.id;
        this._pickerBanner = {
          kind: "ok",
          text: `QR matched Spool #${res.spool.id}: ${res.spool.vendor} ${res.spool.material}! Tap Assign.`,
        };
      } else {
        this._pickerBanner = { kind: "err", text: res.error || "Unrecognized QR code." };
      }
      this._renderPickerModal(ctx);
    };

    mount.querySelector("#pickerQrBtn").onclick = async () => {
      const area = mount.querySelector("#pickerQrArea");
      if (area.style.display !== "none") {
        this._stopQrScanner();
        area.style.display = "none";
        return;
      }
      area.style.display = "block";
      const video = mount.querySelector("#pickerQrVideo");
      try {
        this._qrStream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
        });
        video.srcObject = this._qrStream;
        await video.play();
        if ("BarcodeDetector" in window) {
          const detector = new BarcodeDetector({ formats: ["qr_code"] });
          const scanLoop = async () => {
            if (!this._qrStream) return;
            if (video.readyState === video.HAVE_ENOUGH_DATA) {
              try {
                const barcodes = await detector.detect(video);
                if (barcodes.length > 0 && barcodes[0].rawValue) {
                  await handleQrText(barcodes[0].rawValue);
                  return;
                }
              } catch (_e) {}
            }
            this._qrRaf = requestAnimationFrame(scanLoop);
          };
          this._qrRaf = requestAnimationFrame(scanLoop);
        }
      } catch (_err) {
        this._pickerBanner = {
          kind: "warn",
          text: "Camera unavailable (HTTPS required on mobile). Paste QR string below or open standalone Picker.",
        };
        this._renderPickerModal(ctx);
      }
    };

    mount.querySelector("#pickerManualQrBtn").onclick = () => {
      const v = mount.querySelector("#pickerManualQr").value.trim();
      if (v) handleQrText(v);
    };

    mount.querySelector("#pickerNfcBtn").onclick = async () => {
      const area = mount.querySelector("#pickerNfcArea");
      area.style.display = area.style.display === "none" ? "block" : "none";
      if (area.style.display === "block" && "NDEFReader" in window) {
        try {
          const ndef = new NDEFReader();
          await ndef.scan();
          mount.querySelector("#pickerNfcHint").textContent =
            "Web NFC active! Hold NFC spool tag to the back of your phone...";
          ndef.onreading = async (ev) => {
            if (ev.serialNumber) {
              const res = await this._pickerPost(ctx, {
                action: "scan_nfc",
                uid: ev.serialNumber,
                slot: this._pickerSlot,
              });
              if (res.state) this._pickerState = res.state;
              if (res.scan && res.scan.matched_spool) {
                this._pickerSelectedSpool = res.scan.matched_spool.id;
                this._pickerBanner = {
                  kind: "ok",
                  text: `NFC Tag matched Spool #${res.scan.matched_spool.id} & assigned!`,
                };
              } else if (res.scan) {
                this._pickerBindUid = res.scan.uid;
                this._pickerBanner = {
                  kind: "warn",
                  text: `New NFC Tag (${res.scan.uid}). Select a spool below and tap Bind & Assign.`,
                };
              }
              this._renderPickerModal(ctx);
            }
          };
        } catch (_e) {}
      }
    };

    mount.querySelector("#pickerManualUidBtn").onclick = async () => {
      const uid = mount.querySelector("#pickerManualUid").value.trim();
      if (!uid) return;
      const res = await this._pickerPost(ctx, {
        action: "scan_nfc",
        uid,
        slot: this._pickerSlot,
      });
      if (res.state) this._pickerState = res.state;
      if (res.scan && res.scan.matched_spool) {
        this._pickerSelectedSpool = res.scan.matched_spool.id;
        this._pickerBanner = {
          kind: "ok",
          text: `UID matched Spool #${res.scan.matched_spool.id}!`,
        };
      } else if (res.scan) {
        this._pickerBindUid = res.scan.uid;
        this._pickerBanner = {
          kind: "warn",
          text: `UID ${res.scan.uid} not linked yet. Choose a spool below to bind it.`,
        };
      }
      this._renderPickerModal(ctx);
    };
  }
}

/* -------------------------------------------------------------------------- */
/* 1. Complete All-in-One Card (`flashforge-ad5x-card`)                       */
/* -------------------------------------------------------------------------- */

const COMPLETE_DEFAULTS = {
  title: "",
  entry_id: "",
  layout_mode: "tabs",
  show_header_power: true,
  show_progress: true,
  show_camera: true,
  show_timelapse: true,
  show_obico: true,
  show_ifs: true,
  show_temperatures: true,
  show_tuning: true,
  show_jog: true,
  show_files: true,
  show_gcode_console: true,
  show_firmware: true,
  show_lifetime_stats: false,
};

class FlashForgeAD5XCompleteCard extends AD5XCardBase {
  static getConfigElement() {
    return document.createElement("flashforge-ad5x-card-editor");
  }
  static getStubConfig() {
    return { ...COMPLETE_DEFAULTS };
  }
  setConfig(config) {
    this._config = { ...COMPLETE_DEFAULTS, ...(config || {}) };
    this._forceRender();
  }
  getCardSize() {
    return 8;
  }

  _renderCard(ctx) {
    const topMount = this.shadowRoot.getElementById("topMount");
    const bodyMount = this.shadowRoot.getElementById("bodyMount");
    if (!ctx) {
      this._updateCameraSection(null, { visible: false });
      topMount.innerHTML = `<div style="padding:12px;color:var(--ad5x-muted);">No FlashForge AD5X integration detected yet.</div>`;
      bodyMount.innerHTML = "";
      return;
    }

    const c = this._config;
    topMount.innerHTML =
      this._htmlHeader(ctx, c.title, c.show_header_power) +
      (c.show_progress ? this._htmlProgress(ctx) : "");

    this._updateCameraSection(ctx, { visible: Boolean(c.show_camera), showPills: true });

    const isTabbed = c.layout_mode !== "stacked";
    const tabsHtml = isTabbed
      ? `<div class="tabs">
          <button type="button" class="tab ${this._activeTab === "overview" ? "active" : ""}" data-act="switch-tab" data-tab="overview">🧵 IFS & AI</button>
          <button type="button" class="tab ${this._activeTab === "controls" ? "active" : ""}" data-act="switch-tab" data-tab="controls">🎛️ Controls & Jog</button>
          <button type="button" class="tab ${this._activeTab === "files" ? "active" : ""}" data-act="switch-tab" data-tab="files">📂 Files & System</button>
        </div>`
      : "";

    const showOv = !isTabbed || this._activeTab === "overview";
    const showCt = !isTabbed || this._activeTab === "controls";
    const showFl = !isTabbed || this._activeTab === "files";

    bodyMount.innerHTML = `
      ${tabsHtml}
      ${showOv && c.show_obico ? this._htmlObicoBar(ctx) : ""}
      ${showOv && c.show_timelapse ? this._htmlTimelapseBar(ctx) : ""}
      ${showOv && c.show_ifs ? this._htmlIfsSection(ctx, { showPurge: true, showPalette: true }) : ""}
      ${showCt ? this._htmlTemperaturesAndTuning(ctx, { showTemps: c.show_temperatures, showTuning: c.show_tuning, showPower: c.show_header_power }) : ""}
      ${showCt ? this._htmlJogAndCalibration(ctx, { showJog: c.show_jog, showCalibration: c.show_jog, showConsole: c.show_gcode_console }) : ""}
      ${showFl ? this._htmlFilesAndUpdates(ctx, { showToolMapping: c.show_files, showPrintOptions: c.show_files, showUpload: c.show_files, showFirmware: c.show_firmware }) : ""}
      ${showFl && c.show_lifetime_stats ? this._htmlLifetimeStats(ctx) : ""}
    `;

    this._bindActions(topMount, ctx);
    this._bindActions(bodyMount, ctx);
  }
}

/* -------------------------------------------------------------------------- */
/* 2. Camera, Obico & Timelapse Card (`flashforge-ad5x-camera-card`)          */
/* -------------------------------------------------------------------------- */

const CAMERA_CARD_DEFAULTS = {
  title: "",
  entry_id: "",
  default_feed: "auto",
  aspect_ratio: "16:9",
  show_feed_tabs: true,
  show_obico_controls: true,
  show_timelapse_controls: true,
};

class FlashForgeAD5XCameraCard extends AD5XCardBase {
  static getConfigElement() {
    return document.createElement("flashforge-ad5x-camera-card-editor");
  }
  static getStubConfig() {
    return { ...CAMERA_CARD_DEFAULTS };
  }
  setConfig(config) {
    this._config = { ...CAMERA_CARD_DEFAULTS, ...(config || {}) };
    this._forceRender();
  }
  getCardSize() {
    return 5;
  }

  _renderCard(ctx) {
    const topMount = this.shadowRoot.getElementById("topMount");
    const bodyMount = this.shadowRoot.getElementById("bodyMount");
    if (!ctx) {
      this._updateCameraSection(null, { visible: false });
      topMount.innerHTML = `<div style="padding:12px;color:var(--ad5x-muted);">No FlashForge AD5X detected.</div>`;
      bodyMount.innerHTML = "";
      return;
    }
    const c = this._config;
    topMount.innerHTML = this._htmlHeader(ctx, c.title || `${ctx.printerName} — Camera`, false);
    this._updateCameraSection(ctx, {
      visible: true,
      showPills: Boolean(c.show_feed_tabs),
      defaultFeed: c.default_feed || "auto",
      aspect43: c.aspect_ratio === "4:3",
    });
    bodyMount.innerHTML =
      (c.show_obico_controls ? this._htmlObicoBar(ctx) : "") +
      (c.show_timelapse_controls ? this._htmlTimelapseBar(ctx) : "");
    this._bindActions(topMount, ctx);
    this._bindActions(bodyMount, ctx);
  }
}

/* -------------------------------------------------------------------------- */
/* 3. IFS & Spoolman Station Card (`flashforge-ad5x-ifs-card`)                */
/* -------------------------------------------------------------------------- */

const IFS_CARD_DEFAULTS = {
  title: "",
  entry_id: "",
  show_ifs_status: true,
  show_load_unload_buttons: true,
  show_spoolman_weights: true,
  show_purge_setting: true,
  show_palette_toggle: true,
};

class FlashForgeAD5XIfsCard extends AD5XCardBase {
  static getConfigElement() {
    return document.createElement("flashforge-ad5x-ifs-card-editor");
  }
  static getStubConfig() {
    return { ...IFS_CARD_DEFAULTS };
  }
  setConfig(config) {
    this._config = { ...IFS_CARD_DEFAULTS, ...(config || {}) };
    this._forceRender();
  }
  getCardSize() {
    return 4;
  }

  _renderCard(ctx) {
    const topMount = this.shadowRoot.getElementById("topMount");
    const bodyMount = this.shadowRoot.getElementById("bodyMount");
    this._updateCameraSection(null, { visible: false });
    if (!ctx) {
      topMount.innerHTML = `<div style="padding:12px;color:var(--ad5x-muted);">No FlashForge AD5X detected.</div>`;
      bodyMount.innerHTML = "";
      return;
    }
    const c = this._config;
    topMount.innerHTML = this._htmlHeader(ctx, c.title || `${ctx.printerName} — IFS & Spoolman`, false);
    bodyMount.innerHTML = this._htmlIfsSection(ctx, {
      showStatus: c.show_ifs_status,
      showLoadUnload: c.show_load_unload_buttons,
      showWeights: c.show_spoolman_weights,
      showPurge: c.show_purge_setting,
      showPalette: c.show_palette_toggle,
    });
    this._bindActions(topMount, ctx);
    this._bindActions(bodyMount, ctx);
  }
}

/* -------------------------------------------------------------------------- */
/* 4. Controls, Temps & Jog Card (`flashforge-ad5x-controls-card`)            */
/* -------------------------------------------------------------------------- */

const CONTROLS_CARD_DEFAULTS = {
  title: "",
  entry_id: "",
  show_power_and_light: true,
  show_job_buttons: true,
  show_temperatures: true,
  show_tuning: true,
  show_jog: true,
  show_calibration: true,
  show_gcode_console: false,
};

class FlashForgeAD5XControlsCard extends AD5XCardBase {
  static getConfigElement() {
    return document.createElement("flashforge-ad5x-controls-card-editor");
  }
  static getStubConfig() {
    return { ...CONTROLS_CARD_DEFAULTS };
  }
  setConfig(config) {
    this._config = { ...CONTROLS_CARD_DEFAULTS, ...(config || {}) };
    this._forceRender();
  }
  getCardSize() {
    return 6;
  }

  _renderCard(ctx) {
    const topMount = this.shadowRoot.getElementById("topMount");
    const bodyMount = this.shadowRoot.getElementById("bodyMount");
    this._updateCameraSection(null, { visible: false });
    if (!ctx) {
      topMount.innerHTML = `<div style="padding:12px;color:var(--ad5x-muted);">No FlashForge AD5X detected.</div>`;
      bodyMount.innerHTML = "";
      return;
    }
    const c = this._config;
    topMount.innerHTML =
      this._htmlHeader(ctx, c.title || `${ctx.printerName} — Controls`, c.show_power_and_light) +
      (c.show_job_buttons ? this._htmlProgress(ctx) : "");
    bodyMount.innerHTML =
      this._htmlTemperaturesAndTuning(ctx, {
        showTemps: c.show_temperatures,
        showTuning: c.show_tuning,
        showPower: c.show_power_and_light,
      }) +
      this._htmlJogAndCalibration(ctx, {
        showJog: c.show_jog,
        showCalibration: c.show_calibration,
        showConsole: c.show_gcode_console,
      });
    this._bindActions(topMount, ctx);
    this._bindActions(bodyMount, ctx);
  }
}

/* -------------------------------------------------------------------------- */
/* 5. Files & Firmware Updates Card (`flashforge-ad5x-files-card`)            */
/* -------------------------------------------------------------------------- */

const FILES_CARD_DEFAULTS = {
  title: "",
  entry_id: "",
  show_thumbnail: true,
  show_tool_mapping: true,
  show_print_options: true,
  show_browser_upload: true,
  show_firmware: true,
};

class FlashForgeAD5XFilesCard extends AD5XCardBase {
  static getConfigElement() {
    return document.createElement("flashforge-ad5x-files-card-editor");
  }
  static getStubConfig() {
    return { ...FILES_CARD_DEFAULTS };
  }
  setConfig(config) {
    this._config = { ...FILES_CARD_DEFAULTS, ...(config || {}) };
    this._forceRender();
  }
  getCardSize() {
    return 5;
  }

  _renderCard(ctx) {
    const topMount = this.shadowRoot.getElementById("topMount");
    const bodyMount = this.shadowRoot.getElementById("bodyMount");
    this._updateCameraSection(null, { visible: false });
    if (!ctx) {
      topMount.innerHTML = `<div style="padding:12px;color:var(--ad5x-muted);">No FlashForge AD5X detected.</div>`;
      bodyMount.innerHTML = "";
      return;
    }
    const c = this._config;
    topMount.innerHTML = this._htmlHeader(ctx, c.title || `${ctx.printerName} — Files`, false);
    bodyMount.innerHTML = this._htmlFilesAndUpdates(ctx, {
      showThumbnail: c.show_thumbnail,
      showToolMapping: c.show_tool_mapping,
      showPrintOptions: c.show_print_options,
      showUpload: c.show_browser_upload,
      showFirmware: c.show_firmware,
    });
    this._bindActions(topMount, ctx);
    this._bindActions(bodyMount, ctx);
  }
}

/* -------------------------------------------------------------------------- */
/* Declarative Visual Config Editor Factory                                   */
/* -------------------------------------------------------------------------- */

function createVisualEditorClass(defaults, fields) {
  return class extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._hass = null;
      this._config = { ...defaults };
    }
    setConfig(config) {
      this._config = { ...defaults, ...(config || {}) };
      this._render();
    }
    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      if (first) this._render();
    }
    _emit(updated) {
      this._config = updated;
      this.dispatchEvent(
        new CustomEvent("config-changed", {
          detail: { config: updated },
          bubbles: true,
          composed: true,
        })
      );
    }
    _render() {
      if (!this.shadowRoot) return;
      const printers = discoverAD5XPrinters(this._hass);

      this.shadowRoot.innerHTML = `
        <style>
          .ed { display:flex; flex-direction:column; gap:12px; padding:8px 2px; font-family:inherit; color:var(--primary-text-color); }
          .field { display:flex; flex-direction:column; gap:4px; }
          .field label { font-size:0.84rem; font-weight:600; }
          .chk { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:6px 10px; border-radius:8px; background:rgba(127,137,158,0.08); }
          input[type="text"], select { padding:8px 10px; border-radius:8px; border:1px solid var(--divider-color, #444); background:var(--card-background-color, #1e1e1e); color:var(--primary-text-color); font-size:0.9rem; }
          input[type="checkbox"] { width:18px; height:18px; accent-color:var(--primary-color, #3b82f6); cursor:pointer; }
        </style>
        <div class="ed">
          <div class="field">
            <label>Custom Card Title (optional)</label>
            <input type="text" id="ed_title" value="${escHtml(this._config.title || "")}" placeholder="Auto (Printer Name)" />
          </div>
          ${
            printers.length > 1
              ? `<div class="field">
                  <label>FlashForge AD5X Printer</label>
                  <select id="ed_entry">
                    <option value="">Auto (First Printer)</option>
                    ${printers
                      .map(
                        (p) =>
                          `<option value="${escHtml(p.entryId)}" ${this._config.entry_id === p.entryId ? "selected" : ""}>${escHtml(p.name)}</option>`
                      )
                      .join("")}
                  </select>
                </div>`
              : ""
          }
          ${fields
            .map((f) => {
              if (f.type === "select") {
                return `
                  <div class="field">
                    <label>${escHtml(f.label)}</label>
                    <select data-fkey="${escHtml(f.key)}">
                      ${f.options
                        .map(
                          (o) =>
                            `<option value="${escHtml(o.value)}" ${this._config[f.key] === o.value ? "selected" : ""}>${escHtml(o.label)}</option>`
                        )
                        .join("")}
                    </select>
                  </div>`;
              }
              return `
                <label class="chk">
                  <span>${escHtml(f.label)}</span>
                  <input type="checkbox" data-fkey="${escHtml(f.key)}" ${this._config[f.key] ? "checked" : ""} />
                </label>`;
            })
            .join("")}
        </div>
      `;

      const titleInp = this.shadowRoot.getElementById("ed_title");
      if (titleInp) {
        titleInp.onchange = () => this._emit({ ...this._config, title: titleInp.value });
      }
      const entrySel = this.shadowRoot.getElementById("ed_entry");
      if (entrySel) {
        entrySel.onchange = () => this._emit({ ...this._config, entry_id: entrySel.value });
      }
      this.shadowRoot.querySelectorAll("[data-fkey]").forEach((el) => {
        const key = el.dataset.fkey;
        el.onchange = () => {
          const val = el.type === "checkbox" ? el.checked : el.value;
          this._emit({ ...this._config, [key]: val });
        };
      });
    }
  };
}

/* -------------------------------------------------------------------------- */
/* Register Custom Elements & Lovelace Card Picker Metadata                   */
/* -------------------------------------------------------------------------- */

function defineOnce(tag, cls) {
  if (!customElements.get(tag)) {
    customElements.define(tag, cls);
  }
}

defineOnce("flashforge-ad5x-card", FlashForgeAD5XCompleteCard);
defineOnce(
  "flashforge-ad5x-card-editor",
  createVisualEditorClass(COMPLETE_DEFAULTS, [
    {
      key: "layout_mode",
      label: "Card Layout Style",
      type: "select",
      options: [
        { value: "tabs", label: "Compact Tabs (IFS / Controls / Files)" },
        { value: "stacked", label: "Expanded Stacked View" },
      ],
    },
    { key: "show_header_power", label: "Show Safe Power & Chamber Light Buttons" },
    { key: "show_progress", label: "Show Job Progress & Live Telemetry" },
    { key: "show_camera", label: "Show Camera / Obico / 3D Model Feed" },
    { key: "show_obico", label: "Show Obico ML Controls (if enabled)" },
    { key: "show_timelapse", label: "Show Timelapse Controls (if enabled)" },
    { key: "show_ifs", label: "Show 4-Slot IFS & Spoolman Station" },
    { key: "show_temperatures", label: "Show Nozzle & Bed Temperature Controls" },
    { key: "show_tuning", label: "Show Speed, Fan & Z-Offset Sliders" },
    { key: "show_jog", label: "Show XYZ Jog Pad & Calibration" },
    { key: "show_files", label: "Show Print Files, Multi-Color Mapping & Upload" },
    { key: "show_gcode_console", label: "Show Raw G-Code Console (if enabled)" },
    { key: "show_firmware", label: "Show OTA Firmware Checker (if enabled)" },
    { key: "show_lifetime_stats", label: "Show Lifetime Machine Statistics" },
  ])
);

defineOnce("flashforge-ad5x-camera-card", FlashForgeAD5XCameraCard);
defineOnce(
  "flashforge-ad5x-camera-card-editor",
  createVisualEditorClass(CAMERA_CARD_DEFAULTS, [
    {
      key: "default_feed",
      label: "Default Video / Image Feed",
      type: "select",
      options: [
        { value: "auto", label: "Auto (Best Available)" },
        { value: "external", label: "External Camera" },
        { value: "native", label: "AD5X Native Camera" },
        { value: "obico", label: "Obico ML Annotated Stream" },
        { value: "thumbnail", label: "3D Model Preview Thumbnail" },
      ],
    },
    {
      key: "aspect_ratio",
      label: "Viewport Aspect Ratio",
      type: "select",
      options: [
        { value: "16:9", label: "16:9 Widescreen" },
        { value: "4:3", label: "4:3 Standard" },
      ],
    },
    { key: "show_feed_tabs", label: "Show Feed Switcher Pills" },
    { key: "show_obico_controls", label: "Show Obico ML Guard Controls" },
    { key: "show_timelapse_controls", label: "Show Timelapse Studio Controls" },
  ])
);

defineOnce("flashforge-ad5x-ifs-card", FlashForgeAD5XIfsCard);
defineOnce(
  "flashforge-ad5x-ifs-card-editor",
  createVisualEditorClass(IFS_CARD_DEFAULTS, [
    { key: "show_ifs_status", label: "Show IFS State & Step Header" },
    { key: "show_load_unload_buttons", label: "Show Load / Unload Buttons per Slot" },
    { key: "show_spoolman_weights", label: "Show Remaining Spoolman Grams" },
    { key: "show_purge_setting", label: "Show Purge Waste (mm) Input" },
    { key: "show_palette_toggle", label: "Show 24-Color Palette Snap Toggle" },
  ])
);

defineOnce("flashforge-ad5x-controls-card", FlashForgeAD5XControlsCard);
defineOnce(
  "flashforge-ad5x-controls-card-editor",
  createVisualEditorClass(CONTROLS_CARD_DEFAULTS, [
    { key: "show_power_and_light", label: "Show Safe Power, Auto-Cooldown & Light" },
    { key: "show_job_buttons", label: "Show Active Print Progress & Pause/Cancel" },
    { key: "show_temperatures", label: "Show Nozzle & Bed Target Presets" },
    { key: "show_tuning", label: "Show Speed, Fan & Z-Offset Sliders" },
    { key: "show_jog", label: "Show XYZ Jog Pad & Homing" },
    { key: "show_calibration", label: "Show Bed Leveling & Vibration Buttons" },
    { key: "show_gcode_console", label: "Show Raw G-Code Console" },
  ])
);

defineOnce("flashforge-ad5x-files-card", FlashForgeAD5XFilesCard);
defineOnce(
  "flashforge-ad5x-files-card-editor",
  createVisualEditorClass(FILES_CARD_DEFAULTS, [
    { key: "show_thumbnail", label: "Show 3D Model Preview Thumbnail" },
    { key: "show_tool_mapping", label: "Show Multi-Color Tool → IFS Slot Mapper" },
    { key: "show_print_options", label: "Show Leveling / Flow / 1st Layer Toggles" },
    { key: "show_browser_upload", label: "Show Direct Browser File Upload" },
    { key: "show_firmware", label: "Show OTA Firmware Update Checker" },
  ])
);

window.customCards = window.customCards || [];
const CARD_CATALOG = [
  {
    type: "flashforge-ad5x-card",
    name: "FlashForge AD5X — Complete Card",
    description: "All-in-one dashboard card for AD5X telemetry, cameras, IFS/Spoolman, QR/NFC picker, controls, and files.",
    preview: true,
  },
  {
    type: "flashforge-ad5x-camera-card",
    name: "FlashForge AD5X — Camera, Obico & Timelapse",
    description: "Live External/Native/Obico ML camera switcher with Timelapse Studio and AI Guard controls.",
    preview: true,
  },
  {
    type: "flashforge-ad5x-ifs-card",
    name: "FlashForge AD5X — IFS & Spoolman Station",
    description: "4-slot IFS filament station with Spoolman sync, QR & NFC tag scanner, and Load/Unload controls.",
    preview: true,
  },
  {
    type: "flashforge-ad5x-controls-card",
    name: "FlashForge AD5X — Controls, Temps & Jog",
    description: "Safe smart-plug power, temperatures, live print tuning, XYZ jog pad, calibration, and raw G-code console.",
    preview: true,
  },
  {
    type: "flashforge-ad5x-files-card",
    name: "FlashForge AD5X — Files & Firmware Updates",
    description: "Stored G-code/3MF launcher with multi-color IFS tool mapping, browser upload, and OTA firmware updates.",
    preview: true,
  },
];

for (const entry of CARD_CATALOG) {
  if (!window.customCards.some((c) => c.type === entry.type)) {
    window.customCards.push(entry);
  }
}
