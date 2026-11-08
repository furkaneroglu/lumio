"""
Lumio - MonitorManager (Donanım Katmanı)
Windows High-Level Monitor Configuration API (Dxva2) ve Low-Level VCP DDC/CI arayüzü.
Parlaklık, Ses, Giriş Kaynağı ve Güç/Uyku kontrolünü yönetir.
"""

import sys
import threading
import ctypes
from ctypes import wintypes
from typing import List, Dict, Optional, Tuple

# Win32 / DXVA2 DDC/CI Hata Kodları ve Anlaşılır Açıklamaları
DDCCI_ERROR_MESSAGES = {
    0xC0262582: "Monitörden geçersiz veya bozuk DDC/CI verisi alındı (ERROR_GRAPHICS_DDCCI_INVALID_DATA).",
    0xC0262584: "Monitör bu VCP özelliğini desteklemiyor (ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED).",
    0xC0262585: "I2C veri aktarım hatası (ERROR_GRAPHICS_I2C_ERROR_TRANSMITTING_DATA). Monitör I2C sinyallerine yanıt vermedi.",
    0xC0262586: "I2C veri alma hatası (ERROR_GRAPHICS_I2C_ERROR_RECEIVING_DATA).",
    0xC0262587: "Monitör geçersiz zamanlama durumu döndürdü (ERROR_GRAPHICS_DDCCI_MONITOR_RETURNED_INVALID_TIMING_STATUS_BYTE).",
    0xC0262588: "DDC/CI yetenekler dizgisi okunamadı (ERROR_GRAPHICS_DDCCI_INVALID_CAPABILITIES_STRING).",
    31: "Cihaz yanıt vermiyor (ERROR_GEN_FAILURE). Monitör uyku modunda olabilir veya OSD menüsünde DDC/CI kapalı.",
    6: "Geçersiz tanıtıcı (ERROR_INVALID_HANDLE). Monitör uyku/uyanma sonrası tanıtıcı sıfırlanmış olabilir.",
    1460: "I2C/DDC iletişim zaman aşımına uğradı (ERROR_TIMEOUT).",
}

# Giriş Kaynakları (VESA MCCS VCP 0x60 değerleri)
INPUT_SOURCES = {
    0x01: "VGA 1",
    0x02: "VGA 2",
    0x03: "DVI 1",
    0x04: "DVI 2",
    0x05: "Composite 1",
    0x06: "Composite 2",
    0x07: "S-Video 1",
    0x08: "S-Video 2",
    0x09: "Tuner 1",
    0x0A: "Tuner 2",
    0x0B: "Tuner 3",
    0x0C: "Component 1",
    0x0D: "Component 2",
    0x0E: "Component 3",
    0x0F: "DisplayPort 1",
    0x10: "DisplayPort 2",
    0x11: "HDMI 1",
    0x12: "HDMI 2",
    0x1B: "USB-C",
}

# Monitörler çoğu zaman fiziksel olarak bulunmayan eski analog girişleri de bildirir;
# bunlar varsayılan olarak gizlenir (Ayarlar'dan açılabilir).
LEGACY_INPUTS = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E}

# Monitör yetenek listesi okunamazsa kullanılacak genel varsayılan
DEFAULT_INPUTS = [0x0F, 0x11, 0x12]


def input_name(code: int) -> str:
    """Giriş kodunun okunabilir adını döner (bilinmeyen kodlar için 'Giriş 0xNN')."""
    return INPUT_SOURCES.get(code, f"Giriş 0x{code:02X}")


def input_short_name(code: int) -> str:
    """Dar alanlar (sekmeler) için kısa giriş adı: 'DisplayPort 1' -> 'DP 1'."""
    return input_name(code).replace("DisplayPort", "DP").replace("Composite", "Komp.").replace("Component", "Bileşen")


