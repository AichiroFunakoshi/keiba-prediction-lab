#!/bin/bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
ICON_SOURCE="$SCRIPT_DIR/assets/macos/RaceWeave.png"
APP_NAME="RaceWeave"
APP_VERSION="0.1.0"
DIST_DIRECTORY="$SCRIPT_DIR/dist"
APP_BUNDLE="$DIST_DIRECTORY/$APP_NAME.app"
TEMP_BUILD_ROOT=""

pause_on_error() {
  status=$?
  case "$TEMP_BUILD_ROOT" in
    /private/tmp/raceweave-build.*)
      if [[ -d "$TEMP_BUILD_ROOT" ]]; then
        rm -rf "$TEMP_BUILD_ROOT"
      fi
      ;;
  esac
  if [[ $status -ne 0 ]]; then
    echo
    echo "RaceWeave.appを作成できませんでした。上のメッセージを確認してください。"
    read -r -p "Returnキーを押すと閉じます。" _ || true
  fi
  exit "$status"
}
trap pause_on_error EXIT

cd "$SCRIPT_DIR"

if [[ ! -x "$PYTHON" ]]; then
  echo "仮想環境 .venv が見つかりません。"
  echo "READMEのmacOSアプリ初期設定を完了してください。"
  exit 1
fi

if ! "$PYTHON" -c "import PIL, PyInstaller, webview" 2>/dev/null; then
  echo "デスクトップ用パッケージが見つかりません。"
  echo "source .venv/bin/activate"
  echo "python -m pip install -e '.[desktop]'"
  exit 1
fi

if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "アプリアイコンが見つかりません: $ICON_SOURCE"
  exit 1
fi

TEMP_BUILD_ROOT="$(mktemp -d /private/tmp/raceweave-build.XXXXXX)"
TEMP_APP="$TEMP_BUILD_ROOT/dist/$APP_NAME.app"

export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "jp.aichiro.raceweave" \
  --icon "$ICON_SOURCE" \
  --paths "$SCRIPT_DIR/src" \
  --hidden-import webview \
  --collect-data keiba_prediction_lab \
  --distpath "$TEMP_BUILD_ROOT/dist" \
  --workpath "$TEMP_BUILD_ROOT/build" \
  --specpath "$TEMP_BUILD_ROOT" \
  "$SCRIPT_DIR/scripts/raceweave_desktop.py"

if [[ ! -d "$TEMP_APP" ]]; then
  echo "アプリ本体が生成されませんでした: $TEMP_APP"
  exit 1
fi

/usr/libexec/PlistBuddy -c \
  "Set :CFBundleShortVersionString $APP_VERSION" \
  "$TEMP_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
  "Add :CFBundleVersion string 1" \
  "$TEMP_APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c \
  "Add :LSApplicationCategoryType string public.app-category.utilities" \
  "$TEMP_APP/Contents/Info.plist"
/usr/bin/xattr -cr "$TEMP_APP"
/usr/bin/codesign --force --deep --sign - "$TEMP_APP"
/usr/bin/codesign --verify --deep --strict "$TEMP_APP"

mkdir -p "$DIST_DIRECTORY"
if [[ -d "$APP_BUNDLE" ]]; then
  rm -rf "$APP_BUNDLE"
fi
/usr/bin/ditto "$TEMP_APP" "$APP_BUNDLE"
/usr/bin/codesign --verify --deep --strict "$APP_BUNDLE"

echo
echo "作成しました: $APP_BUNDLE"
echo "FinderからRaceWeave.appを開けます。"
