# Projection Pad — User Manual

**Projection Pad** (`v1.2`) is a virtual screen manager and live presentation switcher for laptop-to-projector and multi-monitor setups.

---

## 1. Studio Interface Overview

The main window is organized into three areas:

1. **Top Toolbar**
   - **Output:** Global target display selector (`Display 2`, `Display 3`, ..., or `All External`).
   - **Display:** Opens **Display Settings** to inspect or change resolution and refresh rate for any connected monitor.
   - **Refresh (`Ctrl+R`):** Re-scans connected displays and open windows.
   - **Preview (`1–10 fps`):** Adjusts thumbnail refresh rate for the pad deck (does not limit projector output FPS).
   - **Webcam Setup:** Installs the built-in **Unity Capture** virtual camera driver or links to **OBS Studio**.

2. **Studio Section (Top Half)**
   - **Program Monitor (Left):** Live preview of the active output, including any lower-right **Camera PIP** overlay (`● STANDBY` / `● LIVE`).
   - **Quick Control Panel (Right):**
     - **Program Output:** `PROJECT` (`Ctrl+P`) and `STOP PROJECTION` (`Ctrl+Shift+P`).
     - **Camera PIP • Lower Right:** `PIP: OFF / ON` toggle (`Ctrl+6`) and `⚙` settings button.
     - **Virtual Webcam • Pad 8:** `Stop Webcam` button when Pad 8 is streaming.

3. **Pad Deck (Bottom Half — 4×2 Grid)**
   - **Pads 1–4:** Screen, Window, or Browser Tab sources.
   - **Pad 5:** Image or Video media playback.
   - **Pad 6:** Camera PIP overlay (ON/OFF).
   - **Pad 7:** Quad Project (2×2 live composite of Pads 1–4).
   - **Pad 8:** Virtual Webcam output (video file or screen/window).

---

## 2. Configuring & Using Each Pad

### Pads 1–4: Screen, Window & Browser Tab
Click an empty pad (or click `⚙` / right-click → **Configure Source...**) to choose:

- **Entire Screen / Display:** Captures a full monitor.
- **Specific Window:** Captures an application window (continues capturing even when covered by other windows; automatically freezes on the last frame if minimized).
- **Browser Tab (Chrome / Edge):** Lists open tabs across Chrome, Edge, Brave, Opera, Vivaldi, and Firefox.
  - Projecting a Browser Tab pad automatically switches that browser window to the chosen tab.
  - If you switch to another tab in the same browser window, the pad and projector **freeze on the last captured frame** of that tab instead of showing the other tab.

**Projecting:**
- Click a configured pad to select it, then click **PROJECT** (`Ctrl+P`).
- While projecting, clicking another configured pad switches the projected output immediately.

---

### Pad 5: Image or Video Media
Click Pad 5 (or `⚙` → **Select Image / Video...**) to choose a local media file:
- **Images:** `.png`, `.jpg`, `.jpeg`, `.bmp`, `.webp`, `.gif`
- **Videos:** `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.flv`, `.m4v`, `.webm`
- **Options:** Custom pad label, **Loop video playback** (on by default), and **Output Monitor**.
- Video audio plays automatically if `ffplay` (FFmpeg) is available on `PATH`.

---

### Pad 6: Camera PIP (Lower-Right Overlay)
Overlays a physical webcam/camera feed onto the **lower-right corner** of all active projected screens and the Program Monitor.

- **Turn ON / OFF:** Click Pad 6, press `Ctrl+6`, or click **PIP: OFF / ON** in the right-hand panel.
- **Settings (`⚙` or right-click → Camera & PIP Settings...):**
  - **Camera Device:** Select camera index (`Detect` scans connected cameras).
  - **Resolution:** `640×480`, `1280×720`, or `1920×1080`.
  - **PIP Size:** `12%`–`45%` of screen width.
  - **Corner Margin:** `0`–`80 px` from the lower-right edges.
  - **Mirror camera horizontally** & **Rounded corners & border** toggles.
  - Settings are saved automatically to `%APPDATA%\ProjectionPad\config.json`.

---

### Pad 7: Quad Project
- Click Pad 7 (`Ctrl+7`) to project **Pads 1–4** simultaneously as a live **2×2 grid** (`1920×1080` composite) with pad labels.
- Click Pad 7 again (or **STOP PROJECTION**) to stop.

---

### Pad 8: Virtual Webcam Output
Streams a **Video File** (with audio via `ffplay`) or a **Screen / Window** to a virtual webcam for Zoom, Teams, or Google Meet.
- Click Pad 8 (`Ctrl+8`) to choose **Video File** or **Screen / Window**, and click again (or **Stop Webcam**) to stop.
- If no virtual camera driver is installed, Projection Pad offers to install the built-in **Unity Capture** driver automatically.

---

## 3. Multi-Monitor Support (2, 3, 4+ Screens)

Projection Pad detects all connected displays (`Display 2`, `Display 3`, `Display 4`, etc.):

- **Automatic Extend Mode & Window Safety (Windows):** On app launch and when pressing **Project**, Projection Pad automatically switches Windows to **Extend** mode (`DisplaySwitch.exe /extend`) so all connected monitors turn on, and moves any open application windows from secondary monitors onto the primary monitor so they aren't hidden behind the projector.
- **Global Output:** Use the **Output:** dropdown in the top toolbar to pick a specific external monitor or **All External**.
- **Per-Pad Output Routing:**
  - Set **Output Monitor** inside `Configure Pad` (Pads 1–4) or `Media Source` (Pad 5), or right-click any pad (Pads 1–5 & Quad Pad 7) → **Output Monitor ▸**.
  - Pads routed to a specific monitor display a tag such as `[D2]`, `[D3]`, or `[ALL]`.
  - Pads assigned to **different** secondary monitors can project **simultaneously** (e.g., Pad 1 on Display 2 and Pad 2 on Display 3 at the same time).
- **Display Settings:** Click **Display** (`Ctrl+D`) and use the **Display** dropdown to view or change the resolution and refresh rate of any connected monitor.

---

## 4. Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+1` – `Ctrl+4` | Select / switch Pads 1–4 |
| `Ctrl+5` | Select / switch Pad 5 (Image / Video) |
| `Ctrl+6` | Toggle Pad 6 (Camera PIP ON/OFF) |
| `Ctrl+7` | Toggle Pad 7 (Quad Project) |
| `Ctrl+8` | Toggle Pad 8 (Virtual Webcam) |
| `Ctrl+P` | Start projecting selected pad |
| `Ctrl+Shift+P` | Stop all projection |
| `Ctrl+R` | Refresh displays and windows |
| `Ctrl+D` | Open Display Settings |
| `Esc` *(on projector window)* | Hide projector window |

---

## 5. Building & Running

- **From source:**
  ```bash
  pip install PyQt5 mss pywin32 pyvirtualcam numpy opencv-python uiautomation
  python projection_pad.py
  ```
- **Pre-built binaries (Windows, macOS, Linux):** Every push to `main` automatically builds all three platforms via GitHub Actions (**Actions → latest build → Artifacts**):
  - `ProjectionPad-Windows` (`ProjectionPad.exe`)
  - `ProjectionPad-macOS` (`ProjectionPad-macOS.zip` containing `ProjectionPad.app`)
  - `ProjectionPad-Linux` (`ProjectionPad-Linux.tar.gz` containing `ProjectionPad`; requires `wmctrl` or `xdotool` for window listing)
