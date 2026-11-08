# Lumio

**Brand-independent monitor control for Windows over DDC/CI.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-blue.svg)](https://microsoft.com/windows)
[![Python](https://img.shields.io/badge/Python-3.8%2B-brightgreen.svg)](https://python.org)

A lightweight system tray app that controls any DDC/CI-compliant external monitor (Dell, LG, Samsung, ASUS, BenQ, ViewSonic, AOC, …) over HDMI / DisplayPort using the VESA DDC/CI (MCCS) protocol: brightness, contrast, screen modes, speaker volume, input source and standby — with a minimal Windows 11 style flyout, global hotkeys and automation.

---

## Quick Start

1. Download **`Lumio.exe`** from the [Releases](../../releases) page (or the latest [Actions](../../actions) build).
2. Double-click it. The app appears in the system tray (near the clock).
3. Click the tray icon for the control panel; right-click for the full menu.
4. *(Optional)* Settings → **Windows ile başlat** to start with Windows.

---

## Features

* **Minimal flyout panel** — antialiased sliders for brightness, contrast and volume (mouse wheel supported), one-click screen modes and input switching, monitor tabs for multi-monitor setups. Rounded Windows 11 corners and native Fluent icons.
* **Screen modes (VCP 0x10 / 0x12 / 0x14)** — Standart, Oyun, Film, Metin, Gece.
* **Automatic brightness schedule** — set brightness per time of day (e.g. 08:00 → 70%, 23:00 → 25%) with smooth transitions. Manual changes are respected until the next scheduled time.
* **Per-app screen modes** — switch to *Oyun* when a game is in front, *Film* for your video player; the previous settings are restored when you leave. Pick apps directly from a list of open windows.
* **Multi-monitor sync with calibration** — control all monitors at once ("Tümü") and set a per-monitor brightness offset so different panels look equally bright.
* **Remembers your choices** — last selected monitor and each monitor's screen mode survive restarts.
* **Works with any brand** — inputs and features are read from each monitor's own MCCS capability report; unused legacy inputs (VGA/DVI) are hidden by default and can be toggled per monitor.
* **Input switching (VCP 0x60)**, **speaker volume & mute (VCP 0x62 / 0x8D)** when supported, **standby (VCP 0xD6)**.
* **Global hotkeys + on-screen indicator** — compact OSD pill that fades out.
* **Hardware protection** — debounce (80 ms) and throttling (100 ms) keep the monitor's I2C bus safe; a watchdog recovers after sleep or disconnects.

---

## Default Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Ctrl + Alt + ↑ / ↓` | Brightness up / down |
| `Ctrl + Alt + → / ←` | Contrast up / down |
| `Ctrl + Alt + V` | Cycle screen mode |
| `Ctrl + Alt + G` | Game mode |
| `Ctrl + Alt + 1 … 4` | Profiles (Gündüz, Gece, Okuma, Oyun) |
| `Ctrl + Alt + PgUp / PgDn` | Volume up / down |
| `Ctrl + Alt + M` | Mute / unmute |
| `Ctrl + Alt + I` | Cycle input source |
| `Ctrl + Alt + S` | Cycle active monitor / all monitors |
| `Ctrl + Alt + P` | Put monitor to standby |

All shortcuts can be changed in Settings or in `%APPDATA%\Lumio\config.json` (settings from older versions are migrated automatically).

---

## Project Structure

```
viewsonic-omni-brightness/
├── .github/workflows/        # CI: tests + EXE build on every push, release on tags
├── lumio/
│   ├── main.py               # Entry point
│   ├── app.py                # Coordinator & single-instance mutex
│   ├── monitor_manager.py    # DDC/CI hardware layer (dxva2 / user32 via ctypes)
│   ├── queue_worker.py       # Async write queue, debounce/throttle, watchdog
│   ├── automation.py         # Brightness schedule & per-app screen modes
│   ├── config.py             # JSON settings & Windows startup entry
│   ├── ui_kit.py             # Shared modern UI components (slider, segmented, toggle…)
│   ├── popup.py              # Tray flyout panel
│   ├── osd.py                # On-screen indicator
│   ├── settings.py           # Settings window
│   ├── tray.py               # Tray icon & context menu (pystray)
│   ├── hotkeys.py            # Global hotkeys (pynput)
│   └── notifications.py      # Tray notifications with spam filter
├── tests/                    # Unit tests
├── build.py                  # PyInstaller one-file build
└── requirements.txt
```

---

## Development

```bash
git clone https://github.com/furkaneroglu/viewsonic-omni-brightness.git
cd viewsonic-omni-brightness
pip install -r requirements.txt

python -m lumio.main          # run from source
python -m unittest discover -s tests -v     # run tests (uses a temporary settings folder)
python build.py                              # build Lumio.exe
```

Requirements: Windows 10/11, Python 3.8+, monitor connected directly to the GPU over HDMI/DP, and **DDC/CI enabled** in the monitor's OSD menu (`Setup Menu → DDC/CI → On`).

---

## Türkçe Özet

Lumio, DDC/CI destekleyen her marka harici monitörün parlaklık, kontrast, ekran modu, ses ve giriş kaynağını sistem tepsisinden yöneten hafif bir Windows uygulamasıdır. Giriş kaynakları ve özellikler her monitörün kendi bildirdiği yetenek listesinden okunur.

* **Sade kontrol paneli:** Tepsi simgesine tıklayın; kaydırıcılar fare tekerleğiyle de çalışır.
* **Otomatik parlaklık:** Günün saatine göre parlaklık yavaşça değişir. Elle değiştirirseniz bir sonraki saate kadar karışmaz.
* **Uygulamaya göre mod:** Oyun açılınca Oyun moduna, film oynatıcıda Film moduna geçer; çıkınca eski ayarlar geri gelir.
* **İki ekranı eşitleme:** "Tümü" modunda her ekrana parlaklık farkı verilebilir.
* **Hatırlama:** Son seçilen monitör ve ekran modları yeniden başlatmada korunur.

**Sorun giderme:** Monitör bulunamazsa monitörün kendi menüsünden `DDC/CI` ayarının **Açık** olduğunu ve kablonun doğrudan ekran kartına bağlı olduğunu kontrol edin (bazı adaptörler/hub'lar DDC/CI sinyalini kesebilir).

---

## License

[MIT](LICENSE)
