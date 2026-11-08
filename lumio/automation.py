"""
Lumio - Otomasyon Servisleri

1. ScheduleEngine   : Günün saatine göre otomatik parlaklık (yumuşak geçişli).
                      Kullanıcı parlaklığı elle değiştirirse bir sonraki plan saatine kadar karışmaz.
2. AppRuleEngine    : Ön plandaki uygulamaya göre otomatik ekran modu (örn. vlc.exe -> Film).
                      Uygulamadan çıkınca önceki mod / parlaklık / kontrast geri yüklenir.
"""

import os
import sys
import ctypes
import threading
from ctypes import wintypes
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .config import ConfigManager
from .monitor_manager import DISPLAY_MODES
from .queue_worker import AsyncWriteQueueWorker


# --- Saat Planı Hesaplama (saf fonksiyonlar, test edilebilir) ---

def parse_time(text: str) -> Optional[int]:
    """'HH:MM' metnini gün içi dakikaya çevirir; geçersizse None döner."""
    try:
        hh, mm = str(text).strip().split(":")
        h, m = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    return None


def normalize_points(points: List[Dict]) -> List[Tuple[int, int]]:
    """Plan noktalarını (dakika, parlaklık) çiftlerine çevirir, geçersizleri atar ve saate göre sıralar."""
    result: Dict[int, int] = {}
    for p in points or []:
        minute = parse_time(p.get("time", "")) if isinstance(p, dict) else None
        if minute is None:
            continue
        try:
            value = max(0, min(100, int(p.get("brightness"))))
        except (TypeError, ValueError):
            continue
        result[minute] = value
    return sorted(result.items())


def scheduled_brightness(points: List[Tuple[int, int]], minute_of_day: float, transition_min: int) -> int:
    """
    Verilen andaki plan parlaklığını hesaplar. Her nokta, kendi saatinden itibaren geçerlidir;
    geçiş süresi boyunca bir önceki noktanın değerinden doğrusal olarak yaklaşılır.
    """
    if not points:
        raise ValueError("Plan noktası yok")
    if len(points) == 1:
        return points[0][1]

    # Şu an etkin olan nokta: saati <= şimdi olan son nokta (yoksa dünden kalan son nokta)
    idx = len(points) - 1
    for i, (minute, _) in enumerate(points):
        if minute <= minute_of_day:
            idx = i
    cur_minute, cur_value = points[idx]
    prev_value = points[idx - 1][1]  # idx = 0 ise -1 → döngüsel olarak son nokta

    elapsed = (minute_of_day - cur_minute) % 1440
    if transition_min > 0 and elapsed < transition_min:
        ratio = elapsed / transition_min
        return int(round(prev_value + (cur_value - prev_value) * ratio))
    return cur_value


def next_point_after(points: List[Tuple[int, int]], now: datetime) -> datetime:
    """Şimdiden sonraki ilk plan noktasının zamanını döner."""
    minute_now = now.hour * 60 + now.minute + now.second / 60.0
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for minute, _ in points:
        if minute > minute_now:
            return midnight + timedelta(minutes=minute)
    return midnight + timedelta(days=1, minutes=points[0][0])


class ScheduleEngine:
    """Saate göre otomatik parlaklık motoru."""

    TICK_SEC = 20.0

    def __init__(self, config: ConfigManager, worker: AsyncWriteQueueWorker):
        self.config = config
        self.worker = worker
        self._lock = threading.Lock()
        self._last_applied: Optional[int] = None
        self._override_until: Optional[datetime] = None
        self._paused = False

    @property
    def enabled(self) -> bool:
        return bool(self.config.schedule.get("enabled")) and bool(normalize_points(self.config.schedule.get("points")))

    @property
    def is_overridden(self) -> bool:
        return self._override_until is not None

    def reset(self):
        """Ayarlar değiştiğinde veya özellik açılıp kapandığında durumu sıfırlar."""
        with self._lock:
            self._last_applied = None
            self._override_until = None

    def set_paused(self, paused: bool):
        """Uygulama kuralı etkin olduğunda plan duraklatılır (kural parlaklığı yönetir)."""
        with self._lock:
            self._paused = paused
            if not paused:
                # Dönüşte mevcut değeri "uygulanmış" say; böylece elle değişiklik sanılmaz
                self._last_applied = self.worker.get_current_brightness()

    def tick(self, now: Optional[datetime] = None):
        now = now or datetime.now()
        sched = self.config.schedule
        points = normalize_points(sched.get("points"))
        with self._lock:
            if not sched.get("enabled") or not points or self._paused:
                self._last_applied = None
                self._override_until = None
                return

            try:
                transition = max(0, int(sched.get("transition_min", 30)))
            except (TypeError, ValueError):
                transition = 30
            minute_now = now.hour * 60 + now.minute + now.second / 60.0
            value = scheduled_brightness(points, minute_now, transition)

            current = self.worker.get_current_brightness()
            if self._override_until is None and self._last_applied is not None and current != self._last_applied:
                # Kullanıcı parlaklığı elle değiştirdi: bir sonraki plan saatine kadar karışma
                self._override_until = next_point_after(points, now)

            if self._override_until is not None:
                if now < self._override_until:
                    return
                self._override_until = None

            if value == self._last_applied and current == value:
                return
            self._last_applied = value

        if current != value:
            self.worker.set_brightness(value, immediate_ui=True)


# --- Ön Plan Uygulaması Tespiti ---

