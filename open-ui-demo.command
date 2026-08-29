#!/bin/bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPOSITORY_ROOT="$SCRIPT_DIR"
PYTHON="$REPOSITORY_ROOT/.venv/bin/python"
DEMO_DIRECTORY="$REPOSITORY_ROOT/local/ui-demo"

pause_on_error() {
  status=$?
  if [[ $status -ne 0 ]]; then
    echo
    echo "UIを起動できませんでした。上のメッセージを確認してください。"
    read -r -p "Returnキーを押すと閉じます。" _ || true
  fi
  exit "$status"
}
trap pause_on_error EXIT

cd "$REPOSITORY_ROOT"

if [[ ! -x "$PYTHON" ]]; then
  echo "仮想環境 .venv が見つかりません。"
  echo "READMEの初期設定を完了してから、もう一度実行してください。"
  exit 1
fi

export PYTHONPATH="$REPOSITORY_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ ! -d "$DEMO_DIRECTORY" ]]; then
  echo "初回用の合成UIデータを作成しています。"
  "$PYTHON" -m keiba_prediction_lab.cli init-ui-demo \
    --output "$DEMO_DIRECTORY"
fi

echo
echo "競馬予想研究UIを起動します。"
echo "これは合成デモです。実レースの予想や購入判断には使用できません。"
echo "終了するときは、この画面で Control-C を押してください。"
echo

"$PYTHON" -m keiba_prediction_lab.cli serve-ui-demo "$DEMO_DIRECTORY"
