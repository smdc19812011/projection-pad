#!/usr/bin/env python3
"""
Projection Pad  v1.0
Virtual screen manager — up to 8 pads, each showing a window or display.
Click a pad to select it; click "Project" to send it fullscreen to HDMI.

Requirements: pip install PyQt5 mss
Optional:     pip install Pillow pywin32 (Windows only)
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
    )
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize
    from PyQt5.QtGui import QPixmap, QImage, QColor, QPainter, QFont, QPalette, QKeySequence
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

# ── constants ─────────────────────────────────────────────────────────────────

APP_NAME    = "Projection Pad"
VERSION     = "1.1"
MAX_PADS    = 8
PAD_COLS    = 4
DEFAULT_FPS = 2
PAD_W, PAD_H = 240, 155

C_BG       = "#0D1117"
C_SURFACE  = "#161B22"
C_SURFACE2 = "#21262D"
C_BORDER   = "#30363D"
C_ACCENT   = "#58A6FF"
C_ACTIVE   = "#3FB950"
C_DANGER   = "#F85149"
C_TEXT     = "#E6EDF3"
C_DIM      = "#8B949E"
C_HOVER    = "#2D333B"

# ── data models ───────────────────────────────────────────────────────────────

class SourceType(Enum):
    EMPTY  = "empty"
    SCREEN = "screen"
    WINDOW = "window"

@dataclass
class WinInfo:
    id: str
    title: str
    app: str = ""
    bounds: Optional[tuple] = None   # (x, y, w, h)

    def label(self) -> str:
        return f"{self.app}: {self.title}" if self.app else self.title

@dataclass
class ScreenInfo:
    index: int
    name: str
    bounds: tuple           # (x, y, w, h)
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

# ── system bridge ─────────────────────────────────────────────────────────────

class Bridge:
    """Cross-platform screen and window discovery."""

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
                            ["xdotool", "getwindowname", wid], text=True, timeout=1
                        ).strip()
                        if name:
                            wins.append(WinInfo(wid, name))
                    except Exception:
                        pass
            except Exception:
                pass
        return wins

# ── capture engine ────────────────────────────────────────────────────────────

class Capture:
    """Thread-safe screenshot capture via mss (screens) and PrintWindow (windows on Windows)."""

    _lock = threading.Lock()

    @classmethod
    def grab(cls, x: int, y: int, w: int, h: int) -> Optional[QPixmap]:
        """Capture a screen region by coordinates (used for full-screen/display pads)."""
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
        """Capture a specific window using PrintWindow — works even when the window
        is behind other windows, minimised, or on a different virtual desktop."""
        if not HAS_WIN32:
            return None
        try:
            hwnd = int(hwnd_str)
            if not win32gui.IsWindow(hwnd):
                return None

            # Restore if minimised so PrintWindow gets real dimensions
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

            # PW_RENDERFULLCONTENT (2) captures hardware-accelerated content (Chrome, etc.)
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
            # On Windows use PrintWindow for accurate per-window capture
            if PLATFORM == "Windows" and HAS_WIN32:
                px = cls.grab_window_win32(win.id)
                if px:
                    return px
            # Fallback: capture the screen region the window occupies
            if win.bounds:
                return cls.grab(*win.bounds)
        return None

# ── capture worker thread ─────────────────────────────────────────────────────

class CaptureThread(QThread):
    frame = pyqtSignal(int, QPixmap)   # pad_index, pixmap

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
                if cfg.stype == SourceType.EMPTY:
                    continue
                px = Capture.for_pad(cfg, ss, ws)
                if px:
                    self.frame.emit(cfg.index, px)
            dt = time.time() - t0
            time.sleep(max(0.0, 1.0 / self._fps - dt))

# ── projector capture thread (high-FPS, single pad) ──────────────────────────

class ProjectorThread(QThread):
    """Dedicated capture loop for the active projected pad at screen refresh rate."""
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
            if cfg and cfg.stype != SourceType.EMPTY:
                px = Capture.for_pad(cfg, ss, ws)
                if px:
                    self.frame.emit(px)
            dt = time.time() - t0
            time.sleep(max(0.0, 1.0 / self._fps - dt))

# ── pad widget ────────────────────────────────────────────────────────────────

class PadWidget(QFrame):
    clicked   = pyqtSignal(int)
    configure = pyqtSignal(int)

    def __init__(self, cfg: PadConfig):
        super().__init__()
        self.cfg        = cfg
        self._projected = False
        self._selected  = False
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

        self.dot  = QLabel("●")
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
        pm = QPixmap(PAD_W, PAD_H - 28)
        pm.fill(QColor(C_SURFACE2))
        p = QPainter(pm)
        p.setPen(QColor(C_DIM))
        p.setFont(QFont("Segoe UI,Inter,sans-serif", 10))
        p.drawText(pm.rect(), Qt.AlignCenter,
                   f"Pad {self.cfg.index + 1}\n⚙  to configure\nCtrl+{self.cfg.index + 1}")
        p.end()
        self.preview.setPixmap(pm)

    def set_frame(self, px: QPixmap):
        scaled = px.scaled(self.preview.size(),
                           Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(scaled)

    def set_state(self, projected: bool = False, selected: bool = False):
        self._projected = projected
        self._selected  = selected
        self._restyle()
        if projected:
            dot_color = C_ACTIVE
        elif selected:
            dot_color = C_ACCENT
        elif self.cfg.stype != SourceType.EMPTY:
            dot_color = "#555E6B"
        else:
            dot_color = C_DIM
        self.dot.setStyleSheet(
            f"color:{dot_color};background:transparent;font-size:7px;")

    def update_cfg(self, cfg: PadConfig):
        self.cfg = cfg
        self.name.setText(cfg.display_name)
        if cfg.stype == SourceType.EMPTY:
            self._placeholder()
        self._restyle()

    def _restyle(self):
        if self._projected:
            border = f"2px solid {C_ACTIVE}"
        elif self._selected:
            border = f"2px solid {C_ACCENT}"
        elif self.cfg.stype != SourceType.EMPTY:
            border = f"1px solid {C_ACCENT}44"
        else:
            border = f"1px solid {C_BORDER}"

        self.setStyleSheet(f"""
