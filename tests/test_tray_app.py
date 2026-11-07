"""
ViewSonic OMNI DDC/CI Tray - Comprehensive Test Suite
Tüm mimari katmanları: MonitorManager, AsyncWriteQueueWorker, ConfigManager,
NotificationEngine, HotkeyService ve Win32 Mutex korumasını test eder.
"""

import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch

# UTF-8 konsol ayarı (Windows CI/CD cp1252 uyumluluğu için)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)

from viewsonic_omni_tray.config import ConfigManager, DEFAULT_CONFIG
from viewsonic_omni_tray.monitor_manager import MonitorManager, MonitorDevice, DDCCIDisconnectedError, DDCCIError
from viewsonic_omni_tray.queue_worker import AsyncWriteQueueWorker, MonitorStatus
from viewsonic_omni_tray.notifications import NotificationEngine
from viewsonic_omni_tray.tray import create_tray_image
from viewsonic_omni_tray.app import SingleInstanceLock


class TestConfigManager(unittest.TestCase):
    """3. Katman: Profil ve Ayar Yönetimi Testleri"""

    def test_config_defaults(self):
        cfg = ConfigManager()
        self.assertEqual(cfg.monitor_match, "ViewSonic")
        self.assertIn("Gündüz", cfg.profiles)
        self.assertEqual(cfg.profiles["Gündüz"], 80)
        self.assertEqual(cfg.profiles["Gece"], 20)
        self.assertEqual(cfg.profiles["Okuma"], 40)
        self.assertEqual(cfg.profiles["Oyun"], 90)
        self.assertGreaterEqual(cfg.throttle_ms, 50)
        self.assertGreaterEqual(cfg.debounce_ms, 40)
        self.assertFalse(cfg.show_notifications)

    def test_config_step_clamping(self):
        cfg = ConfigManager()
        cfg.step_size = 50
        self.assertEqual(cfg.step_size, 25)
        cfg.step_size = -5
        self.assertEqual(cfg.step_size, 1)


class TestMonitorManager(unittest.TestCase):
    """1. Katman: Donanım Katmanı & VCP Capability Check Testleri"""

    def test_enumerate_monitors_and_capabilities(self):
        manager = MonitorManager()
        monitors = manager.enumerate_monitors()
        self.assertIsInstance(monitors, list)
        print(f"\n[Test] Bulunan fiziksel monitor sayisi: {len(monitors)}")
        if monitors:
            m = monitors[0]
            print(f"[Test] Monitor: {m.description}, Parlaklik: {m.current_brightness}")
            print(f"[Test] VCP Luminance Destegi: {m.supports_luminance}")
            if m.capabilities_string:
                print(f"[Test] Capabilities (ozet): {m.capabilities_string[:60]}...")
            self.assertTrue(m.min_brightness <= m.current_brightness <= m.max_brightness)
            self.assertTrue(m.supports_luminance)


class TestRateLimitingAndAsyncQueue(unittest.TestCase):
    """2. Katman: Rate-Limiting & Asenkron Kuyruk Testleri"""

    def test_debounce_and_coalescing(self):
        cfg = ConfigManager()
        worker = AsyncWriteQueueWorker(cfg)
        worker.debounce_sec = 0.05
        worker.throttle_sec = 0.08

        hardware_writes = []
        def mock_set_brightness(idx, val):
            hardware_writes.append((time.time(), val))
            return val

        worker.manager.set_brightness = MagicMock(side_effect=mock_set_brightness)
        fake_dev = MonitorDevice(0, 1234, "ViewSonic VX2718-2KPC", 0)
        worker.active_device = fake_dev
        worker.active_index = 0
        worker.status = MonitorStatus.CONNECTED

        worker._worker_thread.start()

        try:
            # 5 ardışık hızlı slider değişimi (5 ms aralıklarla)
            for v in [10, 20, 30, 40, 80]:
                worker.set_brightness(v, immediate_ui=False)
                time.sleep(0.005)

            # Worker'ın işlemi tamamlamasını bekle
            time.sleep(0.25)

            print(f"\n[Test] 5 hizli slider hareketinde donanima giden yazma sayisi: {len(hardware_writes)}")
            # Coalescing / Debounce sayesinde ara değerlerin (10, 20, 30, 40) elenip
            # donanıma yalnızca son hedef değer olan 80'in gitmiş olması gerekir!
            self.assertLessEqual(len(hardware_writes), 2)
            self.assertEqual(hardware_writes[-1][1], 80)

            # İkinci yazma: Throttle aralığını test et
            worker.set_brightness(50, immediate_ui=False)
            time.sleep(0.25)

            self.assertEqual(hardware_writes[-1][1], 50)
            if len(hardware_writes) >= 2:
                t1 = hardware_writes[-2][0]
                t2 = hardware_writes[-1][0]
                diff = t2 - t1
                print(f"[Test] Iki donanim yazimi arasi gecen sure: {diff*1000:.1f} ms (Min beklenen: {worker.throttle_sec*1000:.1f} ms)")
                self.assertGreaterEqual(diff, worker.throttle_sec - 0.02)

        finally:
            worker.stop()