def parse_vcp_capabilities(caps: str) -> Dict[int, List[int]]:
    """
    MCCS yetenek dizgisinin vcp(...) bölümünü ayrıştırır.
    Örn: 'vcp(10 12 14(05 06) 60( 11 12 0F))' -> {0x10: [], 0x12: [], 0x14: [5, 6], 0x60: [0x11, 0x12, 0x0F]}
    Boşluksuz yazımlara da dayanıklıdır (örn. '14(05 06)16 18').
    """
    result: Dict[int, List[int]] = {}
    if not caps:
        return result
    lower = caps.lower()
    start = -1
    pos = 0
    while True:
        idx = lower.find("vcp(", pos)
        if idx == -1:
            break
        # 'vcpname(' gibi başka anahtarlarla karışmasın: önceki karakter harf olmamalı
        if idx == 0 or not lower[idx - 1].isalpha():
            start = idx + 4
            break
        pos = idx + 4
    if start == -1:
        return result

    depth = 0
    token = ""
    current: Optional[int] = None
    i = start
    while i < len(lower):
        ch = lower[i]
        if ch in "0123456789abcdef":
            token += ch
            if len(token) == 2:
                value = int(token, 16)
                token = ""
                if depth == 0:
                    current = value
                    result.setdefault(value, [])
                elif depth == 1 and current is not None:
                    result[current].append(value)
        else:
            token = ""
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break  # vcp(...) bölümü bitti
                depth -= 1
        i += 1
    return result

# Güç Modları (VCP 0xD6)
POWER_MODES = {
    0x01: "Açık (On)",
    0x04: "Bekleme (Standby)",
    0x05: "Kapalı / Askıya Al (Suspend)"
}

# VESA MCCS Renk Sıcaklığı ve Ton Ön Ayarları (VCP 0x14)
COLOR_PRESETS = {
    0x01: "sRGB",
    0x02: "Doğal (Display Native)",
    0x04: "5000K (Sıcak)",
    0x05: "6500K (Standart / Canlı)",
    0x06: "7500K",
    0x08: "9300K (Soğuk)",
    0x0B: "Kullanıcı (User Color)"
}

# Ekran Modları (ViewMode Presets)
# Her mod donanım seviyesinde Parlaklık (0x10), Kontrast (0x12) ve Renk Sıcaklığı (0x14) değerlerini koordine eder.
DISPLAY_MODES = {
    "Standart": {
        "id": "Standart",
        "name": "Standart Mod",
        "icon": "🌐",
        "brightness": 70,
        "contrast": 70,
        "color_preset": 0x02,  # Doğal / Native
        "desc": "Doğal renk dengesi ve dengeli günlük kullanım"
    },
    "Oyun": {
        "id": "Oyun",
        "name": "Oyun Modu",
        "icon": "🎮",
        "brightness": 90,
        "contrast": 75,
        "color_preset": 0x05,  # 6500K Canlı
        "desc": "Yüksek parlaklık, dinamik kontrast ve canlı renkler"
    },
    "Film": {
        "id": "Film",
        "name": "Film / Sinema",
        "icon": "🎬",
        "brightness": 70,
        "contrast": 80,
        "color_preset": 0x04,  # 5000K Sıcak
        "desc": "Sinematik sıcak tonlar, derin siyahlar ve yüksek kontrast"
    },
    "Metin": {
        "id": "Metin",
        "name": "Metin / Okuma",
        "icon": "📖",
        "brightness": 35,
        "contrast": 50,
        "color_preset": 0x01,  # sRGB / Düşük mavi ışık
        "desc": "Göz yormayan yumuşak tonlar ve düşük kontrast"
    },
    "Gece": {
        "id": "Gece",
        "name": "Gece / Kodlama",
        "icon": "🌙",
        "brightness": 15,
        "contrast": 55,
        "color_preset": 0x04,  # Sıcak ton
        "desc": "Karanlık ortamlar için minimum göz yorgunluğu"
    }
}

# Bilinen Monitör Üretici Kodları (EDID PNP ID)
KNOWN_BRANDS = {
    "LHC": "Rampage",
    "VSC": "ViewSonic",
    "DEL": "Dell",
    "SAM": "Samsung",
    "SEC": "Samsung",
    "GSM": "LG",
    "AOC": "AOC",
    "ACR": "Acer",
    "ASU": "ASUS",
    "BEN": "BenQ",
    "MSI": "MSI",
    "HPN": "HP",
    "HWP": "HP",
    "LEN": "Lenovo",
}

class DDCCIError(Exception):
    """DDC/CI genel donanım hatası."""
    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code

