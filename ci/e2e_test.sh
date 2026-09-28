#!/bin/sh
# ============================================================================
#  离线端到端测试：把 postinst / postrm 跑在伪造的设备文件树上
#  用法: sh ci/e2e_test.sh <未改动的主二进制> <deb> [<spec.json>]
#
#  仅用于本地验证，不参与 CI 产物链路。
# ============================================================================
ORIG="$1"
DEB="$2"
SPEC="${3:-$(cd "$(dirname "$0")/.." && pwd)/spec.json}"
SIM="${UL_SIM:-/tmp/subsim}"

if [ -z "$ORIG" ] || [ -z "$DEB" ]; then
    echo "usage: sh ci/e2e_test.sh <orig_bin> <deb> [spec.json]"
    exit 1
fi
ORIG=$(cd "$(dirname "$ORIG")" && pwd)/$(basename "$ORIG")
case "$DEB" in /*) ;; *) DEB=$(cd "$(dirname "$DEB")" && pwd)/$(basename "$DEB") ;; esac

# App 名 / 二进制名从 spec.json 读，脚本本身不含任何目标特征
APP_NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['app_name'])" "$SPEC")
BIN_NAME=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['bin_name'])" "$SPEC")
echo "spec: app=$APP_NAME bin=$BIN_NAME"
APPDIR="$SIM/var/containers/Bundle/Application/AAA-BBBB/$APP_NAME.app"
BINPATH="$APPDIR/$BIN_NAME"

rm -rf "$SIM"
mkdir -p "$APPDIR"
mkdir -p "$SIM/var/jb/usr/bin" "$SIM/tmp" "$SIM/proc"

cp "$ORIG" "$BINPATH"
chmod 755 "$BINPATH"
cp "$ORIG" "$SIM/orig.bin"

# 模拟越狱环境自带的 ldid
if [ -x /usr/local/bin/ldid ]; then
    cp /usr/local/bin/ldid "$SIM/var/jb/usr/bin/ldid"
    chmod 755 "$SIM/var/jb/usr/bin/ldid"
fi

echo "=========== 解包 DEB ==========="
dpkg-deb -x "$DEB" "$SIM/extracted" 2>/dev/null
find "$SIM/extracted" -type f | sed "s|$SIM/extracted||"

echo
echo "=========== 把脚本里的绝对路径指向模拟树（仅测试用）==========="
for which in postinst postrm; do
    dpkg-deb --ctrl-tarfile "$DEB" 2>/dev/null | tar -xO "./$which" > "$SIM/$which.raw"
    sed \
        -e "s|/var/containers/Bundle/Application|$SIM/var/containers/Bundle/Application|g" \
        -e "s|/private/var/containers/Bundle/Application|$SIM/private/var/containers/Bundle/Application|g" \
        -e "s|/var/jb/usr/bin/ldid /var/jb/bin/ldid /usr/bin/ldid /usr/local/bin/ldid|$SIM/var/jb/usr/bin/ldid|" \
        -e "s|STATE_DIR=\"/tmp/.ulmod\"|STATE_DIR=\"$SIM/tmp/.ulmod\"|" \
        -e "s|/var/jb/bin/launchctl /var/jb/usr/bin/launchctl /bin/launchctl /usr/bin/launchctl|$SIM/none/launchctl|" \
        -e "s|/var/jb/usr/bin/killall /var/jb/bin/killall /usr/bin/killall /bin/killall|$SIM/none/killall|" \
        -e "s|/proc/\[0-9\]\*|$SIM/proc/[0-9]*|g" \
        "$SIM/$which.raw" > "$SIM/$which.sim"
    sh -n "$SIM/$which.sim" || { echo "!! $which 语法错误"; exit 1; }
done
echo "postinst / postrm 语法 OK"

BIN="$BINPATH"
ENT_BEFORE=$("$SIM/var/jb/usr/bin/ldid" -e "$BIN" 2>/dev/null | wc -c)
echo "改写前: sha256=$(sha256sum "$BIN" | cut -d' ' -f1)  entitlements=$ENT_BEFORE B"

echo
echo "=========== 运行 postinst ==========="
SIMROOT="$SIM" sh "$SIM/postinst.sim"
echo "退出码=$?"

echo
echo "=========== 核对补丁 ==========="
python3 - "$BIN" "$SPEC" <<'PYEOF'
import struct, sys, hashlib, json
d = open(sys.argv[1], 'rb').read()
spec = json.load(open(sys.argv[2]))
base = int(spec['image_base'], 16)
ok = 0
for p in spec['patches']:
    off = int(p['vaddr'], 16) - base
    got = d[off:off + 4].hex()
    good = got == p['new']
    ok += good
    print('  vaddr %s  off %-9d %s  %s'
          % (p['vaddr'], off, got, 'OK' if good else 'MISMATCH'))
print('applied = %d / %d' % (ok, len(spec['patches'])))
print('sha256 =', hashlib.sha256(d).hexdigest())
PYEOF

ENT_AFTER=$("$SIM/var/jb/usr/bin/ldid" -e "$BIN" 2>/dev/null | wc -c)
echo "改写后 entitlements = $ENT_AFTER B （应 >0）"

echo
echo "=========== 幂等性：再跑一次 postinst ==========="
SIMROOT="$SIM" sh "$SIM/postinst.sim" | tail -8

echo
echo "=========== 运行 postrm 还原 ==========="
SIMROOT="$SIM" sh "$SIM/postrm.sim"

echo
echo "=========== 还原校验（语义级）==========="
python3 "$(dirname "$0")/verify_restore.py" "$BIN" "$SIM/orig.bin" "$SPEC"
rc=$?
echo
[ "$rc" = "0" ] && echo "★★★★★ 端到端全部通过" || echo "!!!!! 存在问题"
exit $rc