QFrame#Pad{{
  background:{C_SURFACE};
  border:{border};
  border-radius:8px;
}}
QFrame#Pad:hover{{
  background:{C_HOVER};
  border-color:{C_ACCENT};
}}
QWidget#Footer{{
  background:{C_SURFACE2};
  border-top:1px solid {C_BORDER};
  border-bottom-left-radius:8px;
  border-bottom-right-radius:8px;
}}
QLabel#Name{{
  color:{C_TEXT};
  font-size:11px;
  background:transparent;
}}
QToolButton#Gear{{
  background:transparent;
  border:none;
  color:{C_DIM};
  font-size:12px;
}}
QToolButton#Gear:hover{{color:{C_TEXT}}}
QLabel#ShortcutLbl{{
  color:{C_BORDER};
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
            f"QMenu{{background:{C_SURFACE2};border:1px solid {C_BORDER};"
            f"color:{C_TEXT};border-radius:6px;padding:4px}}"
            f"QMenu::item{{padding:6px 20px;border-radius:3px}}"
            f"QMenu::item:selected{{background:{C_HOVER}}}"
        )
        a1 = m.addAction("▶  Project This Pad")
        a1.setEnabled(self.cfg.stype != SourceType.EMPTY)
        a1.triggered.connect(lambda: self.clicked.emit(self.cfg.index))
        m.addSeparator()
        a2 = m.addAction("⚙  Configure Source…")
        a2.triggered.connect(lambda: self.configure.emit(self.cfg.index))
        a3 = m.addAction("✕  Clear Pad")
        a3.setEnabled(self.cfg.stype != SourceType.EMPTY)
        a3.triggered.connect(self._clear)
        m.exec_(e.globalPos())

    def _clear(self):
        self.cfg.stype = SourceType.EMPTY
        self.cfg.sid   = None
        self.cfg.sname = "Empty"
        self.cfg.label = ""
        self.update_cfg(self.cfg)

# ── configure dialog ──────────────────────────────────────────────────────────

