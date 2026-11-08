"""
Lumio - Settings Window
Monitör seçimi, genel seçenekler, profiller ve klavye kısayolları için minimal ayar penceresi.
"""

import threading
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional, Callable, Dict, Any, List

from .config import ConfigManager
from .queue_worker import AsyncWriteQueueWorker
from .monitor_manager import DISPLAY_MODES, input_short_name
from .automation import parse_time, normalize_process_name, list_window_processes
from .ui_kit import (
    Palette, Icons, get_theme, style_window, get_work_area,
    Segmented, IconButton, Toggle, PillButton, Slider, ChipGroup, styled_entry,
)

_HOTKEY_LABELS = [
    ("brightness_up", "Parlaklık artır"),
    ("brightness_down", "Parlaklık azalt"),
    ("contrast_up", "Kontrast artır"),
    ("contrast_down", "Kontrast azalt"),
    ("volume_up", "Ses artır"),
    ("volume_down", "Ses azalt"),
    ("volume_mute", "Sessiz"),
    ("cycle_mode", "Ekran modu"),
    ("cycle_input", "Giriş kaynağı"),
    ("cycle_monitor", "Ekran değiştir"),
    ("sleep_monitor", "Uyku modu"),
    ("profile_day", "Gündüz profili"),
]


class SettingsWindow:
    """Tüm uygulama tercihlerinin düzenlendiği ayar penceresi."""

    def __init__(self, root: tk.Tk, config: ConfigManager, worker: AsyncWriteQueueWorker,
                 on_settings_saved: Optional[Callable[[], None]] = None):
        self.root = root
        self.config = config
        self.worker = worker
        self.on_settings_saved = on_settings_saved
        self.t = get_theme(root)
        self._window: Optional[tk.Toplevel] = None

        self.profile_entries: Dict[str, tk.Entry] = {}
        self.hotkey_entries: Dict[str, tk.Entry] = {}
        self.schedule_rows: List[Dict[str, Any]] = []
        self.rule_rows: List[Dict[str, Any]] = []
        self.calibration_sliders: Dict[str, Slider] = {}
        self.input_chips: Dict[str, Any] = {}

    def show(self):
        if self._window is not None and self._window.winfo_exists():
            self._window.deiconify()
            self._window.lift()
            self._window.focus_force()
            return
        self._create_window()

    # --- Yerleşim Yardımcıları ---
    def _heading(self, parent: tk.Misc, text: str, subtitle: Optional[str] = None) -> tk.Frame:
        px = self.t.px
        wrap = tk.Frame(parent, bg=Palette.BG)
        wrap.pack(fill=tk.X, pady=(px(22), px(4)))
        left = tk.Frame(wrap, bg=Palette.BG)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(left, text=text, font=self.t.font(11, "bold"), fg=Palette.TEXT, bg=Palette.BG,
                 anchor="w").pack(fill=tk.X)
        if subtitle:
            tk.Label(left, text=subtitle, font=self.t.font(8), fg=Palette.MUTED, bg=Palette.BG,
                     anchor="w").pack(fill=tk.X)
        right = tk.Frame(wrap, bg=Palette.BG)
        right.pack(side=tk.RIGHT)
        return right

    def _divider(self, parent: tk.Misc):
        tk.Frame(parent, height=1, bg=Palette.BORDER).pack(fill=tk.X)

    def _row(self, parent: tk.Misc, title: str, subtitle: Optional[str] = None) -> tk.Frame:
        px = self.t.px
        row = tk.Frame(parent, bg=Palette.BG)
        row.pack(fill=tk.X, pady=px(10))
        left = tk.Frame(row, bg=Palette.BG)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(left, text=title, font=self.t.font(10), fg=Palette.TEXT, bg=Palette.BG,
                 anchor="w").pack(fill=tk.X)
        if subtitle:
            tk.Label(left, text=subtitle, font=self.t.font(8), fg=Palette.MUTED, bg=Palette.BG,
                     anchor="w").pack(fill=tk.X)
        right = tk.Frame(row, bg=Palette.BG)
        right.pack(side=tk.RIGHT)
        return right

    def _mono_font(self):
        families = set(tkfont.families(self.root))
        for fam in ("Cascadia Mono", "Consolas"):
            if fam in families:
                return (fam, 9)
        return self.t.font(9)

    # --- Pencere ---
    def _create_window(self):
        t = self.t
        px = t.px
        bg = Palette.BG

        win = tk.Toplevel(self.root)
        win.withdraw()
        win.title("Ayarlar - Lumio")
        win.configure(bg=bg)
        win.resizable(False, True)
        win.bind("<Escape>", lambda e: win.destroy())
        self._window = win

        width = px(600)

        # Sabit alt çubuk
        footer = tk.Frame(win, bg=bg, padx=px(28), pady=px(16))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Frame(win, height=1, bg=Palette.BORDER).pack(side=tk.BOTTOM, fill=tk.X)
        PillButton(footer, t, "Kaydet", command=self._on_save, primary=True, width=px(110)).pack(side=tk.RIGHT)
        PillButton(footer, t, "İptal", command=win.destroy, width=px(90)).pack(side=tk.RIGHT, padx=(0, px(8)))

        # Kaydırılabilir içerik
        canvas = tk.Canvas(win, bg=bg, highlightthickness=0, bd=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        content = tk.Frame(canvas, bg=bg, padx=px(28), pady=px(6))
        canvas.create_window((0, 0), window=content, anchor="nw", tags="content")
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure("content", width=e.width))

        def _on_wheel(event):
            if content.winfo_reqheight() > canvas.winfo_height():
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        win.bind("<MouseWheel>", _on_wheel)

        tk.Label(content, text="Ayarlar", font=t.display_font(18), fg=Palette.TEXT, bg=bg,
                 anchor="w").pack(fill=tk.X, pady=(px(14), 0))

        # 1. Monitör
        right = self._heading(content, "Monitör", "Panelin ve kısayolların kontrol ettiği ekran")
        IconButton(right, t, Icons.REFRESH, command=self._on_rescan).pack()
        self.monitor_holder = tk.Frame(content, bg=bg)
        self.monitor_holder.pack(fill=tk.X, pady=(px(6), 0))
        self.monitor_seg = Segmented(self.monitor_holder, t, [])
        self.monitor_empty_lbl = tk.Label(self.monitor_holder, text="DDC/CI destekli monitör bulunamadı.",
                                          font=t.font(9), fg=Palette.MUTED, bg=bg, anchor="w")
        self._refresh_monitor_options()

        # 2. Giriş kaynakları (monitör başına görünür girişler)
        if self.worker.devices:
            self._build_inputs_section(content)

        # 3. Genel
        self._heading(content, "Genel")
        self.osd_toggle = Toggle(self._row(content, "Ekran göstergesi",
                                           "Kısayol kullanıldığında ekranda küçük gösterge çıkar"),
                                 t, self.config.show_osd, bg=bg)
        self.osd_toggle.pack()
        self._divider(content)
        self.notif_toggle = Toggle(self._row(content, "Bağlantı bildirimleri",
                                             "Monitör uykuya geçtiğinde veya bağlantı koptuğunda bildir"),
                                   t, self.config.show_notifications, bg=bg)
        self.notif_toggle.pack()
        self._divider(content)
        self.startup_toggle = Toggle(self._row(content, "Windows ile başlat",
                                               "Oturum açıldığında sistem tepsisinde çalıştır"),
                                     t, self.config.run_on_startup, bg=bg)
        self.startup_toggle.pack()
        self._divider(content)
        step_right = self._row(content, "Kısayol adımı", "Her tuş basışında değişecek yüzde")
        steps = sorted({1, 2, 5, 10, int(self.config.step_size)})
        self.step_seg = Segmented(step_right, t, [(s, f"%{s}") for s in steps], height=px(30), font_size=8)
        self.step_seg.configure(width=px(46) * len(steps))
        self.step_seg.set(int(self.config.step_size))
        self.step_seg.pack()

        # 4. Otomatik parlaklık (saat planı)
        self._build_schedule_section(content)

        # 5. Uygulamaya göre mod
        self._build_rules_section(content)

        # 6. Ekran eşitleme (yalnızca birden fazla monitörde)
        if len(self.worker.devices) > 1:
            self._build_calibration_section(content)

        # 7. Profiller
        self._heading(content, "Profiller", "Profil kısayollarıyla uygulanan parlaklık değerleri (%)")
        grid = tk.Frame(content, bg=bg)
        grid.pack(fill=tk.X, pady=(px(8), 0))
        self.profile_entries = {}
        for i, (name, val) in enumerate(self.config.profiles.items()):
            r, c = divmod(i, 3)
            cell = tk.Frame(grid, bg=bg)
            cell.grid(row=r, column=c, sticky="w", padx=(0, px(18)), pady=px(5))
            tk.Label(cell, text=name, font=t.font(9), fg=Palette.MUTED, bg=bg, width=8,
                     anchor="w").pack(side=tk.LEFT)
            entry = styled_entry(cell, t, width=5, justify="center")
            entry.insert(0, str(val))
            entry.pack(side=tk.LEFT)
            self.profile_entries[name] = entry

        # 8. Klavye kısayolları
        self._heading(content, "Klavye kısayolları", "Biçim: <ctrl>+<alt>+tuş  ·  boş bırakılırsa devre dışı")
        hk = tk.Frame(content, bg=bg)
        hk.pack(fill=tk.X, pady=(px(8), px(18)))
        hk.columnconfigure(1, weight=1)
        hk.columnconfigure(3, weight=1)
        mono = self._mono_font()
        self.hotkey_entries = {}
        for i, (key_id, label) in enumerate(_HOTKEY_LABELS):
            r, c = divmod(i, 2)
            tk.Label(hk, text=label, font=t.font(9), fg=Palette.MUTED, bg=bg,
                     anchor="w").grid(row=r, column=c * 2, sticky="w", padx=(px(16) if c else 0, px(8)), pady=px(4))
            entry = styled_entry(hk, t, width=18, font=mono)
            entry.insert(0, self.config.hotkeys.get(key_id, ""))
            entry.grid(row=r, column=c * 2 + 1, sticky="ew", pady=px(4))
            self.hotkey_entries[key_id] = entry

        # Boyut ve konum
        win.update_idletasks()
        area = get_work_area()
        if area:
            left, top, right_edge, bottom = area
        else:
            left, top, right_edge, bottom = 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        wanted_h = content.winfo_reqheight() + footer.winfo_reqheight() + 1
        height = min(wanted_h, (bottom - top) - px(60))
        x = left + (right_edge - left - width) // 2
        y = top + max(0, (bottom - top - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(width, min(height, px(360)))

        win.deiconify()
        style_window(win, rounded=False, dark_titlebar=True)
        win.lift()
        win.focus_force()

    # --- Giriş Kaynakları ---
    def _build_inputs_section(self, content: tk.Frame):
        t, px, bg = self.t, self.t.px, Palette.BG
        self._heading(content, "Giriş kaynakları",
                      "Panelde ve menüde gösterilecek girişler. Monitörünüzde olmayan girişleri kapatın.")
        self.input_chips = {}
        for dev in self.worker.devices:
            block = tk.Frame(content, bg=bg)
            block.pack(fill=tk.X, pady=(px(8), 0))
            tk.Label(block, text=dev.description, font=t.font(9), fg=Palette.TEXT, bg=bg,
                     anchor="w").pack(fill=tk.X, pady=(0, px(4)))
            chips = ChipGroup(block, t, [(c, input_short_name(c)) for c in dev.reported_inputs],
                              selected=self.worker.device_visible_inputs(dev))
            chips.pack(anchor="w")
            self.input_chips[dev.description] = (dev, chips)

    # --- Otomatik Parlaklık ---
    def _build_schedule_section(self, content: tk.Frame):
        t, px, bg = self.t, self.t.px, Palette.BG
        sched = self.config.schedule
        right = self._heading(content, "Otomatik parlaklık",
                              "Günün saatine göre parlaklığı ayarlar. Elle değiştirirseniz bir sonraki saate kadar karışmaz.")
        self.schedule_toggle = Toggle(right, t, bool(sched.get("enabled")), bg=bg)
        self.schedule_toggle.pack()

        self.schedule_list = tk.Frame(content, bg=bg)
        self.schedule_list.pack(fill=tk.X, pady=(px(6), 0))
        self.schedule_rows = []
        for point in sched.get("points", []) or []:
            if isinstance(point, dict):
                self._add_schedule_row(point.get("time", ""), point.get("brightness", 50))

        actions = tk.Frame(content, bg=bg)
        actions.pack(fill=tk.X, pady=(px(4), 0))
        PillButton(actions, t, "+  Saat ekle", command=lambda: self._add_schedule_row("12:00", 50)).pack(side=tk.LEFT)

        trans_right = self._row(content, "Geçiş süresi", "Parlaklık yeni değere bu sürede yavaşça geçer")
        options = [(0, "Anında"), (15, "15 dk"), (30, "30 dk"), (60, "60 dk")]
        try:
            current = int(sched.get("transition_min", 30))
        except (TypeError, ValueError):
            current = 30
        if current not in [k for k, _ in options]:
            options.append((current, f"{current} dk"))
        self.transition_seg = Segmented(trans_right, t, options, height=px(30), font_size=8)
        self.transition_seg.configure(width=px(62) * len(options))
        self.transition_seg.set(current)
        self.transition_seg.pack()

    def _add_schedule_row(self, time_text: str, brightness: Any):
        t, px, bg = self.t, self.t.px, Palette.BG
        row = tk.Frame(self.schedule_list, bg=bg)
        row.pack(fill=tk.X, pady=px(3))
        tk.Label(row, text=Icons.CLOCK, font=t.icon_font(10), fg=Palette.MUTED, bg=bg).pack(side=tk.LEFT)
        time_entry = styled_entry(row, t, width=6, justify="center")
        time_entry.insert(0, str(time_text))
        time_entry.pack(side=tk.LEFT, padx=(px(8), px(8)))
        tk.Label(row, text="saatinden itibaren parlaklık", font=t.font(9), fg=Palette.MUTED, bg=bg).pack(side=tk.LEFT)
        value_entry = styled_entry(row, t, width=4, justify="center")
        value_entry.insert(0, str(brightness))
        value_entry.pack(side=tk.LEFT, padx=(px(8), px(4)))
        tk.Label(row, text="%", font=t.font(9), fg=Palette.MUTED, bg=bg).pack(side=tk.LEFT)

        entry = {"frame": row, "time": time_entry, "value": value_entry}
        IconButton(row, t, Icons.CLOSE, icon_size=9, size=px(28),
                   command=lambda: self._remove_row(self.schedule_rows, entry)).pack(side=tk.RIGHT)
        self.schedule_rows.append(entry)

    # --- Uygulamaya Göre Mod ---
    def _build_rules_section(self, content: tk.Frame):
        t, px, bg = self.t, self.t.px, Palette.BG
        rules_cfg = self.config.app_rules
        right = self._heading(content, "Uygulamaya göre mod",
                              "Seçtiğiniz uygulama öndeyken ekran modu değişir; çıkınca önceki ayarlar geri gelir.")
        self.rules_toggle = Toggle(right, t, bool(rules_cfg.get("enabled")), bg=bg)
        self.rules_toggle.pack()

        self.rules_list = tk.Frame(content, bg=bg)
        self.rules_list.pack(fill=tk.X, pady=(px(6), 0))
        self.rules_empty_lbl = tk.Label(self.rules_list, text="Henüz kural yok. Örneğin bir oyunu Oyun moduna bağlayın.",
                                        font=t.font(9), fg=Palette.FAINT, bg=bg, anchor="w")
        self.rule_rows = []
        for rule in rules_cfg.get("rules", []) or []:
            if isinstance(rule, dict):
                self._add_rule_row(rule.get("process", ""), rule.get("mode", "Oyun"))
        self._update_rules_empty()

        actions = tk.Frame(content, bg=bg)
        actions.pack(fill=tk.X, pady=(px(4), 0))
        self.pick_app_btn = PillButton(actions, t, "+  Açık uygulamadan seç", command=self._show_app_picker)
        self.pick_app_btn.pack(side=tk.LEFT)
        PillButton(actions, t, "Elle ekle", command=lambda: self._add_rule_row("", "Oyun", focus=True)).pack(
            side=tk.LEFT, padx=(px(8), 0))

    def _add_rule_row(self, process: str, mode: str, focus: bool = False):
        t, px, bg = self.t, self.t.px, Palette.BG
        row = tk.Frame(self.rules_list, bg=bg)
        row.pack(fill=tk.X, pady=px(3))
        proc_entry = styled_entry(row, t, width=18)
        proc_entry.insert(0, process)
        proc_entry.pack(side=tk.LEFT)
        mode_seg = Segmented(row, t, [(k, k) for k in DISPLAY_MODES], height=px(30), font_size=8)
        mode_seg.configure(width=px(300))
        mode_seg.set(mode if mode in DISPLAY_MODES else "Oyun")
        mode_seg.pack(side=tk.LEFT, padx=(px(8), 0))

        entry = {"frame": row, "process": proc_entry, "mode": mode_seg}
        IconButton(row, t, Icons.CLOSE, icon_size=9, size=px(28),
                   command=lambda: self._remove_row(self.rule_rows, entry)).pack(side=tk.RIGHT)
        self.rule_rows.append(entry)
        self._update_rules_empty()
        if focus:
            proc_entry.focus_set()

    def _update_rules_empty(self):
        if self.rule_rows:
            self.rules_empty_lbl.pack_forget()
        else:
            self.rules_empty_lbl.pack(fill=tk.X, pady=self.t.px(4))

    def _show_app_picker(self):
        """Açık pencereleri olan uygulamaları açılır menüde listeler."""
        try:
            apps = list_window_processes()
        except Exception as e:
            print(f"[!] Uygulama listesi alınamadı: {e}")
            apps = []
        existing = {normalize_process_name(r["process"].get()) for r in self.rule_rows}
        menu = tk.Menu(self._window, tearoff=False, bg=Palette.SURFACE, fg=Palette.TEXT,
                       activebackground=Palette.SELECTED, activeforeground=Palette.TEXT,
                       disabledforeground=Palette.FAINT, bd=0, font=self.t.font(9))
        added = 0
        for proc, _title in apps:
            if proc in existing:
                continue
            menu.add_command(label=proc, command=lambda p=proc: self._add_rule_row(p, "Oyun"))
            added += 1
        if not added:
            menu.add_command(label="Eklenebilecek açık uygulama bulunamadı", state=tk.DISABLED)
        btn = self.pick_app_btn
        try:
            menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height() + self.t.px(4))
        finally:
            menu.grab_release()

    # --- Ekran Eşitleme ---
    def _build_calibration_section(self, content: tk.Frame):
        t, px, bg = self.t, self.t.px, Palette.BG
        self._heading(content, "Ekran eşitleme",
                      "\"Tümü\" seçiliyken bir ekran daha parlak görünüyorsa farkı buradan düzeltin.")
        self.calibration_sliders = {}
        for dev in self.worker.devices:
            right = self._row(content, dev.description)
            value_lbl = tk.Label(right, text="", width=4, anchor="e", font=t.font(10), fg=Palette.TEXT, bg=bg)

            def _fmt(v: int, lbl=value_lbl):
                lbl.config(text=f"{v:+d}" if v else "0")

            slider = Slider(right, t, value=self.config.get_calibration(dev.description), from_=-30, to=30,
                            command=_fmt, origin=0)
            slider.configure(width=px(200))
            slider.pack(side=tk.LEFT)
            value_lbl.pack(side=tk.LEFT, padx=(px(6), 0))
            _fmt(slider.get())
            self.calibration_sliders[dev.description] = slider

    def _remove_row(self, rows: List[Dict[str, Any]], entry: Dict[str, Any]):
        if entry in rows:
            rows.remove(entry)
        entry["frame"].destroy()
        if rows is self.rule_rows:
            self._update_rules_empty()

    # --- Monitör Listesi ---
    def _refresh_monitor_options(self):
        if self._window is None or not self._window.winfo_exists():
            return
        devs = list(self.worker.devices)
        if devs:
            options = [(d.index, d.short_name) for d in devs]
            if len(devs) > 1:
                options.append((-1, "Tümü"))
            self.monitor_seg.set_options(options)
            keys = [k for k, _ in options]
            self.monitor_seg.set(self.worker.active_index if self.worker.active_index in keys else keys[0])
            self.monitor_empty_lbl.pack_forget()
            self.monitor_seg.pack(fill=tk.X)
        else:
            self.monitor_seg.pack_forget()
            self.monitor_empty_lbl.pack(fill=tk.X)

    def _on_rescan(self):
        def _work():
            self.worker.rescan_monitors()
            try:
                self.root.after(0, self._refresh_monitor_options)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True, name="Settings-Rescan").start()

    # --- Kaydet ---
    def _on_save(self):
        selected: Any = self.monitor_seg.get() if self.worker.devices else None
        if selected is not None and selected != self.worker.active_index:
            self.worker.select_device(selected)

        for name, entry in self.profile_entries.items():
            try:
                self.config.profiles[name] = max(0, min(100, int(entry.get())))
            except ValueError:
                pass

        for key_id, entry in self.hotkey_entries.items():
            self.config.hotkeys[key_id] = entry.get().strip()

        step = self.step_seg.get()
        if step is not None:
            self.config.step_size = int(step)

        # Otomatik parlaklık
        points = []
        for row in self.schedule_rows:
            minute = parse_time(row["time"].get())
            try:
                value = max(0, min(100, int(row["value"].get())))
            except ValueError:
                continue
            if minute is not None:
                points.append({"time": f"{minute // 60:02d}:{minute % 60:02d}", "brightness": value})
        points.sort(key=lambda p: p["time"])
        sched = self.config.schedule
        sched["enabled"] = self.schedule_toggle.get()
        sched["points"] = points
        transition = self.transition_seg.get()
        if transition is not None:
            sched["transition_min"] = int(transition)

        # Uygulama kuralları
        rules = []
        seen = set()
        for row in self.rule_rows:
            proc = normalize_process_name(row["process"].get())
            mode = row["mode"].get()
            if proc and proc not in seen and mode in DISPLAY_MODES:
                rules.append({"process": proc, "mode": mode})
                seen.add(proc)
        self.config.app_rules["enabled"] = self.rules_toggle.get()
        self.config.app_rules["rules"] = rules

        # Görünür giriş kaynakları (varsayılanla aynıysa saklanmaz; monitör değişirse varsayılan kullanılır)
        for name, (dev, chips) in self.input_chips.items():
            chosen = chips.get()
            self.config.set_visible_inputs(
                name, None if (not chosen or chosen == dev.default_visible_inputs) else chosen)

        # Ekran eşitleme
        for name, slider in self.calibration_sliders.items():
            value = int(slider.get())
            if value:
                self.config.calibration[name] = value
            else:
                self.config.calibration.pop(name, None)

        self.config.show_osd = self.osd_toggle.get()
        self.config.show_notifications = self.notif_toggle.get()
        self.config.run_on_startup = self.startup_toggle.get()

        self.config.save()

        if self.on_settings_saved:
            self.on_settings_saved()

        self._window.destroy()
