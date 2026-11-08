"""
ViewSonic OMNI DDC/CI Tray - Settings Window
Kullanıcıya özel kısayollar, profiller, bildirimler ve başlangıç ayarlarını yapılandırma ekranı.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable
from .config import ConfigManager
from .queue_worker import AsyncWriteQueueWorker

class SettingsWindow:
    """Tüm uygulama tercihlerinin düzenlenebileceği pencereli ayar yöneticisi."""

    def __init__(self, root: tk.Tk, config: ConfigManager, worker: AsyncWriteQueueWorker, on_settings_saved: Optional[Callable[[], None]] = None):
        self.root = root
        self.config = config
        self.worker = worker
        self.on_settings_saved = on_settings_saved
        self._window: Optional[tk.Toplevel] = None

    def show(self):
        if self._window is not None and self._window.winfo_exists():
            self._window.lift()
            self._window.focus_force()
            return

        self._create_window()

    def _create_window(self):
        self._window = tk.Toplevel(self.root)
        self._window.title("ViewSonic OMNI - Yapılandırma ve Ayarlar")
        self._window.geometry("520x640")
        self._window.minsize(480, 580)
        self._window.configure(bg="#18181b")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - 520) // 2
        y = (sh - 640) // 2
        self._window.geometry(f"520x640+{x}+{y}")

        container = tk.Frame(self._window, bg="#18181b", padx=20, pady=16)
        container.pack(fill=tk.BOTH, expand=True)

        title_lbl = tk.Label(
            container,
            text="⚙️ ViewSonic OMNI DDC/CI Ayarları",
            font=("Segoe UI", 13, "bold"),
            fg="#f4f4f5",
            bg="#18181b"
        )
        title_lbl.pack(anchor="w", pady=(0, 12))

        # --- 1. Bölüm: Hedef Monitör ---
        sec1 = tk.LabelFrame(
            container,
            text=" Hedef Monitör ",
            font=("Segoe UI", 9, "bold"),
            fg="#f59e0b",
            bg="#18181b",
            padx=12,
            pady=8
        )
        sec1.pack(fill=tk.X, pady=(0, 10))

        mon_frame = tk.Frame(sec1, bg="#18181b")
        mon_frame.pack(fill=tk.X)

        self.monitor_var = tk.StringVar()
        self._refresh_monitor_list()

        self.monitor_combo = ttk.Combobox(
            mon_frame,
            textvariable=self.monitor_var,
            state="readonly",
            values=self.monitor_options,
            font=("Segoe UI", 9)
        )
        self.monitor_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self._select_active_monitor_option()

        rescan_btn = tk.Button(
            mon_frame,
            text="Yeniden Tara",
            font=("Segoe UI", 8),
            fg="#d4d4d8",
            bg="#27272a",
            relief=tk.FLAT,
            command=self._on_rescan
        )
        rescan_btn.pack(side=tk.RIGHT)

        # --- 2. Bölüm: Ön Tanımlı Profiller ---
        sec2 = tk.LabelFrame(
            container,
            text=" Profil Parlaklık Yüzdeleri (%) ",
            font=("Segoe UI", 9, "bold"),
            fg="#f59e0b",
            bg="#18181b",
            padx=12,
            pady=8
        )
        sec2.pack(fill=tk.X, pady=(0, 10))

        prof_grid = tk.Frame(sec2, bg="#18181b")
        prof_grid.pack(fill=tk.X)

        self.profile_entries = {}
        row = 0
        col = 0
        for name, default_val in self.config.profiles.items():
            lbl = tk.Label(prof_grid, text=f"{name}:", font=("Segoe UI", 9), fg="#e4e4e7", bg="#18181b")
            lbl.grid(row=row, column=col * 2, sticky="w", padx=(0, 6), pady=3)

            entry = tk.Spinbox(
                prof_grid,
                from_=0,
                to=100,
                width=5,
                font=("Segoe UI", 9),
                bg="#27272a",
                fg="#f4f4f5",
                buttonbackground="#3f3f46"
            )
            entry.delete(0, tk.END)
            entry.insert(0, str(default_val))
            entry.grid(row=row, column=col * 2 + 1, sticky="w", padx=(0, 16), pady=3)
            self.profile_entries[name] = entry

            col += 1
            if col > 1:
                col = 0
                row += 1

        # --- 3. Bölüm: Klavye Kısayolları (Hotkeys) ---
        sec3 = tk.LabelFrame(
            container,
            text=" Klavye Kısayolları (Global Hotkeys) ",
            font=("Segoe UI", 9, "bold"),
            fg="#f59e0b",
            bg="#18181b",
            padx=12,
            pady=8
        )
        sec3.pack(fill=tk.X, pady=(0, 10))

        hotkey_frame = tk.Frame(sec3, bg="#18181b")
        hotkey_frame.pack(fill=tk.X)

        self.hotkey_entries = {}
        hotkey_labels = [
            ("brightness_up", "Parlaklık +:"),
            ("brightness_down", "Parlaklık -:"),
            ("contrast_up", "Kontrast +:"),
            ("contrast_down", "Kontrast -:"),
            ("cycle_mode", "Ekran Modu:"),
            ("cycle_monitor", "Ekran Değiştir:"),
            ("volume_up", "Ses +:"),
            ("volume_down", "Ses -:"),
            ("volume_mute", "Sessiz (Mute):"),
            ("cycle_input", "Giriş Kaynağı:"),
            ("sleep_monitor", "Uyku Modu:"),
            ("profile_day", "Gündüz Profili:")
        ]

        for i, (key_id, desc) in enumerate(hotkey_labels):
            r = i // 2
            c = i % 2
            tk.Label(
                hotkey_frame,
                text=desc,
                font=("Segoe UI", 8),
                fg="#a1a1aa",
                bg="#18181b"
            ).grid(row=r, column=c * 2, sticky="w", padx=(10 if c > 0 else 0, 4), pady=2)

            val = self.config.hotkeys.get(key_id, "")
            e = tk.Entry(
                hotkey_frame,
                font=("Segoe UI", 8),
                bg="#27272a",
                fg="#f4f4f5",
                insertbackground="#f59e0b",
                width=16
            )
            e.insert(0, val)
            e.grid(row=r, column=c * 2 + 1, sticky="ew", padx=(0, 6), pady=2)
            hotkey_frame.columnconfigure(c * 2 + 1, weight=1)
            self.hotkey_entries[key_id] = e

        step_frame = tk.Frame(sec3, bg="#18181b")
        step_frame.pack(fill=tk.X, pady=(6, 0))

        tk.Label(
            step_frame,
            text="Kısayol Adım Boyutu (%):",
            font=("Segoe UI", 8),
            fg="#a1a1aa",
            bg="#18181b"
        ).pack(side=tk.LEFT)

        self.step_spin = tk.Spinbox(
            step_frame,
            from_=1,
            to=25,
            width=5,
            font=("Segoe UI", 8),
            bg="#27272a",
            fg="#f4f4f5"
        )
        self.step_spin.delete(0, tk.END)
        self.step_spin.insert(0, str(self.config.step_size))
        self.step_spin.pack(side=tk.LEFT, padx=8)

        # --- 4. Bölüm: Seçenekler (OSD, Bildirim & Başlangıç) ---
        sec4 = tk.Frame(container, bg="#18181b")
        sec4.pack(fill=tk.X, pady=(0, 14))

        self.osd_check_var = tk.BooleanVar(value=self.config.show_osd)
        tk.Checkbutton(
            sec4,
            text="Kısayol kullanımında ekranda OSD HUD (Parlaklık Çubuğu) göster",
            variable=self.osd_check_var,
            font=("Segoe UI", 9),
            fg="#e4e4e7",
            bg="#18181b",
            selectcolor="#27272a",
            activebackground="#18181b",
            activeforeground="#f4f4f5"
        ).pack(anchor="w", pady=2)

        self.notif_check_var = tk.BooleanVar(value=self.config.show_notifications)
        tk.Checkbutton(
            sec4,
            text="Monitör uyku/kopma durumunda Windows balon bildirimleri göster",
            variable=self.notif_check_var,
            font=("Segoe UI", 9),
            fg="#e4e4e7",
            bg="#18181b",
            selectcolor="#27272a",
            activebackground="#18181b",
            activeforeground="#f4f4f5"
        ).pack(anchor="w", pady=2)

        self.startup_check_var = tk.BooleanVar(value=self.config.run_on_startup)
        tk.Checkbutton(
            sec4,
            text="Windows açılışında otomatik başlat (Sistem Tepsisinde)",
            variable=self.startup_check_var,
            font=("Segoe UI", 9),
            fg="#e4e4e7",
            bg="#18181b",
            selectcolor="#27272a",
            activebackground="#18181b",
            activeforeground="#f4f4f5"
        ).pack(anchor="w", pady=2)

        # --- Butonlar (Kaydet & İptal) ---
        btn_frame = tk.Frame(container, bg="#18181b")
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        save_btn = tk.Button(
            btn_frame,
            text="Kaydet ve Uygula",
            font=("Segoe UI", 9, "bold"),
            fg="#18181b",
            bg="#f59e0b",
            activebackground="#d97706",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=7,
            command=self._on_save
        )
        save_btn.pack(side=tk.RIGHT, padx=(8, 0))

        cancel_btn = tk.Button(
            btn_frame,
            text="İptal",
            font=("Segoe UI", 9),
            fg="#a1a1aa",
            bg="#27272a",
            activebackground="#3f3f46",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=7,
            command=self._window.destroy
        )
        cancel_btn.pack(side=tk.RIGHT)

    def _refresh_monitor_list(self):
        self.monitor_options = []
        for dev in self.worker.devices:
            self.monitor_options.append(f"[{dev.index}] {dev.description}")
        if len(self.worker.devices) > 1:
            self.monitor_options.append("[-1] 🔗 Tüm Ekranlar (Senkron Kontrol)")
        if not self.monitor_options:
            self.monitor_options.append("Monitör bulunamadı")

    def _select_active_monitor_option(self):
        """Combobox'ta şu an aktif olan monitörü seçili getirir."""
        if not self.monitor_options:
            return
        cur_idx = self.worker.active_index
        found_idx = 0
        for i, opt in enumerate(self.monitor_options):
            if opt.startswith(f"[{cur_idx}]"):
                found_idx = i
                break
        self.monitor_combo.current(found_idx)

    def _on_rescan(self):
        self.worker.rescan_monitors()
        self._refresh_monitor_list()
        self.monitor_combo["values"] = self.monitor_options
        self._select_active_monitor_option()
        messagebox.showinfo("Tarama", "Monitör listesi güncellendi.", parent=self._window)

    def _on_save(self):
        selected = self.monitor_var.get()
        if selected and selected.startswith("["):
            try:
                idx = int(selected.split("]")[0][1:])
                self.worker.select_device(idx)
            except Exception:
                pass

        for name, entry in self.profile_entries.items():
            try:
                v = int(entry.get())
                self.config.profiles[name] = max(0, min(100, v))
            except ValueError:
                pass

        for key_id, entry in self.hotkey_entries.items():
            self.config.hotkeys[key_id] = entry.get().strip()

        try:
            self.config.step_size = int(self.step_spin.get())
        except ValueError:
            pass

        self.config.show_osd = self.osd_check_var.get()
        self.config.show_notifications = self.notif_check_var.get()
        self.config.run_on_startup = self.startup_check_var.get()

        self.config.save()

        if self.on_settings_saved:
            self.on_settings_saved()

        self._window.destroy()