_DLGSS = f"""
QDialog{{background:{C_BG};color:{C_TEXT}}}
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
  background:{C_SURFACE2};border:1px solid {C_BORDER};
  color:{C_TEXT};selection-background-color:{C_HOVER}
}}
QListWidget::item{{padding:5px}}
QListWidget::item:selected{{
  background:{C_ACCENT}22;
  border:1px solid {C_ACCENT}44;
  border-radius:3px
}}
QPushButton{{
  background:{C_SURFACE2};border:1px solid {C_BORDER};
  border-radius:6px;color:{C_TEXT};padding:7px 18px
}}
QPushButton:hover{{background:{C_HOVER}}}
QPushButton#OK{{background:{C_ACCENT};border:none;color:{C_BG};font-weight:600}}
QPushButton#OK:hover{{background:#79B8FF}}
"""

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

        # Custom label
        row = QHBoxLayout()
        row.addWidget(QLabel("Label (optional):"))
        self.lbl_edit = QLineEdit()
        self.lbl_edit.setPlaceholderText(f"Pad {self.cfg.index + 1}")
        if self.cfg.label:
            self.lbl_edit.setText(self.cfg.label)
        row.addWidget(self.lbl_edit, 1)
        vl.addLayout(row)

        # Source type selector
        gb = QGroupBox("Source Type")
        tl = QHBoxLayout(gb)
        self.type_cb = QComboBox()
        self.type_cb.addItems(["Empty", "Entire Screen / Display", "Specific Window"])
        idx_map = {SourceType.EMPTY: 0, SourceType.SCREEN: 1, SourceType.WINDOW: 2}
        self.type_cb.setCurrentIndex(idx_map[self.cfg.stype])
        self.type_cb.currentIndexChanged.connect(self._fill)
        tl.addWidget(self.type_cb)
        vl.addWidget(gb)

        # Source list
        self.src_grp = QGroupBox("Select Source")
        sl = QVBoxLayout(self.src_grp)
        self.src_list = QListWidget()
        self.src_list.setMinimumHeight(140)
        ref = QPushButton("↻  Refresh List")
        ref.clicked.connect(self._fill)
        sl.addWidget(self.src_list)
        sl.addWidget(ref)
        vl.addWidget(self.src_grp)

        # Buttons
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

        if ti == 1:   # screens
            for s in self.screens:
                tag = "★ Primary" if s.primary else "⟶ External"
                txt = f"{tag}  {s.name}  ({s.bounds[2]}×{s.bounds[3]})"
                item = QListWidgetItem(txt)
                item.setData(Qt.UserRole, str(s.index))
                self.src_list.addItem(item)
                if self.cfg.stype == SourceType.SCREEN and self.cfg.sid == str(s.index):
                    self.src_list.setCurrentItem(item)

        elif ti == 2:  # windows
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

# ── display settings dialog ───────────────────────────────────────────────────

class DisplaySettingsDialog(QDialog):
    """Enumerate and apply resolution / refresh rate for an external display."""

    def __init__(self, screen: Optional[ScreenInfo], parent=None):
        super().__init__(parent)
        self._screen = screen
        self.setWindowTitle("Display Settings")
        self.setModal(True)
        self.setFixedSize(420, 300)
        self.setStyleSheet(_DLGSS)
        self._modes: List[tuple] = []   # (width, height, hz)
        self._build()

    def _build(self):
        vl = QVBoxLayout(self)
        vl.setSpacing(10)
        vl.setContentsMargins(16, 16, 16, 16)

        if self._screen:
            cur = self._current_mode()
            if cur:
                hz_note = f" @ {cur[2]} Hz" if cur[2] else ""
                info_txt = f"{self._screen.name}  —  current: {cur[0]}×{cur[1]}{hz_note}"
            else:
                info_txt = (f"{self._screen.name}  —  "
                            f"{self._screen.bounds[2]}×{self._screen.bounds[3]}")
        else:
            info_txt = "No external display detected"

        info = QLabel(info_txt)
        info.setStyleSheet(
            f"color:{C_DIM};font-size:11px;background:{C_SURFACE2};"
            f"padding:8px;border-radius:4px;border:1px solid {C_BORDER}")
        vl.addWidget(info)

        if self._screen and HAS_WIN32:
            self._modes = self._enum_modes()

        # Resolution row
        r1 = QHBoxLayout()
        lbl1 = QLabel("Resolution:")
        lbl1.setFixedWidth(100)
        self.res_cb = QComboBox()
        r1.addWidget(lbl1); r1.addWidget(self.res_cb, 1)
        vl.addLayout(r1)

        # Refresh rate row
        r2 = QHBoxLayout()
        lbl2 = QLabel("Refresh Rate:")
        lbl2.setFixedWidth(100)
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

        # Buttons
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
                self.res_cb.addItem(f"{w}×{h}", (w, h))
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
            dm.Fields = 0x80000 | 0x100000 | 0x400000  # DM_PELSWIDTH|HEIGHT|FREQ
            result = win32api.ChangeDisplaySettingsEx(device, dm, 2)  # CDS_TEST
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

# ── projector output window ───────────────────────────────────────────────────

class Projector(QMainWindow):
    """Fullscreen window placed on the external/HDMI display."""

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