class DDCCIDisconnectedError(DDCCIError):
    """Monitör uyku moduna geçtiğinde, DPMS kapandığında veya kablo koptuğunda fırlatılır."""
    pass

class DDCCIUnsupportedError(DDCCIError):
    """Monitör VCP özelliğini desteklemediğinde fırlatılır."""
    pass


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [
        ("hPhysicalMonitor", wintypes.HANDLE),
        ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32)
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128)
    ]


class MonitorDevice:
    """Tek bir fiziksel monitörün özelliklerini ve durumunu temsil eden veri modeli."""
    def __init__(
        self,
        index: int,
        hmonitor: wintypes.HMONITOR,
        description: str,
        current_brightness: int,
        min_brightness: int = 0,
        max_brightness: int = 100,
        current_volume: int = 50,
        current_input: int = 0x12,
        current_contrast: int = 70,
        current_color_preset: int = 0x05,
        current_mode: str = "Standart",
        capabilities_string: str = "",
        brand: str = "",
        model: str = "",
        supports_volume: bool = True,
        is_muted: bool = False
    ):
        self.index = index
        self.hmonitor = hmonitor
        self.description = description
        self.brand = brand
        self.model = model
        self.current_brightness = current_brightness
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.current_volume = current_volume
        self.current_input = current_input
        self.current_contrast = current_contrast
        self.current_color_preset = current_color_preset
        self.current_mode = current_mode
        self.capabilities_string = capabilities_string
        self.supports_volume = supports_volume
        self.is_muted = is_muted
        self._vcp_caps: Optional[Dict[int, List[int]]] = None

    @property
    def short_name(self) -> str:
        if self.brand:
            return self.brand
        if self.description:
            first_word = self.description.split()[0]
            if first_word.lower() not in ("generic", "monitör", "monitor"):
                return first_word
        return f"Ekran {self.index + 1}"

    @property
    def vcp_caps(self) -> Dict[int, List[int]]:
        if self._vcp_caps is None:
            self._vcp_caps = parse_vcp_capabilities(self.capabilities_string)
        return self._vcp_caps

    def supports(self, vcp_code: int) -> bool:
        """Monitör bu VCP özelliğini bildiriyor mu? Yetenek listesi okunamadıysa destekli varsayılır."""
        caps = self.vcp_caps
        return True if not caps else vcp_code in caps

    @property
    def reported_inputs(self) -> List[int]:
        """Monitörün bildirdiği giriş kaynakları (bildirim sırası korunur, tekrarlar atılır)."""
        seen: List[int] = []
        for code in self.vcp_caps.get(0x60, []):
            if code not in seen:
                seen.append(code)
        if not seen:
            seen = list(DEFAULT_INPUTS)
        if self.current_input and self.current_input not in seen:
            seen.append(self.current_input)
        return sorted(seen)

    @property
    def default_visible_inputs(self) -> List[int]:
        """Varsayılan olarak gösterilecek girişler: eski analog girişler hariç (etkin giriş her zaman dahil)."""
        modern = [c for c in self.reported_inputs if c not in LEGACY_INPUTS or c == self.current_input]
        return modern or self.reported_inputs

    @property
    def input_name(self) -> str:
        return input_name(self.current_input)

    @property
    def color_preset_name(self) -> str:
        return COLOR_PRESETS.get(self.current_color_preset, f"0x{self.current_color_preset:02X}")

    @property
    def supports_luminance(self) -> bool:
        return self.supports(0x10)

    def __repr__(self):
        return f"<MonitorDevice #{self.index}: '{self.description}' (%{self.current_brightness}, Kontrast: %{self.current_contrast}, Mod: {self.current_mode}, Ses: %{self.current_volume}, Giriş: {self.input_name})>"


