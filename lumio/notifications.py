"""
Lumio - NotificationEngine (Bildirim & Hata Yönetimi)
Monitör bağlantı kopması, uyku modu ve hatalarda kullanıcıyı Windows bildirimleriyle uyarır.
"""

import time
from typing import Optional, Callable, Any
from .config import ConfigManager

class NotificationEngine:
    """Windows sistem tepsisi balon/toast bildirimlerini yöneten ve mükerrer bildirimleri filtreleyen servis."""

    def __init__(self, config: ConfigManager, tray_icon_provider: Any = None):
        self.config = config
        self.tray_icon_provider = tray_icon_provider
        self._last_notification_time = 0.0
        self._last_message = ""
        self.min_interval_sec = 8.0

    def _resolve_icon(self):
        """Tepsi simgesini sağlayıcıdan çözer."""
        if self.tray_icon_provider is None:
            return None
        if callable(self.tray_icon_provider):
            return self.tray_icon_provider()
        if hasattr(self.tray_icon_provider, "icon"):
            return self.tray_icon_provider.icon
        return self.tray_icon_provider

    def send_notification(self, title: str, message: str, level: str = "info"):
        """Kullanıcıya Windows sistem tepsisi bildirimi gönderir."""
        if not self.config.show_notifications:
            return

        now = time.time()
        # Mükerrer bildirim filtresi (Spam engelleme)
        if message == self._last_message and (now - self._last_notification_time) < self.min_interval_sec:
            return

        self._last_notification_time = now
        self._last_message = message

        icon = self._resolve_icon()
        if icon is not None and hasattr(icon, "notify"):
            try:
                icon.notify(message=message, title=title)
            except Exception as e:
                print(f"[!] Bildirim gönderilemedi: {e}")
        else:
            print(f"[{title}] {message}")
