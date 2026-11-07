# FlashForge Adventurer 5X (AD5X) — Home Assistant Integration

A local-first, HACS-compatible **Home Assistant Custom Integration** for the **FlashForge Adventurer 5X (AD5X)** multi-color 3D printer, featuring native **4-Slot IFS + Spoolman v0.27.0+** filament management (Dropdown, QR Code `WEB+SPOOLMAN:S-{id}`, and NFC Tag UID scanning), optional **Smart Plug** thermal safety & zero-packet sleep, **External & Native Camera Timelapses**, **Obico ML** failure detection & Supervisor RAM management, and **5 bundled Custom Lovelace Cards** with visual settings editors.

---

## ✨ Key Features

- **100% Local LAN Control & Adaptive Polling**
  - Communicates directly over HTTP (`:8898`) and raw TCP G-code (`:8899`) with zero FlashForge Cloud dependency for printing or telemetry.
  - **Without a Smart Plug:** Lightweight async polling (`3s` while printing, `10s` idle, `45s` quiet offline backoff with zero log spam when powered off).
  - **With an Optional Smart Plug:** **Zero network packets** while the smart plug is `off`, immediate wake-up polling on power-on, **Safe Printer Power** interlock (blocks accidental power-off while printing or while nozzle ≥ cooldown threshold, default `50°C`), and **Auto Power-Off After Print & Cooldown**.
- **Spoolman v0.27.0+ Filament Management (IFS Slots 1–4)**
  - **Automatic Load Prompt:** Inserting filament into any IFS slot (`hasFilament: false → true`) sends a push & persistent notification opening the built-in **Spoolman Picker**.
  - **3 Ways to Select a Spool:**
    1. **Searchable Spoolman Dropdown** with color swatches, remaining weight (`g`) & length (`m`), and a **"➕ Create in Spoolman ↗"** shortcut + **Refresh** button if the spool isn't in your library yet.
    2. **Camera QR Scanner** supporting Spoolman's `WEB+SPOOLMAN:S-{id}` QR codes and URLs.
    3. **NFC Tag UID Scanner** supporting Home Assistant `tag_scanned` events (iOS/Android Companion App, ESPHome NFC readers) and Android Web NFC — including **1-click NFC tag binding** to Spoolman v0.27.0+ for brand-new blank tags!
  - **Two-Way Sync & Multi-Color Usage Tracking:** Automatically pushes material type & closest AD5X 24-color palette match to the printer (`msConfig_cmd`), updates Spoolman `location` (`"AD5X Slot X"`, restoring the prior shelf/dryer location on unload), and deducts real-time multi-color filament extrusion + configurable IFS swap purge waste (`mm`) per slot via `PUT /api/v1/spool/{id}/use`.
- **Flexible Camera & Timelapse Studio**
  - Supports an **External Camera** (any Home Assistant `camera.*` entity or RTSP/HTTP URL), the **AD5X Native Camera**, or **Both**.
  - Captures snapshots **By Layer** or every **15s / 30s / 60s**, optionally applies an `ffmpeg` crop (`w:h:x:y`), compiles H.264 `.mp4` videos asynchronously on print completion, and sends a notification when ready.
- **Obico ML AI Failure Guard**
  - Automatically starts the **Obico ML Supervisor App (Add-on)** when the printer turns on and stops it after power-off to free host RAM.
  - Sends actionable mobile push notifications (**Pause**, **Resume**, **Cancel**, **Snooze 10m**) with the annotated Obico bounding-box snapshot attached, plus optional **Auto-Pause**.
- **5 Bundled Custom Lovelace Cards (Auto-Registered!)**
  - Automatically served and registered in Lovelace — no separate frontend plugin install required. Every card includes a **visual GUI Settings Editor** to toggle individual features on or off.

---

## 📦 Installation

### Option A: HACS (Recommended)
1. Open **HACS** in Home Assistant → click the **⋮** menu (top right) → **Custom repositories**.
2. Paste your repository URL and select category **Integration**.
3. Search for **FlashForge AD5X** and click **Download**.
4. **Restart Home Assistant**.

### Option B: Manual Installation
1. Copy the `custom_components/flashforge_ad5x` folder into your Home Assistant `<config>/custom_components/` directory:
   ```text
   <config>/custom_components/flashforge_ad5x/
   ```
2. **Restart Home Assistant**.

---

## 🖨️ Preparing Your FlashForge AD5X

1. On the AD5X touchscreen, go to **Settings → Network** and note the printer's **IP Address** (assigning a static DHCP lease on your router is recommended).
2. Enable **LAN Only Mode** on the touchscreen and note:
   - **Serial Number** (e.g., `SNMOMC9900999`)
   - **LAN Check Code** (8-character code, e.g., `123a456b`)

---

## 🧭 Setup Wizard

