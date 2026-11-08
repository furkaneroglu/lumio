"""
ViewSonic OMNI DDC/CI Tray - AsyncWriteQueueWorker (Rate-Limiting & Asenkron Kuyruk)
Donanım I2C yolunu tıkamamak için kuyruğa alma, debounce, throttling ve periyodik watchdog mimarisi.
Parlaklık, Ses, Giriş Kaynağı ve Güç modunu asenkron olarak yönetir.
"""

import time
import threading
from enum import Enum
from typing import List, Optional, Callable, Dict, Any
from .monitor_manager import MonitorManager, MonitorDevice, INPUT_SOURCES, POWER_MODES, COLOR_PRESETS, DISPLAY_MODES, DDCCIError, DDCCIDisconnectedError
from .config import ConfigManager, ALL_MONITORS_KEY

class MonitorStatus(Enum):
    CONNECTED = "Bağlı"
    SLEEPING = "Uykuda / Beklemede"
    DISCONNECTED = "Bağlantı Yok"
    ERROR = "İletişim Hatası"


class AsyncWriteQueueWorker:
    """
    DDC/CI I2C donanım isteklerini UI iş parçacığından bağımsız yürüten,
    debounce (80ms) ve throttling (100ms) ile donanım koruması sağlayan asenkron kuyruk işçisi.
    """

    def __init__(self, config: ConfigManager, monitor_manager: Optional[MonitorManager] = None):
        self.config = config
        self.manager = monitor_manager or MonitorManager()

        self.devices: List[MonitorDevice] = []
        self.active_device: Optional[MonitorDevice] = None
        self.active_index: int = 0
        # Kullanıcı henüz bir monitör seçmediyse ilk taramada config.monitor_match kullanılır
        self._user_selected: bool = False

        self.status: MonitorStatus = MonitorStatus.DISCONNECTED
        self.status_message: str = "Başlatılıyor..."

        # Parlaklık Durumu
        self._current_brightness: int = 50
        self._target_brightness: int = 50

        # Kontrast Durumu (VCP 0x12)
        self._current_contrast: int = 70
        self._target_contrast: int = 70

        # Renk ve Ekran Modu Durumu (VCP 0x14 ve ViewMode Presets)
        self._current_color_preset: int = 0x05
        self._current_mode: str = "Standart"
        self.supported_modes: List[str] = list(DISPLAY_MODES.keys())

        # Ses Durumu (VCP 0x62) & Mute (0x8D)
        self._current_volume: int = 50
        self._target_volume: int = 50
        self._is_muted: bool = False

        # Giriş Kaynağı Durumu (VCP 0x60)
        self._current_input: int = 0x12 # Varsayılan HDMI 2
        self.supported_inputs: List[int] = [0x0F, 0x11, 0x12] # DP, HDMI 1, HDMI 2

        self._last_write_timestamp: float = 0.0

        # Zamanlama Parametreleri
        self.throttle_sec: float = max(0.05, self.config.throttle_ms / 1000.0)
        self.debounce_sec: float = max(0.04, self.config.debounce_ms / 1000.0)
        self.watchdog_interval: float = max(5.0, float(self.config.watchdog_interval_s))

        # Senkronizasyon
        self._lock = threading.Lock()
        self._request_event = threading.Event()
        self._stop_event = threading.Event()

        # UI ve Bildirim dinleyicileri
        self._brightness_listeners: List[Callable[[int, bool], None]] = []
        self._contrast_listeners: List[Callable[[int, bool], None]] = []
        self._mode_listeners: List[Callable[[str, dict], None]] = []
        self._volume_listeners: List[Callable[[int, bool], None]] = []
        self._input_listeners: List[Callable[[int, str], None]] = []
        self._status_listeners: List[Callable[[MonitorStatus, str], None]] = []
        self._device_listeners: List[Callable[[List[MonitorDevice], Optional[MonitorDevice]], None]] = []
        self._notification_listeners: List[Callable[[str, str, str], None]] = []

        # Arka plan iş parçacıkları
        self._worker_thread = threading.Thread(target=self._hardware_worker_loop, name="DDC-WriteWorker", daemon=True)
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, name="DDC-Watchdog", daemon=True)

    def start(self):
        self.rescan_monitors()
        self._worker_thread.start()
        self._watchdog_thread.start()

    def stop(self):
        self._stop_event.set()
        self._request_event.set()

    # --- Dinleyici Kayıt Metotları ---
    def add_brightness_listener(self, cb: Callable[[int, bool], None]):
        self._brightness_listeners.append(cb)

    def add_contrast_listener(self, cb: Callable[[int, bool], None]):
        self._contrast_listeners.append(cb)

    def add_mode_listener(self, cb: Callable[[str, dict], None]):
        self._mode_listeners.append(cb)

    def add_volume_listener(self, cb: Callable[[int, bool], None]):
        self._volume_listeners.append(cb)

    def add_input_listener(self, cb: Callable[[int, str], None]):
        self._input_listeners.append(cb)

    def add_status_listener(self, cb: Callable[[MonitorStatus, str], None]):
        self._status_listeners.append(cb)

    def add_device_listener(self, cb: Callable[[List[MonitorDevice], Optional[MonitorDevice]], None]):
        self._device_listeners.append(cb)

    def add_notification_listener(self, cb: Callable[[str, str, str], None]):
        self._notification_listeners.append(cb)

    def _notify_brightness(self, value: int, from_hardware: bool = False):
        for cb in list(self._brightness_listeners):
            try:
                cb(value, from_hardware)
            except Exception as e:
                print(f"[!] Brightness callback error: {e}")

    def _notify_contrast(self, value: int, from_hardware: bool = False):
        for cb in list(self._contrast_listeners):
            try:
                cb(value, from_hardware)
            except Exception as e:
                print(f"[!] Contrast callback error: {e}")

    def _notify_mode(self, mode_name: str, mode_data: dict):
        for cb in list(self._mode_listeners):
            try:
                cb(mode_name, mode_data)
            except Exception as e:
                print(f"[!] Mode callback error: {e}")

    def _notify_volume(self, value: int, is_muted: bool = False):
        for cb in list(self._volume_listeners):
            try:
                cb(value, is_muted)
            except Exception as e:
                print(f"[!] Volume callback error: {e}")

    def _notify_input(self, input_code: int, input_name: str):
        for cb in list(self._input_listeners):
            try:
                cb(input_code, input_name)
            except Exception as e:
                print(f"[!] Input callback error: {e}")

    def _notify_status(self, status: MonitorStatus, message: str):
        for cb in list(self._status_listeners):
            try:
                cb(status, message)
            except Exception as e:
                print(f"[!] Status callback error: {e}")

    def _notify_devices(self):
        for cb in list(self._device_listeners):
            try:
                cb(self.devices, self.active_device)
            except Exception as e:
                print(f"[!] Device callback error: {e}")

    def _notify_user(self, title: str, message: str, level: str = "info"):
        for cb in list(self._notification_listeners):
            try:
                cb(title, message, level)
            except Exception as e:
                print(f"[!] Notification callback error: {e}")

    # --- Ekran Eşitleme (Tümü modunda monitör başına parlaklık farkı) ---
    def _offset(self, dev: MonitorDevice) -> int:
        return self.config.get_calibration(dev.description)

    def _device_brightness(self, dev: MonitorDevice, base: int) -> int:
        return max(0, min(100, int(base) + self._offset(dev)))

    def _sync_base_values(self, devs: List[MonitorDevice]):
        """Tümü modunda gösterilecek ortak değerleri (eşitleme farkı çıkarılmış ortalama) hesaplar."""
        n = len(devs)
        b = int(round(sum(d.current_brightness - self._offset(d) for d in devs) / n))
        c = int(round(sum(d.current_contrast for d in devs) / n))
        v = int(round(sum(d.current_volume for d in devs) / n))
        return max(0, min(100, b)), c, v

    # --- Monitör Tarama & Seçim ---
    @property
    def active_monitor_name(self) -> str:
        if self.active_index == -1:
            return "Tüm Ekranlar (Senkron)"
        if self.active_device:
            return self.active_device.description
        return "Monitör"

    def rescan_monitors(self) -> bool:
        try:
            devs = self.manager.enumerate_monitors()
        except Exception as e:
            with self._lock:
                self.status = MonitorStatus.ERROR
                self.status_message = f"Tarama hatası: {e}"
                status = self.status
                msg = self.status_message
            self._notify_status(status, msg)
            return False

        remembered_modes = self.config.last_state.get("modes", {})
        remembered_monitor = self.config.last_state.get("monitor", "")
        for d in devs:
            mode = remembered_modes.get(d.description)
            if mode in DISPLAY_MODES:
                d.current_mode = mode

        with self._lock:
            self.devices = devs

            # İlk taramada en son seçilen monitörü / Tümü modunu geri yükle
            if devs and not self._user_selected and remembered_monitor == ALL_MONITORS_KEY and len(devs) > 1:
                self.active_index = -1
                self._user_selected = True

            if not devs:
                old_status = self.status
                self.active_device = None
                self.active_index = 0
                self.status = MonitorStatus.DISCONNECTED
                self.status_message = "Hiçbir DDC/CI monitör algılanamadı."
                status = self.status
                msg = self.status_message
                should_notify_disconnected = (old_status == MonitorStatus.CONNECTED)
            else:
                prev_status = self.status

                sync_values = None
                if self.active_index == -1:
                    # Senkron / Tüm ekranlar modunu koru
                    self.active_device = None
                    target = devs[0]
                    sync_values = self._sync_base_values(devs)
                    self.status_message = "Tüm Ekranlar (Senkron Kontrol) bağlandı."
                else:
                    target = None
                    if self._user_selected:
                        for d in devs:
                            if d.index == self.active_index:
                                target = d
                                break

                    if not target and remembered_monitor:
                        for d in devs:
                            if d.description == remembered_monitor:
                                target = d
                                break

                    if not target:
                        match_keyword = self.config.monitor_match.lower()
                        for d in devs:
                            if match_keyword in d.description.lower():
                                target = d
                                break

                    if not target:
                        target = devs[0]

                    self.active_device = target
                    self.active_index = target.index
                    self._user_selected = True
                    self.status_message = f"{target.description} bağlandı."

                self._current_brightness = target.current_brightness
                self._target_brightness = target.current_brightness
                self._current_contrast = target.current_contrast
                self._target_contrast = target.current_contrast
                self._current_color_preset = target.current_color_preset
                self._current_mode = target.current_mode
                self._current_volume = target.current_volume
                self._target_volume = target.current_volume
                self._current_input = target.current_input
                if sync_values:
                    b, c, v = sync_values
                    self._current_brightness = self._target_brightness = b
                    self._current_contrast = self._target_contrast = c
                    self._current_volume = self._target_volume = v
                self.status = MonitorStatus.CONNECTED
                status = self.status
                msg = self.status_message
                should_notify_connected = prev_status in (MonitorStatus.SLEEPING, MonitorStatus.DISCONNECTED, MonitorStatus.ERROR)

        if not devs:
            self._notify_status(status, msg)
            self._notify_devices()
            if should_notify_disconnected:
                self._notify_user(
                    "Monitör Bağlantısı Kesildi",
                    "DDC/CI uyumlu monitör bulunamadı.",
                    level="warning"
                )
            return False

        self._notify_brightness(self._current_brightness, from_hardware=True)
        self._notify_contrast(self._current_contrast, from_hardware=True)
        self._notify_mode(self._current_mode, DISPLAY_MODES.get(self._current_mode, {}))
        self._notify_volume(self._current_volume, self._is_muted)
        inp_name = target.input_name if (self.active_device and target) else "HDMI / DP"
        self._notify_input(self._current_input, inp_name)
        self._notify_status(status, msg)
        self._notify_devices()

        return True

    def select_device(self, index: int):
        selected_desc = ""
        remember_key = ""
        with self._lock:
            if index == -1:
                self._user_selected = True
                self.active_index = -1
                self.active_device = None
                if self.devices:
                    avg_b, avg_c, avg_v = self._sync_base_values(self.devices)
                    self._current_brightness = avg_b
                    self._target_brightness = avg_b
                    self._current_contrast = avg_c
                    self._target_contrast = avg_c
                    self._current_volume = avg_v
                    self._target_volume = avg_v
                    self._current_mode = self.devices[0].current_mode
                    self._current_input = self.devices[0].current_input
                self.status = MonitorStatus.CONNECTED
                self.status_message = "Tüm Ekranlar (Senkron Kontrol) seçildi."
                selected_desc = "Tüm Ekranlar (Senkron)"
                remember_key = ALL_MONITORS_KEY
            else:
                for d in self.devices:
                    if d.index == index:
                        self._user_selected = True
                        self.active_device = d
                        self.active_index = index
                        self._current_brightness = d.current_brightness
                        self._target_brightness = d.current_brightness
                        self._current_contrast = d.current_contrast
                        self._target_contrast = d.current_contrast
                        self._current_color_preset = d.current_color_preset
                        self._current_mode = d.current_mode
                        self._current_volume = d.current_volume
                        self._target_volume = d.current_volume
                        self._current_input = d.current_input
                        self.status = MonitorStatus.CONNECTED
                        self.status_message = f"{d.description} seçildi."
                        selected_desc = d.description
                        remember_key = d.description
                        break

        if remember_key:
            self.config.remember_monitor(remember_key)

        if selected_desc:
            input_name = self.active_device.input_name if self.active_device else "HDMI / DP"
            self._notify_brightness(self._current_brightness, from_hardware=True)
            self._notify_contrast(self._current_contrast, from_hardware=True)
            self._notify_mode(self._current_mode, DISPLAY_MODES.get(self._current_mode, {}))
            self._notify_volume(self._current_volume, self._is_muted)
            self._notify_input(self._current_input, input_name)
            self._notify_status(self.status, self.status_message)
            self._notify_devices()

    def cycle_active_monitor(self) -> str:
        """Monitörler ve Senkron mod arasında döner: Monitor 0 -> Monitor 1 -> ... -> Tüm Ekranlar (-1) -> Monitor 0."""
        with self._lock:
            if not self.devices:
                return "Monitör Yok"
            if len(self.devices) == 1:
                return self.devices[0].description

            options = [d.index for d in self.devices] + [-1]
            try:
                cur_pos = options.index(self.active_index)
                next_index = options[(cur_pos + 1) % len(options)]
            except ValueError:
                next_index = options[0]

        self.select_device(next_index)
        return self.active_monitor_name

    # --- Parlaklık Yönetimi ---
    def get_current_brightness(self) -> int:
        with self._lock:
            return self._target_brightness

    def set_brightness(self, value: int, immediate_ui: bool = True):
        clamped = max(0, min(100, int(value)))
        with self._lock:
            self._target_brightness = clamped
            self._request_event.set()
        # Dinleyiciler kilit DIŞINDA çağrılmalı: root.after() başka thread'den çağrıldığında
        # Tk ana thread'ini bekler; ana thread de kilidi bekliyorsa kilitlenme (donma) oluşur.
        if immediate_ui:
            self._notify_brightness(clamped, from_hardware=False)

    def change_brightness_by(self, delta: int):
        with self._lock:
            new_val = self._target_brightness + delta
        self.set_brightness(new_val, immediate_ui=True)

    def apply_profile(self, profile_name: str) -> bool:
        profiles = self.config.profiles
        if profile_name in profiles:
            val = profiles[profile_name]
            self.set_brightness(val, immediate_ui=True)
            return True
        return False

    # --- Ekran Modları (ViewMode Presets) ---
    def get_current_mode(self) -> str:
        with self._lock:
            return self._current_mode

    def apply_display_mode(self, mode_name: str, brightness: Optional[int] = None,
                           contrast: Optional[int] = None, wait_timeout: float = 0.0) -> bool:
        """
        Ekran modunu (Oyun, Film, Metin, Standart, Gece) uygular.
        Monitörün Parlaklık, Kontrast ve Renk Sıcaklığı donanım ayarlarını anında koordine eder.
        brightness / contrast verilirse modun varsayılanları yerine kullanılır (önceki duruma dönüş).
        """
        if mode_name not in DISPLAY_MODES:
            return False

        mode_data = DISPLAY_MODES[mode_name]
        with self._lock:
            self._current_mode = mode_name
            target_idx = self.active_index
            target_b = max(0, min(100, int(brightness))) if brightness is not None else mode_data["brightness"]
            target_c = max(0, min(100, int(contrast))) if contrast is not None else mode_data["contrast"]
            target_cp = mode_data["color_preset"]
            target_dev = self.active_device

            self._target_brightness = target_b
            self._current_brightness = target_b
            self._target_contrast = target_c
            self._current_contrast = target_c
            self._current_color_preset = target_cp

        self._notify_brightness(target_b, from_hardware=False)
        self._notify_contrast(target_c, from_hardware=False)
        self._notify_mode(mode_name, mode_data)

        def _bg_apply():
            try:
                if target_idx == -1:
                    for d in list(self.devices):
                        try:
                            dev_b = self._device_brightness(d, target_b)
                            self.manager.apply_display_mode(d.index, mode_name, brightness=dev_b, contrast=target_c)
                            d.current_mode = mode_name
                            d.current_brightness = dev_b
                            d.current_contrast = target_c
                        except Exception as de:
                            print(f"[!] Display mode error on #{d.index}: {de}")
                else:
                    self.manager.apply_display_mode(target_idx, mode_name, brightness=target_b, contrast=target_c)
                    if target_dev:
                        target_dev.current_mode = mode_name
                        target_dev.current_brightness = target_b
                        target_dev.current_contrast = target_c
            except Exception as e:
                print(f"[!] Ekran modu uygulama hatası: {e}")

        apply_thread = threading.Thread(target=_bg_apply, daemon=True, name="DDC-ApplyMode")
        apply_thread.start()
        if wait_timeout > 0:
            apply_thread.join(wait_timeout)

        if target_idx == -1:
            names = [d.description for d in self.devices]
        else:
            names = [target_dev.description] if target_dev else []
        if names:
            self.config.remember_mode(names, mode_name)
        return True

    def cycle_display_mode(self) -> str:
        """Ekran modları arasında sırayla döner (Standart -> Oyun -> Film -> Metin -> Gece -> Standart)."""
        modes = list(DISPLAY_MODES.keys())
        with self._lock:
            cur = self._current_mode
            try:
                idx = modes.index(cur)
                next_mode = modes[(idx + 1) % len(modes)]
            except ValueError:
                next_mode = modes[0]
        self.apply_display_mode(next_mode)
        return next_mode

    # --- Kontrast Yönetimi (VCP 0x12) ---
    def get_current_contrast(self) -> int:
        with self._lock:
            return self._target_contrast

    def set_contrast(self, value: int, immediate_ui: bool = True):
        clamped = max(0, min(100, int(value)))
        with self._lock:
            self._target_contrast = clamped
            self._request_event.set()
        # Dinleyiciler kilit DIŞINDA çağrılmalı: root.after() başka thread'den çağrıldığında
        # Tk ana thread'ini bekler; ana thread de kilidi bekliyorsa kilitlenme (donma) oluşur.
        if immediate_ui:
            self._notify_contrast(clamped, from_hardware=False)

    def change_contrast_by(self, delta: int):
        with self._lock:
            new_val = self._target_contrast + delta
        self.set_contrast(new_val, immediate_ui=True)

    # --- Ses Yönetimi (VCP 0x62) ---
    def get_current_volume(self) -> int:
        with self._lock:
            return self._target_volume

    def set_volume(self, value: int, immediate_ui: bool = True):
        clamped = max(0, min(100, int(value)))
        with self._lock:
            self._target_volume = clamped
            self._request_event.set()
        # Dinleyiciler kilit DIŞINDA çağrılmalı: root.after() başka thread'den çağrıldığında
        # Tk ana thread'ini bekler; ana thread de kilidi bekliyorsa kilitlenme (donma) oluşur.
        if immediate_ui:
            self._notify_volume(clamped, self._is_muted)

    def change_volume_by(self, delta: int):
        with self._lock:
            new_val = self._target_volume + delta
        self.set_volume(new_val, immediate_ui=True)

    @property
    def is_muted(self) -> bool:
        with self._lock:
            return self._is_muted

    def toggle_mute(self) -> bool:
        with self._lock:
            self._is_muted = not self._is_muted
            muted = self._is_muted
            target_idx = self.active_index
            vol = self._target_volume
        self._notify_volume(vol, muted)

        def _bg_mute():
            try:
                if target_idx == -1:
                    for d in list(self.devices):
                        try:
                            self.manager.set_mute(d.index, muted)
                        except Exception as de:
                            print(f"[!] Mute hatası #{d.index}: {de}")
                else:
                    self.manager.set_mute(target_idx, muted)
            except Exception as e:
                print(f"[!] Mute hatası: {e}")

        threading.Thread(target=_bg_mute, daemon=True, name="DDC-Mute").start()
        return muted

    # --- Giriş Kaynağı Yönetimi (VCP 0x60) ---
    def get_current_input(self) -> int:
        with self._lock:
            return self._current_input

    def set_input_source(self, source_code: int):
        """Giriş kaynağını (DisplayPort 0x0F, HDMI 1 0x11, HDMI 2 0x12) ayarlar."""
        with self._lock:
            target_idx = self.active_index
            target_dev = self.active_device
            self._current_input = source_code
        input_name = INPUT_SOURCES.get(source_code, f"0x{source_code:02X}")
        self._notify_input(source_code, input_name)

        def _bg_input():
            try:
                if target_idx == -1:
                    for d in list(self.devices):
                        try:
                            self.manager.set_input_source(d.index, source_code)
                            d.current_input = source_code
                        except Exception as de:
                            print(f"[!] Giriş değiştirme hatası #{d.index}: {de}")
                else:
                    self.manager.set_input_source(target_idx, source_code)
                    if target_dev:
                        target_dev.current_input = source_code
            except Exception as e:
                print(f"[!] Giriş değiştirme hatası: {e}")

        threading.Thread(target=_bg_input, daemon=True, name="DDC-Input").start()

    def cycle_input_source(self):
        """Giriş kaynakları arasında sırayla döner (DP -> HDMI 1 -> HDMI 2 -> DP)."""
        with self._lock:
            cur = self._current_input
            try:
                idx = self.supported_inputs.index(cur)
                next_input = self.supported_inputs[(idx + 1) % len(self.supported_inputs)]
            except ValueError:
                next_input = self.supported_inputs[0]
        self.set_input_source(next_input)

    # --- Güç / Uyku Yönetimi (VCP 0xD6) ---
    def sleep_monitor(self):
        """Monitörü bekleme (Standby 0x04) moduna alır."""
        with self._lock:
            target_idx = self.active_index
            self.status = MonitorStatus.SLEEPING
            self.status_message = "Monitör uyku modunda."
        self._notify_status(self.status, self.status_message)

        def _bg_sleep():
            try:
                if target_idx == -1:
                    for d in list(self.devices):
                        try:
                            self.manager.set_power_mode(d.index, 0x04)
                        except Exception as de:
                            print(f"[!] Uykuya alma hatası #{d.index}: {de}")
                else:
                    self.manager.set_power_mode(target_idx, 0x04)
            except Exception as e:
                print(f"[!] Uykuya alma hatası: {e}")

        threading.Thread(target=_bg_sleep, daemon=True, name="DDC-Sleep").start()

    def wake_monitor(self):
        """Monitörü uyandırır (On 0x01)."""
        with self._lock:
            target_idx = self.active_index

        def _bg_wake():
            try:
                if target_idx == -1:
                    for d in list(self.devices):
                        try:
                            self.manager.set_power_mode(d.index, 0x01)
                        except Exception as de:
                            print(f"[!] Uyandırma hatası #{d.index}: {de}")
                else:
                    self.manager.set_power_mode(target_idx, 0x01)
                self.rescan_monitors()
            except Exception as e:
                print(f"[!] Uyandırma hatası: {e}")

        threading.Thread(target=_bg_wake, daemon=True, name="DDC-Wake").start()

    # --- Asenkron Donanım Kuyruk Döngüsü (Parlaklık & Ses) ---
    def _hardware_worker_loop(self):
        while not self._stop_event.is_set():
            self._request_event.wait(timeout=0.2)
            if self._stop_event.is_set():
                break

            with self._lock:
                target_b = self._target_brightness
                cur_b = self._current_brightness
                target_c = self._target_contrast
                cur_c = self._current_contrast
                target_v = self._target_volume
                cur_v = self._current_volume
                target_dev_idx = self.active_index
                has_dev = (self.active_device is not None) or (self.active_index == -1 and bool(self.devices))

            need_b = target_b != cur_b
            need_c = target_c != cur_c
            need_v = target_v != cur_v

            if not has_dev or (not need_b and not need_c and not need_v):
                self._request_event.clear()
                continue

            # 1. Debounce gecikmesi
            time.sleep(self.debounce_sec)
            with self._lock:
                target_b = self._target_brightness
                cur_b = self._current_brightness
                target_c = self._target_contrast
                cur_c = self._current_contrast
                target_v = self._target_volume
                cur_v = self._current_volume

            need_b = target_b != cur_b
            need_c = target_c != cur_c
            need_v = target_v != cur_v

            if not need_b and not need_c and not need_v:
                self._request_event.clear()
                continue

            # 2. Throttling kontrolü
            elapsed = time.time() - self._last_write_timestamp
            if elapsed < self.throttle_sec:
                time.sleep(self.throttle_sec - elapsed)

            # 3. Donanıma yazma
            try:
                if target_dev_idx == -1:
                    # Senkron Mod: Tüm monitörlere yaz
                    for dev in list(self.devices):
                        try:
                            if need_b:
                                vb = self._device_brightness(dev, target_b)
                                self.manager.set_brightness(dev.index, vb)
                                dev.current_brightness = vb
                            if need_c:
                                vc = self.manager.set_contrast(dev.index, target_c)
                                dev.current_contrast = vc
                            if need_v:
                                vv = self.manager.set_volume(dev.index, target_v)
                                dev.current_volume = vv
                        except Exception as de:
                            print(f"[!] Senkron donanım yazma hatası (#{dev.index}): {de}")

                    with self._lock:
                        if need_b:
                            self._current_brightness = target_b
                        if need_c:
                            self._current_contrast = target_c
                        if need_v:
                            self._current_volume = target_v
                    if need_b:
                        self._notify_brightness(target_b, from_hardware=True)
                    if need_c:
                        self._notify_contrast(target_c, from_hardware=True)
                    if need_v:
                        self._notify_volume(target_v, self._is_muted)
                else:
                    # Tekil Monitör Modu
                    with self._lock:
                        dev = self.active_device
                    if need_b:
                        self.manager.set_brightness(target_dev_idx, target_b)
                        with self._lock:
                            self._current_brightness = target_b
                            if dev:
                                dev.current_brightness = target_b
                        self._notify_brightness(target_b, from_hardware=True)

                    if need_c:
                        self.manager.set_contrast(target_dev_idx, target_c)
                        with self._lock:
                            self._current_contrast = target_c
                            if dev:
                                dev.current_contrast = target_c
                        self._notify_contrast(target_c, from_hardware=True)

                    if need_v:
                        self.manager.set_volume(target_dev_idx, target_v)
                        with self._lock:
                            self._current_volume = target_v
                            if dev:
                                dev.current_volume = target_v
                        self._notify_volume(target_v, self._is_muted)

                self._last_write_timestamp = time.time()

                reconnected = False
                with self._lock:
                    if self.status != MonitorStatus.CONNECTED:
                        self.status = MonitorStatus.CONNECTED
                        self.status_message = "Bağlantı aktif."
                        reconnected = True
                if reconnected:
                    self._notify_status(MonitorStatus.CONNECTED, "Bağlantı aktif.")

            except DDCCIDisconnectedError:
                self._drop_pending_writes(target_b, target_c, target_v)
                with self._lock:
                    self.status = MonitorStatus.SLEEPING
                    self.status_message = "Monitör uyku modunda veya bağlantı kesildi."
                self._notify_status(self.status, self.status_message)
                time.sleep(0.5)

            except DDCCIError as e:
                self._drop_pending_writes(target_b, target_c, target_v)
                with self._lock:
                    self.status = MonitorStatus.ERROR
                    self.status_message = f"DDC/CI Hatası: {e}"
                self._notify_status(self.status, self.status_message)
                time.sleep(0.5)

            except Exception as e:
                self._drop_pending_writes(target_b, target_c, target_v)
                with self._lock:
                    self.status = MonitorStatus.ERROR
                    self.status_message = f"Bilinmeyen hata: {e}"
                self._notify_status(self.status, self.status_message)
                time.sleep(0.5)

            finally:
                with self._lock:
                    if (self._target_brightness == self._current_brightness and 
                        self._target_contrast == self._current_contrast and
                        self._target_volume == self._current_volume):
                        self._request_event.clear()

    def _drop_pending_writes(self, target_b: int, target_c: int, target_v: int):
        """
        Başarısız yazma isteğini terk eder. Aksi halde hedef != mevcut kaldığı için döngü
        aynı (desteklenmeyen / ulaşılamayan) komutu her 0.5 sn'de sonsuza dek tekrar dener.
        Watchdog yeniden taramada gerçek donanım değerlerini geri yükler.
        Kullanıcı bu arada yeni bir değer istediyse o istek korunur.
        """
        with self._lock:
            if self._target_brightness == target_b:
                self._current_brightness = target_b
            if self._target_contrast == target_c:
                self._current_contrast = target_c
            if self._target_volume == target_v:
                self._current_volume = target_v

    # --- Watchdog & Yeniden Bağlantı Döngüsü ---
    def _watchdog_loop(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.watchdog_interval):
                break

            if self.status in (MonitorStatus.SLEEPING, MonitorStatus.DISCONNECTED, MonitorStatus.ERROR):
                success = self.rescan_monitors()
                if success:
                    self._request_event.set()
            elif self.status == MonitorStatus.CONNECTED and self.active_index >= 0 and self.active_device is not None:
                idle_time = time.time() - self._last_write_timestamp
                if idle_time > self.watchdog_interval:
                    try:
                        hw_val = self.manager.get_brightness(self.active_index)
                        notify = False
                        with self._lock:
                            if hw_val != self._current_brightness and not self._request_event.is_set():
                                self._current_brightness = hw_val
                                self._target_brightness = hw_val
                                if self.active_device:
                                    self.active_device.current_brightness = hw_val
                                notify = True
                        if notify:
                            self._notify_brightness(hw_val, from_hardware=True)
                    except DDCCIDisconnectedError:
                        with self._lock:
                            self.status = MonitorStatus.SLEEPING
                            self.status_message = "Monitör uykuya geçti."
                            status = self.status
                            msg = self.status_message
                        self._notify_status(status, msg)
                    except Exception:
                        pass