class MonitorManager:
    """
    Windows Dxva2 ve User32 API'lerini sarmalayan,
    parlaklık, ses, giriş kaynağı ve güç kontrolünü yöneten donanım soyutlama katmanı.
    """

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("MonitorManager yalnızca Windows işletim sisteminde (Dxva2 API) çalışır.")

        self._hw_lock = threading.Lock()
        self._capabilities_cache: Dict[str, str] = {}
        self.user32 = ctypes.windll.user32
        self.dxva2 = ctypes.windll.dxva2
        self._init_api_signatures()

    def _init_api_signatures(self):
        # Dxva2 Win32 Fonksiyon İmzaları
        self.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HMONITOR,
            ctypes.POINTER(wintypes.DWORD)
        ]
        self.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL

        self.dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [
            wintypes.HMONITOR,
            wintypes.DWORD,
            ctypes.POINTER(PHYSICAL_MONITOR)
        ]
        self.dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL

        self.dxva2.GetMonitorBrightness.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD)
        ]
        self.dxva2.GetMonitorBrightness.restype = wintypes.BOOL

        self.dxva2.SetMonitorBrightness.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD
        ]
        self.dxva2.SetMonitorBrightness.restype = wintypes.BOOL

        # Low-level VCP (Ses 0x62, Giriş 0x60, Güç 0xD6)
        self.dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
            wintypes.HANDLE,
            wintypes.BYTE,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD)
        ]
        self.dxva2.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL

        self.dxva2.SetVCPFeature.argtypes = [
            wintypes.HANDLE,
            wintypes.BYTE,
            wintypes.DWORD
        ]
        self.dxva2.SetVCPFeature.restype = wintypes.BOOL

        self.dxva2.GetCapabilitiesStringLength.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD)
        ]
        self.dxva2.GetCapabilitiesStringLength.restype = wintypes.BOOL

        self.dxva2.CapabilitiesRequestAndCapabilitiesReply.argtypes = [
            wintypes.HANDLE,
            ctypes.c_char_p,
            wintypes.DWORD
        ]
        self.dxva2.CapabilitiesRequestAndCapabilitiesReply.restype = wintypes.BOOL

        self.dxva2.DestroyPhysicalMonitors.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(PHYSICAL_MONITOR)
        ]
        self.dxva2.DestroyPhysicalMonitors.restype = wintypes.BOOL

        self.MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM
        )
        self.user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC,
            ctypes.c_void_p,
            self.MONITORENUMPROC,
            wintypes.LPARAM
        ]
        self.user32.EnumDisplayMonitors.restype = wintypes.BOOL

        # 64-bit handle'ların int'e kırpılmaması için imzalar açıkça tanımlanır
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
        self.user32.GetMonitorInfoW.restype = wintypes.BOOL
        self.user32.EnumDisplayDevicesW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD
        ]
        self.user32.EnumDisplayDevicesW.restype = wintypes.BOOL

    def _format_win32_error(self, err_code: int) -> str:
        unsigned = err_code & 0xFFFFFFFF
        msg = DDCCI_ERROR_MESSAGES.get(unsigned) or DDCCI_ERROR_MESSAGES.get(err_code)
        if msg:
            return f"0x{unsigned:08X} - {msg}"
        return f"0x{unsigned:08X} (Kod: {err_code})"

    def _read_capabilities(self, handle: wintypes.HANDLE, cache_key: str = "") -> str:
        if cache_key and cache_key in self._capabilities_cache:
            return self._capabilities_cache[cache_key]
        try:
            str_len = wintypes.DWORD()
            if self.dxva2.GetCapabilitiesStringLength(handle, ctypes.byref(str_len)):
                if str_len.value > 0:
                    buf = (ctypes.c_char * str_len.value)()
                    if self.dxva2.CapabilitiesRequestAndCapabilitiesReply(handle, buf, str_len.value):
                        caps = buf.value.decode("ascii", errors="ignore")
                        if cache_key:
                            self._capabilities_cache[cache_key] = caps
                        return caps
        except Exception:
            pass
        return ""

    def _execute_on_handle(self, target_index: int, func):
        """Belirtilen hedef indeks için fiziksel handle alır, func(handle) çağırır ve handle'ı serbest bırakır."""
        with self._hw_lock:
            hmonitors: List[wintypes.HMONITOR] = []

            def _enum_cb(hmon, hdc, lprect, lparam):
                hmonitors.append(hmon)
                return True

            self.user32.EnumDisplayMonitors(None, None, self.MONITORENUMPROC(_enum_cb), 0)

            current_idx = 0
            for hmon in hmonitors:
                count = wintypes.DWORD()
                if not self.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
                    continue

                phys_array = (PHYSICAL_MONITOR * count.value)()
                if not self.dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, phys_array):
                    continue

                try:
                    for i in range(count.value):
                        if current_idx == target_index:
                            handle = phys_array[i].hPhysicalMonitor
                            desc = str(phys_array[i].szPhysicalMonitorDescription)
                            return func(handle, desc)
                        current_idx += 1
                finally:
                    self.dxva2.DestroyPhysicalMonitors(count.value, phys_array)

            raise DDCCIDisconnectedError(f"Monitör #{target_index} bulunamadı.")

    def _resolve_friendly_name(self, hmon: wintypes.HMONITOR, raw_desc: str, global_idx: int) -> Tuple[str, str, str]:
        """
        Windows Display Devices ve Registry EDID verilerini tarayarak
        monitörün gerçek üretici (brand) ve model (model) adını çözer.
        Örn: 'Generic PnP Monitor' -> ('Dell U2720Q', 'Dell', 'U2720Q')
        """
        brand = ""
        model = ""
        friendly_name = raw_desc

        try:
            # 1. HMONITOR üzerinden GDI Device Name (szDevice örn: \\.\DISPLAY2) al
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if self.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                sz_device = mi.szDevice

                # 2. EnumDisplayDevicesW ile DeviceID ve DeviceString al
                dd = DISPLAY_DEVICEW()
                dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
                # EDD_GET_DEVICE_INTERFACE_NAME = 1
                if self.user32.EnumDisplayDevicesW(sz_device, 0, ctypes.byref(dd), 1):
                    dev_id = dd.DeviceID
                    dev_str = dd.DeviceString

                    # 3. PNP Manufacturer Kodunu çıkar (Örn: DISPLAY#LHCFFFF#... -> LHC)
                    pnp_id_part = ""
                    instance_part = ""
                    if "DISPLAY#" in dev_id.upper():
                        parts = dev_id.upper().split("DISPLAY#")[1].split("#")
                        if len(parts) >= 2:
                            pnp_id_part = parts[0]
                            instance_part = parts[1]
                            pnp_code = pnp_id_part[:3]
                            brand = KNOWN_BRANDS.get(pnp_code, "")

                    # 4. Registry üzerinden EDID descriptor parse et
                    if pnp_id_part and instance_part:
                        import winreg
                        reg_path = rf"SYSTEM\CurrentControlSet\Enum\DISPLAY\{pnp_id_part}\{instance_part}\Device Parameters"
                        try:
                            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path) as key:
                                edid, _ = winreg.QueryValueEx(key, "EDID")
                                if isinstance(edid, (bytes, bytearray)) and len(edid) >= 128:
                                    for offset in (54, 72, 90, 108):
                                        if offset + 18 <= len(edid):
                                            tag = edid[offset + 3]
                                            if tag == 0xFC:
                                                name_bytes = edid[offset + 5 : offset + 18]
                                                model = name_bytes.decode("ascii", errors="ignore").split("\n")[0].split("\r")[0].strip()
                                                if model:
                                                    break
                        except Exception:
                            pass

                    # Model veya Brand tespit edildiyse friendly_name oluştur
                    if brand and model:
                        friendly_name = f"{brand} {model}"
                    elif brand and not model:
                        if dev_str and "generic" not in dev_str.lower():
                            friendly_name = f"{brand} {dev_str}"
                        else:
                            friendly_name = f"{brand} Monitör"
                    elif model:
                        friendly_name = model
                    elif dev_str and "generic" not in dev_str.lower():
                        friendly_name = dev_str
        except Exception as e:
            print(f"[!] _resolve_friendly_name hatası: {e}")

        # Eğer hala generic ise
        if not friendly_name or "generic" in friendly_name.lower():
            friendly_name = f"Monitör #{global_idx + 1}"

        return friendly_name, brand, model

    def enumerate_monitors(self) -> List[MonitorDevice]:
        """Sisteme bağlı tüm DDC/CI monitörleri tarar; parlaklık, ses ve giriş kaynağını okur."""
        with self._hw_lock:
            hmonitors: List[wintypes.HMONITOR] = []

            def _enum_cb(hmon, hdc, lprect, lparam):
                hmonitors.append(hmon)
                return True

            if not self.user32.EnumDisplayMonitors(None, None, self.MONITORENUMPROC(_enum_cb), 0):
                return []

            devices: List[MonitorDevice] = []
            global_idx = 0

            for hmon in hmonitors:
                count = wintypes.DWORD()
                if not self.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(count)):
                    continue

                if count.value == 0:
                    continue

                phys_array = (PHYSICAL_MONITOR * count.value)()
                if not self.dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, count.value, phys_array):
                    continue

                try:
                    for i in range(count.value):
                        handle = phys_array[i].hPhysicalMonitor
                        desc = str(phys_array[i].szPhysicalMonitorDescription)

                        min_b, cur_b, max_b = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                        success = self.dxva2.GetMonitorBrightness(
                            handle,
                            ctypes.byref(min_b),
                            ctypes.byref(cur_b),
                            ctypes.byref(max_b)
                        )

                        if success:
                            caps = self._read_capabilities(handle, cache_key=f"{desc}_{global_idx}")

                            # Ses okuma (0x62)
                            pvct, cur_vol, mx_vol = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
                            vol_val = 50
                            vol_ok = bool(self.dxva2.GetVCPFeatureAndVCPFeatureReply(
                                handle, 0x62, ctypes.byref(pvct), ctypes.byref(cur_vol), ctypes.byref(mx_vol)))
                            if vol_ok:
                                vol_val = cur_vol.value

                            # Sessiz durumu (0x8D: 1 = sessiz, 2 = açık)
                            cur_mute, mx_mute = wintypes.DWORD(), wintypes.DWORD()
                            muted = False
                            if self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x8D, ctypes.byref(pvct), ctypes.byref(cur_mute), ctypes.byref(mx_mute)):
                                muted = (cur_mute.value & 0xFF) == 1

                            # Giriş kaynağı okuma (0x60)
                            cur_inp, mx_inp = wintypes.DWORD(), wintypes.DWORD()
                            inp_val = 0x12
                            if self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x60, ctypes.byref(pvct), ctypes.byref(cur_inp), ctypes.byref(mx_inp)):
                                inp_val = cur_inp.value & 0xFF

                            # Kontrast okuma (0x12)
                            cur_cnt, mx_cnt = wintypes.DWORD(), wintypes.DWORD()
                            cnt_val = 70
                            if self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x12, ctypes.byref(pvct), ctypes.byref(cur_cnt), ctypes.byref(mx_cnt)):
                                cnt_val = cur_cnt.value

                            # Renk Preset okuma (0x14)
                            cur_cp, mx_cp = wintypes.DWORD(), wintypes.DWORD()
                            cp_val = 0x05
                            if self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x14, ctypes.byref(pvct), ctypes.byref(cur_cp), ctypes.byref(mx_cp)):
                                cp_val = cur_cp.value

                            friendly_desc, brand, model = self._resolve_friendly_name(hmon, desc, global_idx)

                            dev = MonitorDevice(
                                index=global_idx,
                                hmonitor=hmon,
                                description=friendly_desc,
                                current_brightness=cur_b.value,
                                min_brightness=min_b.value,
                                max_brightness=max_b.value,
                                current_volume=vol_val,
                                current_input=inp_val,
                                current_contrast=cnt_val,
                                current_color_preset=cp_val,
                                current_mode="Standart",
                                capabilities_string=caps,
                                brand=brand,
                                model=model,
                                supports_volume=vol_ok or 0x62 in parse_vcp_capabilities(caps),
                                is_muted=muted
                            )
                            devices.append(dev)
                        # İndeks, DDC/CI desteklemeyen monitörler için de artırılmalıdır;
                        # aksi halde _execute_on_handle() farklı (yanlış) bir monitörü hedefler.
                        global_idx += 1
                finally:
                    self.dxva2.DestroyPhysicalMonitors(count.value, phys_array)

            return devices

    # --- Parlaklık (Luminance VCP 0x10) ---
    def set_brightness(self, target_index: int, value: int) -> int:
        value = max(0, min(100, int(value)))

        def _action(handle, desc):
            if not self.dxva2.SetMonitorBrightness(handle, wintypes.DWORD(value)):
                err = ctypes.GetLastError()
                raise DDCCIError(f"Parlaklık ayarlanamadı: {self._format_win32_error(err)}", code=err)
            # Not: DDC/CI, Set komutundan sonra ~50 ms bekleme gerektirir; anında geri okuma
            # çoğu monitörde eski değeri döndürür. Başarılı yazılan değer döndürülür.
            return value

        return self._execute_on_handle(target_index, _action)

    def get_brightness(self, target_index: int) -> int:
        def _action(handle, desc):
            min_b, cur_b, max_b = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
            if not self.dxva2.GetMonitorBrightness(handle, ctypes.byref(min_b), ctypes.byref(cur_b), ctypes.byref(max_b)):
                err = ctypes.GetLastError()
                raise DDCCIDisconnectedError(f"Parlaklık okunamadı: {self._format_win32_error(err)}", code=err)
            return cur_b.value

        return self._execute_on_handle(target_index, _action)

    # --- Ses Seviyesi (Volume VCP 0x62) & Mute (0x8D) ---
    def set_volume(self, target_index: int, value: int) -> int:
        value = max(0, min(100, int(value)))

        def _action(handle, desc):
            if not self.dxva2.SetVCPFeature(handle, 0x62, wintypes.DWORD(value)):
                err = ctypes.GetLastError()
                raise DDCCIError(f"Ses ayarlanamadı: {self._format_win32_error(err)}", code=err)
            # Not: DDC/CI, Set komutundan sonra ~50 ms bekleme gerektirir; anında geri okuma
            # çoğu monitörde eski değeri döndürür. Başarılı yazılan değer döndürülür.
            return value

        return self._execute_on_handle(target_index, _action)

    def get_volume(self, target_index: int) -> int:
        def _action(handle, desc):
            pvct, cur_vol, mx = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
            if not self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x62, ctypes.byref(pvct), ctypes.byref(cur_vol), ctypes.byref(mx)):
                err = ctypes.GetLastError()
                raise DDCCIDisconnectedError(f"Ses okunamadı: {self._format_win32_error(err)}", code=err)
            return cur_vol.value

        return self._execute_on_handle(target_index, _action)

    def set_mute(self, target_index: int, mute: bool) -> bool:
        # VESA MCCS 0x8D: 1 = Mute, 2 = Unmute
        val = 1 if mute else 2

        def _action(handle, desc):
            if not self.dxva2.SetVCPFeature(handle, 0x8D, wintypes.DWORD(val)):
                err = ctypes.GetLastError()
                raise DDCCIError(f"Sessize alma başarısız: {self._format_win32_error(err)}", code=err)
            return mute

        return self._execute_on_handle(target_index, _action)

    # --- Giriş Kaynağı (Input Source VCP 0x60) ---
    def set_input_source(self, target_index: int, source_code: int) -> int:
        def _action(handle, desc):
            if not self.dxva2.SetVCPFeature(handle, 0x60, wintypes.DWORD(source_code)):
                err = ctypes.GetLastError()
                raise DDCCIError(f"Giriş kaynağı değiştirilemedi: {self._format_win32_error(err)}", code=err)
            return source_code

        return self._execute_on_handle(target_index, _action)

    def get_input_source(self, target_index: int) -> int:
        def _action(handle, desc):
            pvct, cur_inp, mx = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
            if not self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x60, ctypes.byref(pvct), ctypes.byref(cur_inp), ctypes.byref(mx)):
                err = ctypes.GetLastError()
                raise DDCCIDisconnectedError(f"Giriş kaynağı okunamadı: {self._format_win32_error(err)}", code=err)
            return cur_inp.value & 0xFF

        return self._execute_on_handle(target_index, _action)

    # --- Güç ve Uyku Yönetimi (Power Mode VCP 0xD6) ---
    def set_power_mode(self, target_index: int, mode: int = 0x04) -> bool:
        """Monitörü uyku/bekleme moduna (0x04 Standby) veya açılışa (0x01 On) alır."""
        def _action(handle, desc):
            if not self.dxva2.SetVCPFeature(handle, 0xD6, wintypes.DWORD(mode)):
                err = ctypes.GetLastError()
                raise DDCCIError(f"Güç modu ayarlanamadı: {self._format_win32_error(err)}", code=err)
            return True

        return self._execute_on_handle(target_index, _action)

    # --- Kontrast (Contrast VCP 0x12) ---
    def set_contrast(self, target_index: int, value: int) -> int:
        value = max(0, min(100, int(value)))

        def _action(handle, desc):
            if not self.dxva2.SetVCPFeature(handle, 0x12, wintypes.DWORD(value)):
                err = ctypes.GetLastError()
                raise DDCCIError(f"Kontrast ayarlanamadı: {self._format_win32_error(err)}", code=err)
            # Not: DDC/CI, Set komutundan sonra ~50 ms bekleme gerektirir; anında geri okuma
            # çoğu monitörde eski değeri döndürür. Başarılı yazılan değer döndürülür.
            return value

        return self._execute_on_handle(target_index, _action)

    def get_contrast(self, target_index: int) -> int:
        def _action(handle, desc):
            pvct, cur_cnt, mx = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
            if not self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x12, ctypes.byref(pvct), ctypes.byref(cur_cnt), ctypes.byref(mx)):
                err = ctypes.GetLastError()
                raise DDCCIDisconnectedError(f"Kontrast okunamadı: {self._format_win32_error(err)}", code=err)
            return cur_cnt.value

        return self._execute_on_handle(target_index, _action)

    # --- Renk Preset / Sıcaklık (Color Preset VCP 0x14) ---
    def set_color_preset(self, target_index: int, preset_code: int) -> int:
        def _action(handle, desc):
            if not self.dxva2.SetVCPFeature(handle, 0x14, wintypes.DWORD(preset_code)):
                err = ctypes.GetLastError()
                raise DDCCIError(f"Renk modu ayarlanamadı: {self._format_win32_error(err)}", code=err)
            return preset_code

        return self._execute_on_handle(target_index, _action)

    def get_color_preset(self, target_index: int) -> int:
        def _action(handle, desc):
            pvct, cur_cp, mx = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
            if not self.dxva2.GetVCPFeatureAndVCPFeatureReply(handle, 0x14, ctypes.byref(pvct), ctypes.byref(cur_cp), ctypes.byref(mx)):
                err = ctypes.GetLastError()
                raise DDCCIDisconnectedError(f"Renk modu okunamadı: {self._format_win32_error(err)}", code=err)
            return cur_cp.value

        return self._execute_on_handle(target_index, _action)

    # --- Ekran Modları (ViewMode Presets) ---
    def apply_display_mode(self, target_index: int, mode_name: str,
                           brightness: Optional[int] = None, contrast: Optional[int] = None) -> dict:
        """
        Belirtilen ekran modunu (Oyun, Film, Metin, Standart, Gece) donanım üzerinde uygular.
        Parlaklık (0x10), Kontrast (0x12) ve Renk Ön Ayarını (0x14) koordine eder.
        brightness / contrast verilirse modun varsayılan değerleri yerine bunlar yazılır
        (ekran eşitleme farkı veya önceki duruma geri dönüş için).
        """
        if mode_name not in DISPLAY_MODES:
            raise ValueError(f"Bilinmeyen ekran modu: {mode_name}. Geçerli modlar: {list(DISPLAY_MODES.keys())}")

        mode_data = DISPLAY_MODES[mode_name]

        def _action(handle, desc):
            # 1. Renk Modu (0x14)
            cp = mode_data.get("color_preset")
            if cp:
                self.dxva2.SetVCPFeature(handle, 0x14, wintypes.DWORD(cp))

            # 2. Kontrast (0x12)
            cnt = contrast if contrast is not None else mode_data.get("contrast")
            if cnt is not None:
                cnt = max(0, min(100, int(cnt)))
                self.dxva2.SetVCPFeature(handle, 0x12, wintypes.DWORD(cnt))

            # 3. Parlaklık (0x10)
            br = brightness if brightness is not None else mode_data.get("brightness")
            if br is not None:
                br = max(0, min(100, int(br)))
                self.dxva2.SetMonitorBrightness(handle, wintypes.DWORD(br))

            return mode_data

        return self._execute_on_handle(target_index, _action)
