#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
生成全套 dpkg 维护脚本（preinst / postinst / prerm / postrm）。

为什么必须这么做：
  dylib 是**进程启动时**由 Substrate/ElleKit 注入的。装完包若目标进程还在跑，
  它用的是旧映像 —— dylib 文件在磁盘上却从未被加载，现象与「插件无效」完全相同。

为什么不能只用裸 killall：
  roothide 的 bootstrap 工具**只接受 jbroot-based 路径**，
  而 dpkg 维护脚本的 PATH 里未必有 killall → 静默失败。
  ⇒ 必须按「绝对路径候选」逐个探测，并用 /proc 兜底。

纪律：
  · 纯 POSIX sh（bootstrap 的 /bin/sh 可能是 ash）
  · 开头 trap 兜底，任何情况下都不能阻断 dpkg
  · 结束后用 /proc 轮询确认进程确实退出
"""
import os
import sys

COMMON = r'''#!/bin/sh
# 任何异常都不得阻断 dpkg
trap 'exit 0' EXIT

LOG="/tmp/.tweak_maint.log"
log() { echo "[%s] $*" >> "$LOG" 2>/dev/null || true; }

# 在多个候选路径里找可执行文件。
# roothide 的 bootstrap 工具只接受 jbroot-based 路径，所以候选里既有系统路径
# 也有 /var/jb 前缀，最后再用 jbroot 工具换算一次。
find_bin() {
  _name="$1"
  for _c in \
      "/usr/bin/$_name" "/bin/$_name" "/usr/sbin/$_name" "/sbin/$_name" \
      "/var/jb/usr/bin/$_name" "/var/jb/bin/$_name" \
      "/var/jb/usr/sbin/$_name" "/var/jb/sbin/$_name" ; do
    if [ -x "$_c" ]; then echo "$_c"; return 0; fi
  done
  for _j in /usr/bin/jbroot /var/jb/usr/bin/jbroot /bin/jbroot ; do
    if [ -x "$_j" ]; then
      _r=$("$_j" "/usr/bin/$_name" 2>/dev/null || true)
      if [ -n "$_r" ] && [ -x "$_r" ]; then echo "$_r"; return 0; fi
    fi
  done
  return 1
}

# 通过 /proc 判断进程是否存活
alive() {
  _p="$1"
  [ -d /proc ] || return 1
  for _d in /proc/[0-9]* ; do
    [ -r "$_d/comm" ] || continue
    if [ "$(cat "$_d/comm" 2>/dev/null)" = "$_p" ]; then return 0; fi
  done
  return 1
}

# 关闭进程：killall -> kill -9 -> /proc 兜底，最多 3 轮
kill_proc() {
  _p="$1"
  _ka=$(find_bin killall || true)
  _i=0
  while [ "$_i" -lt 3 ]; do
    if ! alive "$_p"; then return 0; fi
    if [ -n "$_ka" ]; then
      "$_ka" "$_p" >/dev/null 2>&1 || true
      sleep 1 2>/dev/null || true
      if ! alive "$_p"; then return 0; fi
      "$_ka" -9 "$_p" >/dev/null 2>&1 || true
      sleep 1 2>/dev/null || true
      if ! alive "$_p"; then return 0; fi
    fi
    if [ -d /proc ]; then
      for _d in /proc/[0-9]* ; do
        [ -r "$_d/comm" ] || continue
        if [ "$(cat "$_d/comm" 2>/dev/null)" = "$_p" ]; then
          _pid=${_d#/proc/}
          kill -9 "$_pid" >/dev/null 2>&1 || true
        fi
      done
      sleep 1 2>/dev/null || true
      if ! alive "$_p"; then return 0; fi
    fi
    _i=$(( _i + 1 ))
  done
  return 1
}

PROCS="@@PROCS@@"
kill_all() {
  for p in $PROCS; do
    [ -n "$p" ] || continue
    log "killing $p"
    kill_proc "$p" || log "$p still alive"
  done
}
'''

TAIL = r'''
log "@@STAGE@@"
kill_all
exit 0
'''

# 四个阶段都关闭进程：安装前后与卸载前后都不能让旧映像继续跑
STAGES = ['preinst', 'postinst', 'prerm', 'postrm']


def main():
    procs = os.environ.get('TARGET_PROCS', '').strip()
    if not procs:
        print('!! 缺少 TARGET_PROCS（逗号分隔的进程名）', file=sys.stderr)
        return 1
    out = sys.argv[1] if len(sys.argv) > 1 else 'layout/DEBIAN'
    os.makedirs(out, exist_ok=True)

    body = COMMON.replace('@@PROCS@@', procs.replace(',', ' ').strip())
    for stage in STAGES:
        path = os.path.join(out, stage)
        with open(path, 'w') as f:
            f.write(body + TAIL.replace('@@STAGE@@', stage))
        os.chmod(path, 0o755)
        print('  %-10s %6d B  mode=755' % (stage, os.path.getsize(path)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
