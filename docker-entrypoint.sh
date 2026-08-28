#!/bin/bash
set -e

# 清理陈旧 X 锁/套接字与残留进程：docker restart 会复用容器写入层，
# 上次运行留下的 /tmp/.X99-lock 会让 Xvfb 启动失败（Server is already active），
# 导致 headed 浏览器 "Failed to launch"。这里做成幂等，restart 也能干净起显示。
pkill -9 Xvfb 2>/dev/null || true
pkill -9 x11vnc 2>/dev/null || true
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true

# 启动虚拟显示
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
export DISPLAY=:99

# 等待 Xvfb 就绪（套接字出现，最多 ~10s；比固定 sleep 1 更稳）
for _ in $(seq 1 20); do
    [ -S /tmp/.X11-unix/X99 ] && break
    sleep 0.5
done

# 启动 x11vnc（无密码，仅本地 VNC）
if [ -n "$VNC_PASSWORD" ]; then
    x11vnc -display :99 -rfbauth <(x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vncpass && echo /tmp/vncpass) -forever -shared &
else
    x11vnc -display :99 -nopw -forever -shared &
fi

# 启动 noVNC（端口 6080 -> VNC 5900）
websockify --web=/usr/share/novnc 6080 localhost:5900 &

# 启动 FastAPI 后端
exec uvicorn main:app --host 0.0.0.0 --port 8000
