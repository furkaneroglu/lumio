"""
ViewSonic OMNI DDC/CI Tray - Main Application Coordinator
Tekil örnek (Single Instance) denetimi, Tkinter ana döngüsü ve tüm modüllerin koordinasyonu.
Parlaklık, Ses, Giriş Kaynağı ve Güç kontrollerini birbirine bağlar.
"""

import sys
import ctypes
from ctypes import wintypes
import tkinter as tk
from typing import Optional

from .config import ConfigManager
from .monitor_manager import MonitorManager
from .queue_worker import AsyncWriteQueueWorker, MonitorStatus
from .notifications import NotificationEngine
from .osd import OSDOverlay
from .popup import SliderPopup
from .settings import SettingsWindow
from .hotkeys import HotkeyService
from .tray import TrayApp
from .automation import AutomationService

MUTEX_NAME = "Global\\ViewSonicOmniTray_SingleInstance_Mutex_App"

def set_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class SingleInstanceLock:
    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self.mutex = None
        self.already_running = False

    def acquire(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        self.mutex = kernel32.CreateMutexW(None, False, self.name)
        last_error = kernel32.GetLastError()
        ERROR_ALREADY_EXISTS = 183
        if last_error == ERROR_ALREADY_EXISTS:
            self.already_running = True
            return False
        return True

    def release(self):
        if self.mutex:
            ctypes.windll.kernel32.CloseHandle(self.mutex)
            self.mutex = None


class ViewSonicOmniApp:
    """Tüm modülleri birbirine bağlayan ana koordinatör sınıf."""

    def __init__(self):
        set_dpi_awareness()

        self.lock = SingleInstanceLock()
        if not self.lock.acquire():
            print("[!] ViewSonic OMNI Tray uygulaması zaten arka planda çalışıyor.")
            sys.exit(0)

        # 1. Temel Servisler ve Donanım Katmanı
        self.config = ConfigManager()
        self.monitor_manager = MonitorManager()
        self.worker = AsyncWriteQueueWorker(self.config, self.monitor_manager)

        # 2. Tkinter Kök Penceresi
        self.root = tk.Tk()
        self.root.withdraw()

        # 3. UI Bileşenleri
        self.osd = OSDOverlay(self.root, self.config)
        self.popup = SliderPopup(
            self.root,
            self.config,
            self.worker,
            on_open_settings=self.open_settings
        )
        self.settings_window = SettingsWindow(
            self.root,
            self.config,
            self.worker,
            on_settings_saved=self._on_settings_saved
        )

        # 4. Kısayol Servisi
        self.hotkeys = HotkeyService(
            self.config,
            self.worker,
            on_hotkey_triggered=self._on_hotkey_triggered
        )

        # 5. Otomasyon (saate göre parlaklık, uygulamaya göre mod)
        self.automation = AutomationService(self.config, self.worker)

        self.popup.automation = self.automation

        # 6. Sistem Tepsisi
        self.tray = TrayApp(
            self.config,
            self.worker,
            on_open_popup=self._safe_open_popup,
            on_open_settings=self._safe_open_settings,
            on_exit=self.quit,
            on_automation_changed=self.automation.reload
        )

        # 7. Bildirim Motoru
        self.notifications = NotificationEngine(
            self.config,
            tray_icon_provider=lambda: self.tray.icon
        )

        # 8. Dinleyicilerin Bağlanması
        self.worker.add_brightness_listener(self._on_worker_brightness)
        self.worker.add_contrast_listener(self._on_worker_contrast)
        self.worker.add_mode_listener(self._on_worker_mode)
        self.worker.add_volume_listener(self._on_worker_volume)
        self.worker.add_input_listener(self._on_worker_input)
        self.worker.add_status_listener(self._on_worker_status)
        self.worker.add_notification_listener(self._on_worker_notification)

    def run(self):
        self.worker.start()
        self.automation.start()
        self.hotkeys.start()
        self.tray.start()

        try:
            self.root.mainloop()
        finally:
            self.cleanup()

    def quit(self):
        self.root.after(0, self._do_quit)

    def _do_quit(self):
        self.cleanup()
        self.root.destroy()
        sys.exit(0)

    def cleanup(self):
        try:
            self.hotkeys.stop()
            self.automation.stop()
            self.tray.stop()
            self.worker.stop()
            self.lock.release()
        except Exception as e:
            print(f"[!] Cleanup hatası: {e}")

    # --- UI Tetikleyicileri ---
    def _safe_open_popup(self):
        self.root.after(0, self.popup.toggle)

    def _safe_open_settings(self):
        self.root.after(0, self.open_settings)

    def open_settings(self):
        self.settings_window.show()

    def _on_hotkey_triggered(self, action_name: str, new_val: int, hud_type: str = "brightness", is_muted: bool = False):
        desc = self.worker.active_monitor_name
        self.root.after(0, lambda: self.osd.show(desc, new_val, action_name, hud_type, is_muted))

    def _refresh_all_ui(self):
        desc = self.worker.active_monitor_name
        b = self.worker.get_current_brightness()
        c = self.worker.get_current_contrast()
        v = self.worker.get_current_volume()
        i = self.worker.get_current_input()
        m = self.worker.get_current_mode()
        st = self.worker.status

        self.popup.update_values(b, c, v, i, st, m, desc)
        self.tray.update_icon_and_title(b, st)

    def _on_worker_brightness(self, value: int, from_hardware: bool):
        self.root.after(0, self._refresh_all_ui)

    def _on_worker_contrast(self, value: int, from_hardware: bool):
        self.root.after(0, self._refresh_all_ui)

    def _on_worker_mode(self, mode_name: str, mode_data: dict):
        self.root.after(0, self._refresh_all_ui)

    def _on_worker_volume(self, value: int, is_muted: bool):
        self.root.after(0, self._refresh_all_ui)

    def _on_worker_input(self, input_code: int, input_name: str):
        self.root.after(0, self._refresh_all_ui)

    def _on_worker_status(self, status: MonitorStatus, message: str):
        self.root.after(0, self._refresh_all_ui)

    def _on_worker_notification(self, title: str, message: str, level: str):
        self.root.after(0, lambda: self.notifications.send_notification(title, message, level))

    def _on_settings_saved(self):
        self.hotkeys.reload()
        self.worker.throttle_sec = max(0.05, self.config.throttle_ms / 1000.0)
        self.worker.debounce_sec = max(0.04, self.config.debounce_ms / 1000.0)
        self.worker.watchdog_interval = max(5.0, float(self.config.watchdog_interval_s))
        self.automation.reload()
