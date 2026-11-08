"""
Lumio - System Tray Icon and Menu Manager (Tray UI)
pystray ve Pillow kullanarak sistem tepsisi simgesi, bildirimler ve sağ tık menüsünü yönetir.
Parlaklık, Ses, Giriş Kaynağı ve Uyku kontrollerini menüden sunar.
"""

import threading
from typing import Optional, Callable
from PIL import Image, ImageDraw
import pystray
from .config import ConfigManager
from .queue_worker import AsyncWriteQueueWorker, MonitorStatus
from .monitor_manager import DISPLAY_MODES, input_name


def create_tray_image(brightness: int = 50, status: MonitorStatus = MonitorStatus.CONNECTED) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if status == MonitorStatus.CONNECTED:
        bg_color = (24, 24, 27, 255)
    elif status == MonitorStatus.SLEEPING:
        bg_color = (40, 35, 20, 255)
    else:
        bg_color = (45, 20, 20, 255)

    draw.rounded_rectangle((4, 4, size - 4, size - 4), radius=14, fill=bg_color)

    if status == MonitorStatus.CONNECTED:
        ratio = max(0.0, min(1.0, brightness / 100.0))
        r = min(255, int(190 + ratio * 65))
        g = min(255, int(140 + ratio * 115))
        b = min(255, int(20 + ratio * 130))
        sun_color = (r, g, b)

        draw.ellipse((20, 14, 44, 38), fill=sun_color)

        bar_bg = (63, 63, 70, 255)
        draw.rounded_rectangle((10, 46, size - 10, 52), radius=3, fill=bar_bg)

        fill_w = int((size - 20) * ratio)
        if fill_w > 0:
            draw.rounded_rectangle((10, 46, 10 + fill_w, 52), radius=3, fill=sun_color)

    elif status == MonitorStatus.SLEEPING:
        draw.ellipse((18, 14, 42, 38), fill=(245, 158, 11))
        draw.ellipse((26, 12, 48, 36), fill=bg_color)
        draw.rounded_rectangle((10, 46, size - 10, 52), radius=3, fill=(100, 80, 20))

    else:
        draw.line((18, 18, 46, 46), fill=(239, 68, 68), width=5)
        draw.line((18, 46, 46, 18), fill=(239, 68, 68), width=5)

    return img


