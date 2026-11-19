#!/usr/bin/env python3
"""
Projection Pad  v1.2
Virtual screen manager — up to 8 pads, each showing a window or display.
Pads 1–6: click to select, then 'Project' to send fullscreen to HDMI.
Pad 7:    screen/window → OBS Virtual Camera (webcam) output.
Pad 8:    video file    → OBS Virtual Camera (webcam) output with audio.

Requirements: pip install PyQt5 mss
Optional:     pip install pywin32 pyvirtualcam numpy opencv-python (Windows)
"""

import sys
import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional, List
from enum import Enum

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QFrame, QLabel,
        QPushButton, QVBoxLayout, QHBoxLayout, QGridLayout,
        QMenu, QAction, QDialog, QComboBox, QMessageBox,
        QToolButton, QSlider, QListWidget, QListWidgetItem,
        QGroupBox, QLineEdit, QDialogButtonBox, QShortcut,
        QFileDialog,
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QUrl
    from PyQt5.QtGui import (QPixmap, QImage, QColor, QPainter, QFont,
                             QPalette, QKeySequence, QDesktopServices)
except ImportError:
    sys.exit("PyQt5 required:  pip install PyQt5")

try:
    import mss
except ImportError:
    sys.exit("mss required:  pip install mss")

PLATFORM = platform.system()   # 'Windows' | 'Darwin' | 'Linux'

if PLATFORM == "Windows":
    try:
        import win32gui, win32process, win32api, win32ui, win32con
        import ctypes
        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
else:
    HAS_WIN32 = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import pyvirtualcam
    HAS_PYVIRTUALCAM = True
except ImportError:
    HAS_PYVIRTUALCAM = False

# ── constants ──────────────────────────────────────────────────────────────────

APP_NAME    = "Projection Pad"
VERSION     = "1.2"
MAX_PADS    = 8
PAD_COLS    = 4
DEFAULT_FPS = 2
PAD_W, PAD_H = 185, 185

# Stream Deck White — body / chrome (light theme)
C_BG       = "#EBEBEB"
C_SURFACE  = "#F5F5F7"
C_SURFACE2 = "#E5E5EA"
C_BORDER   = "#C6C6C8"
C_ACCENT   = "#1A1A1A"
C_ACTIVE   = "#34C759"
C_DANGER   = "#FF3B30"
C_TEXT     = "#1C1C1E"
C_DIM      = "#8E8E93"
C_HOVER    = "#E0E0E5"

# Stream Deck White — key / pad face (dark LCD face)
C_KEY_BG   = "#1A1A1A"
C_KEY_FT   = "#111111"
C_TEXT_K   = "#F5F5F7"
C_DIM_K    = "#555558"
C_WEBCAM   = "#FF9F0A"   # Elgato signature orange

# Webcam pad indices (zero-based)
WEBCAM_PAD_SCREEN = 6
WEBCAM_PAD_VIDEO  = 7

# ── data models ────────────────────────────────────────────────────────────────

class SourceType(Enum):
    EMPTY  = "empty"
    SCREEN = "screen"
    WINDOW = "window"
    VIDEO  = "video"

@dataclass
class WinInfo:
    id: str
    title: str
    app: str = ""
    bounds: Optional[tuple] = None

    def label(self) -> str:
        return f"{self.app}: {self.title}" if self.app else self.title

@dataclass
class ScreenInfo:
    index: int
    name: str
    bounds: tuple
    primary: bool = False

@dataclass
class PadConfig:
    index: int
    label: str = ""
    stype: SourceType = SourceType.EMPTY
    sid:   Optional[str] = None
    sname: str = "Empty"
    projected: bool = False

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        return f"Pad {self.index + 1}" if self.stype == SourceType.EMPTY else self.sname

# ── system bridge ──────────────────────────────────────────────────────────────

class Bridge:
    @staticmethod
    def screens() -> List[ScreenInfo]:
        result  = []
        primary = QApplication.primaryScreen()
        for i, s in enumerate(QApplication.screens()):
            g = s.geometry()
            result.append(ScreenInfo(
                index=i, name=s.name() or f"Display {i+1}",
                bounds=(g.x(), g.y(), g.width(), g.height()),
                primary=(s is primary),
            ))
        return result

    @staticmethod
    def external_screen() -> Optional[ScreenInfo]:
        ss  = Bridge.screens()
        ext = [s for s in ss if not s.primary]
        return ext[0] if ext else None

    @staticmethod
    def windows() -> List[WinInfo]:
        if PLATFORM == "Windows" and HAS_WIN32:
            return Bridge._wins_win32()
        if PLATFORM == "Darwin":
            return Bridge._wins_macos()
        if PLATFORM == "Linux":
            return Bridge._wins_linux()
        return []

    @staticmethod
    def _wins_win32() -> List[WinInfo]:
        wins = []
        def cb(hwnd, _):
            if not (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd)):
                return True
            title = win32gui.GetWindowText(hwnd)
            if len(title) < 2:
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                h   = win32api.OpenProcess(0x400, False, pid)
                app = os.path.basename(
                    win32process.GetModuleFileNameEx(h, 0)).replace(".exe", "")
                win32api.CloseHandle(h)
            except Exception:
                app = ""
            r = win32gui.GetWindowRect(hwnd)
            w, h = r[2] - r[0], r[3] - r[1]
            if w > 50 and h > 50:
                wins.append(WinInfo(str(hwnd), title, app, (r[0], r[1], w, h)))
            return True
        win32gui.EnumWindows(cb, None)
        return wins

    @staticmethod
    def _wins_macos() -> List[WinInfo]:
        wins = []
        try:
            from Quartz import (CGWindowListCopyWindowInfo,
                                kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
            for w in CGWindowListCopyWindowInfo(
                    kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
                name  = w.get("kCGWindowName", "")
                owner = w.get("kCGWindowOwnerName", "")
                b     = w.get("kCGWindowBounds", {})
                if not name:
                    continue
                x, y = int(b.get("X", 0)), int(b.get("Y", 0))
                ww, hh = int(b.get("Width", 0)), int(b.get("Height", 0))
                if ww > 50 and hh > 50:
                    wins.append(WinInfo(str(w.get("kCGWindowNumber", "")),
                                        name, owner, (x, y, ww, hh)))
        except Exception:
            pass
        return wins

    @staticmethod
    def _wins_linux() -> List[WinInfo]:
        wins = []
        try:
            out = subprocess.check_output(["wmctrl", "-l", "-G"],
                                          text=True, timeout=3)
            for line in out.splitlines():
                parts = line.split(None, 7)
                if len(parts) < 8:
                    continue
                wid   = parts[0]
                x, y  = int(parts[2]), int(parts[3])
                w, h  = int(parts[4]), int(parts[5])
                title = parts[7]
                if title and w > 50 and h > 50 and title not in ("N/A", ""):
                    wins.append(WinInfo(wid, title, bounds=(x, y, w, h)))
        except Exception:
            try:
                out = subprocess.check_output(
                    ["xdotool", "search", "--onlyvisible", "--name", ""],
                    text=True, timeout=3)
                for wid in out.splitlines():
                    if not wid:
                        continue
                    try:
                        name = subprocess.check_output(
                            ["xdotool", "getwindowname", wid],
                            text=True, timeout=1).strip()
                        if name:
                            wins.append(WinInfo(wid, name))
                    except Exception:
                        pass
            except Exception:
                pass
        return wins

# ── capture engine ─────────────────────────────────────────────────────────────

class Capture:
    _lock = threading.Lock()

    @classmethod
    def grab(cls, x: int, y: int, w: int, h: int) -> Optional[QPixmap]:
        if w <= 0 or h <= 0:
            return None
        try:
            with cls._lock:
                with mss.mss() as sct:
                    img = sct.grab({"top": y, "left": x, "width": w, "height": h})
                    qi = QImage(bytes(img.bgra), img.width, img.height,
                                img.width * 4, QImage.Format_ARGB32)
                    return QPixmap.fromImage(qi)
        except Exception:
            return None

    @classmethod
    def grab_window_win32(cls, hwnd_str: str) -> Optional[QPixmap]:
        if not HAS_WIN32:
            return None
        try:
            hwnd = int(hwnd_str)
            if not win32gui.IsWindow(hwnd):
                return None
            placement = win32gui.GetWindowPlacement(hwnd)
            was_minimised = (placement[1] == win32con.SW_SHOWMINIMIZED)
            if was_minimised:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w <= 0 or h <= 0:
                return None
            hwnd_dc  = win32gui.GetWindowDC(hwnd)
            mfc_dc   = win32ui.CreateDCFromHandle(hwnd_dc)
            mem_dc   = mfc_dc.CreateCompatibleDC()
            bmp      = win32ui.CreateBitmap()
            bmp.CreateCompatibleBitmap(mfc_dc, w, h)
            mem_dc.SelectObject(bmp)
            ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 2)
            info = bmp.GetInfo()
            data = bmp.GetBitmapBits(True)
            win32gui.DeleteObject(bmp.GetHandle())
            mem_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)
            if was_minimised:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            qi = QImage(data, info['bmWidth'], info['bmHeight'], QImage.Format_ARGB32)
            return QPixmap.fromImage(qi)
        except Exception:
            return None

    @classmethod
    def for_pad(cls, cfg: PadConfig, screens: List[ScreenInfo],
                windows: List[WinInfo]) -> Optional[QPixmap]:
        if cfg.stype == SourceType.SCREEN:
            try:
                s = next(s for s in screens if str(s.index) == cfg.sid)
                return cls.grab(*s.bounds)
            except StopIteration:
                return None
        if cfg.stype == SourceType.WINDOW:
            try:
                win = next(w for w in windows if w.id == cfg.sid)
            except StopIteration:
                return None
            if PLATFORM == "Windows" and HAS_WIN32:
                px = cls.grab_window_win32(win.id)
                if px:
                    return px
            if win.bounds:
                return cls.grab(*win.bounds)
        return None

