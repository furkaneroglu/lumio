#!/usr/bin/env python3
"""
Lumio - Build Script
PyInstaller kullanarak bağımsız tek dosya (.exe) derler.
"""

import os
import sys
import subprocess
import shutil

# Konsol UTF-8 ayarı
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(ROOT_DIR, "lumio", "main.py")
ICON_FILE = os.path.join(ROOT_DIR, "lumio", "app_icon.ico")
EXE_NAME = "Lumio"

def build():
    print("=" * 60)
    print("[*] Lumio - EXE Derleme Başlatılıyor...")
    print("=" * 60)

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", EXE_NAME,
        "--icon", ICON_FILE,
        MAIN_SCRIPT
    ]

    print("Çalıştırılan komut:")
    print(" ".join(cmd))
    print("-" * 60)

    res = subprocess.run(cmd, cwd=ROOT_DIR)
    if res.returncode != 0:
        print("\n[-] Hata: Derleme başarısız oldu!")
        sys.exit(res.returncode)

    built_exe = os.path.join(ROOT_DIR, "dist", f"{EXE_NAME}.exe")
    final_exe = os.path.join(ROOT_DIR, f"{EXE_NAME}.exe")

    if os.path.exists(built_exe):
        shutil.copy2(built_exe, final_exe)
        print(f"\n[+] Başarılı! Bağımsız EXE oluşturuldu:\n   -> {final_exe}")

    print("[*] Geçici derleme dosyaları temizleniyor...")
    for folder in ["build", "dist"]:
        p = os.path.join(ROOT_DIR, folder)
        if os.path.exists(p):
            shutil.rmtree(p, ignore_errors=True)
    spec_file = os.path.join(ROOT_DIR, f"{EXE_NAME}.spec")
    if os.path.exists(spec_file):
        try:
            os.remove(spec_file)
        except OSError:
            pass

    print("[+] İşlem tamamlandı!")

if __name__ == "__main__":
    build()
