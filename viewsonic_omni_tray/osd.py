"""
ViewSonic OMNI DDC/CI Tray - On-Screen Display (OSD) Overlay
Kısayol kullanıldığında ekranın altında beliren, Windows 11 tarzı minimal gösterge.
"""

import tkinter as tk
from typing import Optional

from .config import ConfigManager
from .ui_kit import Palette, Icons, get_theme, style_window, get_work_area, Fader, render_bar

_OSD_BG = "#242424"


class OSDOverlay:
    """Ekran üzerinde beliren ve kendiliğinden kaybolan küçük gösterge penceresi."""

    def __init__(self, root: tk.Tk, config: ConfigManager):
        self.root = root
        self.config = config
        self.t = get_theme(root)
        self._window: Optional[tk.Toplevel] = None
        self._fader: Optional[Fader] = None
        self._hide_timer_id: Optional[str] = None
        self._bar_photo = None

        self.width = self.t.px(320)
        self._create_window()

    def _create_window(self):
        t = self.t
        px = t.px
        bg = _OSD_BG

        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=bg)
        self._window = w
        self._fader = Fader(w)

        body = tk.Frame(w, bg=bg, padx=px(18), pady=px(12))
        body.pack(fill=tk.BOTH, expand=True)

        self.caption_lbl = tk.Label(body, text="", font=t.font(8), fg=Palette.FAINT, bg=bg, anchor="w")
        self.caption_lbl.pack(fill=tk.X)

        row = tk.Frame(body, bg=bg)
        row.pack(fill=tk.X, pady=(px(4), 0))

        self.icon_lbl = tk.Label(row, text=Icons.BRIGHTNESS, font=t.icon_font(14), fg=Palette.TEXT, bg=bg)
        self.icon_lbl.pack(side=tk.LEFT)

        self.value_lbl = tk.Label(row, text="", width=3, anchor="e", font=t.font(11, "bold"),
                                  fg=Palette.TEXT, bg=bg)
        self.text_lbl = tk.Label(row, text="", font=t.font(11, "bold"), fg=Palette.TEXT, bg=bg, anchor="w")

        self._bar_w = self.width - px(18) * 2 - px(28) - px(44)
        self._bar_h = px(4)
        self.bar = tk.Canvas(row, width=self._bar_w, height=px(20), bg=bg, highlightthickness=0, bd=0)

    def show(
        self,
        monitor_name: str,
        value: int,
        action_title: Optional[str] = None,
        hud_type: str = "brightness",
        is_muted: bool = False
    ):
        """Göstergeyi günceller ve ekranda gösterir."""
        if not self.config.show_osd:
            return
        if self._window is None:
            self._create_window()

        if self._hide_timer_id is not None:
            self.root.after_cancel(self._hide_timer_id)
            self._hide_timer_id = None
        self._fader.cancel()

        caption = monitor_name
        glyph = Icons.BRIGHTNESS
        text: Optional[str] = None
        ratio = max(0.0, min(1.0, value / 100.0))
        color = Palette.ACCENT

        if hud_type == "contrast":
            glyph = Icons.CONTRAST
        elif hud_type == "volume":
            glyph = Icons.MUTE if is_muted else Icons.VOLUME
            if is_muted:
                text = "Sessiz"
        elif hud_type == "input":
            caption, glyph, text = "Giriş kaynağı", Icons.MONITOR, action_title or "Bilinmiyor"
        elif hud_type == "mode":
            caption, glyph, text = "Ekran modu", Icons.PALETTE, action_title or str(value)
        elif hud_type == "monitor":
            caption, glyph, text = "Kontrol edilen ekran", Icons.MONITOR, action_title or monitor_name
        elif hud_type == "sleep":
            glyph, text = Icons.SLEEP, "Uyku moduna alındı"

        self.caption_lbl.config(text=caption)
        self.icon_lbl.config(text=glyph, fg=Palette.DANGER if (hud_type == "volume" and is_muted) else Palette.TEXT)

        self.value_lbl.pack_forget()
        self.bar.pack_forget()
        self.text_lbl.pack_forget()
        if text is None:
            self.value_lbl.config(text=str(value))
            self.value_lbl.pack(side=tk.RIGHT)
            self._bar_photo = render_bar(self._bar_w, self._bar_h, ratio, color, Palette.TRACK, _OSD_BG)
            self.bar.delete("all")
            self.bar.create_image(0, self.t.px(20) // 2, anchor="w", image=self._bar_photo)
            self.bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(self.t.px(12), self.t.px(8)))
        else:
            self.text_lbl.config(text=text)
            self.text_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(self.t.px(12), 0))

        w = self._window
        w.geometry(f"{self.width}x1")
        w.update_idletasks()
        height = w.winfo_reqheight()

        area = get_work_area()
        if area:
            left, top, right, bottom = area
        else:
            left, top, right, bottom = 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 48
        x = left + (right - left - self.width) // 2
        y = bottom - height - self.t.px(48)

        w.geometry(f"{self.width}x{height}+{x}+{y}")
        if not w.winfo_viewable():
            w.attributes("-alpha", 0.0)
            w.deiconify()
            style_window(w, rounded=True, dark_titlebar=False, border_color=Palette.BORDER)
            self._fader.run(0.0, 0.97, duration_ms=90, steps=4)
        else:
            w.attributes("-alpha", 0.97)
        w.lift()

        duration = self.config.data.get("ui", {}).get("osd_duration_ms", 1400)
        self._hide_timer_id = self.root.after(duration, self._fade_out)

    def _fade_out(self):
        self._hide_timer_id = None
        if self._fader:
            self._fader.run(0.97, 0.0, duration_ms=180, steps=6, on_done=self.hide)
        else:
            self.hide()

    def hide(self):
        if self._hide_timer_id is not None:
            self.root.after_cancel(self._hide_timer_id)
            self._hide_timer_id = None
        if self._window is not None:
            self._window.withdraw()