# ── main window stylesheet ────────────────────────────────────────────────────

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
  background:{C_ACCENT};border:none;color:{C_BG};font-weight:600
}}
QPushButton#Project:hover{{background:#79B8FF}}
QPushButton#Stop{{
  background:{C_DANGER};border:none;color:white;font-weight:600
}}
QPushButton#Stop:hover{{background:#FF6B6B}}
#Header,#Controls{{background:{C_SURFACE};border-bottom:1px solid {C_BORDER}}}
#Controls{{border-top:1px solid {C_BORDER};border-bottom:none}}
QSlider::groove:horizontal{{
  height:3px;background:{C_BORDER};border-radius:2px
}}
QSlider::sub-page:horizontal{{background:{C_ACCENT};border-radius:2px}}
QSlider::handle:horizontal{{
  background:{C_ACCENT};width:11px;height:11px;
  margin:-4px 0;border-radius:6px
}}
"""

# ── main window ───────────────────────────────────────────────────────────────

class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{VERSION}")
        self.setMinimumSize(1030, 560)
        self.pads: List[PadConfig]  = [PadConfig(i) for i in range(MAX_PADS)]
        self.pad_widgets: List[PadWidget] = []
        self.selected: Optional[int]      = None
        self.projector: Optional[Projector] = None
        self.screens: List[ScreenInfo]    = []
        self.windows: List[WinInfo]       = []
        self.worker: Optional[CaptureThread] = None
        self.proj_worker: Optional[ProjectorThread] = None
        self._proj_fps: int = 60
        self.setStyleSheet(_MAINSS)
        self._build()
        self._refresh()
        self._start_worker()
        # Auto-switch to extend mode after UI renders
        QTimer.singleShot(600, self._init_display)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self):
        c = QWidget()
        c.setObjectName("Central")
        self.setCentralWidget(c)
        vl = QVBoxLayout(c)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        # Header ───────────────────────────────────────────────────────────────
        hdr = QWidget(); hdr.setObjectName("Header"); hdr.setFixedHeight(58)
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(20, 8, 20, 8)

        tc = QVBoxLayout(); tc.setSpacing(1)
        t  = QLabel("PROJECTION PAD")
        t.setFont(QFont("Segoe UI,Inter,sans-serif", 16, QFont.Bold))
        t.setStyleSheet(f"color:{C_TEXT};letter-spacing:1px;background:transparent")
        sub = QLabel(f"Virtual Screen Manager  •  v{VERSION}")
        sub.setStyleSheet(f"color:{C_DIM};font-size:10px;background:transparent")
        tc.addWidget(t); tc.addWidget(sub)

        self.di = QLabel("Detecting displays…")
        self.di.setStyleSheet(f"color:{C_DIM};font-size:11px;background:transparent")
        self.di.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        hl.addLayout(tc); hl.addStretch(); hl.addWidget(self.di)
        vl.addWidget(hdr)

        # Pad grid ─────────────────────────────────────────────────────────────
        gw = QWidget(); gw.setObjectName("Central")
        gl = QGridLayout(gw)
        gl.setContentsMargins(20, 20, 20, 20)
        gl.setSpacing(12)
        for i in range(MAX_PADS):
            pw = PadWidget(self.pads[i])
            pw.clicked.connect(self._pad_click)
            pw.configure.connect(self._configure)
            self.pad_widgets.append(pw)
            gl.addWidget(pw, i // PAD_COLS, i % PAD_COLS)
        vl.addWidget(gw, 1)

        # Keyboard shortcuts ───────────────────────────────────────────────────
        for i in range(MAX_PADS):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._pad_click(idx))
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self._project)
        QShortcut(QKeySequence("Ctrl+Shift+P"), self).activated.connect(self._stop)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._refresh)
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(
            self._display_settings)

        # Controls ─────────────────────────────────────────────────────────────
        ctrl = QWidget(); ctrl.setObjectName("Controls"); ctrl.setFixedHeight(56)
        cl   = QHBoxLayout(ctrl); cl.setContentsMargins(20, 10, 20, 10); cl.setSpacing(12)

        fps_lbl = QLabel("Preview:")
        fps_lbl.setStyleSheet(f"color:{C_DIM};font-size:11px;background:transparent")
        self.fps_sl = QSlider(Qt.Horizontal)
        self.fps_sl.setRange(1, 10); self.fps_sl.setValue(DEFAULT_FPS)
        self.fps_sl.setFixedWidth(90)
        self.fps_lbl2 = QLabel(f"{DEFAULT_FPS} fps")
        self.fps_lbl2.setStyleSheet(f"color:{C_DIM};font-size:11px;background:transparent")
        self.fps_lbl2.setFixedWidth(38)
        self.fps_sl.valueChanged.connect(self._fps_changed)

        refresh_btn = QPushButton("↻  Refresh Windows")
        refresh_btn.clicked.connect(self._refresh)

        self.proj_btn = QPushButton("▶  Project Selected")
        self.proj_btn.setObjectName("Project")
        self.proj_btn.setEnabled(False)
        self.proj_btn.clicked.connect(self._project)

        self.stop_btn = QPushButton("■  Stop")
        self.stop_btn.setObjectName("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)

        cl.addWidget(fps_lbl); cl.addWidget(self.fps_sl); cl.addWidget(self.fps_lbl2)
        cl.addStretch()
        self.disp_btn = QPushButton("⚙  Display")
        self.disp_btn.clicked.connect(self._display_settings)
        cl.addWidget(self.disp_btn)
        cl.addWidget(refresh_btn); cl.addWidget(self.proj_btn); cl.addWidget(self.stop_btn)
        vl.addWidget(ctrl)

        self.statusBar().showMessage(
            "Ready — Ctrl+1–8 select pad  •  Ctrl+P project  •  Ctrl+Shift+P stop  •  Ctrl+R refresh  •  Ctrl+D display")

        # Auto-refresh window list every 10 s
        self._src_timer = QTimer()
        self._src_timer.setInterval(10_000)
        self._src_timer.timeout.connect(self._refresh)
        self._src_timer.start()

    # ── logic ─────────────────────────────────────────────────────────────────

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
                f"🖥  {len(self.screens)} displays  •  "
                f"External: {ext.name}  •  {self._proj_fps} Hz")
        else:
            self._proj_fps = 60
            self.di.setText(
                f"🖥  {len(self.screens)} display(s) — no external detected")
        self.statusBar().showMessage(
            f"Sources refreshed — {len(self.windows)} windows, {len(self.screens)} screens")

    def _start_worker(self):
        self.worker = CaptureThread(self.pads)
        self.worker.set_sources(self.screens, self.windows)
        self.worker.frame.connect(self._on_frame)
        self.worker.start()

    def _on_frame(self, idx: int, px: QPixmap):
        if 0 <= idx < len(self.pad_widgets):
            self.pad_widgets[idx].set_frame(px)

    def _pad_click(self, idx: int):
        cfg = self.pads[idx]
        if cfg.stype == SourceType.EMPTY:
            self._configure(idx)
            return
        self.selected = idx
        for i, pw in enumerate(self.pad_widgets):
            pw.set_state(projected=self.pads[i].projected, selected=(i == idx))
        self.proj_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Selected: {cfg.display_name} — click 'Project' to send to HDMI")
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
        # Start or update the dedicated high-FPS projector capture thread
        if self.proj_worker is None:
            self.proj_worker = ProjectorThread()
            self.proj_worker.frame.connect(self._on_proj_frame)
        self.proj_worker.set_target(pad, self.screens, self.windows)
        self.proj_worker.set_fps(self._proj_fps)
        if not self.proj_worker.isRunning():
            self.proj_worker.start()

        self.proj_btn.setText("⟳  Projecting…")
        self.stop_btn.setEnabled(True)
        self.statusBar().showMessage(
            f"Projecting: {pad.display_name}  •  {self._proj_fps} fps")

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
        self.proj_btn.setText("▶  Project Selected")
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage("Projection stopped")

    def _configure(self, idx: int):
        cfg = self.pads[idx]
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
        """On Windows: if no external display is visible yet, switch to extend mode."""
        if PLATFORM != "Windows":
            return
        if len(QApplication.screens()) < 2:
            try:
                subprocess.Popen(
                    ["DisplaySwitch.exe", "/extend"],
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
            except Exception:
                pass
            # Re-detect after the display switch settles
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
        if self.proj_worker:
            self.proj_worker.stop()
            self.proj_worker.wait(2000)
        if self.worker:
            self.worker.stop()
            self.worker.wait(2000)
        if self.projector:
            self.projector.close()
        super().closeEvent(e)

# ── entry point ───────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if PLATFORM == "Windows":
        app.setFont(QFont("Segoe UI", 10))
    elif PLATFORM == "Darwin":
        app.setFont(QFont("SF Pro Text", 11))
    else:
        app.setFont(QFont("Inter,Noto Sans,Ubuntu,sans-serif", 10))
    w = Main()
    w.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
