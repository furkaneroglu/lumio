"""
ViewSonic OMNI DDC/CI Tray - On-Screen Display (OSD HUD) Overlay
Parlaklık, Ses, Giriş Kaynağı ve Güç durumlarında ekranda beliren şık, modern gösterge.
"""

import tkinter as tk
from typing import Optional
from .config import ConfigManager

class OSDOverlay:
    """Ekran üzerinde şık, yarı saydam ve otomatik kaybolan OSD HUD penceresi."""

    def __init__(self, root: tk.Tk, config: ConfigManager):
        self.root = root
        self.config = config
        self._window: Optional[tk.Toplevel] = None
        self._hide_timer_id: Optional[str] = None

        self.width = 340
        self.height = 110
        self._create_window()

    def _create_window(self):
        self._window = tk.Toplevel(self.root)
        self._window.withdraw()
        self._window.overrideredirect(True)
        self._window.attributes("-topmost", True)
        self._window.attributes("-alpha", 0.94)
        self._window.configure(bg="#18181b")

        self.frame = tk.Frame(self._window, bg="#18181b", highlightbackground="#3f3f46", highlightthickness=1)
        self.frame.pack(fill=tk.BOTH, expand=True)

        self.title_label = tk.Label(
            self.frame,
            text="ViewSonic OMNI",
            font=("Segoe UI", 10, "bold"),
            fg="#a1a1aa",
            bg="#18181b"
        )
        self.title_label.pack(pady=(12, 2))

        self.value_label = tk.Label(
            self.frame,
            text="☀️ Parlaklık: %50",
            font=("Segoe UI", 13, "bold"),
            fg="#f4f4f5",
            bg="#18181b"
        )
        self.value_label.pack(pady=(0, 8))

        self.canvas = tk.Canvas(
            self.frame,
            width=280,
            height=14,
            bg="#18181b",
            highlightthickness=0
        )
        self.canvas.pack(pady=(0, 12))

    def show(
        self,
        monitor_name: str,
        value: int,
        action_title: Optional[str] = None,
        hud_type: str = "brightness",
        is_muted: bool = False
    ):
        """OSD HUD penceresini günceller ve ekranda gösterir."""
        if not self.config.show_osd:
            return

        if self._window is None:
            self._create_window()

        if self._hide_timer_id is not None:
            self.root.after_cancel(self._hide_timer_id)
            self._hide_timer_id = None

        header = action_title if action_title else monitor_name
        self.title_label.config(text=header)

        # Bar rengi ve ikon belirleme
        bar_color = "#f59e0b" # Amber (Parlaklık)

        if hud_type == "volume":
            bar_color = "#06b6d4" # Camgöbeği (Ses)
            if is_muted or value == 0:
                icon_text = "🔇  Ses: Sessiz (Mute)"
                fill_ratio = 0.0
            else:
                icon_text = f"🔊  Ses Seviyesi: %{value}"
                fill_ratio = max(0.0, min(1.0, value / 100.0))
            self.value_label.config(text=icon_text)

        elif hud_type == "input":
            # action_title giriş adını taşır
            self.value_label.config(text=f"🔌  Giriş: {action_title or 'Bilinmiyor'}")
            self.title_label.config(text="Giriş Kaynağı Değiştirildi")
            fill_ratio = 1.0
            bar_color = "#10b981" # Yeşil

        elif hud_type == "mode":
            self.title_label.config(text="ViewSonic OMNI - Ekran Modu")
            self.value_label.config(text=action_title or f"Mod: {value}")
            fill_ratio = 1.0
            bar_color = "#8b5cf6" # Mor / Violet

        elif hud_type == "contrast":
            self.value_label.config(text=f"🌓  Kontrast: %{value}")
            fill_ratio = max(0.0, min(1.0, value / 100.0))
            bar_color = "#3b82f6" # Mavi

        elif hud_type == "monitor":
            self.title_label.config(text="Aktif Ekran Seçimi")
            self.value_label.config(text=f"🖥️  {action_title or monitor_name}")
            fill_ratio = 1.0
            bar_color = "#3b82f6"

        elif hud_type == "sleep":
            self.value_label.config(text="🌙  Monitör Uyku Moduna Alındı")
            fill_ratio = 0.0

        else: # brightness
            if value >= 70:
                sun_icon = "☀️"
            elif value >= 35:
                sun_icon = "🌤️"
            else:
                sun_icon = "🌙"
            self.value_label.config(text=f"{sun_icon}  Parlaklık: %{value}")
            fill_ratio = max(0.0, min(1.0, value / 100.0))

        # Barı çiz
        self.canvas.delete("all")
        bar_w = 280
        bar_h = 14
        radius = 6

        self._draw_pill(self.canvas, 0, 0, bar_w, bar_h, radius=radius, fill="#27272a")

        fill_w = int(bar_w * fill_ratio)
        if fill_w > radius * 2:
            self._draw_pill(self.canvas, 0, 0, fill_w, bar_h, radius=radius, fill=bar_color)
        elif fill_w > 0:
            self.canvas.create_rectangle(0, 0, fill_w, bar_h, fill=bar_color, outline="")

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = (screen_w - self.width) // 2
        y = screen_h - self.height - 110

        self._window.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self._window.deiconify()
        self._window.lift()

        duration = self.config.data.get("ui", {}).get("osd_duration_ms", 1400)
        self._hide_timer_id = self.root.after(duration, self.hide)

    def hide(self):
        if self._window is not None:
            self._window.withdraw()
        self._hide_timer_id = None

    @staticmethod
    def _draw_pill(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int = 6, **kwargs):
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1
        ]
        return canvas.create_polygon(points, smooth=True, **kwargs)
