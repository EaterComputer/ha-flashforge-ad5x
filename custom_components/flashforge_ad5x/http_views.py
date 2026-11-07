"""HTTP Views serving the built-in Spoolman Picker (Dropdown + QR + NFC + Create Link)."""

from __future__ import annotations

import hmac
import html
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PICKER_API_BASE
from .spoolman import parse_spoolman_qr

if TYPE_CHECKING:
    from .coordinator import FlashForgeAD5XCoordinator

_LOGGER = logging.getLogger(__name__)


def _resolve_authorized_coordinator(
    hass: HomeAssistant, request: web.Request, entry_id: str | None, token: str | None
) -> FlashForgeAD5XCoordinator | None:
    """Authorize request via either signed picker token OR active HA user session."""
    entries_map: dict[str, FlashForgeAD5XCoordinator] = hass.data.get(DOMAIN, {})
    if not entries_map:
        return None

    coord = entries_map.get(entry_id) if entry_id else next(iter(entries_map.values()))
    if not coord:
        return None

    # Allow authenticated Home Assistant users OR valid picker token from notification URL
    if request.get("hass_user") is not None:
        return coord
    if token and hmac.compare_digest(str(token), coord.picker_token):
        return coord
    return None


def _build_picker_state_payload(coord: FlashForgeAD5XCoordinator) -> dict[str, Any]:
    """Serialize current IFS slots, Spoolman spools, and recent NFC scan for the Picker UI."""
    detail = (coord.data or {}).get("detail") or {}
    matl = detail.get("matlStationInfo") or {}
    raw_slots = {
        int(s["slotId"]): s
        for s in (matl.get("slotInfos") or [])
        if isinstance(s, dict) and s.get("slotId") in (1, 2, 3, 4)
    }

    slots: list[dict[str, Any]] = []
    for slot_num in (1, 2, 3, 4):
        s_info = raw_slots.get(slot_num) or {}
        assigned = coord.get_assigned_spool(slot_num)
        slots.append(
            {
                "slot": slot_num,
                "has_filament": bool(s_info.get("hasFilament", False)),
                "printer_material": s_info.get("materialName") or "",
                "printer_color": s_info.get("materialColor") or "",
                "assigned_spool": assigned,
            }
        )

    spools = sorted(
        coord.spools_cache.values(),
        key=lambda s: (s.get("vendor", "").lower(), s.get("material", "").lower(), s["id"]),
    )

    return {
        "entry_id": coord.entry.entry_id,
        "printer_name": coord.printer_name,
        "printer_online": coord.printer_online,
        "printer_status": coord.printer_status,
        "spoolman_enabled": coord.spoolman_enabled,
        "spoolman_ui_url": coord.spoolman.ui_base_url if coord.spoolman else "",
        "spoolman_create_url": coord.spoolman.create_spool_url if coord.spoolman else "",
        "slots": slots,
        "spools": spools,
        "last_scanned_tag": coord.last_scanned_tag,
    }


class AD5XSpoolPickerPageView(HomeAssistantView):
    """Serves the mobile-friendly Spoolman Dropdown + QR + NFC Picker page."""

    url = f"{PICKER_API_BASE}/picker"
    name = f"api:{DOMAIN}:picker"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry_id = request.query.get("entry_id")
        token = request.query.get("token")
        slot = int(request.query.get("slot", "1")) if request.query.get("slot", "1").isdigit() else 1
        slot = min(4, max(1, slot))
        preselect_spool = request.query.get("spool_id", "")
        bind_uid = request.query.get("bind_uid", "")

        coord = _resolve_authorized_coordinator(hass, request, entry_id, token)
        if not coord:
            return web.Response(
                status=401,
                text="Unauthorized: invalid or missing picker token.",
            )

        coord.active_picker_slot = slot
        await coord.async_refresh_spoolman_cache()
        initial_state = _build_picker_state_payload(coord)

        page_html = _RENDER_PICKER_HTML(
            initial_state_json=json.dumps(initial_state),
            entry_id=html.escape(coord.entry.entry_id),
            token=html.escape(token or coord.picker_token),
            initial_slot=slot,
            preselect_spool=html.escape(preselect_spool),
            bind_uid=html.escape(bind_uid),
        )
        return web.Response(text=page_html, content_type="text/html")


