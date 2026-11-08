"""
ViewSonic OMNI DDC/CI Tray - ConfigManager (Profil ve Ayar Yönetimi)
Kullanıcı tercihlerini JSON formatında saklar ve Windows başlangıç kaydını yönetir.
"""

import os
import sys
import json
import copy
import winreg
from typing import Dict, Any

APP_NAME = "ViewSonicOmniTray"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULT_CONFIG: Dict[str, Any] = {
    "monitor_match": "ViewSonic",
    "selected_index": 0,
    "step_size": 5,
    "profiles": {
        "Gündüz": 80,
        "Gece": 20,
        "Okuma": 40,
        "Oyun": 90,
        "Sinema": 30
    },
    "hotkeys": {
        "brightness_up": "<ctrl>+<alt>+<up>",
        "brightness_down": "<ctrl>+<alt>+<down>",
        "contrast_up": "<ctrl>+<alt>+<right>",
        "contrast_down": "<ctrl>+<alt>+<left>",
        "cycle_mode": "<ctrl>+<alt>+v",
        "mode_game": "<ctrl>+<alt>+g",
        "profile_day": "<ctrl>+<alt>+1",
        "profile_night": "<ctrl>+<alt>+2",
        "profile_read": "<ctrl>+<alt>+3",
        "profile_game": "<ctrl>+<alt>+4",
        "volume_up": "<ctrl>+<alt>+<page_up>",
        "volume_down": "<ctrl>+<alt>+<page_down>",
        "volume_mute": "<ctrl>+<alt>+m",
        "cycle_input": "<ctrl>+<alt>+i",
        "cycle_monitor": "<ctrl>+<alt>+s",
        "sleep_monitor": "<ctrl>+<alt>+p"
    },
    "hardware": {
        "throttle_ms": 100,      # Donanım I2C yolunu korumak için iki yazma arası min ms
        "debounce_ms": 80,       # Hızlı slider hareketlerinde ara değerleri filtreleme süresi
        "watchdog_interval_s": 15 # Uyku/kopma sonrası yeniden doğrulama periyodu (sn)
    },
    "ui": {
        "show_osd": True,        # Kısayol kullanıldığında ekranda mini HUD göster
        "osd_duration_ms": 1400, # HUD ekranda kalma süresi
        "show_notifications": False # Windows bildirimlerini göster (varsayılan: kapalı)
    },
    "run_on_startup": False
}


def get_config_dir() -> str:
    """Kullanıcı AppData klasöründe uygulama ayar dizinini döner."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        cfg_dir = os.path.join(appdata, APP_NAME)
    else:
        cfg_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(cfg_dir, exist_ok=True)
    return cfg_dir


def get_config_path() -> str:
    return os.path.join(get_config_dir(), "config.json")


class ConfigManager:
    """Uygulama ayarlarını yükleyen, saklayan ve güncelleyen yönetici sınıf."""

    def __init__(self):
        self.path = get_config_path()
        self.data: Dict[str, Any] = {}
        self.load()

    def load(self):
        """Diskteki yapılandırmayı okur, eksik anahtarları varsayılanlarla tamamlar."""
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self.data = self._merge_defaults(loaded, DEFAULT_CONFIG)
            except Exception as e:
                print(f"[!] Ayar dosyası okunamadı, varsayılanlar yükleniyor: {e}")
                self.data = copy.deepcopy(DEFAULT_CONFIG)
                self.save()
        else:
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self.save()

    def _merge_defaults(self, loaded: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
        result = copy.deepcopy(defaults)
        for k, v in loaded.items():
            if isinstance(v, dict) and k in result and isinstance(result[k], dict):
                result[k] = self._merge_defaults(v, result[k])
            else:
                result[k] = v
        return result

    def save(self):
        """Yapılandırmayı JSON dosyasına yazar."""
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[!] Ayar kaydedilemedi: {e}")

    # Kolay erişim özellikleri
    @property
    def monitor_match(self) -> str:
        return self.data.get("monitor_match", "ViewSonic")

    @monitor_match.setter
    def monitor_match(self, val: str):
        self.data["monitor_match"] = val

    @property
    def profiles(self) -> Dict[str, int]:
        return self.data.get("profiles", DEFAULT_CONFIG["profiles"])

    @property
    def hotkeys(self) -> Dict[str, str]:
        return self.data.get("hotkeys", DEFAULT_CONFIG["hotkeys"])

    @property
    def step_size(self) -> int:
        return self.data.get("step_size", 5)

    @step_size.setter
    def step_size(self, val: int):
        self.data["step_size"] = max(1, min(25, int(val)))

    @property
    def throttle_ms(self) -> int:
        return self.data.get("hardware", {}).get("throttle_ms", 100)

    @property
    def debounce_ms(self) -> int:
        return self.data.get("hardware", {}).get("debounce_ms", 80)

    @property
    def watchdog_interval_s(self) -> int:
        return self.data.get("hardware", {}).get("watchdog_interval_s", 15)

    @property
    def show_osd(self) -> bool:
        return self.data.get("ui", {}).get("show_osd", True)

    @show_osd.setter
    def show_osd(self, val: bool):
        self.data.setdefault("ui", {})["show_osd"] = bool(val)

    @property
    def show_notifications(self) -> bool:
        return self.data.get("ui", {}).get("show_notifications", False)

    @show_notifications.setter
    def show_notifications(self, val: bool):
        self.data.setdefault("ui", {})["show_notifications"] = bool(val)

    @property
    def run_on_startup(self) -> bool:
        return self.data.get("run_on_startup", False)

    @run_on_startup.setter
    def run_on_startup(self, val: bool):
        self.data["run_on_startup"] = bool(val)
        self.apply_startup_registry(self.data["run_on_startup"])

    def apply_startup_registry(self, enable: bool):
        """Windows başlangıç kaydını günceller (EXE veya pythonw.exe ile arka planda sessiz çalışacak şekilde)."""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                if enable:
                    if getattr(sys, "frozen", False):
                        # PyInstaller EXE olarak paketlendiyse doğrudan .exe yolunu kaydet
                        cmd = f'"{sys.executable}"'
                    else:
                        py_dir = os.path.dirname(sys.executable)
                        pyw_exe = os.path.join(py_dir, "pythonw.exe")
                        if not os.path.exists(pyw_exe):
                            pyw_exe = sys.executable

                        main_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
                        cmd = f'"{pyw_exe}" "{main_script}"'
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as e:
            print(f"[!] Windows başlangıç kaydı güncellenemedi: {e}")


# Geriye dönük uyumluluk takma adı
AppConfig = ConfigManager

__all__ = [
    "ConfigManager",
    "AppConfig",
    "DEFAULT_CONFIG",
    "get_config_path",
    "get_config_dir"
]