1. Go to **Settings → Devices & Services → + Add Integration** and search for **FlashForge AD5X**.
2. **Step 1 — Auto-Discovery & LAN Check Code:** Powered-on printers on your network are **automatically discovered via UDP** (pre-filling the IP Address, Serial Number, and Name — you only need to type your 8-character **LAN Check Code**!). *(If your printer is currently powered off behind a smart plug, enter IP & Serial manually and uncheck **Validate connection now** — plus if your router ever reassigns its DHCP IP later, the integration auto-detects the new IP by matching Serial Number!)*
3. **Step 2 — Power & Optional Modules:** Optionally pick your **Smart Plug** (`switch.*`), **Notification Targets** (`notify.mobile_app_*`), and check whichever optional modules you want enabled (Spoolman, Camera, Timelapse, Obico ML, Print Files, Raw G-Code Console, OTA Firmware Checker). Disabled modules won't clutter your device with unused entities!
4. **Steps 3–5:** Configure only the modules you enabled. You can revisit **Configure** on the integration card anytime to enable/disable modules later.

---

## 🏷️ Spoolman QR & NFC Tag Workflow

- **Requirements:** [Spoolman](https://github.com/Donkie/Spoolman) (**v0.27.0+** recommended for native `tags` / NFC UID storage). No separate Home Assistant Spoolman integration is required — `flashforge_ad5x` talks directly to Spoolman's REST API v1.
- **When You Insert Filament:**
  - Within seconds of sliding filament into IFS Slot 1–4, Home Assistant sends a notification to your phone.
  - Tap the notification to open the **Spoolman Picker** (`/api/flashforge_ad5x/picker`), or click **🧵 Spool** on any slot in the Lovelace card.
- **Using NFC Tags (iOS & Android):**
  - Stick any NTAG213/215/216 NFC sticker on your spools.
  - Scan the tag using the **Home Assistant Companion App** (Settings → Tags, or iOS Shortcuts / Android direct NFC scan) or an ESPHome NFC reader, or tap **📶 Scan NFC** on Android Chrome.
  - **Known Tag:** If you just loaded filament into a slot (or have that slot open in the Picker), the spool is **immediately assigned** to that IFS slot, its color & material sync to the AD5X touchscreen, and its Spoolman location updates to `AD5X Slot X`.
  - **Brand-New Blank Tag:** Home Assistant prompts you to pick the spool (or click **➕ Create in Spoolman ↗** → **🔄 Refresh**) and binds that NFC UID into Spoolman v0.27.0+ permanently!

---

## 🖥️ Custom Dashboard Cards

After restarting Home Assistant (refresh your browser cache once with `Cmd+Shift+R` / `Ctrl+F5`), edit any Lovelace dashboard → **+ Add Card** and search for **FlashForge AD5X**:

| Card | Type | Purpose |
| :--- | :--- | :--- |
| **FlashForge AD5X — Complete Card** | `custom:flashforge-ad5x-card` | All-in-one card with Compact Tabs (`IFS & AI` / `Controls & Jog` / `Files & System`) or Stacked mode |
| **FlashForge AD5X — Camera, Obico & Timelapse** | `custom:flashforge-ad5x-camera-card` | Live switcher between External, Native, Obico ML, and 3D Model Preview + Timelapse Studio |
| **FlashForge AD5X — IFS & Spoolman Station** | `custom:flashforge-ad5x-ifs-card` | Dedicated 4-slot IFS station with Spoolman Picker modal (Dropdown / QR / NFC) & Load/Unload |
| **FlashForge AD5X — Controls, Temps & Jog** | `custom:flashforge-ad5x-controls-card` | Safe power, auto-cooldown, temps, live speed/fan/Z-offset sliders, XYZ jog pad & G-code console |
| **FlashForge AD5X — Files & Firmware Updates** | `custom:flashforge-ad5x-files-card` | Stored `.gcode`/`.3mf` launcher, multi-color tool-to-slot mapper, direct browser upload & OTA updates |

Every card supports Home Assistant's **Visual Card Editor** — click the pencil icon on the card to toggle any subsection on or off without writing YAML!

---

## 🛠️ Services (Actions)

All services live under the `flashforge_ad5x` domain and are documented in Developer Tools → Actions:

- `flashforge_ad5x.assign_spool` / `flashforge_ad5x.unassign_spool`
- `flashforge_ad5x.configure_ifs_slot` / `flashforge_ad5x.ifs_load_unload`
- `flashforge_ad5x.start_print` / `flashforge_ad5x.upload_file`
- `flashforge_ad5x.jog` / `flashforge_ad5x.send_gcode` (returns TCP response data!)
- `flashforge_ad5x.rename_printer`
- `flashforge_ad5x.check_firmware` / `flashforge_ad5x.download_firmware`

---

## 📜 Credits & Reference

- Original Spanish Home Assistant YAML package concept preserved in [`legacy_yaml/ff_ad5x.yaml`](legacy_yaml/ff_ad5x.yaml) for reference.
