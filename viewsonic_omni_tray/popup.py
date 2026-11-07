"""
ViewSonic OMNI DDC/CI Tray - Taskbar Slider & Presets Popup (Tray UI)
Parlaklık, Kontrast, Ekran Modları (ViewMode), Ses, Giriş Kaynağı ve Uyku kontrollerini içeren modern koyu temalı hızlı kontrol paneli.
"""

import time
import tkinter as tk
from typing import Optional, Callable, Dict
from .config import ConfigManager
from .queue_worker import AsyncWriteQueueWorker, MonitorStatus
from .monitor_manager import INPUT_SOURCES, DISPLAY_MODES

class SliderPopup:
    """Görev çubuğu üzerinde açılan hızlı parlaklık, kontrast, ekran modu ve ses kontrol penceresi."""

    def __init__(self, root: tk.Tk, config: ConfigManager, worker: AsyncWriteQueueWorker, on_open_settings: Optional[Callable[[], None]] = None):
        self.root = root
        self.config = config
        self.worker = worker
        self.on_open_settings = on_open_settings

        self.width = 380
        self.height = 570
        self._last_toggle_time: float = 0.0
        self._last_hide_time: float = 0.0
        self._window: Optional[tk.Toplevel] = None
        self._mode_buttons: Dict[str, tk.Button] = {}
        self.input_buttons: Dict[int, tk.Button] = {}
        self._monitor_buttons: Dict[int, tk.Button] = {}
        self.mon_tab_frame: Optional[tk.Frame] = None

        self._create_window()

    def _create_window(self):
        self._window = tk.Toplevel(self.root)
        self._window.withdraw()
        self._window.overrideredirect(True)
        self._window.attributes("-topmost", True)
        self._window.configure(bg="#18181b")

        self._window.bind("<FocusOut>", self._on_focus_out)
        self._window.bind("<Escape>", lambda e: self.hide())

        main_frame = tk.Frame(
            self._window,
            bg="#18181b",
            highlightbackground="#3f3f46",
            highlightthickness=1,
            padx=16,
            pady=12
        )
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Başlık ve Durum Satırı
        header_frame = tk.Frame(main_frame, bg="#18181b")
        header_frame.pack(fill=tk.X, pady=(0, 6))

        title_sub_frame = tk.Frame(header_frame, bg="#18181b")
        title_sub_frame.pack(side=tk.LEFT)

        self.title_lbl = tk.Label(
            title_sub_frame,
            text="ViewSonic OMNI",
            font=("Segoe UI", 11, "bold"),
            fg="#f4f4f5",
            bg="#18181b"
        )
        self.title_lbl.pack(anchor="w")

        self.desc_lbl = tk.Label(
            title_sub_frame,
            text="VX2718-2KPC",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#18181b"
        )
        self.desc_lbl.pack(anchor="w")

        self.status_badge = tk.Label(
            header_frame,
            text="● Bağlı",
            font=("Segoe UI", 8, "bold"),
            fg="#10b981",
            bg="#27272a",
            padx=8,
            pady=2
        )
        self.status_badge.pack(side=tk.RIGHT)

        # Monitör Seçici Sekmeler (ViewSonic, Rampage, Tüm Ekranlar)
        self.mon_tab_frame = tk.Frame(main_frame, bg="#18181b")
        self.mon_tab_frame.pack(fill=tk.X, pady=(2, 4))
        self._build_monitor_tabs()

        sep1 = tk.Frame(main_frame, height=1, bg="#27272a")
        sep1.pack(fill=tk.X, pady=(4, 6))

        # 2. Ekran Modları (ViewMode) - 1-Tık Ön Ayarlar
        mode_header = tk.Frame(main_frame, bg="#18181b")
        mode_header.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            mode_header,
            text="📺 Ekran Modları (ViewMode)",
            font=("Segoe UI", 9, "bold"),
            fg="#e4e4e7",
            bg="#18181b"
        ).pack(side=tk.LEFT)

        self.active_mode_lbl = tk.Label(
            mode_header,
            text="Standart",
            font=("Segoe UI", 8, "bold"),
            fg="#8b5cf6",
            bg="#18181b"
        )
        self.active_mode_lbl.pack(side=tk.RIGHT)

        mode_btn_frame = tk.Frame(main_frame, bg="#18181b")
        mode_btn_frame.pack(fill=tk.X, pady=(0, 8))

        modes_list = [
            ("Oyun", "🎮 Oyun"),
            ("Film", "🎬 Film"),
            ("Metin", "📖 Metin"),
            ("Standart", "🌐 Standart"),
            ("Gece", "🌙 Gece")
        ]

        self._mode_buttons = {}
        for mode_key, btn_label in modes_list:
            btn = tk.Button(
                mode_btn_frame,
                text=btn_label,
                font=("Segoe UI", 8),
                fg="#f4f4f5",
                bg="#27272a",
                activebackground="#8b5cf6",
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                pady=5,
                command=lambda m=mode_key: self._on_mode_click(m)
            )
            btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
            self._mode_buttons[mode_key] = btn

        # 3. Parlaklık Slider'ı (VCP 0x10)
        b_frame = tk.Frame(main_frame, bg="#18181b")
        b_frame.pack(fill=tk.X, pady=(0, 2))

        tk.Label(
            b_frame,
            text="☀️ Ekran Parlaklığı",
            font=("Segoe UI", 9, "bold"),
            fg="#e4e4e7",
            bg="#18181b"
        ).pack(side=tk.LEFT)

        self.brightness_text_lbl = tk.Label(
            b_frame,
            text="%50",
            font=("Segoe UI", 10, "bold"),
            fg="#f59e0b",
            bg="#18181b"
        )
        self.brightness_text_lbl.pack(side=tk.RIGHT)

        self.scale_var = tk.IntVar(value=50)
        self.scale = tk.Scale(
            main_frame,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=self.scale_var,
            showvalue=False,
            bg="#27272a",
            troughcolor="#18181b",
            activebackground="#f59e0b",
            highlightthickness=0,
            relief=tk.FLAT,
            sliderrelief=tk.FLAT,
            length=340,
            command=self._on_brightness_moved
        )
        self.scale.pack(fill=tk.X, pady=(2, 4))

        # Adım butonları (-10%, -5%, +5%, +10%)
        stepper_frame = tk.Frame(main_frame, bg="#18181b")
        stepper_frame.pack(fill=tk.X, pady=(0, 6))

        for text, delta in [("-10%", -10), ("-5%", -5), ("+5%", +5), ("+10%", +10)]:
            btn = tk.Button(
                stepper_frame,
                text=text,
                font=("Segoe UI", 8),
                fg="#d4d4d8",
                bg="#27272a",
                activebackground="#3f3f46",
                relief=tk.FLAT,
                bd=0,
                pady=2,
                command=lambda d=delta: self._on_b_step(d)
            )
            btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)

        # 4. Kontrast Slider'ı (VCP 0x12)
        c_frame = tk.Frame(main_frame, bg="#18181b")
        c_frame.pack(fill=tk.X, pady=(2, 2))

        tk.Label(
            c_frame,
            text="🌓 Ekran Kontrastı",
            font=("Segoe UI", 9, "bold"),
            fg="#e4e4e7",
            bg="#18181b"
        ).pack(side=tk.LEFT)

        self.contrast_text_lbl = tk.Label(
            c_frame,
            text="%70",
            font=("Segoe UI", 10, "bold"),
            fg="#3b82f6",
            bg="#18181b"
        )
        self.contrast_text_lbl.pack(side=tk.RIGHT)

        self.contrast_scale_var = tk.IntVar(value=70)
        self.contrast_scale = tk.Scale(
            main_frame,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=self.contrast_scale_var,
            showvalue=False,
            bg="#27272a",
            troughcolor="#18181b",
            activebackground="#3b82f6",
            highlightthickness=0,
            relief=tk.FLAT,
            sliderrelief=tk.FLAT,
            length=340,
            command=self._on_contrast_moved
        )
        self.contrast_scale.pack(fill=tk.X, pady=(2, 6))

        sep2 = tk.Frame(main_frame, height=1, bg="#27272a")
        sep2.pack(fill=tk.X, pady=(2, 6))

        # 5. Ses Seviyesi Slider'ı (VCP 0x62)
        v_frame = tk.Frame(main_frame, bg="#18181b")
        v_frame.pack(fill=tk.X, pady=(2, 2))

        tk.Label(
            v_frame,
            text="🔊 Monitör Hoparlör Sesi",
            font=("Segoe UI", 9, "bold"),
            fg="#e4e4e7",
            bg="#18181b"
        ).pack(side=tk.LEFT)

        self.volume_text_lbl = tk.Label(
            v_frame,
            text="%50",
            font=("Segoe UI", 10, "bold"),
            fg="#06b6d4",
            bg="#18181b"
        )
        self.volume_text_lbl.pack(side=tk.RIGHT)

        vol_row = tk.Frame(main_frame, bg="#18181b")
        vol_row.pack(fill=tk.X, pady=(2, 6))

        self.vol_scale_var = tk.IntVar(value=50)
        self.vol_scale = tk.Scale(
            vol_row,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=self.vol_scale_var,
            showvalue=False,
            bg="#27272a",
            troughcolor="#18181b",
            activebackground="#06b6d4",
            highlightthickness=0,
            relief=tk.FLAT,
            sliderrelief=tk.FLAT,
            length=270,
            command=self._on_volume_moved
        )
        self.vol_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.mute_btn = tk.Button(
            vol_row,
            text="Mute",
            font=("Segoe UI", 8, "bold"),
            fg="#f4f4f5",
            bg="#27272a",
            activebackground="#ef4444",
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            command=self._on_mute_clicked
        )
        self.mute_btn.pack(side=tk.RIGHT)

        sep3 = tk.Frame(main_frame, height=1, bg="#27272a")
        sep3.pack(fill=tk.X, pady=(2, 6))

        # 6. Giriş Kaynağı Değiştirici (VCP 0x60)
        tk.Label(
            main_frame,
            text="🔌 Giriş Kaynağı (Input Switcher)",
            font=("Segoe UI", 9, "bold"),
            fg="#a1a1aa",
            bg="#18181b"
        ).pack(anchor="w", pady=(0, 4))

        input_frame = tk.Frame(main_frame, bg="#18181b")
        input_frame.pack(fill=tk.X, pady=(0, 8))

        self.input_buttons = {}
        for code, name in INPUT_SOURCES.items():
            btn = tk.Button(
                input_frame,
                text=name,
                font=("Segoe UI", 8),
                fg="#d4d4d8",
                bg="#27272a",
                activebackground="#10b981",
                relief=tk.FLAT,
                bd=0,
                pady=4,
                command=lambda c=code: self._on_input_selected(c)
            )
            btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
            self.input_buttons[code] = btn

        # 7. Alt Çubuk (Ayarlar, Uyku Modu ve Kapat)
        bottom_frame = tk.Frame(main_frame, bg="#18181b")
        bottom_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=(4, 0))

        settings_btn = tk.Button(
            bottom_frame,
            text="⚙️ Ayarlar",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#18181b",
            activebackground="#27272a",
            relief=tk.FLAT,
            bd=0,
            command=self._on_settings_click
        )
        settings_btn.pack(side=tk.LEFT)

        sleep_btn = tk.Button(
            bottom_frame,
            text="🌙 Monitörü Uykuya Al",
            font=("Segoe UI", 8),
            fg="#f59e0b",
            bg="#27272a",
            activebackground="#3f3f46",
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=3,
            command=self._on_sleep_click
        )
        sleep_btn.pack(side=tk.LEFT, padx=6)

        close_btn = tk.Button(
            bottom_frame,
            text="Kapat",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#18181b",
            activebackground="#27272a",
            relief=tk.FLAT,
            bd=0,
            command=self.hide
        )
        close_btn.pack(side=tk.RIGHT)

    def _on_brightness_moved(self, val_str: str):
        val = int(val_str)
        self.brightness_text_lbl.config(text=f"%{val}")
        self.worker.set_brightness(val, immediate_ui=False)

    def _on_contrast_moved(self, val_str: str):
        val = int(val_str)
        self.contrast_text_lbl.config(text=f"%{val}")
        self.worker.set_contrast(val, immediate_ui=False)

    def _on_volume_moved(self, val_str: str):
        val = int(val_str)
        self.volume_text_lbl.config(text=f"%{val}")
        self.worker.set_volume(val, immediate_ui=False)

    def _on_b_step(self, delta: int):
        cur = self.scale_var.get()
        new_val = max(0, min(100, cur + delta))
        self.scale_var.set(new_val)
        self.brightness_text_lbl.config(text=f"%{new_val}")
        self.worker.set_brightness(new_val, immediate_ui=True)

    def _on_mute_clicked(self):
        muted = self.worker.toggle_mute()
        self.mute_btn.config(
            text="Unmute" if muted else "Mute",
            bg="#ef4444" if muted else "#27272a"
        )

    def _on_mode_click(self, mode_key: str):
        self.worker.apply_display_mode(mode_key)
        self._highlight_active_mode(mode_key)

    def _on_input_selected(self, source_code: int):
        self.worker.set_input_source(source_code)
        self._highlight_active_input(source_code)

    def _on_sleep_click(self):
        self.hide()
        self.worker.sleep_monitor()

    def _on_settings_click(self):
        self.hide()
        if self.on_open_settings:
            self.on_open_settings()

    def _build_monitor_tabs(self):
        if self.mon_tab_frame is None:
            return

        for widget in self.mon_tab_frame.winfo_children():
            widget.destroy()
        self._monitor_buttons.clear()

        devs = self.worker.devices
        if len(devs) <= 1:
            self.mon_tab_frame.pack_forget()
            return

        self.mon_tab_frame.pack(fill=tk.X, pady=(2, 6))

        for d in devs:
            label = f"🖥️ {d.short_name}"
            btn = tk.Button(
                self.mon_tab_frame,
                text=label,
                font=("Segoe UI", 8),
                fg="#f4f4f5",
                bg="#27272a",
                activebackground="#3b82f6",
                activeforeground="#ffffff",
                relief=tk.FLAT,
                bd=0,
                pady=4,
                command=lambda idx=d.index: self._on_monitor_tab_click(idx)
            )
            btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
            self._monitor_buttons[d.index] = btn

        sync_btn = tk.Button(
            self.mon_tab_frame,
            text="🔗 Tüm Ekranlar",
            font=("Segoe UI", 8),
            fg="#f4f4f5",
            bg="#27272a",
            activebackground="#3b82f6",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            pady=4,
            command=lambda: self._on_monitor_tab_click(-1)
        )
        sync_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=1)
        self._monitor_buttons[-1] = sync_btn

        self._highlight_active_monitor(self.worker.active_index)

    def _on_monitor_tab_click(self, idx: int):
        self.worker.select_device(idx)
        self._highlight_active_monitor(idx)

    def _highlight_active_monitor(self, active_idx: int):
        for idx, btn in self._monitor_buttons.items():
            if idx == active_idx:
                btn.config(bg="#3b82f6", fg="#ffffff", font=("Segoe UI", 8, "bold"))
            else:
                btn.config(bg="#27272a", fg="#d4d4d8", font=("Segoe UI", 8))

    def _highlight_active_mode(self, active_mode: str):
        self.active_mode_lbl.config(text=active_mode)
        for mode_key, btn in self._mode_buttons.items():
            if mode_key == active_mode:
                btn.config(bg="#8b5cf6", fg="#ffffff", font=("Segoe UI", 8, "bold"))
            else:
                btn.config(bg="#27272a", fg="#f4f4f5", font=("Segoe UI", 8))

    def _highlight_active_input(self, active_code: int):
        for code, btn in self.input_buttons.items():
            if code == active_code:
                btn.config(bg="#10b981", fg="#ffffff", font=("Segoe UI", 8, "bold"))
            else:
                btn.config(bg="#27272a", fg="#d4d4d8", font=("Segoe UI", 8))

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
        self.scale_var.set(brightness)
        self.brightness_text_lbl.config(text=f"%{brightness}")

        self.contrast_scale_var.set(contrast)
        self.contrast_text_lbl.config(text=f"%{contrast}")

        self.vol_scale_var.set(volume)
        self.volume_text_lbl.config(text=f"%{volume}")

        self._highlight_active_mode(active_mode)
        self._highlight_active_input(input_code)
        self._highlight_active_monitor(self.worker.active_index)

        if self.worker.active_index == -1:
            self.title_lbl.config(text="Çift Ekran (Senkron)")
            names = " + ".join([d.short_name for d in self.worker.devices]) if self.worker.devices else "Tüm Ekranlar"
            self.desc_lbl.config(text=f"{names} Senkron Kontrol")
        elif self.worker.active_device:
            dev = self.worker.active_device
            if dev.brand:
                self.title_lbl.config(text=f"{dev.brand} Monitör")
                self.desc_lbl.config(text=dev.model or dev.description)
            else:
                self.title_lbl.config(text="Monitör")
                self.desc_lbl.config(text=dev.description)
        elif active_desc:
            self.desc_lbl.config(text=active_desc)

        if status == MonitorStatus.CONNECTED:
            self.status_badge.config(text="● Bağlı", fg="#10b981")
        elif status == MonitorStatus.SLEEPING:
            self.status_badge.config(text="● Uykuda", fg="#f59e0b")
        else:
            self.status_badge.config(text="● Bağlantı Yok", fg="#ef4444")

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

        if len(self.worker.devices) > 1:
            self.height = 620
        else:
            self.height = 570

        self._build_monitor_tabs()

        cur_b = self.worker.get_current_brightness()
        cur_c = self.worker.get_current_contrast()
        cur_v = self.worker.get_current_volume()
        cur_i = self.worker.get_current_input()
        cur_m = self.worker.get_current_mode()
        desc = self.worker.active_device.description if self.worker.active_device else "ViewSonic OMNI"

        self.update_values(cur_b, cur_c, cur_v, cur_i, self.worker.status, cur_m, desc)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - self.width - 16
        y = sh - self.height - 54

        self._window.geometry(f"{self.width}x{self.height}+{x}+{y}")
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()

    def hide(self):
        if self._window is not None and self._window.winfo_viewable():
            self._last_hide_time = time.time()
            self._window.withdraw()

    def _on_focus_out(self, event):
        if self._window is not None and self._window.winfo_viewable():
            self.root.after(100, self._check_focus_and_hide)

    def _check_focus_and_hide(self):
        if self._window is not None and self._window.winfo_viewable():
            try:
                focused = self._window.focus_get()
                if focused is None:
                    self.hide()
            except Exception:
                self.hide()
