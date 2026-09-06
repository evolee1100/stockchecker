#!/bin/bash
# 把本機變更推上去，並自動處理跟雲端排程的衝突。
#
# 為什麼需要這個：雲端排程一天跑三次，每次都會把 data/ 的快照與翻譯快取 commit 回 repo。
# 本機開發時只要也跑過 fetch.py，兩邊就會撞在同一批檔案上。這裡定義了怎麼合：
#   data/translations.json  → 兩邊聯集（翻譯是累積的，選一邊會掉資料）
#   data/history/*.json     → 取本機（同一天的快照，本機的比較新）
set -e
cd "$(dirname "$0")"

git pull --rebase origin main || true

while [ -n "$(git diff --name-only --diff-filter=U)" ]; do
  for f in $(git diff --name-only --diff-filter=U); do
    case "$f" in
      data/translations.json)
        python3 - <<'PY'
import json, subprocess
def side(st):
    o = subprocess.run(['git','show',st+':data/translations.json'], capture_output=True)
    return json.loads(o.stdout.decode()) if o.returncode == 0 else {}
m = side(':2'); m.update(side(':3'))
json.dump(m, open('data/translations.json','w'), ensure_ascii=False, indent=0, sort_keys=True)
print('  翻譯快取合併 → %d 筆' % len(m))
PY
        ;;
      *) git checkout --theirs "$f" 2>/dev/null || true; echo "  取本機版本：$f" ;;
    esac
    git add "$f"
  done
  GIT_EDITOR=true git rebase --continue || break
done

git push origin main
echo "已推送"