class _ForegroundProbe:
    """Ön plandaki pencerenin süreç adını (örn. 'vlc.exe') bulur."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self):
        self.available = sys.platform == "win32"
        if not self.available:
            return
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.user32.GetForegroundWindow.argtypes = []
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def foreground(self) -> Tuple[Optional[int], Optional[str]]:
        if not self.available:
            return None, None
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None, None
        return self.process_of(hwnd)

    def process_of(self, hwnd) -> Tuple[Optional[int], Optional[str]]:
        """Pencerenin ait olduğu sürecin kimliğini ve dosya adını döner."""
        if not self.available or not hwnd:
            return None, None
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None, None
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return pid.value, None
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return pid.value, os.path.basename(buf.value).lower()
        finally:
            self.kernel32.CloseHandle(handle)
        return pid.value, None


_IGNORED_PROCESSES = {
    "explorer.exe", "applicationframehost.exe", "shellexperiencehost.exe", "searchhost.exe",
    "startmenuexperiencehost.exe", "textinputhost.exe", "systemsettings.exe", "lockapp.exe",
}


def list_window_processes() -> List[Tuple[str, str]]:
    """
    Şu an görünür penceresi olan uygulamaları (süreç adı, pencere başlığı) olarak listeler.
    Ayarlar ekranında kural eklerken 'Açık uygulamadan seç' listesi için kullanılır.
    """
    if sys.platform != "win32":
        return []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    probe = _ForegroundProbe()
    own_pid = os.getpid()
    found: Dict[str, str] = {}

    def _callback(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER: sahipli pencereleri atla
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid, proc = probe.process_of(hwnd)
            if proc and pid != own_pid and proc not in _IGNORED_PROCESSES and proc not in found:
                found[proc] = buf.value
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(_callback), 0)
    return sorted(found.items(), key=lambda item: item[0])


def normalize_process_name(name: str) -> str:
    name = os.path.basename(str(name).strip().strip('"')).lower()
    if name and not name.endswith(".exe"):
        name += ".exe"
    return name


class AppRuleEngine:
    """Ön plandaki uygulamaya göre ekran modunu otomatik değiştiren motor."""

    TICK_SEC = 1.5

    def __init__(self, config: ConfigManager, worker: AsyncWriteQueueWorker, schedule: ScheduleEngine,
                 probe: Optional[_ForegroundProbe] = None):
        self.config = config
        self.worker = worker
        self.schedule = schedule
        self.probe = probe or _ForegroundProbe()
        self._own_pid = os.getpid()
        self._active_process: Optional[str] = None
        self._active_mode: Optional[str] = None
        self._saved_state: Optional[Tuple[str, int, int]] = None

    def rules(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for rule in self.config.app_rules.get("rules", []) or []:
            if not isinstance(rule, dict):
                continue
            proc = normalize_process_name(rule.get("process", ""))
            mode = rule.get("mode")
            if proc and mode in DISPLAY_MODES:
                result[proc] = mode
        return result

    @property
    def active_process(self) -> Optional[str]:
        return self._active_process

    def tick(self):
        rules = self.rules() if self.config.app_rules.get("enabled") else {}
        if not rules:
            self._restore()
            return

        pid, proc = self.probe.foreground()
        if pid is None or pid == self._own_pid or proc is None:
            # Kendi panelimiz / ayarlarımız veya okunamayan pencere: durumu değiştirme
            return

        mode = rules.get(proc)
        if mode is None:
            self._restore()
            return

        if proc == self._active_process and mode == self._active_mode:
            return

        if self._saved_state is None:
            self._saved_state = (
                self.worker.get_current_mode(),
                self.worker.get_current_brightness(),
                self.worker.get_current_contrast(),
            )
        self._active_process = proc
        self._active_mode = mode
        self.schedule.set_paused(True)
        self.worker.apply_display_mode(mode)

    def _restore(self, wait_timeout: float = 0.0):
        if self._active_process is None:
            return
        saved = self._saved_state
        self._active_process = None
        self._active_mode = None
        self._saved_state = None
        if saved:
            mode, brightness, contrast = saved
            self.worker.apply_display_mode(mode, brightness=brightness, contrast=contrast,
                                           wait_timeout=wait_timeout)
        self.schedule.set_paused(False)

    def shutdown(self):
        """Uygulama kapanırken kural etkinse önceki duruma dön (donanıma yazılmasını bekle)."""
        self._restore(wait_timeout=3.0)


class AutomationService:
    """Plan ve uygulama kuralı motorlarını arka planda çalıştıran servis."""

    def __init__(self, config: ConfigManager, worker: AsyncWriteQueueWorker):
        self.config = config
        self.worker = worker
        self.schedule = ScheduleEngine(config, worker)
        self.app_rules = AppRuleEngine(config, worker, self.schedule)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="Automation", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        try:
            self.app_rules.shutdown()
        except Exception as e:
            print(f"[!] Otomasyon kapatma hatası: {e}")

    def reload(self):
        """Ayarlar değiştiğinde çağrılır: durumu sıfırlar ve hemen yeniden değerlendirir."""
        self.schedule.reset()
        self._wake.set()

    def _loop(self):
        next_schedule = 0.0
        elapsed = 0.0
        while not self._stop.is_set():
            woke = self._wake.wait(AppRuleEngine.TICK_SEC)
            if self._stop.is_set():
                break
            if woke:
                self._wake.clear()
                next_schedule = 0.0
            try:
                self.app_rules.tick()
            except Exception as e:
                print(f"[!] Uygulama kuralı hatası: {e}")

            elapsed += AppRuleEngine.TICK_SEC
            if woke or elapsed >= next_schedule:
                elapsed = 0.0
                next_schedule = ScheduleEngine.TICK_SEC
                try:
                    self.schedule.tick()
                except Exception as e:
                    print(f"[!] Zamanlama hatası: {e}")
