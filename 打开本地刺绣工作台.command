#!/bin/zsh
set -eu

cd -- "${0:A:h}"
studio_url="http://127.0.0.1:8876"
studio_python="$PWD/.venv/bin/python"
studio_log="$HOME/Library/Logs/EmbroideryStudio/studio.log"

if [[ ! -x "$studio_python" ]]; then
  print -u2 "找不到工作台的 Python 环境，请把启动器放在 embroidery-lab 项目文件夹中。"
  exit 1
fi

studio_ready() {
  /usr/bin/curl --noproxy '*' --fail --silent --max-time 2 "$studio_url/api/projects" |
    "$studio_python" -c 'import json, sys; d=json.load(sys.stdin); sys.exit(not (d.get("engine_version") and isinstance(d.get("projects"),list)))' 2>/dev/null
}

if ! studio_ready; then
  mkdir -p -- "${studio_log:h}"
  nohup "$studio_python" studio_server.py --port 8876 </dev/null >>"$studio_log" 2>&1 &
  studio_pid=$!
  for attempt in {1..30}; do
    if studio_ready; then break; fi
    if ! kill -0 "$studio_pid" 2>/dev/null; then break; fi
    sleep 0.5
  done
  if ! studio_ready; then
    print -u2 "本地工作台启动失败，详情保存在 $studio_log"
    exit 1
  fi
fi

print "本地刺绣工作台已就绪 $studio_url"
exec /usr/bin/open "$studio_url/"
