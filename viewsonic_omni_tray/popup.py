"""
ViewSonic OMNI DDC/CI Tray - Quick Control Flyout (Tray UI)
Görev çubuğunun üzerinde açılan, Windows 11 tarzı minimal hızlı kontrol paneli:
parlaklık, kontrast, ses, ekran modu, giriş kaynağı ve monitör seçimi.
"""

import time
import tkinter as tk
from typing import Optional, Callable, Dict, Tuple

from .config import ConfigManager
from .queue_worker import AsyncWriteQueueWorker, MonitorStatus
from .monitor_manager import INPUT_SOURCES, DISPLAY_MODES
from .ui_kit import (
    Palette, Icons, get_theme, style_window, get_work_area, Fader,
    Slider, Segmented, IconButton,
)

_STATUS_STYLE = {
    MonitorStatus.CONNECTED: ("Bağlı", Palette.SUCCESS),
    MonitorStatus.SLEEPING: ("Uykuda", Palette.WARNING),
    MonitorStatus.DISCONNECTED: ("Bağlantı yok", Palette.DANGER),
    MonitorStatus.ERROR: ("İletişim hatası", Palette.DANGER),
}


class SliderPopup:
    """Görev çubuğu üzerinde açılan hızlı kontrol paneli."""

    def __init__(self, root: tk.Tk, config: ConfigManager, worker: AsyncWriteQueueWorker,
                 on_open_settings: Optional[Callable[[], None]] = None):
        self.root = root
        self.config = config
        self.worker = worker
        self.on_open_settings = on_open_settings
        self.automation = None  # app tarafından atanır (AutomationService)
        self.t = get_theme(root)

        self.width = self.t.px(360)
        self._last_toggle_time: float = 0.0
        self._last_hide_time: float = 0.0
        self._window: Optional[tk.Toplevel] = None
        self._fader: Optional[Fader] = None
        self._device_sig: Optional[Tuple] = None
        self._wheel_targets: Dict[str, Slider] = {}

        self._create_window()

    # --- Arayüz Oluşturma ---
    def _create_window(self):
        t = self.t
        px = t.px
        bg = Palette.BG

        w = tk.Toplevel(self.root)
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=bg)
        w.bind("<FocusOut>", self._on_focus_out)
        w.bind("<Escape>", lambda e: self.hide())
        w.bind("<MouseWheel>", self._on_wheel)
        self._window = w
        self._fader = Fader(w)

        body = tk.Frame(w, bg=bg, padx=px(18), pady=px(16))
        body.pack(fill=tk.BOTH, expand=True)

        # 1. Başlık: monitör adı + durum, sağda uyku ve ayarlar
        header = tk.Frame(body, bg=bg)
        header.pack(fill=tk.X)

        titles = tk.Frame(header, bg=bg)
        titles.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.title_lbl = tk.Label(titles, text="Monitör", font=t.display_font(12), fg=Palette.TEXT,
                                  bg=bg, anchor="w")
        self.title_lbl.pack(fill=tk.X)

        status_row = tk.Frame(titles, bg=bg)
        status_row.pack(fill=tk.X)
        self.status_dot = tk.Label(status_row, text="●", font=t.font(7), fg=Palette.SUCCESS, bg=bg)
        self.status_dot.pack(side=tk.LEFT)
        self.status_lbl = tk.Label(status_row, text="Bağlı", font=t.font(9), fg=Palette.MUTED, bg=bg, anchor="w")
        self.status_lbl.pack(side=tk.LEFT, padx=(px(4), 0))

        IconButton(header, t, Icons.SETTINGS, command=self._on_settings_click).pack(side=tk.RIGHT)
        IconButton(header, t, Icons.SLEEP, command=self._on_sleep_click).pack(side=tk.RIGHT, padx=(0, px(2)))

        # 2. Monitör seçici (yalnızca birden fazla monitör varsa görünür)
        self.monitor_holder = tk.Frame(body, bg=bg, height=0)
        self.monitor_holder.pack(fill=tk.X)
        self.monitor_seg = Segmented(self.monitor_holder, t, [], command=self._on_monitor_selected)

        # 3. Kaydırıcılar
        sliders = tk.Frame(body, bg=bg)
        sliders.pack(fill=tk.X, pady=(px(14), 0))
        self.b_slider, self.b_val, _ = self._slider_row(sliders, Icons.BRIGHTNESS, self._on_brightness_moved)
        self.c_slider, self.c_val, _ = self._slider_row(sliders, Icons.CONTRAST, self._on_contrast_moved)
        self.v_slider, self.v_val, self.vol_icon = self._slider_row(
            sliders, Icons.VOLUME, self._on_volume_moved, icon_command=self._on_mute_clicked
        )

        # 4. Ekran modu
        self._section_label(body, "Ekran modu")
        self.mode_seg = Segmented(body, t, [(k, k) for k in DISPLAY_MODES], command=self._on_mode_click,
                                  font_size=8)
        self.mode_seg.pack(fill=tk.X)

        # 5. Giriş kaynağı
        self._section_label(body, "Giriş kaynağı")
        self.input_seg = Segmented(body, t, list(INPUT_SOURCES.items()), command=self._on_input_selected)
        self.input_seg.pack(fill=tk.X)

    def _section_label(self, parent: tk.Misc, text: str):
        tk.Label(parent, text=text, font=self.t.font(8), fg=Palette.FAINT, bg=Palette.BG,
                 anchor="w").pack(fill=tk.X, pady=(self.t.px(14), self.t.px(6)))

    def _slider_row(self, parent: tk.Misc, glyph: str, command: Callable[[int], None],
                    icon_command: Optional[Callable[[], None]] = None):
        t = self.t
        row = tk.Frame(parent, bg=Palette.BG)
        row.pack(fill=tk.X, pady=t.px(2))

        icon = IconButton(row, t, glyph, command=icon_command, icon_size=12)
        icon.pack(side=tk.LEFT)

        val_lbl = tk.Label(row, text="0", width=3, anchor="e", font=t.font(10), fg=Palette.TEXT, bg=Palette.BG)
        val_lbl.pack(side=tk.RIGHT)

        def _on_change(v: int, lbl=val_lbl, cb=command):
            lbl.config(text=str(v))
            cb(v)

        slider = Slider(row, t, command=_on_change)
        slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(t.px(4), t.px(6)))

        for widget in (row, icon, val_lbl, slider):
            self._wheel_targets[str(widget)] = slider
        return slider, val_lbl, icon

    # --- Kullanıcı Etkileşimleri ---
    def _on_brightness_moved(self, val: int):
        self.worker.set_brightness(val, immediate_ui=False)

    def _on_contrast_moved(self, val: int):
        self.worker.set_contrast(val, immediate_ui=False)

    def _on_volume_moved(self, val: int):
        self.worker.set_volume(val, immediate_ui=False)

    def _on_mute_clicked(self):
        self._apply_mute_visual(self.worker.toggle_mute())

    def _on_mode_click(self, mode_key: str):
        self.worker.apply_display_mode(mode_key)

    def _on_input_selected(self, source_code: int):
        self.worker.set_input_source(source_code)

    def _on_monitor_selected(self, idx: int):
        self.worker.select_device(idx)

    def _on_sleep_click(self):
        self.hide()
        self.worker.sleep_monitor()

    def _on_settings_click(self):
        self.hide()
        if self.on_open_settings:
            self.on_open_settings()

    def _on_wheel(self, event):
        """Fare tekerleğini imlecin altındaki kaydırıcıya yönlendirir."""
        widget = self._window.winfo_containing(event.x_root, event.y_root) if self._window else None
        while widget is not None:
            slider = self._wheel_targets.get(str(widget))
            if slider is not None:
                slider.step(2 if event.delta > 0 else -2)
                return
            widget = widget.master

    # --- Durum Güncelleme ---
    def _apply_mute_visual(self, muted: bool):
        self.vol_icon.configure_icon(
            glyph=Icons.MUTE if muted else Icons.VOLUME,
            color=Palette.DANGER if muted else Palette.MUTED
        )
        self.v_slider.set_dimmed(muted)

    def _sync_monitor_selector(self):
        devs = list(self.worker.devices)
        sig = tuple((d.index, d.short_name) for d in devs)
        if sig != self._device_sig:
            self._device_sig = sig
            if len(devs) > 1:
                options = [(d.index, d.short_name) for d in devs] + [(-1, "Tümü")]
                self.monitor_seg.set_options(options)
                self.monitor_seg.pack(fill=tk.X, pady=(self.t.px(14), 0))
            else:
                self.monitor_seg.pack_forget()
        self.monitor_seg.set(self.worker.active_index)

    def update_values(
        self,
        brightness: int,
        contrast: int,
        volume: int,
        input_code: int,
        status: MonitorStatus,
        active_mode: str = "Standart",
        active_desc: Optional[str] = None
    ):
        for slider, lbl, value in ((self.b_slider, self.b_val, brightness),
                                   (self.c_slider, self.c_val, contrast),
                                   (self.v_slider, self.v_val, volume)):
            if not slider.dragging:
                slider.set(value)
                lbl.config(text=str(value))

        self.mode_seg.set(active_mode)
        self.input_seg.set(input_code)
        self._apply_mute_visual(self.worker.is_muted)
        self._sync_monitor_selector()

        # Başlık ve alt bilgi
        detail = ""
        if self.worker.active_index == -1:
            self.title_lbl.config(text="Tüm ekranlar")
            detail = " + ".join(d.short_name for d in self.worker.devices)
        elif self.worker.active_device:
            self.title_lbl.config(text=self.worker.active_device.description)
            detail = INPUT_SOURCES.get(input_code, "")
        elif active_desc:
            self.title_lbl.config(text=active_desc)

        status_text, status_color = _STATUS_STYLE.get(status, ("Bağlantı yok", Palette.DANGER))
        if status == MonitorStatus.CONNECTED and detail:
            status_text = f"{status_text}  ·  {detail}"
        auto_hint = self._automation_hint()
        if status == MonitorStatus.CONNECTED and auto_hint:
            status_text = f"{status_text}  ·  {auto_hint}"
        self.status_dot.config(fg=status_color)
        self.status_lbl.config(text=status_text)

    def _automation_hint(self) -> str:
        auto = self.automation
        if auto is None:
            return ""
        try:
            proc = auto.app_rules.active_process
            if proc:
                return f"{proc.removesuffix('.exe')} için {self.worker.get_current_mode()}"
            if auto.schedule.enabled:
                return "Elle ayarlandı" if auto.schedule.is_overridden else "Otomatik"
        except Exception:
            pass
        return ""

    # --- Göster / Gizle ---
    def toggle(self):
        now = time.time()
        if now - self._last_toggle_time < 0.25:
            return
        self._last_toggle_time = now

        # FocusOut sonrası hızlı tetiklemeyi önle
        if now - self._last_hide_time < 0.25:
            return

        if self._window is not None and self._window.winfo_viewable():
            self.hide()
        else:
            self.show()

    def show(self):
        if self._window is None:
            self._create_window()
        w = self._window

        desc = self.worker.active_device.description if self.worker.active_device else "Monitör"
        self.update_values(
            self.worker.get_current_brightness(),
            self.worker.get_current_contrast(),
            self.worker.get_current_volume(),
            self.worker.get_current_input(),
            self.worker.status,
            self.worker.get_current_mode(),
            desc
        )

        w.geometry(f"{self.width}x1")
        w.update_idletasks()
        height = w.winfo_reqheight()

        margin = self.t.px(12)
        area = get_work_area()
        if area:
            left, top, right, bottom = area
        else:
            left, top, right, bottom = 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight() - 48
        x = right - self.width - margin
        y = bottom - height - margin

        w.geometry(f"{self.width}x{height}+{x}+{y}")
        w.attributes("-alpha", 0.0)
        w.deiconify()
        style_window(w, rounded=True, dark_titlebar=False, border_color=Palette.BORDER)
        w.lift()
        w.focus_force()
        self._fader.run(0.0, 1.0, duration_ms=120, steps=6)

    def hide(self):
        if self._window is not None and self._window.winfo_viewable():
            self._last_hide_time = time.time()
            if self._fader:
                self._fader.cancel()
            self._window.withdraw()

    def _on_focus_out(self, event):
        if self._window is not None and self._window.winfo_viewable():
            self.root.after(100, self._check_focus_and_hide)

    def _check_focus_and_hide(self):
        if self._window is not None and self._window.winfo_viewable():
            try:
                if self._window.focus_get() is None:
                    self.hide()
            except Exception:
                self.hide()