# ── capture worker thread ──────────────────────────────────────────────────────

class CaptureThread(QThread):
    frame = pyqtSignal(int, QPixmap)

    def __init__(self, pads: List[PadConfig]):
        super().__init__()
        self.pads    = pads
        self._screens: List[ScreenInfo] = []
        self._windows: List[WinInfo]    = []
        self._fps    = DEFAULT_FPS
        self._live   = False
        self._mu     = threading.Lock()

    def set_sources(self, screens: List[ScreenInfo], windows: List[WinInfo]):
        with self._mu:
            self._screens = screens[:]
            self._windows = windows[:]

    def set_fps(self, v: int):
        self._fps = max(1, min(v, 10))

    def stop(self):
        self._live = False

    def run(self):
        self._live = True
        while self._live:
            t0 = time.time()
            with self._mu:
                ss, ws, cfgs = self._screens[:], self._windows[:], self.pads[:]
            for cfg in cfgs:
                if not self._live:
                    break
                if cfg.stype in (SourceType.EMPTY, SourceType.VIDEO):
                    continue
                # WebcamThread handles previews for webcam pads
                if cfg.index in (WEBCAM_PAD_SCREEN, WEBCAM_PAD_VIDEO):
                    continue
                px = Capture.for_pad(cfg, ss, ws)
                if px:
                    self.frame.emit(cfg.index, px)
            dt = time.time() - t0
            time.sleep(max(0.0, 1.0 / self._fps - dt))

# ── projector capture thread ───────────────────────────────────────────────────

class ProjectorThread(QThread):
    frame = pyqtSignal(QPixmap)

    def __init__(self):
        super().__init__()
        self._cfg: Optional[PadConfig] = None
        self._screens: List[ScreenInfo] = []
        self._windows: List[WinInfo]    = []
        self._fps    = 60
        self._live   = False
        self._mu     = threading.Lock()

    def set_target(self, cfg: PadConfig, screens: List[ScreenInfo],
                   windows: List[WinInfo]):
        with self._mu:
            self._cfg     = cfg
            self._screens = screens[:]
            self._windows = windows[:]

    def update_sources(self, screens: List[ScreenInfo], windows: List[WinInfo]):
        with self._mu:
            self._screens = screens[:]
            self._windows = windows[:]

    def set_fps(self, fps: int):
        self._fps = max(1, min(fps, 120))

    def stop(self):
        self._live = False

    def run(self):
        self._live = True
        while self._live:
            t0 = time.time()
            with self._mu:
                cfg, ss, ws = self._cfg, self._screens[:], self._windows[:]
            if cfg and cfg.stype not in (SourceType.EMPTY, SourceType.VIDEO):
                px = Capture.for_pad(cfg, ss, ws)
                if px:
                    self.frame.emit(px)
            dt = time.time() - t0
            time.sleep(max(0.0, 1.0 / self._fps - dt))

# ── webcam output thread ───────────────────────────────────────────────────────

