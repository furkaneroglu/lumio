#!/usr/bin/env python3
"""
ViewSonic OMNI DDC/CI Tray Application Entry Point
"""

import sys
import os
import traceback

# Windows konsolunda Unicode/Emoji çökmesini önle
if hasattr(sys, "stdout") and sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if hasattr(sys, "stderr") and sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Paket dizinini Python yoluna ekle
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from viewsonic_omni_tray.app import ViewSonicOmniApp
from viewsonic_omni_tray.config import get_config_dir

def main():
    try:
        app = ViewSonicOmniApp()
        app.run()
    except Exception as e:
        # Olası bir hatada error.log dosyasına yaz
        log_path = os.path.join(get_config_dir(), "error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- Hata Raporu ---\n")
            traceback.print_exc(file=f)
        raise e

if __name__ == "__main__":
    main()
