#!/data/data/com.termux/files/usr/bin/sh
set -eu

if ! command -v pkg >/dev/null 2>&1; then
    echo "This installer is for Termux." >&2
    exit 1
fi

pkg update -y
pkg install -y python python-pip android-tools clang openssl
python -m pip install setuptools wheel
python -m pip install --no-build-isolation "sqlcipher3==0.6.2"
python -m pip install --upgrade "https://github.com/edward9s/reddex/archive/refs/heads/main.zip"

echo "reddex installed."
