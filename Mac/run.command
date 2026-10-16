#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")"
finish_with_error() {
  if [[ -t 0 ]]; then
    read "?按 Enter 關閉此視窗…"
  fi
  exit 1
}
if [[ ! -x .venv/bin/python ]]; then
  print "尚未建立 App 的 Python 環境，請先依 Mac/README.md 安裝。"
  print "專案目錄：$PWD"
  finish_with_error
fi
if .venv/bin/python main.py; then
  exit 0
else
  finish_with_error
fi