class AD5XSpoolPickerApiView(HomeAssistantView):
    """JSON endpoint backing both the standalone Picker page and the Lovelace card modal."""

    url = f"{PICKER_API_BASE}/spool_action"
    name = f"api:{DOMAIN}:spool_action"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        entry_id = request.query.get("entry_id")
        token = request.query.get("token")
        refresh = request.query.get("refresh") == "1"
        active_slot = request.query.get("active_slot")

        coord = _resolve_authorized_coordinator(hass, request, entry_id, token)
        if not coord:
            return self.json_message("Unauthorized", status_code=401)

        if active_slot and active_slot.isdigit() and int(active_slot) in (1, 2, 3, 4):
            coord.active_picker_slot = int(active_slot)
        if refresh:
            await coord.async_refresh_spoolman_cache()

        return self.json(_build_picker_state_payload(coord))

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        try:
            body = await request.json()
        except ValueError:
            return self.json_message("Invalid JSON", status_code=400)

        entry_id = body.get("entry_id")
        token = body.get("token")
        coord = _resolve_authorized_coordinator(hass, request, entry_id, token)
        if not coord:
            return self.json_message("Unauthorized", status_code=401)

        action = body.get("action")
        try:
            if action == "assign":
                slot = int(body["slot"])
                spool_id = int(body["spool_id"])
                bind_uid = str(body.get("bind_uid") or "").strip() or None
                spool = await coord.async_assign_spool_to_slot(
                    slot_id=slot,
                    spool_id=spool_id,
                    bind_nfc_uid=bind_uid,
                )
                return self.json(
                    {
                        "ok": True,
                        "assigned": spool,
                        "state": _build_picker_state_payload(coord),
                    }
                )

            if action == "unassign":
                slot = int(body["slot"])
                await coord.async_unassign_spool(slot_id=slot)
                return self.json({"ok": True, "state": _build_picker_state_payload(coord)})

            if action == "parse_qr":
                raw_qr = str(body.get("qr") or "")
                spool_id = parse_spoolman_qr(raw_qr)
                if spool_id is None:
                    return self.json(
                        {"ok": False, "error": f"Unrecognized Spoolman QR code: {raw_qr}"}
                    )
                if spool_id not in coord.spools_cache and coord.spoolman:
                    single = await coord.spoolman.async_get_spool(spool_id)
                    if single:
                        coord.spools_cache[spool_id] = single
                spool = coord.spools_cache.get(spool_id)
                if not spool:
                    return self.json(
                        {
                            "ok": False,
                            "spool_id": spool_id,
                            "error": f"Spool #{spool_id} does not exist in Spoolman yet.",
                        }
                    )
                return self.json({"ok": True, "spool": spool})

            if action == "scan_nfc":
                uid = str(body.get("uid") or "").strip()
                slot_opt = int(body["slot"]) if str(body.get("slot", "")).isdigit() else None
                res = await coord.async_process_nfc_scan(uid, explicit_slot=slot_opt)
                return self.json(
                    {
                        "ok": True,
                        "scan": res,
                        "state": _build_picker_state_payload(coord),
                    }
                )

            if action == "refresh":
                await coord.async_refresh_spoolman_cache()
                return self.json({"ok": True, "state": _build_picker_state_payload(coord)})

            return self.json_message(f"Unknown action: {action}", status_code=400)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Spool picker action %s failed", action)
            return self.json({"ok": False, "error": str(err)}, status_code=400)