class WebcamThread(QThread):
    """Outputs a screen/window or video file as a virtual webcam via pyvirtualcam + OBS."""
    frame_preview = pyqtSignal(int, QPixmap)
    error         = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._live   = False
        self._mu     = threading.Lock()
        self._mode: str = "screen"
        self._screen_cfg: Optional[PadConfig] = None
        self._screens: List[ScreenInfo] = []
        self._windows: List[WinInfo]    = []
        self._video_path: str = ""

    def set_screen_mode(self, cfg: PadConfig, screens: List[ScreenInfo],
                        windows: List[WinInfo]):
        with self._mu:
            self._mode       = "screen"
            self._screen_cfg = cfg
            self._screens    = screens[:]
            self._windows    = windows[:]

    def set_video_mode(self, path: str):
        with self._mu:
            self._mode       = "video"
            self._video_path = path

    def stop(self):
        self._live = False

    def run(self):
        self._live = True
        if self._mode == "screen":
            self._run_screen()
        else:
            self._run_video()

    def _run_screen(self):
        if not HAS_PYVIRTUALCAM:
            self.error.emit(
                "pyvirtualcam not installed.\n\n"
                "Install:  pip install pyvirtualcam\n\n"
                "Also requires OBS Studio with OBS Virtual Camera enabled.")
            return
        with self._mu:
            cfg, ss, ws = self._screen_cfg, self._screens[:], self._windows[:]
        if cfg is None:
            return
        px = Capture.for_pad(cfg, ss, ws)
        if not px:
            self.error.emit("Could not capture source for webcam output.")
            return
        cam_w = max((px.width()  // 2) * 2, 640)
        cam_h = max((px.height() // 2) * 2, 480)
        try:
            with pyvirtualcam.Camera(width=cam_w, height=cam_h, fps=30,
                                     fmt=pyvirtualcam.PixelFormat.RGB,
                                     print_fps=False) as cam:
                _prev_tick = 0
                while self._live:
                    with self._mu:
                        cfg2, ss2, ws2 = (self._screen_cfg,
                                          self._screens[:], self._windows[:])
                    px2 = Capture.for_pad(cfg2, ss2, ws2) if cfg2 else None
                    if px2 and HAS_NUMPY:
                        scaled  = px2.scaled(cam_w, cam_h,
                                             Qt.KeepAspectRatioByExpanding,
                                             Qt.SmoothTransformation)
                        cropped = scaled.copy(0, 0, cam_w, cam_h)
                        img = cropped.toImage().convertToFormat(QImage.Format_RGB888)
                        ptr = img.bits()
                        ptr.setsize(cam_h * cam_w * 3)
                        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(
                            (cam_h, cam_w, 3)).copy()
                        cam.send(arr)
                        cam.sleep_until_next_frame()
                        tick = int(time.time() * 5)
                        if tick != _prev_tick:
                            _prev_tick = tick
                            self.frame_preview.emit(WEBCAM_PAD_SCREEN, cropped)
                    else:
                        time.sleep(1.0 / 30)
        except Exception as ex:
            self.error.emit(
                f"Virtual camera error:\n{ex}\n\n"
                "Ensure OBS Studio is installed and OBS Virtual Camera is active.")

    def _run_video(self):
        if not HAS_PYVIRTUALCAM:
            self.error.emit(
                "pyvirtualcam not installed.\n\n"
                "Install:  pip install pyvirtualcam\n\n"
                "Also requires OBS Studio with OBS Virtual Camera enabled.")
            return
        if not HAS_CV2:
            self.error.emit(
                "opencv-python not installed.\n\n"
                "Install:  pip install opencv-python")
            return
        with self._mu:
            path = self._video_path
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self.error.emit(f"Could not open video file:\n{path}")
            return
        fps   = cap.get(cv2.CAP_PROP_FPS) or 25.0
        iw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        ih    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cam_w = max((iw // 2) * 2, 640)
        cam_h = max((ih // 2) * 2, 480)
        audio_proc = None
        try:
            flags = 0x08000000 if PLATFORM == "Windows" else 0
            audio_proc = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-vn", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except (FileNotFoundError, OSError):
            pass
        try:
            with pyvirtualcam.Camera(width=cam_w, height=cam_h, fps=int(fps),
                                     fmt=pyvirtualcam.PixelFormat.RGB,
                                     print_fps=False) as cam:
                frame_num    = 0
                prev_interval = max(1, int(fps // 5))
                while self._live:
                    ret, frame = cap.read()
                    if not ret:
                        break
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    if frame_rgb.shape[1] != cam_w or frame_rgb.shape[0] != cam_h:
                        frame_rgb = cv2.resize(frame_rgb, (cam_w, cam_h))
                    cam.send(frame_rgb)
                    cam.sleep_until_next_frame()
                    frame_num += 1
                    if frame_num % prev_interval == 0:
                        qi = QImage(frame_rgb.tobytes(), cam_w, cam_h,
                                    cam_w * 3, QImage.Format_RGB888)
                        self.frame_preview.emit(WEBCAM_PAD_VIDEO,
                                                QPixmap.fromImage(qi))
        except Exception as ex:
            self.error.emit(f"Video webcam error:\n{ex}")
        finally:
            cap.release()
            if audio_proc:
                try:
                    audio_proc.terminate()
                except Exception:
                    pass

# ── pad widget ─────────────────────────────────────────────────────────────────

class PadWidget(QFrame):
    clicked   = pyqtSignal(int)
    configure = pyqtSignal(int)

    def __init__(self, cfg: PadConfig):
        super().__init__()
        self.cfg          = cfg
        self._projected   = False
        self._selected    = False
        self._webcam_live = False
        self._setup()

    def _setup(self):
        self.setFixedSize(PAD_W, PAD_H)
        self.setObjectName("Pad")
        self.setCursor(Qt.PointingHandCursor)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setObjectName("Preview")
        self._placeholder()

        footer = QWidget()
        footer.setObjectName("Footer")
        footer.setFixedHeight(28)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(8, 0, 6, 0)
        fl.setSpacing(4)

        self.dot = QLabel("●")
        self.dot.setObjectName("Dot")
        self.dot.setFixedWidth(10)

        self.name = QLabel(self.cfg.display_name)
        self.name.setObjectName("Name")

        self.sc_lbl = QLabel(f"Ctrl+{self.cfg.index + 1}")
        self.sc_lbl.setObjectName("ShortcutLbl")
        self.sc_lbl.setFixedWidth(44)
        self.sc_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.btn = QToolButton()
        self.btn.setText("⚙")
        self.btn.setObjectName("Gear")
        self.btn.setFixedSize(18, 18)
        self.btn.clicked.connect(lambda: self.configure.emit(self.cfg.index))

        fl.addWidget(self.dot)
        fl.addWidget(self.name, 1)
        fl.addWidget(self.sc_lbl)
        fl.addWidget(self.btn)

        self.setToolTip(
            f"Pad {self.cfg.index + 1}  •  Shortcut: Ctrl+{self.cfg.index + 1}")

        vl.addWidget(self.preview, 1)
        vl.addWidget(footer)
        self._restyle()

    def _placeholder(self):
        from PyQt5.QtCore import QRect
        ph_h = PAD_H - 28   # 157 px
        pm = QPixmap(PAD_W, ph_h)
        pm.fill(QColor(C_KEY_BG))
        p = QPainter(pm)

        if self.cfg.index == WEBCAM_PAD_SCREEN:
            # Symbol row  (y=14..54)
            p.setPen(QColor(C_WEBCAM))
            p.setFont(QFont("Segoe UI,Inter,sans-serif", 22, QFont.Bold))
            p.drawText(QRect(0, 14, PAD_W, 40), Qt.AlignCenter, "●")
            # Label rows  (y=62..116)
            p.setFont(QFont("Segoe UI,Inter,sans-serif", 13, QFont.Bold))
            p.drawText(QRect(0, 62, PAD_W, 26), Qt.AlignCenter, "SCREEN")
            p.drawText(QRect(0, 90, PAD_W, 26), Qt.AlignCenter, "WEBCAM")
            # Hint row  (y=130..150)
            p.setPen(QColor(C_DIM_K))
            p.setFont(QFont("Segoe UI,Inter,sans-serif", 8))
            p.drawText(QRect(4, 130, PAD_W - 8, 20), Qt.AlignCenter,
                       "click to start  •  gear: set source")

        elif self.cfg.index == WEBCAM_PAD_VIDEO:
            # Symbol row
            p.setPen(QColor(C_WEBCAM))
            p.setFont(QFont("Segoe UI,Inter,sans-serif", 22, QFont.Bold))
            p.drawText(QRect(0, 14, PAD_W, 40), Qt.AlignCenter, "▶")
            # Label rows
            p.setFont(QFont("Segoe UI,Inter,sans-serif", 13, QFont.Bold))
            p.drawText(QRect(0, 62, PAD_W, 26), Qt.AlignCenter, "VIDEO")
            p.drawText(QRect(0, 90, PAD_W, 26), Qt.AlignCenter, "WEBCAM")
            # Hint row
            p.setPen(QColor(C_DIM_K))
            p.setFont(QFont("Segoe UI,Inter,sans-serif", 8))
            p.drawText(QRect(4, 130, PAD_W - 8, 20), Qt.AlignCenter,
                       "click to select video file")

        else:
            p.setPen(QColor(C_DIM_K))
            p.setFont(QFont("Segoe UI,Inter,sans-serif", 10))
            p.drawText(pm.rect(), Qt.AlignCenter,
                       f"Pad {self.cfg.index + 1}\n⚙  to configure\nCtrl+{self.cfg.index + 1}")

        p.end()
        self.preview.setPixmap(pm)

    def set_frame(self, px: QPixmap):
        scaled = px.scaled(self.preview.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(scaled)

    def set_state(self, projected: bool = False, selected: bool = False,
                  webcam: bool = False):
        self._projected   = projected
        self._selected    = selected
        self._webcam_live = webcam
        self._restyle()
        if webcam:
            dot_color = C_WEBCAM
        elif projected:
            dot_color = C_ACTIVE
        elif selected:
            dot_color = "#FFFFFF"
        elif self.cfg.stype not in (SourceType.EMPTY, SourceType.VIDEO):
            dot_color = "#666668"
        else:
            dot_color = C_DIM_K
        self.dot.setStyleSheet(
            f"color:{dot_color};background:transparent;font-size:7px;")

    def update_cfg(self, cfg: PadConfig):
        self.cfg = cfg
        self.name.setText(cfg.display_name)
        if cfg.stype in (SourceType.EMPTY, SourceType.VIDEO):
            self._placeholder()
        self._restyle()

    def _restyle(self):
        is_webcam = self.cfg.index in (WEBCAM_PAD_SCREEN, WEBCAM_PAD_VIDEO)
        if self._webcam_live:
            border = f"2px solid {C_WEBCAM}"
        elif self._projected:
            border = f"2px solid {C_ACTIVE}"
        elif self._selected:
            border = "2px solid #FFFFFF"
        elif self.cfg.stype not in (SourceType.EMPTY, SourceType.VIDEO):
            border = f"1px solid {C_ACTIVE}44"
        elif is_webcam:
            border = f"1px solid {C_WEBCAM}44"
        else:
            border = "1px solid #2E2E2E"

        hover_border = C_WEBCAM if is_webcam else "#FFFFFF"

        self.setStyleSheet(f"""
QFrame#Pad{{
  background:{C_KEY_BG};
  border:{border};
  border-radius:8px;
}}
QFrame#Pad:hover{{
  background:#222222;
  border-color:{hover_border};
}}
QWidget#Footer{{
  background:{C_KEY_FT};
  border-top:1px solid #2A2A2A;
  border-bottom-left-radius:8px;
  border-bottom-right-radius:8px;
}}
QLabel#Name{{
  color:{C_TEXT_K};
  font-size:11px;
  background:transparent;
}}
QToolButton#Gear{{
  background:transparent;
  border:none;
  color:{C_DIM_K};
  font-size:12px;
}}
QToolButton#Gear:hover{{color:{C_TEXT_K}}}
QLabel#ShortcutLbl{{
  color:#3A3A3C;
  font-size:9px;
  background:transparent;
}}
""")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.cfg.index)
        super().mousePressEvent(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.setStyleSheet(
            f"QMenu{{background:{C_KEY_BG};border:1px solid #2E2E2E;"
            f"color:{C_TEXT_K};border-radius:6px;padding:4px}}"
            f"QMenu::item{{padding:6px 20px;border-radius:3px}}"
            f"QMenu::item:selected{{background:#2D2D2D}}"
        )
        is_webcam = self.cfg.index in (WEBCAM_PAD_SCREEN, WEBCAM_PAD_VIDEO)
        if is_webcam:
            label = "CAM  Start Webcam Output"
            a1 = m.addAction(label)
            a1.triggered.connect(lambda: self.clicked.emit(self.cfg.index))
            m.addSeparator()
            a2 = m.addAction("CONF  Configure Source...")
            a2.triggered.connect(lambda: self.configure.emit(self.cfg.index))
        else:
            a1 = m.addAction("PLAY  Project This Pad")
            a1.setEnabled(self.cfg.stype != SourceType.EMPTY)
            a1.triggered.connect(lambda: self.clicked.emit(self.cfg.index))
            m.addSeparator()
            a2 = m.addAction("CONF  Configure Source...")
            a2.triggered.connect(lambda: self.configure.emit(self.cfg.index))
            a3 = m.addAction("CLR   Clear Pad")
            a3.setEnabled(self.cfg.stype != SourceType.EMPTY)
            a3.triggered.connect(self._clear)
        m.exec_(e.globalPos())

    def _clear(self):
        self.cfg.stype = SourceType.EMPTY
        self.cfg.sid   = None
        self.cfg.sname = "Empty"
        self.cfg.label = ""
        self.update_cfg(self.cfg)

# ── dialog stylesheet (light theme) ───────────────────────────────────────────

_DLGSS = f"""
QDialog{{background:{C_SURFACE};color:{C_TEXT}}}
QLabel{{color:{C_TEXT}}}
QGroupBox{{
  color:{C_DIM};border:1px solid {C_BORDER};
  border-radius:6px;margin-top:10px;padding-top:8px;font-size:11px
}}
QGroupBox::title{{subcontrol-origin:margin;padding:0 6px;color:{C_DIM}}}
QComboBox,QListWidget,QLineEdit{{
  background:{C_SURFACE};border:1px solid {C_BORDER};
  border-radius:4px;color:{C_TEXT};padding:4px 8px
}}
QComboBox::drop-down{{border:none}}
QComboBox QAbstractItemView{{
  background:{C_SURFACE};border:1px solid {C_BORDER};
  color:{C_TEXT};selection-background-color:{C_HOVER}
}}
QListWidget::item{{padding:5px}}
QListWidget::item:selected{{
  background:{C_ACTIVE}22;
  border:1px solid {C_ACTIVE}44;
  border-radius:3px
}}
QPushButton{{
  background:{C_SURFACE2};border:1px solid {C_BORDER};
  border-radius:6px;color:{C_TEXT};padding:7px 18px
}}
QPushButton:hover{{background:{C_HOVER}}}
QPushButton#OK{{background:{C_ACCENT};border:none;color:#FFFFFF;font-weight:600}}
QPushButton#OK:hover{{background:#333333}}
"""

# ── webcam setup dialog ────────────────────────────────────────────────────────

class WebcamSetupDialog(QDialog):
    """Explains virtual camera requirements and provides one-click download buttons."""

    OBS_URL     = "https://obsproject.com/"
    UNITY_URL   = "https://github.com/schellingb/UnityCapture"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Virtual Camera Setup")
        self.setModal(True)
        self.setFixedSize(500, 400)
        self.setStyleSheet(_DLGSS)
        self._build()

    def _build(self):
        vl = QVBoxLayout(self)
        vl.setSpacing(12)
        vl.setContentsMargins(20, 20, 20, 20)

        intro = QLabel(
            "Webcam output (Pads 7 & 8) needs a virtual camera driver so that "
            "apps like Zoom, Teams, and Google Meet can detect it as a camera input.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{C_TEXT};font-size:12px;")
        vl.addWidget(intro)

        # ── Option A: OBS Studio ──────────────────────────────────────────────
        obs_grp = QGroupBox("Option A  —  OBS Studio  (recommended, free)")
        obs_vl  = QVBoxLayout(obs_grp)
        obs_desc = QLabel(
            "Free open-source streaming app with a built-in virtual camera.\n"
            "①  Install OBS Studio\n"
            "②  Open OBS and click “Start Virtual Camera” in the Controls panel\n"
            "③  Keep OBS running in the background — that’s it")
        obs_desc.setWordWrap(True)
        obs_desc.setStyleSheet(f"color:{C_DIM};font-size:11px;")
        obs_btn = QPushButton("⬇  Download OBS Studio  —  obsproject.com")
        obs_btn.setObjectName("OK")
        obs_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self.OBS_URL)))
        obs_vl.addWidget(obs_desc)
        obs_vl.addWidget(obs_btn)
        vl.addWidget(obs_grp)

        # ── Option B: Unity Capture ───────────────────────────────────────────
        uc_grp = QGroupBox("Option B  —  Unity Capture  (lightweight driver)")
        uc_vl  = QVBoxLayout(uc_grp)
        uc_desc = QLabel(
            "Small standalone driver — install once, no app needs to stay open.\n"
            "Virtual camera is always available after installation.")
        uc_desc.setWordWrap(True)
        uc_desc.setStyleSheet(f"color:{C_DIM};font-size:11px;")
        uc_btn = QPushButton("⬇  Open Unity Capture on GitHub")
        uc_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self.UNITY_URL)))
        uc_vl.addWidget(uc_desc)
        uc_vl.addWidget(uc_btn)
        vl.addWidget(uc_grp)

        # ── after-install note ────────────────────────────────────────────────
        note = QLabel(
            "After installing, click the webcam pad again to start streaming.")
        note.setStyleSheet(f"color:{C_DIM};font-size:10px;")
        vl.addWidget(note)

        btns = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btns.addStretch()
        btns.addWidget(close_btn)
        vl.addLayout(btns)


# ── configure dialog ───────────────────────────────────────────────────────────

class ConfigDialog(QDialog):
    def __init__(self, cfg: PadConfig, wins: List[WinInfo],
                 screens: List[ScreenInfo], parent=None):
        super().__init__(parent)
        self.cfg     = cfg
        self.wins    = wins
        self.screens = screens
        self.setWindowTitle(f"Configure Pad {cfg.index + 1}")
        self.setModal(True)
        self.setFixedSize(420, 400)
        self.setStyleSheet(_DLGSS)
        self._build()

    def _build(self):
        vl = QVBoxLayout(self)
        vl.setSpacing(10)
        vl.setContentsMargins(16, 16, 16, 16)

        row = QHBoxLayout()
        row.addWidget(QLabel("Label (optional):"))
        self.lbl_edit = QLineEdit()
        self.lbl_edit.setPlaceholderText(f"Pad {self.cfg.index + 1}")
        if self.cfg.label:
            self.lbl_edit.setText(self.cfg.label)
        row.addWidget(self.lbl_edit, 1)
        vl.addLayout(row)

        gb = QGroupBox("Source Type")
        tl = QHBoxLayout(gb)
        self.type_cb = QComboBox()
        self.type_cb.addItems(["Empty", "Entire Screen / Display", "Specific Window"])
        idx_map = {SourceType.EMPTY: 0, SourceType.SCREEN: 1, SourceType.WINDOW: 2}
        self.type_cb.setCurrentIndex(idx_map.get(self.cfg.stype, 0))
        self.type_cb.currentIndexChanged.connect(self._fill)
        tl.addWidget(self.type_cb)
        vl.addWidget(gb)

        self.src_grp = QGroupBox("Select Source")
        sl = QVBoxLayout(self.src_grp)
        self.src_list = QListWidget()
        self.src_list.setMinimumHeight(140)
        ref = QPushButton("Refresh List")
        ref.clicked.connect(self._fill)
        sl.addWidget(self.src_list)
        sl.addWidget(ref)
        vl.addWidget(self.src_grp)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply")
        ok.setObjectName("OK")
        ok.clicked.connect(self._apply)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        vl.addLayout(btns)
        self._fill()

    def _fill(self):
        self.src_list.clear()
        ti = self.type_cb.currentIndex()
        self.src_grp.setEnabled(ti > 0)
        if ti == 1:
            for s in self.screens:
                tag = "Primary" if s.primary else "External"
                txt = f"{tag}  {s.name}  ({s.bounds[2]}x{s.bounds[3]})"
                item = QListWidgetItem(txt)
                item.setData(Qt.UserRole, str(s.index))
                self.src_list.addItem(item)
                if self.cfg.stype == SourceType.SCREEN and self.cfg.sid == str(s.index):
                    self.src_list.setCurrentItem(item)
        elif ti == 2:
            for w in self.wins:
                item = QListWidgetItem(w.label())
                item.setData(Qt.UserRole, w.id)
                self.src_list.addItem(item)
                if self.cfg.stype == SourceType.WINDOW and self.cfg.sid == w.id:
                    self.src_list.setCurrentItem(item)

    def _apply(self):
        ti = self.type_cb.currentIndex()
        self.cfg.label = self.lbl_edit.text().strip()
        if ti == 0:
            self.cfg.stype = SourceType.EMPTY
            self.cfg.sid   = None
            self.cfg.sname = "Empty"
        else:
            item = self.src_list.currentItem()
            if not item:
                QMessageBox.warning(self, "No selection", "Please select a source.")
                return
            self.cfg.stype = SourceType.SCREEN if ti == 1 else SourceType.WINDOW
            self.cfg.sid   = item.data(Qt.UserRole)
            self.cfg.sname = item.text()
        self.accept()

# ── video webcam config dialog ─────────────────────────────────────────────────

class VideoWebcamConfigDialog(QDialog):
    def __init__(self, current_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Video Webcam — Pad 8")
        self.setModal(True)
        self.setFixedSize(500, 180)
        self.setStyleSheet(_DLGSS)
        self._path = current_path
        self._build()

    @property
    def path(self) -> str:
        return self._path

    def _build(self):
        vl = QVBoxLayout(self)
        vl.setSpacing(10)
        vl.setContentsMargins(16, 16, 16, 16)

        info = QLabel(
            "Select a video file to output as a virtual webcam on Pad 8.\n"
            "Audio is played via ffplay (requires FFmpeg installed).")
        info.setWordWrap(True)
        info.setStyleSheet(f"color:{C_DIM};font-size:11px;")
        vl.addWidget(info)

        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("No file selected...")
        if self._path:
            self.path_edit.setText(self._path)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        vl.addLayout(row)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Start Webcam")
        ok.setObjectName("OK")
        ok.clicked.connect(self._apply)
        btns.addStretch()
        btns.addWidget(cancel)
        btns.addWidget(ok)
        vl.addLayout(btns)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Video File", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.wmv *.flv *.m4v);;All Files (*)")
        if path:
            self.path_edit.setText(path)

    def _apply(self):
        p = self.path_edit.text().strip()
        if not p:
            QMessageBox.warning(self, "No File", "Please select a video file.")
            return
        if not os.path.isfile(p):
            QMessageBox.warning(self, "File Not Found", f"File not found:\n{p}")
            return
        self._path = p
        self.accept()

# ── display settings dialog ────────────────────────────────────────────────────

class DisplaySettingsDialog(QDialog):
    def __init__(self, screen: Optional[ScreenInfo], parent=None):
        super().__init__(parent)
        self._screen = screen
        self.setWindowTitle("Display Settings")
        self.setModal(True)
        self.setFixedSize(420, 300)
        self.setStyleSheet(_DLGSS)
        self._modes: List[tuple] = []
        self._build()

    def _build(self):
        vl = QVBoxLayout(self)
        vl.setSpacing(10)
        vl.setContentsMargins(16, 16, 16, 16)

        if self._screen:
            cur = self._current_mode()
            if cur:
                hz_note = f" @ {cur[2]} Hz" if cur[2] else ""
                info_txt = f"{self._screen.name}  —  current: {cur[0]}x{cur[1]}{hz_note}"
            else:
                info_txt = (f"{self._screen.name}  —  "
                            f"{self._screen.bounds[2]}x{self._screen.bounds[3]}")
        else:
            info_txt = "No external display detected"

        info = QLabel(info_txt)
        info.setStyleSheet(
            f"color:{C_DIM};font-size:11px;background:{C_SURFACE2};"
            f"padding:8px;border-radius:4px;border:1px solid {C_BORDER}")
        vl.addWidget(info)

        if self._screen and HAS_WIN32:
            self._modes = self._enum_modes()

        r1 = QHBoxLayout()
        lbl1 = QLabel("Resolution:"); lbl1.setFixedWidth(100)
        self.res_cb = QComboBox()
        r1.addWidget(lbl1); r1.addWidget(self.res_cb, 1)
        vl.addLayout(r1)

        r2 = QHBoxLayout()
        lbl2 = QLabel("Refresh Rate:"); lbl2.setFixedWidth(100)
        self.hz_cb = QComboBox()
        r2.addWidget(lbl2); r2.addWidget(self.hz_cb, 1)
        vl.addLayout(r2)

        if self._modes:
            self._fill_res()
            self.res_cb.currentIndexChanged.connect(self._fill_hz)
        else:
            reason = "Windows + pywin32 required" if not HAS_WIN32 else "no modes found"
            self.res_cb.addItem(f"({reason})")
            self.hz_cb.addItem("—")
            self.res_cb.setEnabled(False)
            self.hz_cb.setEnabled(False)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("Apply"); self.ok_btn.setObjectName("OK")
        self.ok_btn.setEnabled(bool(self._modes))
        self.ok_btn.clicked.connect(self._apply)
        btns.addStretch(); btns.addWidget(cancel); btns.addWidget(self.ok_btn)
        vl.addStretch(); vl.addLayout(btns)

    def _enum_modes(self) -> List[tuple]:
        if not (HAS_WIN32 and self._screen):
            return []
        try:
            device = QApplication.screens()[self._screen.index].name()
            seen, modes = set(), []
            i = 0
            while True:
                try:
                    dm = win32api.EnumDisplaySettings(device, i)
                    if dm.BitsPerPel >= 24:
                        key = (dm.PelsWidth, dm.PelsHeight, dm.DisplayFrequency)
                        if key not in seen:
                            seen.add(key); modes.append(key)
                    i += 1
                except Exception:
                    break
            modes.sort(key=lambda m: (m[0] * m[1], m[2]), reverse=True)
            return modes
        except Exception:
            return []

    def _current_mode(self) -> Optional[tuple]:
        if not (HAS_WIN32 and self._screen):
            return None
        try:
            device = QApplication.screens()[self._screen.index].name()
            dm = win32api.EnumDisplaySettings(device, 0xFFFFFFFF)
            return (dm.PelsWidth, dm.PelsHeight, dm.DisplayFrequency)
        except Exception:
            return None

    def _fill_res(self):
        self.res_cb.blockSignals(True); self.res_cb.clear()
        seen = {}
        cur = self._current_mode()
        best = -1
        for idx, (w, h, hz) in enumerate(self._modes):
            if (w, h) not in seen:
                seen[(w, h)] = self.res_cb.count()
                self.res_cb.addItem(f"{w}x{h}", (w, h))
                if cur and cur[0] == w and cur[1] == h:
                    best = self.res_cb.count() - 1
        if best >= 0:
            self.res_cb.setCurrentIndex(best)
        self.res_cb.blockSignals(False)
        self._fill_hz()

    def _fill_hz(self):
        self.hz_cb.clear()
        data = self.res_cb.currentData()
        if not data:
            return
        w, h = data
        cur = self._current_mode()
        best = -1
        for mw, mh, hz in self._modes:
            if mw == w and mh == h:
                self.hz_cb.addItem(f"{hz} Hz", hz)
                if cur and cur[0] == w and cur[1] == h and cur[2] == hz:
                    best = self.hz_cb.count() - 1
        if best >= 0:
            self.hz_cb.setCurrentIndex(best)

    def _apply(self):
        if not (self._screen and HAS_WIN32):
            self.accept(); return
        res = self.res_cb.currentData()
        hz  = self.hz_cb.currentData()
        if not res or not hz:
            return
        w, h = res
        try:
            device = QApplication.screens()[self._screen.index].name()
            dm = win32api.EnumDisplaySettings(device, 0xFFFFFFFF)
            dm.PelsWidth = w; dm.PelsHeight = h; dm.DisplayFrequency = hz
            dm.Fields = 0x80000 | 0x100000 | 0x400000
            result = win32api.ChangeDisplaySettingsEx(device, dm, 2)
            if result < 0:
                QMessageBox.warning(self, "Cannot Apply",
                    "This resolution/refresh combination is not supported.")
                return
            win32api.ChangeDisplaySettingsEx(device, dm, 0)
            if result == 1:
                QMessageBox.information(self, "Applied",
                    "Mode changed. A restart may be needed for full effect.")
            self.accept()
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))

# ── projector output window ────────────────────────────────────────────────────

class Projector(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Projection Pad — Output")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._lbl = QLabel()
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setStyleSheet("background: black;")
        self.setCentralWidget(self._lbl)
        self._info = QLabel("Projection Pad", self)
        self._info.setStyleSheet(
            "color: rgba(255,255,255,.35); font-size: 12px; padding: 6px;")

    def place(self, si: Optional[ScreenInfo]):
        ss = QApplication.screens()
        if si and si.index < len(ss):
            self.setGeometry(ss[si.index].geometry())
        elif len(ss) > 1:
            self.setGeometry(ss[1].geometry())
        else:
            self.setGeometry(ss[0].geometry())

    def push(self, px: Optional[QPixmap], name: str = ""):
        if px:
            sc = px.scaled(self._lbl.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._lbl.setPixmap(sc)
            self._info.setText(name)
            self._lbl.setStyleSheet("background: black;")
        else:
            self._lbl.setPixmap(QPixmap())
            self._lbl.setText("No Signal")
            self._lbl.setStyleSheet(
                "background: black; color: rgba(255,255,255,.25); font-size: 28px;")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        super().keyPressEvent(e)

# ── main window stylesheet ─────────────────────────────────────────────────────

_MAINSS = f"""
QMainWindow, #Central{{background:{C_BG}}}
QStatusBar{{
  background:{C_SURFACE};color:{C_DIM};
  border-top:1px solid {C_BORDER};font-size:11px
}}
QPushButton{{
  background:{C_SURFACE2};border:1px solid {C_BORDER};
  border-radius:6px;color:{C_TEXT};padding:6px 14px;font-size:12px
}}
QPushButton:hover{{background:{C_HOVER};border-color:{C_ACCENT}}}
QPushButton#Project{{
  background:{C_ACCENT};border:none;color:#FFFFFF;font-weight:600
}}
QPushButton#Project:hover{{background:#333333}}
QPushButton#Stop{{
  background:{C_DANGER};border:none;color:#FFFFFF;font-weight:600
}}
QPushButton#Stop:hover{{background:#CC2A20}}
QPushButton#WebcamStop{{
  background:{C_WEBCAM};border:none;color:#1C1C1E;font-weight:600
}}
QPushButton#WebcamStop:hover{{background:#E08800}}
#Header{{background:{C_SURFACE};border-bottom:1px solid {C_BORDER}}}
#Controls{{background:{C_SURFACE};border-top:1px solid {C_BORDER}}}
QSlider::groove:horizontal{{
  height:3px;background:{C_BORDER};border-radius:2px
}}
QSlider::sub-page:horizontal{{background:{C_ACCENT};border-radius:2px}}
QSlider::handle:horizontal{{
  background:{C_ACCENT};width:11px;height:11px;
  margin:-4px 0;border-radius:6px
}}
"""

# ── main window ────────────────────────────────────────────────────────────────

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{VERSION}")
        self.setMinimumSize(820, 540)
        self.pads: List[PadConfig]  = [PadConfig(i) for i in range(MAX_PADS)]
        self.pad_widgets: List[PadWidget] = []
        self.selected: Optional[int]      = None
        self.projector: Optional[Projector] = None
        self.screens: List[ScreenInfo]    = []
        self.windows: List[WinInfo]       = []
        self.worker:       Optional[CaptureThread]  = None
        self.proj_worker:  Optional[ProjectorThread] = None
        self.webcam_worker: Optional[WebcamThread]  = None
        self._proj_fps: int  = 60
        self._webcam_video_path: str = ""
        self.setStyleSheet(_MAINSS)
        self._build()
        self._refresh()
        self._start_worker()
        QTimer.singleShot(600, self._init_display)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build(self):
        c = QWidget()
        c.setObjectName("Central")
        self.setCentralWidget(c)
        vl = QVBoxLayout(c)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # Header
        hdr = QWidget(); hdr.setObjectName("Header"); hdr.setFixedHeight(58)
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(20, 8, 20, 8)

        tc = QVBoxLayout(); tc.setSpacing(1)
        t  = QLabel("PROJECTION PAD")
        t.setFont(QFont("Segoe UI,Inter,sans-serif", 16, QFont.Bold))
        t.setStyleSheet(f"color:{C_TEXT};letter-spacing:1px;background:transparent")
        sub = QLabel(f"Virtual Screen Manager  •  v{VERSION}")
        sub.setStyleSheet(f"color:{C_DIM};font-size:10px;background:transparent")
        tc.addWidget(t); tc.addWidget(sub)

        self.di = QLabel("Detecting displays...")
        self.di.setStyleSheet(f"color:{C_DIM};font-size:11px;background:transparent")
        self.di.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        hl.addLayout(tc); hl.addStretch(); hl.addWidget(self.di)
        vl.addWidget(hdr)

        # Deck body — Stream Deck White chassis aesthetic: light body, dark keys
        deck = QWidget()
        deck.setObjectName("Deck")
        deck.setStyleSheet(
            f"QWidget#Deck{{background:{C_BG};}}"
        )
        gl = QGridLayout(deck)
        gl.setContentsMargins(20, 20, 20, 20)
        gl.setSpacing(12)
        for i in range(MAX_PADS):
            pw = PadWidget(self.pads[i])
            pw.clicked.connect(self._pad_click)
            pw.configure.connect(self._configure)
            self.pad_widgets.append(pw)
            gl.addWidget(pw, i // PAD_COLS, i % PAD_COLS)
        vl.addWidget(deck, 1)

        # Keyboard shortcuts
        for i in range(MAX_PADS):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._pad_click(idx))
        QShortcut(QKeySequence("Ctrl+P"),
                  self).activated.connect(self._project)
        QShortcut(QKeySequence("Ctrl+Shift+P"),
                  self).activated.connect(self._stop)
        QShortcut(QKeySequence("Ctrl+R"),
                  self).activated.connect(self._refresh)
        QShortcut(QKeySequence("Ctrl+D"),
                  self).activated.connect(self._display_settings)

        # Controls bar
        ctrl = QWidget(); ctrl.setObjectName("Controls"); ctrl.setFixedHeight(56)
        cl   = QHBoxLayout(ctrl)
        cl.setContentsMargins(20, 10, 20, 10); cl.setSpacing(10)

        fps_lbl = QLabel("Preview:")
        fps_lbl.setStyleSheet(f"color:{C_DIM};font-size:11px;background:transparent")
        self.fps_sl = QSlider(Qt.Horizontal)
        self.fps_sl.setRange(1, 10); self.fps_sl.setValue(DEFAULT_FPS)
        self.fps_sl.setFixedWidth(90)
        self.fps_lbl2 = QLabel(f"{DEFAULT_FPS} fps")
        self.fps_lbl2.setStyleSheet(f"color:{C_DIM};font-size:11px;background:transparent")
        self.fps_lbl2.setFixedWidth(38)
        self.fps_sl.valueChanged.connect(self._fps_changed)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)

        self.proj_btn = QPushButton("Project")
        self.proj_btn.setObjectName("Project")
        self.proj_btn.setEnabled(False)
        self.proj_btn.clicked.connect(self._project)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)

        self.wcam_stop_btn = QPushButton("Stop Webcam")
        self.wcam_stop_btn.setObjectName("WebcamStop")
        self.wcam_stop_btn.setEnabled(False)
        self.wcam_stop_btn.clicked.connect(self._stop_webcam)

        wcam_setup_btn = QPushButton("Webcam Setup")
        wcam_setup_btn.clicked.connect(self._webcam_setup)

        cl.addWidget(fps_lbl); cl.addWidget(self.fps_sl); cl.addWidget(self.fps_lbl2)
        cl.addStretch()
        self.disp_btn = QPushButton("Display")
        self.disp_btn.clicked.connect(self._display_settings)
        cl.addWidget(self.disp_btn)
        cl.addWidget(refresh_btn)
        cl.addWidget(wcam_setup_btn)
        cl.addWidget(self.proj_btn)
        cl.addWidget(self.stop_btn)
        cl.addWidget(self.wcam_stop_btn)
        vl.addWidget(ctrl)

        self.statusBar().showMessage(
            "Ready — Ctrl+1-6 select pad  •  Ctrl+7/8 webcam  "
            "•  Ctrl+P project  •  Ctrl+R refresh  •  Ctrl+D display")

        self._src_timer = QTimer()
        self._src_timer.setInterval(10_000)
        self._src_timer.timeout.connect(self._refresh)
        self._src_timer.start()

    # ── logic ──────────────────────────────────────────────────────────────────

    def _refresh(self):
        self.screens = Bridge.screens()
        self.windows = Bridge.windows()
        if self.worker:
            self.worker.set_sources(self.screens, self.windows)
        ext = Bridge.external_screen()
        if ext:
            qt_screens = QApplication.screens()
            if ext.index < len(qt_screens):
                self._proj_fps = round(qt_screens[ext.index].refreshRate())
            if self.proj_worker and self.proj_worker.isRunning():
                self.proj_worker.update_sources(self.screens, self.windows)
                self.proj_worker.set_fps(self._proj_fps)
            self.di.setText(
                f"  {len(self.screens)} displays  "
                f"External: {ext.name}  {self._proj_fps} Hz")
        else:
            self._proj_fps = 60
            self.di.setText(
                f"  {len(self.screens)} display(s) — no external detected")
        self.statusBar().showMessage(
            f"Sources refreshed — {len(self.windows)} windows, "
            f"{len(self.screens)} screens")

    def _start_worker(self):
        self.worker = CaptureThread(self.pads)
        self.worker.set_sources(self.screens, self.windows)
        self.worker.frame.connect(self._on_frame)
        self.worker.start()

    def _on_frame(self, idx: int, px: QPixmap):
        if 0 <= idx < len(self.pad_widgets):
            self.pad_widgets[idx].set_frame(px)

    def _on_webcam_frame(self, idx: int, px: QPixmap):
        if 0 <= idx < len(self.pad_widgets):
            self.pad_widgets[idx].set_frame(px)

    def _on_webcam_error(self, msg: str):
        self._stop_webcam()
        no_backend = ("device not found" in msg.lower() or
                      "no camera registered" in msg.lower() or
                      "not installed" in msg.lower())
        if no_backend:
            WebcamSetupDialog(self).exec_()
        else:
            QMessageBox.critical(self, "Webcam Error", msg)

    def _webcam_setup(self):
        WebcamSetupDialog(self).exec_()

    def _pad_click(self, idx: int):
        if idx == WEBCAM_PAD_SCREEN:
            self._start_webcam_screen()
            return
        if idx == WEBCAM_PAD_VIDEO:
            self._start_webcam_video()
            return
        cfg = self.pads[idx]
        if cfg.stype == SourceType.EMPTY:
            self._configure(idx)
            return
        self.selected = idx
        for i, pw in enumerate(self.pad_widgets):
            pw.set_state(projected=self.pads[i].projected, selected=(i == idx))
        self.proj_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Selected: {cfg.display_name} — click Project to send to HDMI")
        if self.projector and self.projector.isVisible():
            self._project()

    def _project(self):
        if self.selected is None:
            return
        ext = Bridge.external_screen()
        if not ext and len(self.screens) < 2:
            r = QMessageBox.question(
                self, "No External Display",
                "No external display detected.\n\n"
                "Connect an HDMI/display and click Refresh,\n"
                "or project to this screen for a preview?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r == QMessageBox.No:
                return
            ext = self.screens[0] if self.screens else None

        if self.projector is None:
            self.projector = Projector()
        self.projector.place(ext)
        self.projector.showFullScreen()

        for i, cfg in enumerate(self.pads):
            cfg.projected = (i == self.selected)
            self.pad_widgets[i].set_state(
                projected=cfg.projected, selected=(i == self.selected))

        pad = self.pads[self.selected]
        if self.proj_worker is None:
            self.proj_worker = ProjectorThread()
            self.proj_worker.frame.connect(self._on_proj_frame)
        self.proj_worker.set_target(pad, self.screens, self.windows)
        self.proj_worker.set_fps(self._proj_fps)
        if not self.proj_worker.isRunning():
            self.proj_worker.start()

        self.proj_btn.setText("Projecting...")
        self.stop_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Projecting: {pad.display_name}  {self._proj_fps} fps")

    def _stop(self):
        if self.proj_worker:
            self.proj_worker.stop()
            self.proj_worker.wait(1000)
            self.proj_worker = None
        if self.projector:
            self.projector.hide()
        for i, cfg in enumerate(self.pads):
            cfg.projected = False
            self.pad_widgets[i].set_state(selected=(i == self.selected))
        self.proj_btn.setText("Project")
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage("Projection stopped")

    def _start_webcam_screen(self):
        cfg = self.pads[WEBCAM_PAD_SCREEN]
        # If not configured, open source dialog first
        if cfg.stype == SourceType.EMPTY:
            self._configure(WEBCAM_PAD_SCREEN)
            if cfg.stype == SourceType.EMPTY:
                return
        # Toggle: if already running, stop it
        if self.webcam_worker and self.webcam_worker.isRunning():
            self._stop_webcam()
            return
        self._stop_webcam()
        self.webcam_worker = WebcamThread()
        self.webcam_worker.set_screen_mode(cfg, self.screens, self.windows)
        self.webcam_worker.frame_preview.connect(self._on_webcam_frame)
        self.webcam_worker.error.connect(self._on_webcam_error)
        self.webcam_worker.start()
        self.pad_widgets[WEBCAM_PAD_SCREEN].set_state(webcam=True)
        self.wcam_stop_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Webcam output active (Pad 7) — {cfg.display_name}")

    def _start_webcam_video(self):
        # Toggle: if running, stop
        if self.webcam_worker and self.webcam_worker.isRunning():
            self._stop_webcam()
            return
        dlg = VideoWebcamConfigDialog(self._webcam_video_path, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._webcam_video_path = dlg.path
        self._stop_webcam()
        self.webcam_worker = WebcamThread()
        self.webcam_worker.set_video_mode(self._webcam_video_path)
        self.webcam_worker.frame_preview.connect(self._on_webcam_frame)
        self.webcam_worker.error.connect(self._on_webcam_error)
        self.webcam_worker.start()
        self.pad_widgets[WEBCAM_PAD_VIDEO].set_state(webcam=True)
        self.wcam_stop_btn.setEnabled(True)
        fname = os.path.basename(self._webcam_video_path)
        self.statusBar().showMessage(f"Video webcam active (Pad 8) — {fname}")

    def _stop_webcam(self):
        if self.webcam_worker:
            self.webcam_worker.stop()
            self.webcam_worker.wait(2000)
            self.webcam_worker = None
        self.pad_widgets[WEBCAM_PAD_SCREEN].set_state(webcam=False)
        self.pad_widgets[WEBCAM_PAD_VIDEO].set_state(webcam=False)
        self.pad_widgets[WEBCAM_PAD_SCREEN]._placeholder()
        self.pad_widgets[WEBCAM_PAD_VIDEO]._placeholder()
        self.wcam_stop_btn.setEnabled(False)
        self.statusBar().showMessage("Webcam output stopped")

    def _configure(self, idx: int):
        cfg = self.pads[idx]
        if idx == WEBCAM_PAD_VIDEO:
            dlg = VideoWebcamConfigDialog(self._webcam_video_path, self)
            if dlg.exec_() == QDialog.Accepted:
                self._webcam_video_path = dlg.path
                cfg.stype = SourceType.VIDEO
                cfg.sname = os.path.basename(dlg.path)
                self.pad_widgets[idx].update_cfg(cfg)
                self.statusBar().showMessage(
                    f"Pad 8 video configured: {cfg.sname}")
            return
        dlg = ConfigDialog(cfg, self.windows, self.screens, self)
        if dlg.exec_() == QDialog.Accepted:
            self.pad_widgets[idx].update_cfg(cfg)
            if self.worker:
                self.worker.pads = self.pads
            self.statusBar().showMessage(
                f"Pad {idx + 1} configured: {cfg.sname}")

    def _fps_changed(self, v: int):
        self.fps_lbl2.setText(f"{v} fps")
        if self.worker:
            self.worker.set_fps(v)

    def _init_display(self):
        if PLATFORM != "Windows":
            return
        if len(QApplication.screens()) < 2:
            try:
                subprocess.Popen(
                    ["DisplaySwitch.exe", "/extend"],
                    creationflags=0x08000000,
                )
            except Exception:
                pass
            QTimer.singleShot(1500, self._refresh)
        else:
            self._refresh()

    def _on_proj_frame(self, px: QPixmap):
        if self.projector and self.projector.isVisible() and self.selected is not None:
            self.projector.push(px, self.pads[self.selected].display_name)

    def _display_settings(self):
        ext = Bridge.external_screen()
        dlg = DisplaySettingsDialog(ext, self)
        dlg.exec_()
        self._refresh()

    def closeEvent(self, e):
        if self.webcam_worker:
            self.webcam_worker.stop()
            self.webcam_worker.wait(2000)
        if self.proj_worker:
            self.proj_worker.stop()
            self.proj_worker.wait(2000)
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        if self.projector:
            self.projector.close()
        super().closeEvent(e)

# ── entry point ────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    w = Main()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