class TestNotificationEngine(unittest.TestCase):
    """6. Katman: Bildirim ve Hata Yönetimi Testleri"""

    def test_notification_deduplication(self):
        cfg = ConfigManager()
        cfg.show_notifications = True

        mock_icon = MagicMock()
        engine = NotificationEngine(cfg, tray_icon_provider=lambda: mock_icon)

        # İlk bildirim gitmeli
        engine.send_notification("Test Başlık", "Monitör uykuya geçti", level="warning")
        self.assertEqual(mock_icon.notify.call_count, 1)

        # Hemen ardından gelen aynı mesaj spam olarak engellenmeli
        engine.send_notification("Test Başlık", "Monitör uykuya geçti", level="warning")
        self.assertEqual(mock_icon.notify.call_count, 1)

        # Farklı bir mesaj gelirse gitmeli
        engine.send_notification("Test Başlık", "Monitör tekrar bağlandı", level="info")
        self.assertEqual(mock_icon.notify.call_count, 2)


class TestTrayImageRendering(unittest.TestCase):
    """4. Katman: Tray UI Simge Üretimi & Dinamik Menü Testleri"""

    def test_image_dimensions_and_states(self):
        img_connected = create_tray_image(75, MonitorStatus.CONNECTED)
        self.assertEqual(img_connected.size, (64, 64))

        img_sleeping = create_tray_image(20, MonitorStatus.SLEEPING)
        self.assertEqual(img_sleeping.size, (64, 64))

        img_disc = create_tray_image(0, MonitorStatus.DISCONNECTED)
        self.assertEqual(img_disc.size, (64, 64))

    def test_dynamic_tray_menu(self):
        from viewsonic_omni_tray.tray import TrayApp
        cfg = ConfigManager()
        worker = AsyncWriteQueueWorker(cfg)
        worker.manager = MagicMock()
        worker.manager.enumerate_monitors.return_value = []
        tray = TrayApp(cfg, worker, MagicMock(), MagicMock(), MagicMock())
        menu = tray._build_menu()
        self.assertIsNotNone(menu)
        items = tray._build_menu_items()
        self.assertGreater(len(items), 5)


class TestSingleInstanceLock(unittest.TestCase):
    """Win32 Mutex Tekil Örnek Denetimi Testi"""

    def test_mutex_collision(self):
        lock1 = SingleInstanceLock("Global\\TestViewSonicOmni_PlanSuite_Mutex")
        self.assertTrue(lock1.acquire())

        lock2 = SingleInstanceLock("Global\\TestViewSonicOmni_PlanSuite_Mutex")
        self.assertFalse(lock2.acquire())
        self.assertTrue(lock2.already_running)

        lock1.release()
        lock2.release()


class TestDisplayModes(unittest.TestCase):
    """Ekran Modları (ViewMode) ve Kontrast Testleri"""

    def test_display_modes_definition(self):
        from viewsonic_omni_tray.monitor_manager import DISPLAY_MODES
        for required_mode in ["Oyun", "Film", "Metin", "Standart", "Gece"]:
            self.assertIn(required_mode, DISPLAY_MODES)
            data = DISPLAY_MODES[required_mode]
            self.assertIn("brightness", data)
            self.assertIn("contrast", data)
            self.assertIn("color_preset", data)
            self.assertTrue(0 <= data["brightness"] <= 100)
            self.assertTrue(0 <= data["contrast"] <= 100)

    def test_cycle_display_modes(self):
        from viewsonic_omni_tray.config import ConfigManager
        from viewsonic_omni_tray.queue_worker import AsyncWriteQueueWorker
        cfg = ConfigManager()
        worker = AsyncWriteQueueWorker(cfg)
        worker.manager = MagicMock()
        worker.active_device = MagicMock()
        worker.active_index = 0

        # Varsayılan mod
        self.assertEqual(worker.get_current_mode(), "Standart")

        # Döngü testi
        next_mode = worker.cycle_display_mode()
        self.assertEqual(next_mode, "Oyun")
        self.assertEqual(worker.get_current_mode(), "Oyun")
        self.assertEqual(worker.get_current_brightness(), 90)
        self.assertEqual(worker.get_current_contrast(), 75)


