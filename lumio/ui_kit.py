"""
Lumio - Modern / Minimal UI Kit
Windows 11 görünümlü ortak arayüz bileşenleri: tema, kaydırıcı, segment seçici,
ikon butonu, anahtar (toggle) ve buton. Kenar yumuşatma (antialias) için bileşenler
Pillow ile yüksek çözünürlükte çizilip küçültülür; metinler Tk tarafından (ClearType) çizilir.
"""

import sys
import ctypes
from ctypes import wintypes
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Any

from PIL import Image, ImageDraw, ImageTk

# Kenar yumuşatma için süper örnekleme katsayısı
_SS = 4


class Palette:
    BG = "#1c1c1c"
    SURFACE = "#272727"
    SURFACE_HOVER = "#313131"
    SELECTED = "#3b3b3b"
    BORDER = "#363636"
    TRACK = "#454545"
    KNOB_RING = "#454545"
    TEXT = "#f3f3f3"
    MUTED = "#a3a3a3"
    FAINT = "#727272"
    ACCENT = "#5b9cff"
    ACCENT_HOVER = "#78afff"
    ON_ACCENT = "#0b1a33"
    SUCCESS = "#4ade80"
    WARNING = "#fbbf24"
    DANGER = "#f87171"


class Icons:
    """Segoe Fluent Icons / Segoe MDL2 Assets kod noktaları."""
    BRIGHTNESS = ""
    CONTRAST = ""
    VOLUME = ""
    MUTE = ""
    SETTINGS = ""
    SLEEP = ""
    POWER = ""
    MONITOR = ""
    LINK = ""
    PALETTE = ""
    REFRESH = ""
    CLOSE = ""
    ADD = ""
    CLOCK = ""
    GAME = ""


class Theme:
    """DPI ölçeği ve yazı tipi seçimini tutan tema nesnesi."""

    def __init__(self, root: tk.Misc):
        try:
            self.scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            self.scale = 1.0

        try:
            families = set(tkfont.families(root))
        except Exception:
            families = set()

        self.font_family = "Segoe UI Variable Text" if "Segoe UI Variable Text" in families else "Segoe UI"
        self.display_family = "Segoe UI Variable Display" if "Segoe UI Variable Display" in families else "Segoe UI"
        self.icon_family = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else "Segoe MDL2 Assets"

    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    def font(self, size: int, weight: str = "normal") -> Tuple[str, int, str]:
        return (self.font_family, size, weight)

    def display_font(self, size: int, weight: str = "bold") -> Tuple[str, int, str]:
        return (self.display_family, size, weight)

    def icon_font(self, size: int) -> Tuple[str, int]:
        return (self.icon_family, size)


_theme_cache: Dict[str, Theme] = {}


def get_theme(root: tk.Misc) -> Theme:
    key = str(root.winfo_toplevel())
    if key not in _theme_cache:
        _theme_cache[key] = Theme(root)
    return _theme_cache[key]


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


# --- Win32 Pencere Yardımcıları ---