class AD5XFileUploadApiView(HomeAssistantView):
    """Multipart upload endpoint allowing Lovelace cards to upload `.gcode`/`.3mf` files directly."""

    url = f"{PICKER_API_BASE}/upload"
    name = f"api:{DOMAIN}:upload"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        reader = await request.multipart()
        entry_id: str | None = None
        slot = 1
        print_now = False
        filename = "upload.gcode"
        file_bytes: bytes | None = None

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "entry_id":
                entry_id = (await part.text()).strip()
            elif part.name == "slot":
                raw_slot = (await part.text()).strip()
                if raw_slot.isdigit():
                    slot = min(4, max(1, int(raw_slot)))
            elif part.name == "print_now":
                print_now = (await part.text()).strip().lower() in ("1", "true", "yes")
            elif part.name == "file":
                filename = part.filename or filename
                file_bytes = await part.read(decode=False)

        if not file_bytes:
            return self.json({"ok": False, "error": "No file payload received."}, status_code=400)

        entries_map: dict[str, FlashForgeAD5XCoordinator] = hass.data.get(DOMAIN, {})
        coord = entries_map.get(entry_id) if entry_id else next(iter(entries_map.values()), None)
        if not coord:
            return self.json({"ok": False, "error": "Printer coordinator not found."}, status_code=404)

        opts = coord.persistent_state.get("print_options") or {}
        mappings = coord.build_material_mappings(default_slot=slot)
        try:
            res = await coord.api.async_upload_gcode_bytes(
                filename=filename,
                file_bytes=file_bytes,
                material_mappings=mappings,
                print_now=print_now,
                leveling_before_print=bool(opts.get("leveling", True)),
                flow_calibration=bool(opts.get("flow", False)),
                first_layer_inspection=bool(opts.get("inspection", False)),
            )
            coord.selected_file = filename
            await coord.async_request_refresh()
            return self.json({"ok": True, "filename": filename, "response": res})
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Direct browser upload to AD5X failed")
            return self.json({"ok": False, "error": str(err)}, status_code=400)