class TestMultiMonitorAndSync(unittest.TestCase):
    """Çoklu Monitör (ViewSonic + Rampage) ve Senkron Mod Testleri"""

    def test_monitor_device_brand_and_properties(self):
        dev_vs = MonitorDevice(
            index=0,
            hmonitor=1001,
            description="ViewSonic VX2718-2KPC",
            current_brightness=60,
            brand="ViewSonic",
            model="VX2718-2KPC"
        )
        self.assertTrue(dev_vs.is_viewsonic)
        self.assertFalse(dev_vs.is_rampage)
        self.assertEqual(dev_vs.short_name, "ViewSonic")

        dev_rp = MonitorDevice(
            index=1,
            hmonitor=1002,
            description="Rampage GL27R200Q",
            current_brightness=100,
            brand="Rampage",
            model="GL27R200Q"
        )
        self.assertFalse(dev_rp.is_viewsonic)
        self.assertTrue(dev_rp.is_rampage)
        self.assertEqual(dev_rp.short_name, "Rampage")

    def test_select_device_and_cycle(self):
        cfg = ConfigManager()
        worker = AsyncWriteQueueWorker(cfg)
        dev0 = MonitorDevice(0, 1001, "ViewSonic VX2718-2KPC", 60, brand="ViewSonic", model="VX2718")
        dev1 = MonitorDevice(1, 1002, "Rampage GL27R200Q", 100, brand="Rampage", model="GL27R200Q")
        worker.devices = [dev0, dev1]
        worker.active_device = dev0
        worker.active_index = 0

        # Rampage seç
        worker.select_device(1)
        self.assertEqual(worker.active_index, 1)
        self.assertEqual(worker.active_device.description, "Rampage GL27R200Q")
        self.assertEqual(worker.active_monitor_name, "Rampage GL27R200Q")

        # Senkron Mod seç (-1)
        worker.select_device(-1)
        self.assertEqual(worker.active_index, -1)
        self.assertIsNone(worker.active_device)
        self.assertEqual(worker.active_monitor_name, "Tüm Ekranlar (Senkron)")

        # Döngü: -1 -> 0
        n0 = worker.cycle_active_monitor()
        self.assertEqual(worker.active_index, 0)
        self.assertEqual(n0, "ViewSonic VX2718-2KPC")

        # Döngü: 0 -> 1
        n1 = worker.cycle_active_monitor()
        self.assertEqual(worker.active_index, 1)
        self.assertEqual(n1, "Rampage GL27R200Q")

        # Döngü: 1 -> -1 (Senkron)
        n_sync = worker.cycle_active_monitor()
        self.assertEqual(worker.active_index, -1)
        self.assertEqual(n_sync, "Tüm Ekranlar (Senkron)")

    def test_sync_hardware_write(self):
        cfg = ConfigManager()
        worker = AsyncWriteQueueWorker(cfg)
        worker.debounce_sec = 0.01
        worker.throttle_sec = 0.01

        dev0 = MonitorDevice(0, 1001, "ViewSonic VX2718-2KPC", 50)
        dev1 = MonitorDevice(1, 1002, "Rampage GL27R200Q", 50)
        worker.devices = [dev0, dev1]
        worker.select_device(-1)

        written_targets = []
        def mock_set_b(idx, val):
            written_targets.append((idx, val))
            return val

        worker.manager.set_brightness = MagicMock(side_effect=mock_set_b)
        worker._worker_thread.start()

        try:
            worker.set_brightness(75, immediate_ui=False)
            time.sleep(0.15)

            # Her iki monitöre de yazılmış olmalı!
            indices = [item[0] for item in written_targets]
            self.assertIn(0, indices)
            self.assertIn(1, indices)
            self.assertEqual(worker.get_current_brightness(), 75)
            self.assertEqual(dev0.current_brightness, 75)
            self.assertEqual(dev1.current_brightness, 75)
        finally:
            worker.stop()


if __name__ == "__main__":
    unittest.main()