def style_window(window: tk.Misc, rounded: bool = True, dark_titlebar: bool = True,
                 border_color: Optional[str] = None):
    """Windows 11'de yuvarlak köşe, koyu başlık çubuğu ve kenarlık rengini uygular (desteklenmezse sessizce geçer)."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        if not hwnd:
            return
        dwm = ctypes.windll.dwmapi

        def _set(attr: int, value: int):
            val = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(attr), ctypes.byref(val), ctypes.sizeof(val))

        if dark_titlebar:
            _set(20, 1)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        if rounded:
            _set(33, 2)  # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND
        if border_color:
            r, g, b = _hex_to_rgb(border_color)
            _set(34, r | (g << 8) | (b << 16))  # DWMWA_BORDER_COLOR (COLORREF)
    except Exception:
        pass


def get_work_area() -> Optional[Tuple[int, int, int, int]]:
    """Birincil ekranın görev çubuğu hariç çalışma alanını (left, top, right, bottom) döner."""
    if sys.platform != "win32":
        return None
    try:
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


class Fader:
    """Bir pencerenin saydamlığını yumuşak geçişle değiştirir."""

    def __init__(self, window: tk.Misc):
        self.window = window
        self._job: Optional[str] = None

    def cancel(self):
        if self._job is not None:
            try:
                self.window.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def run(self, start: float, end: float, duration_ms: int = 120, steps: int = 6,
            on_done: Optional[Callable[[], None]] = None):
        self.cancel()
        interval = max(1, duration_ms // steps)

        def _step(i: int):
            alpha = start + (end - start) * (i / steps)
            try:
                self.window.attributes("-alpha", alpha)
            except Exception:
                return
            if i < steps:
                self._job = self.window.after(interval, lambda: _step(i + 1))
            else:
                self._job = None
                if on_done:
                    on_done()

        _step(0)


# --- Çizim Yardımcıları ---

def render_bar(width: int, height: int, ratio: float, fill: str, track: str, bg: str) -> ImageTk.PhotoImage:
    """Yuvarlak uçlu ince ilerleme çubuğu çizer."""
    w, h = max(1, width), max(1, height)
    img = Image.new("RGB", (w * _SS, h * _SS), bg)
    d = ImageDraw.Draw(img)
    r = h * _SS / 2
    d.rounded_rectangle([0, 0, w * _SS - 1, h * _SS - 1], radius=r, fill=track)
    ratio = max(0.0, min(1.0, ratio))
    fill_w = int(w * _SS * ratio)
    if fill_w > 0:
        fill_w = max(fill_w, int(2 * r))
        d.rounded_rectangle([0, 0, fill_w - 1, h * _SS - 1], radius=r, fill=fill)
    return ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))


class Slider(tk.Canvas):
    """Windows 11 tarzı yatay kaydırıcı (sürükleme, tıklama ve fare tekerleği destekli)."""

    def __init__(self, parent: tk.Misc, theme: Theme, value: int = 0, from_: int = 0, to: int = 100,
                 command: Optional[Callable[[int], None]] = None, bg: str = Palette.BG,
                 accent: str = Palette.ACCENT, height: Optional[int] = None, origin: Optional[int] = None):
        self.t = theme
        super().__init__(parent, height=height or theme.px(26), bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._bg = bg
        self._accent = accent
        self._from = from_
        self._to = to
        # Dolgu bu değerden başlar (örn. -30..+30 aralığında 0); verilmezse en soldan
        self._origin = from_ if origin is None else origin
        self._value = int(value)
        self._command = command
        self._hover = False
        self._dragging = False
        self._dimmed = False
        self._photo: Optional[ImageTk.PhotoImage] = None

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    @property
    def dragging(self) -> bool:
        return self._dragging

    def get(self) -> int:
        return self._value

    def set(self, value: int):
        """Değeri komut tetiklemeden ayarlar. Kullanıcı sürüklerken dışarıdan gelen değerler yok sayılır."""
        if self._dragging:
            return
        value = self._clamp(value)
        if value != self._value:
            self._value = value
            self._redraw()

    def set_dimmed(self, dimmed: bool):
        if dimmed != self._dimmed:
            self._dimmed = dimmed
            self._redraw()

    def step(self, delta: int):
        self._set_from_user(self._value + delta)

    def _clamp(self, value: float) -> int:
        return int(max(self._from, min(self._to, round(value))))

    def _set_from_user(self, value: float):
        value = self._clamp(value)
        if value != self._value:
            self._value = value
            self._redraw()
            if self._command:
                self._command(value)

    def _geometry(self) -> Tuple[float, float]:
        pad = self.t.px(11)
        return pad, max(pad + 1, self.winfo_width() - pad)

    def _value_from_x(self, x: float) -> float:
        x0, x1 = self._geometry()
        ratio = (x - x0) / (x1 - x0)
        return self._from + max(0.0, min(1.0, ratio)) * (self._to - self._from)

    def _on_press(self, event):
        self._dragging = True
        self._set_from_user(self._value_from_x(event.x))
        self._redraw()

    def _on_motion(self, event):
        if self._dragging:
            self._set_from_user(self._value_from_x(event.x))

    def _on_release(self, event):
        self._dragging = False
        self._redraw()

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._redraw()

    def _redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        S = _SS
        px = self.t.px
        img = Image.new("RGB", (w * S, h * S), self._bg)
        d = ImageDraw.Draw(img)

        x0, x1 = self._geometry()
        cy = h / 2
        track_h = px(4)
        span = max(1, (self._to - self._from))
        kx = x0 + (x1 - x0) * (self._value - self._from) / span
        ox = x0 + (x1 - x0) * (self._origin - self._from) / span
        accent = Palette.FAINT if self._dimmed else self._accent

        d.rounded_rectangle([x0 * S, (cy - track_h / 2) * S, x1 * S, (cy + track_h / 2) * S],
                            radius=track_h / 2 * S, fill=Palette.TRACK)
        fx0, fx1 = min(ox, kx), max(ox, kx)
        if fx1 > fx0:
            d.rounded_rectangle([fx0 * S, (cy - track_h / 2) * S, fx1 * S, (cy + track_h / 2) * S],
                                radius=track_h / 2 * S, fill=accent)

        outer = px(10)
        inner = px(7) if (self._hover or self._dragging) else px(5)
        if self._dragging:
            inner = px(6)
        d.ellipse([(kx - outer) * S, (cy - outer) * S, (kx + outer) * S, (cy + outer) * S], fill=Palette.KNOB_RING)
        d.ellipse([(kx - inner) * S, (cy - inner) * S, (kx + inner) * S, (cy + inner) * S], fill=accent)

        self._photo = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)


class Segmented(tk.Canvas):
    """Yuvarlak köşeli segment seçici (sekme / seçenek grubu)."""

    def __init__(self, parent: tk.Misc, theme: Theme, options: Sequence[Tuple[Any, str]],
                 command: Optional[Callable[[Any], None]] = None, bg: str = Palette.BG,
                 height: Optional[int] = None, font_size: int = 9):
        self.t = theme
        super().__init__(parent, height=height or theme.px(34), bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._bg = bg
        self._items: List[Tuple[Any, str]] = list(options)
        self._command = command
        self._selected: Any = None
        self._hover_idx: Optional[int] = None
        self._font_size = font_size
        self._photo: Optional[ImageTk.PhotoImage] = None

        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)

    def set_options(self, options: Sequence[Tuple[Any, str]]):
        self._items = list(options)
        self._redraw()

    def set(self, key: Any):
        if key != self._selected:
            self._selected = key
            self._redraw()

    def get(self) -> Any:
        return self._selected

    def _index_at(self, x: float) -> Optional[int]:
        if not self._items:
            return None
        w = self.winfo_width()
        idx = int(x // (w / len(self._items)))
        return idx if 0 <= idx < len(self._items) else None

    def _on_motion(self, event):
        self._set_hover(self._index_at(event.x))

    def _set_hover(self, idx: Optional[int]):
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._redraw()

    def _on_click(self, event):
        idx = self._index_at(event.x)
        if idx is None:
            return
        key = self._items[idx][0]
        self.set(key)
        if self._command:
            self._command(key)

    def _redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2 or not self._items:
            self.delete("all")
            return
        S = _SS
        px = self.t.px
        img = Image.new("RGB", (w * S, h * S), self._bg)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, w * S - 1, h * S - 1], radius=px(8) * S, fill=Palette.SURFACE)

        n = len(self._items)
        seg_w = w / n
        inset = px(3)
        sel_idx = next((i for i, (k, _) in enumerate(self._items) if k == self._selected), None)

        for i in range(n):
            if i == sel_idx or i == self._hover_idx:
                color = Palette.SELECTED if i == sel_idx else Palette.SURFACE_HOVER
                d.rounded_rectangle([(i * seg_w + inset) * S, inset * S,
                                     ((i + 1) * seg_w - inset) * S, (h - inset) * S],
                                    radius=px(6) * S, fill=color)
        if sel_idx is not None:
            cx = (sel_idx + 0.5) * seg_w
            bar_w, bar_h = px(16), px(3)
            d.rounded_rectangle([(cx - bar_w / 2) * S, (h - inset - bar_h - px(2)) * S,
                                 (cx + bar_w / 2) * S, (h - inset - px(2)) * S],
                                radius=bar_h / 2 * S, fill=Palette.ACCENT)

        self._photo = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)
        for i, (_, label) in enumerate(self._items):
            selected = i == sel_idx
            self.create_text(
                (i + 0.5) * seg_w, h / 2 - (px(1) if selected else 0),
                text=label,
                fill=Palette.TEXT if selected else Palette.MUTED,
                font=self.t.font(self._font_size, "bold" if selected else "normal")
            )


class ChipGroup(tk.Canvas):
    """Birden fazla seçilebilen, yan yana yuvarlak düğmeler (örn. görünür giriş kaynakları)."""

    def __init__(self, parent: tk.Misc, theme: Theme, options: Sequence[Tuple[Any, str]],
                 selected: Sequence[Any] = (), command: Optional[Callable[[List[Any]], None]] = None,
                 bg: str = Palette.BG, min_selected: int = 1):
        self.t = theme
        self._font = theme.font(8)
        self._font_bold = theme.font(8, "bold")
        self._items: List[Tuple[Any, str]] = list(options)
        self._chosen: List[Any] = [k for k, _ in self._items if k in selected]
        self._command = command
        self._min = min_selected
        self._hover_idx: Optional[int] = None
        self._bg = bg
        self._photo: Optional[ImageTk.PhotoImage] = None

        measure = tkfont.Font(root=parent, font=self._font_bold)
        self._pad_x, self._gap = theme.px(12), theme.px(6)
        self._widths = [measure.measure(label) + 2 * self._pad_x for _, label in self._items]
        self._chip_h = theme.px(28)
        total_w = sum(self._widths) + self._gap * max(0, len(self._widths) - 1)
        super().__init__(parent, width=max(1, total_w), height=self._chip_h, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def get(self) -> List[Any]:
        return [k for k, _ in self._items if k in self._chosen]

    def _index_at(self, x: float) -> Optional[int]:
        pos = 0
        for i, w in enumerate(self._widths):
            if pos <= x < pos + w:
                return i
            pos += w + self._gap
        return None

    def _on_motion(self, event):
        self._set_hover(self._index_at(event.x))

    def _set_hover(self, idx: Optional[int]):
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._redraw()

    def _on_click(self, event):
        idx = self._index_at(event.x)
        if idx is None:
            return
        key = self._items[idx][0]
        if key in self._chosen:
            if len(self._chosen) <= self._min:
                return  # en az bir seçenek kalmalı
            self._chosen.remove(key)
        else:
            self._chosen.append(key)
        self._redraw()
        if self._command:
            self._command(self.get())

    def _redraw(self):
        S = _SS
        w, h = int(self.cget("width")), self._chip_h
        img = Image.new("RGB", (w * S, h * S), self._bg)
        d = ImageDraw.Draw(img)
        pos = 0
        for i, ((key, _), cw) in enumerate(zip(self._items, self._widths)):
            on = key in self._chosen
            if on:
                fill = Palette.SELECTED
            else:
                fill = Palette.SURFACE_HOVER if i == self._hover_idx else Palette.SURFACE
            d.rounded_rectangle([pos * S, 0, (pos + cw) * S - 1, h * S - 1], radius=h * S / 2, fill=fill)
            if on:
                d.rounded_rectangle([pos * S, 0, (pos + cw) * S - 1, h * S - 1], radius=h * S / 2,
                                    outline=Palette.ACCENT, width=max(1, int(S * self.t.scale)))
            pos += cw + self._gap
        self._photo = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)
        pos = 0
        for (key, label), cw in zip(self._items, self._widths):
            on = key in self._chosen
            self.create_text(pos + cw / 2, h / 2, text=label, fill=Palette.TEXT if on else Palette.MUTED,
                             font=self._font_bold if on else self._font)
            pos += cw + self._gap


class IconButton(tk.Canvas):
    """Yuvarlak üzerine gelme efektli ikon butonu."""

    def __init__(self, parent: tk.Misc, theme: Theme, glyph: str, command: Optional[Callable[[], None]] = None,
                 bg: str = Palette.BG, size: Optional[int] = None, icon_size: int = 11,
                 color: str = Palette.MUTED):
        self.t = theme
        self._size = size or theme.px(32)
        super().__init__(parent, width=self._size, height=self._size, bg=bg, highlightthickness=0, bd=0,
                         cursor="hand2" if command else "")
        self._bg = bg
        self._glyph = glyph
        self._color = color
        self._icon_size = icon_size
        self._command = command
        self._hover = False
        self._photo: Optional[ImageTk.PhotoImage] = None

        if command:
            self.bind("<Enter>", lambda e: self._set_hover(True))
            self.bind("<Leave>", lambda e: self._set_hover(False))
            self.bind("<Button-1>", lambda e: self._command())
        self._redraw()

    def configure_icon(self, glyph: Optional[str] = None, color: Optional[str] = None):
        changed = False
        if glyph is not None and glyph != self._glyph:
            self._glyph = glyph
            changed = True
        if color is not None and color != self._color:
            self._color = color
            changed = True
        if changed:
            self._redraw()

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._redraw()

    def _redraw(self):
        s = self._size
        self.delete("all")
        if self._hover:
            S = _SS
            img = Image.new("RGB", (s * S, s * S), self._bg)
            ImageDraw.Draw(img).rounded_rectangle([0, 0, s * S - 1, s * S - 1], radius=self.t.px(6) * S,
                                                  fill=Palette.SURFACE_HOVER)
            self._photo = ImageTk.PhotoImage(img.resize((s, s), Image.LANCZOS))
            self.create_image(0, 0, anchor="nw", image=self._photo)
        color = Palette.TEXT if self._hover and self._color == Palette.MUTED else self._color
        self.create_text(s / 2, s / 2, text=self._glyph, fill=color, font=self.t.icon_font(self._icon_size))


class Toggle(tk.Canvas):
    """Windows 11 tarzı açma/kapama anahtarı."""

    def __init__(self, parent: tk.Misc, theme: Theme, value: bool = False,
                 command: Optional[Callable[[bool], None]] = None, bg: str = Palette.SURFACE):
        self.t = theme
        self._width, self._height = theme.px(40), theme.px(20)
        super().__init__(parent, width=self._width, height=self._height, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._bg = bg
        self._value = bool(value)
        self._command = command
        self._hover = False
        self._photo: Optional[ImageTk.PhotoImage] = None
        self.bind("<Button-1>", lambda e: self.toggle())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._redraw()

    def get(self) -> bool:
        return self._value

    def set(self, value: bool):
        self._value = bool(value)
        self._redraw()

    def toggle(self):
        self.set(not self._value)
        if self._command:
            self._command(self._value)

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._redraw()

    def _redraw(self):
        S = _SS
        w, h = self._width, self._height
        img = Image.new("RGB", (w * S, h * S), self._bg)
        d = ImageDraw.Draw(img)
        r = h * S / 2
        if self._value:
            fill = Palette.ACCENT_HOVER if self._hover else Palette.ACCENT
            d.rounded_rectangle([0, 0, w * S - 1, h * S - 1], radius=r, fill=fill)
            knob_color = Palette.ON_ACCENT
            cx = (w - h / 2) * S
        else:
            d.rounded_rectangle([0, 0, w * S - 1, h * S - 1], radius=r, fill=Palette.MUTED if self._hover else Palette.FAINT)
            d.rounded_rectangle([S * 1.2, S * 1.2, w * S - 1 - S * 1.2, h * S - 1 - S * 1.2], radius=r - S * 1.2,
                                fill=self._bg)
            knob_color = Palette.TEXT if self._hover else Palette.MUTED
            cx = (h / 2) * S
        kr = (h / 2 - self.t.px(5)) * S
        d.ellipse([cx - kr, h * S / 2 - kr, cx + kr, h * S / 2 + kr], fill=knob_color)
        self._photo = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)


class PillButton(tk.Canvas):
    """Yuvarlak köşeli birincil / ikincil buton."""

    def __init__(self, parent: tk.Misc, theme: Theme, text: str, command: Optional[Callable[[], None]] = None,
                 primary: bool = False, bg: str = Palette.BG, width: Optional[int] = None):
        self.t = theme
        font = theme.font(9, "bold" if primary else "normal")
        text_w = tkfont.Font(root=parent, font=font).measure(text)
        self._width = width or (text_w + theme.px(36))
        self._height = theme.px(34)
        super().__init__(parent, width=self._width, height=self._height, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._bg = bg
        self._text = text
        self._font = font
        self._primary = primary
        self._command = command
        self._hover = False
        self._photo: Optional[ImageTk.PhotoImage] = None
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self._command() if self._command else None)
        self._redraw()

    def _set_hover(self, hover: bool):
        self._hover = hover
        self._redraw()

    def _redraw(self):
        S = _SS
        w, h = self._width, self._height
        if self._primary:
            fill = Palette.ACCENT_HOVER if self._hover else Palette.ACCENT
            fg = Palette.ON_ACCENT
        else:
            fill = Palette.SELECTED if self._hover else Palette.SURFACE
            fg = Palette.TEXT
        img = Image.new("RGB", (w * S, h * S), self._bg)
        ImageDraw.Draw(img).rounded_rectangle([0, 0, w * S - 1, h * S - 1], radius=self.t.px(6) * S, fill=fill)
        self._photo = ImageTk.PhotoImage(img.resize((w, h), Image.LANCZOS))
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


def styled_entry(parent: tk.Misc, theme: Theme, width: int = 16, justify: str = "left",
                 font: Optional[Tuple] = None) -> tk.Entry:
    """Koyu temaya uygun, düz kenarlı metin kutusu."""
    return tk.Entry(
        parent,
        width=width,
        justify=justify,
        font=font or theme.font(9),
        bg=Palette.SURFACE,
        fg=Palette.TEXT,
        insertbackground=Palette.TEXT,
        relief=tk.FLAT,
        bd=theme.px(5),  # düz kenarlık, metin için iç boşluk görevi görür
        highlightthickness=1,
        highlightbackground=Palette.BORDER,
        highlightcolor=Palette.ACCENT,
        selectbackground=Palette.ACCENT,
        selectforeground=Palette.ON_ACCENT,
    )


__all__ = [
    "Palette", "Icons", "Theme", "get_theme", "style_window", "get_work_area", "Fader",
    "render_bar", "Slider", "Segmented", "IconButton", "Toggle", "PillButton", "styled_entry",
]