class TrayApp:
    """Sistem tepsisi ikonunu ve sağ tık menüsünü yöneten sınıf."""

    def __init__(
        self,
        config: ConfigManager,
        worker: AsyncWriteQueueWorker,
        on_open_popup: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_exit: Callable[[], None],
        on_automation_changed: Optional[Callable[[], None]] = None
    ):
        self.config = config
        self.worker = worker
        self.on_open_popup = on_open_popup
        self.on_open_settings = on_open_settings
        self.on_exit = on_exit
        self.on_automation_changed = on_automation_changed

        self.icon: Optional[pystray.Icon] = None

    def start(self):
        initial_img = create_tray_image(
            self.worker.get_current_brightness(),
            self.worker.status
        )
        self.icon = pystray.Icon(
            name="Lumio",
            icon=initial_img,
            title="Lumio",
            menu=pystray.Menu(lambda: self._build_menu_items())
        )
        self._install_menu_refresh_hook()
        self.icon.run_detached()

    def _install_menu_refresh_hook(self):
        """
        pystray'in Win32 arka ucu menüyü yalnızca update_menu() çağrıldığında yeniden oluşturur;
        callable menü öğeleri kendiliğinden yenilenmez. Sağ tıkta, menü gösterilmeden hemen önce
        (pystray'in kendi thread'inde) menüyü yeniden oluşturarak güncel durumun görünmesini sağlar.
        """
        icon = self.icon
        handlers = getattr(icon, "_message_handlers", None)
        if not isinstance(handlers, dict):
            return
        try:
            from pystray._util import win32 as pywin32
        except Exception:
            return
        original = handlers.get(pywin32.WM_NOTIFY)
        if original is None:
            return

        def _on_notify(wparam, lparam):
            if lparam == pywin32.WM_RBUTTONUP:
                try:
                    icon.update_menu()
                except Exception as e:
                    print(f"[!] Tray menu refresh error: {e}")
            return original(wparam, lparam)

        handlers[pywin32.WM_NOTIFY] = _on_notify

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass

    def update_icon_and_title(self, brightness: int, status: MonitorStatus):
        if self.icon is None:
            return

        desc = self.worker.active_monitor_name
        status_str = status.value
        vol = self.worker.get_current_volume()
        cnt = self.worker.get_current_contrast()
        mode = self.worker.get_current_mode()
        inp_name = input_name(self.worker.get_current_input())
        title_text = f"Ekran Kontrol ({desc})\nMod: {mode} | P: %{brightness} | K: %{cnt} | S: %{vol} | Giriş: {inp_name} [{status_str}]"

        # Windows NOTIFYICONDATA.szTip en fazla 128 WCHAR (NUL dahil) alır;
        # daha uzun metin pystray'de ValueError fırlatır ve simge güncellemesi de atlanırdı.
        if len(title_text) > 127:
            title_text = title_text[:126] + "…"

        try:
            self.icon.icon = create_tray_image(brightness, status)
        except Exception as e:
            print(f"[!] Tray icon update error: {e}")
        try:
            self.icon.title = title_text
        except Exception as e:
            print(f"[!] Tray title update error: {e}")

    def _build_menu_items(self):
        cur_b = self.worker.get_current_brightness()
        cur_c = self.worker.get_current_contrast()
        cur_v = self.worker.get_current_volume()
        cur_i = self.worker.get_current_input()
        cur_m = self.worker.get_current_mode()
        status = self.worker.status
        desc = self.worker.active_monitor_name

        if status == MonitorStatus.CONNECTED:
            status_tag = f"🟢 {desc} ({cur_m} | P:%{cur_b} | K:%{cur_c} | S:%{cur_v})"
        elif status == MonitorStatus.SLEEPING:
            status_tag = f"🟡 {desc} (Uykuda)"
        else:
            status_tag = f"🔴 {desc} (Bağlantı Yok)"

        def make_monitor_select_action(idx: int):
            return lambda icon, item: self.worker.select_device(idx)

        def make_brightness_action(val: int):
            return lambda icon, item: self.worker.set_brightness(val)

        def make_contrast_action(val: int):
            return lambda icon, item: self.worker.set_contrast(val)

        def make_volume_action(val: int):
            return lambda icon, item: self.worker.set_volume(val)

        def make_input_action(code: int):
            return lambda icon, item: self.worker.set_input_source(code)

        def make_mode_action(mode_name: str):
            return lambda icon, item: self.worker.apply_display_mode(mode_name)

        def make_profile_action(prof_name: str):
            return lambda icon, item: self.worker.apply_profile(prof_name)

        # 0. Aktif Ekran Seçimi Menüsü
        monitor_select_items = []
        for dev in self.worker.devices:
            is_active = (self.worker.active_index == dev.index)
            prefix = "● " if is_active else "  "
            label = f"{prefix}🖥️ {dev.description}"
            monitor_select_items.append(pystray.MenuItem(label, make_monitor_select_action(dev.index)))

        if len(self.worker.devices) > 1:
            is_sync_active = (self.worker.active_index == -1)
            prefix = "● " if is_sync_active else "  "
            monitor_select_items.append(pystray.Menu.SEPARATOR)
            monitor_select_items.append(pystray.MenuItem(f"{prefix}🔗 Tüm Ekranlar (Senkron)", make_monitor_select_action(-1)))

        monitor_select_items.append(pystray.Menu.SEPARATOR)
        monitor_select_items.append(pystray.MenuItem("🔄 Ekranları Yeniden Tara", lambda icon, item: self._on_rescan_clicked()))

        # 1. Ekran Modları (ViewMode)
        mode_items = []
        for mode_key, mode_info in DISPLAY_MODES.items():
            is_active = (mode_key == cur_m)
            prefix = "● " if is_active else "  "
            label = f"{prefix}{mode_info['icon']} {mode_info['name']} (%{mode_info['brightness']} P / %{mode_info['contrast']} K)"
            mode_items.append(pystray.MenuItem(label, make_mode_action(mode_key)))

        # 2. Hızlı Parlaklık
        quick_brightness = [
            pystray.MenuItem("%10", make_brightness_action(10)),
            pystray.MenuItem("%25", make_brightness_action(25)),
            pystray.MenuItem("%50", make_brightness_action(50)),
            pystray.MenuItem("%75", make_brightness_action(75)),
            pystray.MenuItem("%100", make_brightness_action(100)),
        ]

        # 3. Hızlı Kontrast
        quick_contrast = [
            pystray.MenuItem("%40", make_contrast_action(40)),
            pystray.MenuItem("%50", make_contrast_action(50)),
            pystray.MenuItem("%60", make_contrast_action(60)),
            pystray.MenuItem("%70", make_contrast_action(70)),
            pystray.MenuItem("%80", make_contrast_action(80)),
        ]

        # 4. Hızlı Ses Seviyesi
        quick_volume = [
            pystray.MenuItem("%20", make_volume_action(20)),
            pystray.MenuItem("%40", make_volume_action(40)),
            pystray.MenuItem("%60", make_volume_action(60)),
            pystray.MenuItem("%80", make_volume_action(80)),
            pystray.MenuItem("%100", make_volume_action(100)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sessiz (Mute / Unmute)", lambda icon, item: self.worker.toggle_mute()),
        ]

        # 5. Giriş Kaynağı Seçimi (monitörün bildirdiği ve kullanıcının görünür bıraktığı girişler)
        input_items = []
        for code in self.worker.visible_inputs():
            name = input_name(code)
            is_active = (code == cur_i)
            tag = f"● {name}" if is_active else name
            input_items.append(
                pystray.MenuItem(tag, make_input_action(code))
            )

        # 6. Profiller
        profile_items = []
        for name, val in self.config.profiles.items():
            profile_items.append(
                pystray.MenuItem(
                    f"{name} (%{val})",
                    make_profile_action(name)
                )
            )

        menu_items = [
            # Başlık
            pystray.MenuItem(status_tag, lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,

            # Hızlı Açılır Panel
            pystray.MenuItem("🎛️  Kontrol Panelini Aç", lambda icon, item: self.on_open_popup(), default=True),
            pystray.Menu.SEPARATOR,

            # Aktif Ekran Seçimi (Çoklu Monitör)
            pystray.MenuItem("🖥️ Aktif Ekran Seçimi", pystray.Menu(*monitor_select_items)),
            pystray.Menu.SEPARATOR,

            # Ekran Modları (ViewMode)
            pystray.MenuItem("📺 Ekran Modları (ViewMode)", pystray.Menu(*mode_items)),

            # Alt Menüler
            pystray.MenuItem("☀️ Parlaklık", pystray.Menu(*quick_brightness)),
            pystray.MenuItem("🌓 Kontrast", pystray.Menu(*quick_contrast)),
            pystray.MenuItem("🔊 Monitör Sesi", pystray.Menu(*quick_volume),
                             visible=self.worker.supports_volume),
            pystray.MenuItem("🔌 Giriş Kaynağı", pystray.Menu(*input_items)),
            pystray.MenuItem("📋 Profiller", pystray.Menu(*profile_items)),
            pystray.Menu.SEPARATOR,

            # Uyku Modu ve Tarama
            # Otomasyon
            pystray.MenuItem(
                "🕒 Saate göre otomatik parlaklık",
                lambda icon, item: self._toggle_feature("schedule"),
                checked=lambda item: bool(self.config.schedule.get("enabled"))
            ),
            pystray.MenuItem(
                "🎮 Uygulamaya göre otomatik mod",
                lambda icon, item: self._toggle_feature("app_rules"),
                checked=lambda item: bool(self.config.app_rules.get("enabled"))
            ),
            pystray.Menu.SEPARATOR,

            pystray.MenuItem("🌙 Monitörü Uyku Moduna Al", lambda icon, item: self.worker.sleep_monitor()),
            pystray.MenuItem("🔄 Monitörü Yeniden Tara / Doğrula", lambda icon, item: self._on_rescan_clicked()),

            # Ayarlar & Çıkış
            pystray.MenuItem("⚙️ Ayarlar...", lambda icon, item: self.on_open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Çıkış", lambda icon, item: self.on_exit()),
        ]

        return menu_items

    def _toggle_feature(self, key: str):
        section = self.config.schedule if key == "schedule" else self.config.app_rules
        section["enabled"] = not bool(section.get("enabled"))
        self.config.save()
        if self.on_automation_changed:
            self.on_automation_changed()

    def _on_rescan_clicked(self):
        threading.Thread(target=self.worker.rescan_monitors, daemon=True).start()