def _RENDER_PICKER_HTML(
    initial_state_json: str,
    entry_id: str,
    token: str,
    initial_slot: int,
    preselect_spool: str,
    bind_uid: str,
) -> str:
    """Render standalone responsive HTML5 Spool Picker with Camera QR & NFC support."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
  <title>AD5X — Select Spoolman Filament</title>
  <script src="https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.min.js" defer></script>
  <style>
    :root {{
      --bg: #111318;
      --card: #1b1f27;
      --border: #2d3340;
      --text: #f1f3f9;
      --muted: #9aa3b5;
      --accent: #3b82f6;
      --success: #10b981;
      --warn: #f59e0b;
      --danger: #ef4444;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 16px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg); color: var(--text);
      max-width: 580px; margin-inline: auto;
    }}
    h1 {{ font-size: 1.25rem; margin: 0 0 4px; display: flex; align-items: center; gap: 8px; }}
    .sub {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 16px; }}
    .card {{
      background: var(--card); border: 1px solid var(--border);
      border-radius: 14px; padding: 14px; margin-bottom: 14px;
    }}
    .slot-tabs {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 14px; }}
    .slot-tab {{
      background: var(--card); border: 2px solid var(--border); color: var(--text);
      border-radius: 12px; padding: 10px 6px; text-align: center; cursor: pointer;
      transition: 0.15s ease;
    }}
    .slot-tab.active {{ border-color: var(--accent); background: rgba(59, 130, 246, 0.14); }}
    .slot-dot {{
      width: 18px; height: 18px; border-radius: 50%; margin: 0 auto 5px;
      border: 1px solid rgba(255,255,255,0.35);
    }}
    .slot-label {{ font-weight: 600; font-size: 0.85rem; }}
    .slot-meta {{ font-size: 0.72rem; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .btn-row {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }}
    button, .btn-link {{
      appearance: none; border: 1px solid var(--border); background: #252b37;
      color: var(--text); padding: 10px 14px; border-radius: 10px; font-size: 0.9rem;
      font-weight: 600; cursor: pointer; text-decoration: none; display: inline-flex;
      align-items: center; justify-content: center; gap: 6px; flex: 1;
    }}
    button.primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    button.danger {{ background: rgba(239,68,68,0.15); border-color: var(--danger); color: #fca5a5; }}
    input[type="text"] {{
      width: 100%; padding: 10px 12px; border-radius: 10px;
      border: 1px solid var(--border); background: #13161d; color: var(--text);
      font-size: 0.95rem; margin-bottom: 10px;
    }}
    .spool-list {{ max-height: 310px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }}
    .spool-item {{
      display: flex; align-items: center; gap: 10px; padding: 10px;
      border: 1px solid var(--border); border-radius: 10px; cursor: pointer;
      background: #151922;
    }}
    .spool-item.selected {{ border-color: var(--accent); background: rgba(59,130,246,0.16); }}
    .swatch {{ width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0; border: 1px solid rgba(255,255,255,0.3); }}
    .spool-info {{ flex: 1; min-width: 0; }}
    .spool-title {{ font-weight: 600; font-size: 0.92rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .spool-sub {{ font-size: 0.78rem; color: var(--muted); }}
    .banner {{
      padding: 10px 12px; border-radius: 10px; font-size: 0.86rem; margin-bottom: 12px; display: none;
    }}
    .banner.info {{ display: block; background: rgba(59,130,246,0.15); border: 1px solid var(--accent); }}
    .banner.ok {{ display: block; background: rgba(16,185,129,0.16); border: 1px solid var(--success); }}
    .banner.warn {{ display: block; background: rgba(245,158,11,0.16); border: 1px solid var(--warn); }}
    .banner.err {{ display: block; background: rgba(239,68,68,0.16); border: 1px solid var(--danger); }}
    video {{ width: 100%; border-radius: 10px; background: #000; max-height: 260px; object-fit: cover; }}
    .create-row {{
      display: flex; justify-content: space-between; align-items: center; gap: 8px;
      margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); font-size: 0.84rem;
    }}
    .create-row a {{ color: #60a5fa; text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>🧵 Select Filament Spool</h1>
  <div class="sub" id="printerHeader">Loading printer...</div>

  <div id="statusBanner" class="banner"></div>

  <div class="slot-tabs" id="slotTabs"></div>

  <div class="card">
    <div class="btn-row">
      <button type="button" id="btnQr">📷 Scan QR Code</button>
      <button type="button" id="btnNfc">📶 Scan NFC Tag</button>
    </div>

    <div id="qrBox" style="display:none; margin-bottom:10px;">
      <video id="qrVideo" playsinline muted></video>
      <canvas id="qrCanvas" style="display:none;"></canvas>
      <div style="display:flex; gap:6px; margin-top:6px;">
        <input type="text" id="manualQr" placeholder="Or paste WEB+SPOOLMAN:S-12" style="margin:0;" />
        <button type="button" id="btnParseQr" style="flex:0 0 auto;">Find</button>
      </div>
    </div>

    <div id="nfcBox" style="display:none; margin-bottom:10px;" class="banner info">
      <div id="nfcPromptText">Listening for NFC tag scan (tap tag with HA Companion App, reader, or Android Web NFC)...</div>
      <div style="display:flex; gap:6px; margin-top:8px;">
        <input type="text" id="manualUid" placeholder="Or enter NFC UID (e.g. 04:A2:B3...)" style="margin:0;" />
        <button type="button" id="btnLookupUid" style="flex:0 0 auto;">Check UID</button>
      </div>
    </div>

    <input type="text" id="searchInput" placeholder="🔍 Search Spoolman spools by ID, vendor, material, name..." />
    <div class="spool-list" id="spoolList"></div>

    <div class="create-row">
      <span>Filament not in Spoolman yet?</span>
      <div style="display:flex; gap:10px; align-items:center;">
        <a id="createSpoolLink" href="#" target="_blank" rel="noopener">➕ Create in Spoolman ↗</a>
        <button type="button" id="btnRefresh" style="padding:6px 10px; font-size:0.8rem; flex:0 0 auto;">🔄 Refresh</button>
      </div>
    </div>
  </div>

  <div class="btn-row">
    <button type="button" class="primary" id="btnAssign">✅ Assign Selected Spool to Slot</button>
    <button type="button" class="danger" id="btnUnassign" style="flex:0 0 auto;">Clear Slot</button>
  </div>

  <script>
    const ENTRY_ID = "{entry_id}";
    const TOKEN = "{token}";
    const API_URL = "{PICKER_API_BASE}/spool_action";
    let appState = {initial_state_json};
    let selectedSlot = {initial_slot};
    let selectedSpoolId = "{preselect_spool}" ? parseInt("{preselect_spool}", 10) : null;
    let pendingBindUid = "{bind_uid}" || "";
    let lastSeenScanTs = (appState.last_scanned_tag && appState.last_scanned_tag.timestamp) || 0;
    let qrStream = null;
    let qrRaf = null;

    function showBanner(msg, kind = "info") {{
      const el = document.getElementById("statusBanner");
      el.className = "banner " + kind;
      el.textContent = msg;
    }}

    function render() {{
      document.getElementById("printerHeader").textContent =
        `${{appState.printer_name}} • ${{appState.printer_online ? appState.printer_status.toUpperCase() : "OFFLINE"}}`;

      const createLink = document.getElementById("createSpoolLink");
      if (appState.spoolman_create_url) {{
        createLink.href = appState.spoolman_create_url;
      }}

      if (pendingBindUid) {{
        showBanner(
          `Linking NFC Tag UID ${{pendingBindUid}}: select a spool below (or create one in Spoolman & click Refresh) then tap Assign.`,
          "warn"
        );
      }}

      const tabs = document.getElementById("slotTabs");
      tabs.innerHTML = "";
      (appState.slots || []).forEach(s => {{
        const div = document.createElement("div");
        div.className = "slot-tab" + (s.slot === selectedSlot ? " active" : "");
        const col = (s.assigned_spool && s.assigned_spool.color_hex) || s.printer_color || "#333";
        const meta = s.assigned_spool
          ? `#${{s.assigned_spool.id}} ${{s.assigned_spool.material}}`
          : (s.has_filament ? (s.printer_material || "Loaded") : "Empty");
        div.innerHTML = `
          <div class="slot-dot" style="background:${{col}}"></div>
          <div class="slot-label">Slot ${{s.slot}}</div>
          <div class="slot-meta">${{meta}}</div>
        `;
        div.onclick = () => {{
          selectedSlot = s.slot;
          const slotObj = appState.slots.find(x => x.slot === selectedSlot);
          if (slotObj && slotObj.assigned_spool && !pendingBindUid) {{
            selectedSpoolId = slotObj.assigned_spool.id;
          }}
          fetch(`${{API_URL}}?entry_id=${{encodeURIComponent(ENTRY_ID)}}&token=${{encodeURIComponent(TOKEN)}}&active_slot=${{selectedSlot}}`);
          render();
        }};
        tabs.appendChild(div);
      }});

      const q = document.getElementById("searchInput").value.trim().toLowerCase();
      const listEl = document.getElementById("spoolList");
      listEl.innerHTML = "";
      const filtered = (appState.spools || []).filter(sp => {{
        if (!q) return true;
        const hay = `#${{sp.id}} ${{sp.vendor}} ${{sp.material}} ${{sp.name}} ${{sp.location}}`.toLowerCase();
        return hay.includes(q);
      }});

      if (!filtered.length) {{
        listEl.innerHTML = `<div style="color:var(--muted);padding:14px;text-align:center;">No matching spools found in Spoolman.</div>`;
      }} else {{
        filtered.forEach(sp => {{
          const row = document.createElement("div");
          row.className = "spool-item" + (sp.id === selectedSpoolId ? " selected" : "");
          const remW = sp.remaining_weight != null ? `${{sp.remaining_weight}}g` : "N/A";
          const remM = sp.remaining_length_m != null ? `${{sp.remaining_length_m}}m` : "";
          const loc = sp.location ? ` • 📍 ${{sp.location}}` : "";
          row.innerHTML = `
            <div class="swatch" style="background:${{sp.color_hex}}"></div>
            <div class="spool-info">
              <div class="spool-title">#${{sp.id}} — ${{sp.vendor}} ${{sp.material}} (${{sp.name}})</div>
              <div class="spool-sub">⚖️ ${{remW}} ${{remM ? `(${{remM}})` : ""}}${{loc}}</div>
            </div>
          `;
          row.onclick = () => {{
            selectedSpoolId = sp.id;
            render();
          }};
          listEl.appendChild(row);
        }});
      }}

      document.getElementById("btnAssign").textContent = pendingBindUid
        ? `🔗 Bind NFC & Assign to Slot ${{selectedSlot}}`
        : `✅ Assign to IFS Slot ${{selectedSlot}}`;
    }}

    async function apiPost(payload) {{
      const res = await fetch(API_URL, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ entry_id: ENTRY_ID, token: TOKEN, ...payload }}),
      }});
      return await res.json();
    }}

    document.getElementById("searchInput").addEventListener("input", render);

    document.getElementById("btnRefresh").onclick = async () => {{
      showBanner("Refreshing spools from Spoolman...", "info");
      const data = await apiPost({{ action: "refresh" }});
      if (data.state) appState = data.state;
      showBanner("Spoolman inventory refreshed!", "ok");
      render();
    }};

    document.getElementById("btnAssign").onclick = async () => {{
      if (!selectedSpoolId) {{
        showBanner("Please select a spool from the list (or scan QR/NFC) first.", "warn");
        return;
      }}
      showBanner(`Assigning Spool #${{selectedSpoolId}} to IFS Slot ${{selectedSlot}}...`, "info");
      const data = await apiPost({{
        action: "assign",
        slot: selectedSlot,
        spool_id: selectedSpoolId,
        bind_uid: pendingBindUid || undefined,
      }});
      if (data.ok) {{
        pendingBindUid = "";
        appState = data.state;
        showBanner(
          `Assigned Spool #${{selectedSpoolId}} (${{data.assigned.vendor}} ${{data.assigned.material}}) to Slot ${{selectedSlot}}!`,
          "ok"
        );
        render();
      }} else {{
        showBanner(data.error || "Failed to assign spool.", "err");
      }}
    }};

    document.getElementById("btnUnassign").onclick = async () => {{
      const data = await apiPost({{ action: "unassign", slot: selectedSlot }});
      if (data.ok) {{
        appState = data.state;
        showBanner(`Cleared Spoolman assignment for Slot ${{selectedSlot}}.`, "info");
        render();
      }}
    }};

    // --- Camera QR Code Scanner (`WEB+SPOOLMAN:S-{id}`) ---
    async function stopQrScanner() {{
      if (qrRaf) cancelAnimationFrame(qrRaf);
      qrRaf = null;
      if (qrStream) {{
        qrStream.getTracks().forEach(t => t.stop());
        qrStream = null;
      }}
    }}

    async function handleDecodedQr(rawText) {{
      await stopQrScanner();
      document.getElementById("qrBox").style.display = "none";
      const data = await apiPost({{ action: "parse_qr", qr: rawText }});
      if (data.ok && data.spool) {{
        selectedSpoolId = data.spool.id;
        showBanner(`QR matched Spool #${{data.spool.id}}: ${{data.spool.vendor}} ${{data.spool.material}}! Tap Assign to confirm.`, "ok");
        render();
      }} else {{
        showBanner(data.error || "Could not match QR code in Spoolman.", "err");
      }}
    }}

    document.getElementById("btnQr").onclick = async () => {{
      const box = document.getElementById("qrBox");
      if (box.style.display !== "none") {{
        await stopQrScanner();
        box.style.display = "none";
        return;
      }}
      box.style.display = "block";
      const video = document.getElementById("qrVideo");
      const canvas = document.getElementById("qrCanvas");
      const ctx = canvas.getContext("2d", {{ willReadFrequently: true }});
      try {{
        qrStream = await navigator.mediaDevices.getUserMedia({{ video: {{ facingMode: "environment" }} }});
        video.srcObject = qrStream;
        await video.play();
        const detector = ("BarcodeDetector" in window) ? new BarcodeDetector({{ formats: ["qr_code"] }}) : null;

        const tick = async () => {{
          if (!qrStream) return;
          if (video.readyState === video.HAVE_ENOUGH_DATA) {{
            if (detector) {{
              try {{
                const codes = await detector.detect(video);
                if (codes.length > 0 && codes[0].rawValue) {{
                  await handleDecodedQr(codes[0].rawValue);
                  return;
                }}
              }} catch (_e) {{}}
            }}
            if (window.jsQR) {{
              canvas.width = video.videoWidth;
              canvas.height = video.videoHeight;
              ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
              const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
              const code = window.jsQR(imgData.data, imgData.width, imgData.height);
              if (code && code.data) {{
                await handleDecodedQr(code.data);
                return;
              }}
            }}
          }}
          qrRaf = requestAnimationFrame(tick);
        }};
        qrRaf = requestAnimationFrame(tick);
      }} catch (err) {{
        showBanner("Camera access unavailable (HTTPS required on mobile browsers). You can paste the QR code text below.", "warn");
      }}
    }};

    document.getElementById("btnParseQr").onclick = () => {{
      const val = document.getElementById("manualQr").value.trim();
      if (val) handleDecodedQr(val);
    }};

    // --- NFC Tag Scanner (HA `tag_scanned` live listener + Android Web NFC) ---
    document.getElementById("btnNfc").onclick = async () => {{
      const box = document.getElementById("nfcBox");
      box.style.display = box.style.display === "none" ? "block" : "none";
      if (box.style.display === "block" && "NDEFReader" in window) {{
        try {{
          const ndef = new NDEFReader();
          await ndef.scan();
          document.getElementById("nfcPromptText").textContent =
            "Web NFC active! Hold your NFC filament tag against the back of your phone...";
          ndef.onreading = async (event) => {{
            if (event.serialNumber) {{
              await submitNfcUid(event.serialNumber);
            }}
          }};
        }} catch (_err) {{}}
      }}
    }};

    async function submitNfcUid(uid) {{
      const data = await apiPost({{ action: "scan_nfc", uid, slot: selectedSlot }});
      if (data.state) appState = data.state;
      if (data.scan && data.scan.matched_spool) {{
        selectedSpoolId = data.scan.matched_spool.id;
        showBanner(
          `NFC Tag ${{data.scan.uid}} matched Spool #${{selectedSpoolId}} and assigned to Slot ${{selectedSlot}}!`,
          "ok"
        );
      }} else if (data.scan) {{
        pendingBindUid = data.scan.uid;
      }}
      render();
    }}

    document.getElementById("btnLookupUid").onclick = () => {{
      const uid = document.getElementById("manualUid").value.trim();
      if (uid) submitNfcUid(uid);
    }};

    // Poll every 2.5s while picker is open so HA Companion App `tag_scanned` events appear live
    setInterval(async () => {{
      try {{
        const res = await fetch(`${{API_URL}}?entry_id=${{encodeURIComponent(ENTRY_ID)}}&token=${{encodeURIComponent(TOKEN)}}&active_slot=${{selectedSlot}}`);
        if (!res.ok) return;
        const fresh = await res.json();
        if (fresh.last_scanned_tag && fresh.last_scanned_tag.timestamp > lastSeenScanTs) {{
          lastSeenScanTs = fresh.last_scanned_tag.timestamp;
          appState = fresh;
          const scan = fresh.last_scanned_tag;
          if (scan.matched_spool) {{
            selectedSpoolId = scan.matched_spool.id;
            showBanner(
              `NFC Tag ${{scan.uid}} matched Spool #${{selectedSpoolId}}` +
              (scan.auto_assigned_slot ? ` → Assigned to Slot ${{scan.auto_assigned_slot}}!` : "!"),
              "ok"
            );
          }} else {{
            pendingBindUid = scan.uid;
          }}
          render();
        }}
      }} catch (_e) {{}}
    }}, 2500);

    render();
  </script>
</body>
</html>"""
