"""
Lumio - Comprehensive Test Suite
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

import tempfile

# Testler kullanıcının gerçek ayar dosyasına (%APPDATA%) dokunmasın: geçici klasöre yönlendir
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="vs_omni_test_")

repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)

from lumio.config import ConfigManager, DEFAULT_CONFIG
from lumio.monitor_manager import MonitorManager, MonitorDevice, DDCCIDisconnectedError, DDCCIError
from lumio.queue_worker import AsyncWriteQueueWorker, MonitorStatus
from lumio.notifications import NotificationEngine
from lumio.tray import create_tray_image
from lumio.app import SingleInstanceLock


class TestConfigManager(unittest.TestCase):
    """3. Katman: Profil ve Ayar Yönetimi Testleri"""

    def test_config_defaults(self):
        cfg = ConfigManager()
        self.assertEqual(cfg.monitor_match, "")
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
        from lumio.tray import TrayApp
        cfg = ConfigManager()
        worker = AsyncWriteQueueWorker(cfg)
        worker.manager = MagicMock()
        worker.manager.enumerate_monitors.return_value = []
        tray = TrayApp(cfg, worker, MagicMock(), MagicMock(), MagicMock())
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
        from lumio.monitor_manager import DISPLAY_MODES
        for required_mode in ["Oyun", "Film", "Metin", "Standart", "Gece"]:
            self.assertIn(required_mode, DISPLAY_MODES)
            data = DISPLAY_MODES[required_mode]
            self.assertIn("brightness", data)
            self.assertIn("contrast", data)
            self.assertIn("color_preset", data)
            self.assertTrue(0 <= data["brightness"] <= 100)
            self.assertTrue(0 <= data["contrast"] <= 100)

    def test_cycle_display_modes(self):
        from lumio.config import ConfigManager
        from lumio.queue_worker import AsyncWriteQueueWorker
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
    """Çoklu Monitör ve Senkron Mod Testleri (marka bağımsız)"""

    def test_monitor_device_short_name(self):
        dev = MonitorDevice(0, 1001, "Dell U2720Q", 60, brand="Dell", model="U2720Q")
        self.assertEqual(dev.short_name, "Dell")
        generic = MonitorDevice(1, 1002, "Generic PnP Monitor", 60)
        self.assertEqual(generic.short_name, "Ekran 2")

    def test_capabilities_parsing_and_inputs(self):
        from lumio.monitor_manager import parse_vcp_capabilities
        # Gerçek monitörlerden alınmış yetenek dizgileri (boşluksuz / düzensiz yazımlar dahil)
        caps_a = ("(prot(monitor)type(LCD)model(RTK)cmds(01 02 03 07 0C E3 F3)vcp(02 04 05 06 08 0B 0C 10 12 "
                  "14(01 02 04 05 06 08 0B) 16 18 1A 52 60(01 03 04 0F 10 11 12) 87 AC AE B2 B6 C6 C8 CA "
                  "CC(01 02 03 04 06 0A 0D) D6(01 04 05) DF FD FF)mswhql(1)asset_eep(40)mccs_ver(2.2))")
        caps_b = ("(prot(monitor)type(lcd)MStarcmds(01 02 03 07 0C E3 F3)vcp(02 04 05 08 10 12 14(05 06 08 0B 0C 0D)"
                  "16 18 1A 60( 11 12 0F 10) 62 AC AE B2 B6 C6 C8 C9 D6(01 04 05)  DF FD FF)mccs_ver(2.1)mswhql(1))")
        parsed_a = parse_vcp_capabilities(caps_a)
        parsed_b = parse_vcp_capabilities(caps_b)
        self.assertEqual(parsed_a[0x60], [0x01, 0x03, 0x04, 0x0F, 0x10, 0x11, 0x12])
        self.assertEqual(parsed_b[0x60], [0x11, 0x12, 0x0F, 0x10])
        self.assertIn(0x16, parsed_b)          # '...0D)16' boşluksuz yazım
        self.assertNotIn(0x62, parsed_a)
        self.assertIn(0x62, parsed_b)
        self.assertNotIn(0x01, parsed_a)       # cmds(01 ...) vcp listesine karışmamalı

        dev = MonitorDevice(0, 1, "Test", 50, current_input=0x12, capabilities_string=caps_a)
        self.assertEqual(dev.reported_inputs, [0x01, 0x03, 0x04, 0x0F, 0x10, 0x11, 0x12])
        self.assertEqual(dev.default_visible_inputs, [0x0F, 0x10, 0x11, 0x12])  # VGA / DVI varsayılan gizli
        self.assertTrue(dev.supports(0x10))
        self.assertFalse(dev.supports(0x62))

        no_caps = MonitorDevice(1, 2, "Test 2", 50, current_input=0x1B)
        self.assertEqual(no_caps.reported_inputs, [0x0F, 0x11, 0x12, 0x1B])     # varsayılan + etkin giriş

    def test_visible_inputs_user_choice_and_sync(self):
        cfg = ConfigManager()
        worker = AsyncWriteQueueWorker(cfg, MagicMock())
        caps = "vcp(10 60(0F 10 11 12))"
        a = MonitorDevice(0, 1, "Monitor A", 50, current_input=0x11, capabilities_string=caps)
        b = MonitorDevice(1, 2, "Monitor B", 50, current_input=0x0F, capabilities_string="vcp(10 60(0F 11))")
        worker.devices = [a, b]
        worker.select_device(0)
        self.assertEqual(worker.visible_inputs(), [0x0F, 0x10, 0x11, 0x12])
        cfg.set_visible_inputs("Monitor A", [0x11, 0x0F])
        self.assertEqual(worker.visible_inputs(), [0x0F, 0x11])
        worker.select_device(-1)
        self.assertEqual(worker.visible_inputs(), [0x0F, 0x11])  # ortak girişler
        cfg.set_visible_inputs("Monitor A", None)

    def test_legacy_config_is_migrated(self):
        import json, shutil
        from lumio import config as config_module
        appdata = os.environ["APPDATA"]
        new_dir = os.path.join(appdata, config_module.APP_NAME)
        old_dir = os.path.join(appdata, "ViewSonicOmniTray")
        backup = None
        new_path = os.path.join(new_dir, "config.json")
        if os.path.exists(new_path):
            backup = open(new_path, encoding="utf-8").read()
            os.remove(new_path)
        try:
            os.makedirs(old_dir, exist_ok=True)
            with open(os.path.join(old_dir, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"step_size": 7}, f)
            cfg = ConfigManager()
            self.assertEqual(cfg.step_size, 7)
            self.assertTrue(os.path.exists(new_path))
        finally:
            shutil.rmtree(old_dir, ignore_errors=True)
            if backup is not None:
                with open(new_path, "w", encoding="utf-8") as f:
                    f.write(backup)

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


class TestAutomation(unittest.TestCase):
    """Otomatik parlaklık (saat planı), uygulama kuralları ve ekran eşitleme testleri"""

    def tearDown(self):
        # Testler birbirini etkilemesin: geçici ayar dosyasını sil (sonraki ConfigManager varsayılanla başlar)
        from lumio.config import get_config_path
        try:
            os.remove(get_config_path())
        except OSError:
            pass

    def _fake_worker(self, brightness=50, mode="Standart", contrast=70):
        state = {"b": brightness, "m": mode, "c": contrast}
        w = MagicMock()
        w.get_current_brightness.side_effect = lambda: state["b"]
        w.get_current_mode.side_effect = lambda: state["m"]
        w.get_current_contrast.side_effect = lambda: state["c"]
        w.set_brightness.side_effect = lambda v, immediate_ui=True: state.__setitem__("b", v)

        def _apply(mode_name, brightness=None, contrast=None, wait_timeout=0.0):
            from lumio.monitor_manager import DISPLAY_MODES
            state["m"] = mode_name
            state["b"] = brightness if brightness is not None else DISPLAY_MODES[mode_name]["brightness"]
            state["c"] = contrast if contrast is not None else DISPLAY_MODES[mode_name]["contrast"]
            return True
        w.apply_display_mode.side_effect = _apply
        return w, state

    def test_schedule_interpolation_wraps_midnight(self):
        from lumio.automation import normalize_points, scheduled_brightness
        pts = normalize_points([{"time": "08:00", "brightness": 70}, {"time": "23:00", "brightness": 20},
                                {"time": "hatalı", "brightness": 5}])
        self.assertEqual(pts, [(480, 70), (1380, 20)])
        self.assertEqual(scheduled_brightness(pts, 3 * 60, 30), 20)       # gece: dünden kalan değer
        self.assertEqual(scheduled_brightness(pts, 8 * 60 + 15, 30), 45)  # geçişin ortası
        self.assertEqual(scheduled_brightness(pts, 12 * 60, 30), 70)
        self.assertEqual(scheduled_brightness(pts, 8 * 60, 0), 70)        # anında geçiş

    def test_schedule_engine_respects_manual_override(self):
        from datetime import datetime
        from lumio.automation import ScheduleEngine
        cfg = ConfigManager()
        cfg.schedule.update({"enabled": True, "transition_min": 0,
                             "points": [{"time": "08:00", "brightness": 70}, {"time": "20:00", "brightness": 30}]})
        worker, state = self._fake_worker(brightness=50)
        engine = ScheduleEngine(cfg, worker)

        engine.tick(datetime(2026, 1, 1, 12, 0))
        self.assertEqual(state["b"], 70)

        state["b"] = 40  # kullanıcı elle değiştirdi
        engine.tick(datetime(2026, 1, 1, 13, 0))
        self.assertEqual(state["b"], 40)
        self.assertTrue(engine.is_overridden)

        engine.tick(datetime(2026, 1, 1, 20, 1))  # bir sonraki plan saati geldi
        self.assertEqual(state["b"], 30)
        self.assertFalse(engine.is_overridden)

    def test_app_rule_applies_and_restores(self):
        from lumio.automation import AppRuleEngine, ScheduleEngine
        cfg = ConfigManager()
        cfg.app_rules.update({"enabled": True, "rules": [{"process": "Game", "mode": "Oyun"}]})
        cfg.schedule["enabled"] = False
        worker, state = self._fake_worker(brightness=33, mode="Metin", contrast=44)
        probe = MagicMock()
        engine = AppRuleEngine(cfg, worker, ScheduleEngine(cfg, worker), probe=probe)

        probe.foreground.return_value = (1234, "game.exe")
        engine.tick()
        self.assertEqual(state["m"], "Oyun")
        self.assertEqual(engine.active_process, "game.exe")

        probe.foreground.return_value = (os.getpid(), "python.exe")  # kendi penceremiz: değişiklik yok
        engine.tick()
        self.assertEqual(state["m"], "Oyun")

        probe.foreground.return_value = (999, "notepad.exe")
        engine.tick()
        self.assertEqual((state["m"], state["b"], state["c"]), ("Metin", 33, 44))
        self.assertIsNone(engine.active_process)

    def test_sync_mode_applies_calibration_offsets(self):
        cfg = ConfigManager()
        cfg.calibration["Rampage GL27R200Q"] = -10
        worker = AsyncWriteQueueWorker(cfg, MagicMock())
        worker.debounce_sec = 0.01
        worker.throttle_sec = 0.01
        dev0 = MonitorDevice(0, 1, "ViewSonic VX2718-2KPC", 50)
        dev1 = MonitorDevice(1, 2, "Rampage GL27R200Q", 40)
        worker.devices = [dev0, dev1]
        worker.select_device(-1)
        self.assertEqual(worker.get_current_brightness(), 50)  # (50 + (40+10)) / 2

        writes = {}
        worker.manager.set_brightness.side_effect = lambda idx, val: writes.__setitem__(idx, val) or val
        worker._worker_thread.start()
        try:
            worker.set_brightness(80, immediate_ui=False)
            time.sleep(0.2)
            self.assertEqual(writes, {0: 80, 1: 70})
        finally:
            worker.stop()

    def test_last_selected_monitor_is_restored(self):
        cfg = ConfigManager()
        cfg.last_state["monitor"] = "Rampage GL27R200Q"
        manager = MagicMock()
        manager.enumerate_monitors.return_value = [
            MonitorDevice(0, 1, "ViewSonic VX2718-2KPC", 50),
            MonitorDevice(1, 2, "Rampage GL27R200Q", 40),
        ]
        worker = AsyncWriteQueueWorker(cfg, manager)
        worker.rescan_monitors()
        self.assertEqual(worker.active_index, 1)


if __name__ == "__main__":
    unittest.main()
