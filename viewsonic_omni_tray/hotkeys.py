"""
ViewSonic OMNI DDC/CI Tray - HotkeyService (Global Kısayol Dinleyicisi)
pynput.keyboard.GlobalHotKeys kullanarak sistem genelinde klavye kısayollarını dinler.
Parlaklık, Ses Seviyesi, Giriş Kaynağı ve Uyku modunu yönetir.
"""

import threading
from typing import Dict, Callable, Optional
from pynput import keyboard
from .config import ConfigManager
from .queue_worker import AsyncWriteQueueWorker


class HotkeyService:
    """Sistem genelinde çalışan kısayol tuşlarını yöneten ve OSD HUD ile bağlayan servis."""

    def __init__(
        self,
        config: ConfigManager,
        worker: AsyncWriteQueueWorker,
        on_hotkey_triggered: Optional[Callable[[str, int, str, bool], None]] = None
    ):
        self.config = config
        self.worker = worker
        # on_hotkey_triggered(action_name, value, hud_type, is_muted)
        self.on_hotkey_triggered = on_hotkey_triggered
        self._listener: Optional[keyboard.GlobalHotKeys] = None
        self._lock = threading.Lock()

    def start(self):
        self.reload()

    def stop(self):
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None

    def reload(self):
        """Ayar dosyasından güncel kısayol tuşlarını okur ve dinleyiciyi yeniden kurar."""
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:
                    pass
                self._listener = None

            hotkey_mapping = {}
            hotkeys = self.config.hotkeys
            step = self.config.step_size

            # 1. Parlaklık Artır / Azalt
            if hotkeys.get("brightness_up"):
                hotkey_mapping[hotkeys["brightness_up"]] = lambda: self._on_change_brightness(step, "Parlaklık Artır")

            if hotkeys.get("brightness_down"):
                hotkey_mapping[hotkeys["brightness_down"]] = lambda: self._on_change_brightness(-step, "Parlaklık Azalt")

            # 2. Kontrast Artır / Azalt
            if hotkeys.get("contrast_up"):
                hotkey_mapping[hotkeys["contrast_up"]] = lambda: self._on_change_contrast(step, "Kontrast Artır")

            if hotkeys.get("contrast_down"):
                hotkey_mapping[hotkeys["contrast_down"]] = lambda: self._on_change_contrast(-step, "Kontrast Azalt")

            # 3. Ekran Modları (ViewMode) Döngüsü ve Hızlı Oyun Modu
            if hotkeys.get("cycle_mode"):
                hotkey_mapping[hotkeys["cycle_mode"]] = lambda: self._on_cycle_mode()

            if hotkeys.get("mode_game"):
                hotkey_mapping[hotkeys["mode_game"]] = lambda: self._on_apply_mode("Oyun")

            # 4. Profiller (1..4)
            if hotkeys.get("profile_day"):
                hotkey_mapping[hotkeys["profile_day"]] = lambda: self._on_apply_profile("Gündüz")

            if hotkeys.get("profile_night"):
                hotkey_mapping[hotkeys["profile_night"]] = lambda: self._on_apply_profile("Gece")

            if hotkeys.get("profile_read"):
                hotkey_mapping[hotkeys["profile_read"]] = lambda: self._on_apply_profile("Okuma")

            if hotkeys.get("profile_game"):
                hotkey_mapping[hotkeys["profile_game"]] = lambda: self._on_apply_profile("Oyun")

            # 5. Ses Seviyesi Artır / Azalt / Mute
            if hotkeys.get("volume_up"):
                hotkey_mapping[hotkeys["volume_up"]] = lambda: self._on_change_volume(step, "Ses Artır")

            if hotkeys.get("volume_down"):
                hotkey_mapping[hotkeys["volume_down"]] = lambda: self._on_change_volume(-step, "Ses Azalt")

            if hotkeys.get("volume_mute"):
                hotkey_mapping[hotkeys["volume_mute"]] = lambda: self._on_toggle_mute()

            # 6. Giriş Kaynağı Döngüsü (Cycle Input)
            if hotkeys.get("cycle_input"):
                hotkey_mapping[hotkeys["cycle_input"]] = lambda: self._on_cycle_input()

            # 7. Aktif Ekran / Senkron Geçişi (Cycle Monitor)
            if hotkeys.get("cycle_monitor"):
                hotkey_mapping[hotkeys["cycle_monitor"]] = lambda: self._on_cycle_monitor()

            # 8. Monitörü Uyku Moduna Al
            if hotkeys.get("sleep_monitor"):
                hotkey_mapping[hotkeys["sleep_monitor"]] = lambda: self._on_sleep_monitor()

            # Tek bir geçersiz kısayol (örn. ayarlarda yanlış yazılmış) tüm dinleyicinin
            # çökmesine yol açmasın: her birini ayrı doğrula, hatalıları atla.
            valid_mapping = {}
            for combo, callback in hotkey_mapping.items():
                try:
                    keyboard.HotKey.parse(combo)
                    valid_mapping[combo] = callback
                except Exception as e:
                    print(f"[!] Geçersiz kısayol atlandı '{combo}': {e}")
            hotkey_mapping = valid_mapping

            try:
                self._listener = keyboard.GlobalHotKeys(hotkey_mapping)
                self._listener.daemon = True
                self._listener.start()
            except Exception as e:
                print(f"[!] Kısayol dinleyicisi başlatılamadı: {e}")

    def _on_change_brightness(self, delta: int, action_name: str):
        self.worker.change_brightness_by(delta)
        new_val = self.worker.get_current_brightness()
        if self.on_hotkey_triggered:
            self.on_hotkey_triggered(action_name, new_val, "brightness", False)

    def _on_change_contrast(self, delta: int, action_name: str):
        self.worker.change_contrast_by(delta)
        new_val = self.worker.get_current_contrast()
        if self.on_hotkey_triggered:
            self.on_hotkey_triggered(action_name, new_val, "contrast", False)

    def _on_cycle_mode(self):
        new_mode = self.worker.cycle_display_mode()
        from .monitor_manager import DISPLAY_MODES
        mode_info = DISPLAY_MODES.get(new_mode, {})
        if self.on_hotkey_triggered:
            icon = mode_info.get("icon", "📺")
            name = mode_info.get("name", new_mode)
            br = mode_info.get("brightness", 70)
            cnt = mode_info.get("contrast", 70)
            self.on_hotkey_triggered(f"{icon} {name} (%{br} P / %{cnt} K)", br, "mode", False)

    def _on_apply_mode(self, mode_name: str):
        self.worker.apply_display_mode(mode_name)
        from .monitor_manager import DISPLAY_MODES
        mode_info = DISPLAY_MODES.get(mode_name, {})
        if self.on_hotkey_triggered:
            icon = mode_info.get("icon", "📺")
            name = mode_info.get("name", mode_name)
            br = mode_info.get("brightness", 70)
            cnt = mode_info.get("contrast", 70)
            self.on_hotkey_triggered(f"{icon} {name} (%{br} P / %{cnt} K)", br, "mode", False)

    def _on_apply_profile(self, profile_name: str):
        success = self.worker.apply_profile(profile_name)
        if success:
            val = self.config.profiles.get(profile_name, 50)
            if self.on_hotkey_triggered:
                self.on_hotkey_triggered(f"Profil: {profile_name}", val, "brightness", False)

    def _on_change_volume(self, delta: int, action_name: str):
        self.worker.change_volume_by(delta)
        new_val = self.worker.get_current_volume()
        if self.on_hotkey_triggered:
            self.on_hotkey_triggered(action_name, new_val, "volume", False)

    def _on_toggle_mute(self):
        muted = self.worker.toggle_mute()
        vol = self.worker.get_current_volume()
        if self.on_hotkey_triggered:
            action = "Sessize Alındı" if muted else "Ses Açıldı"
            self.on_hotkey_triggered(action, vol, "volume", muted)

    def _on_cycle_input(self):
        self.worker.cycle_input_source()
        # Giriş değiştiğinde OSD göster
        inp = self.worker.get_current_input()
        from .monitor_manager import INPUT_SOURCES
        name = INPUT_SOURCES.get(inp, "DisplayPort / HDMI")
        if self.on_hotkey_triggered:
            self.on_hotkey_triggered(name, 100, "input", False)

    def _on_cycle_monitor(self):
        active_name = self.worker.cycle_active_monitor()
        if self.on_hotkey_triggered:
            self.on_hotkey_triggered(active_name, 100, "monitor", False)

    def _on_sleep_monitor(self):
        self.worker.sleep_monitor()
        if self.on_hotkey_triggered:
            self.on_hotkey_triggered("Uyku Modu", 0, "sleep", False)


# Geriye dönük takma ad
HotkeyManager = HotkeyService

__all__ = ["HotkeyService", "HotkeyManager"]
