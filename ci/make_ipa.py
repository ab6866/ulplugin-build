#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把补丁注入原始 IPA，并用原 entitlements 重签。

流程：解包 → 逐点校验并改写主二进制 → 删 FairPlay 票据(SC_Info) → 用原
entitlements adhoc 重签 → 重新压缩为 IPA。

环境变量：UL_LDID（可选，指定 ldid 路径）
"""
import os, sys, json, zipfile, struct, shutil, subprocess, hashlib, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = json.load(open(os.path.join(ROOT, 'spec.json')))


def find_binary(names):
    for n in names:
        if n.endswith('.app/') or '/.app/' in n:
            continue
    return None


def main():
    if len(sys.argv) < 3:
        print('usage: patch_ipa.py <in.ipa> <out.ipa>')
        return 1
    src, dst = sys.argv[1], sys.argv[2]
    app_name = SPEC['app_name']
    bin_name = SPEC['bin_name']
    base = int(SPEC['image_base'], 16)
    ldid = os.environ.get('UL_LDID') or shutil.which('ldid')

    tmp = tempfile.mkdtemp(prefix='ulmodipa')
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(tmp)

        payload = os.path.join(tmp, 'Payload')
        appdir = os.path.join(payload, app_name + '.app')
        if not os.path.isdir(appdir):
            cands = [d for d in os.listdir(payload) if d.endswith('.app')]
            if not cands:
                print('!! Payload 下找不到 .app'); return 1
            appdir = os.path.join(payload, cands[0])
        bpath = os.path.join(appdir, bin_name)
        if not os.path.isfile(bpath):
            print('!! 找不到主二进制 %s' % bpath); return 1

        data = bytearray(open(bpath, 'rb').read())

        # ---- entitlements（重签必须显式传，否则会被抹掉）----
        ent = None
        if ldid:
            r = subprocess.run([ldid, '-e', bpath], capture_output=True)
            if r.returncode == 0 and len(r.stdout) > 10:
                ent = os.path.join(tmp, 'entitlements.plist')
                open(ent, 'wb').write(r.stdout)
                print('已获取原 entitlements (%d B)' % len(r.stdout))

        # ---- 逐点改写 ----
        applied = 0
        for p in SPEC['patches']:
            off = int(p['vaddr'], 16) - base
            want = bytes.fromhex(p['want'])
            new = bytes.fromhex(p['new'])
            got = bytes(data[off:off + 4])
            if got == new:
                print('  [OK  ] %#x 已是目标字节' % off); applied += 1; continue
            if got != want:
                print('  [SKIP] %#x 原字节 %s != %s' % (off, got.hex(), p['want'])); continue
            data[off:off + 4] = new
            if bytes(data[off:off + 4]) == new:
                print('  [OK  ] %#x %s -> %s' % (off, p['want'], p['new'])); applied += 1
            else:
                print('  [ERR ] %#x 回读不一致' % off)
        print('applied = %d / %d' % (applied, len(SPEC['patches'])))
        if applied != len(SPEC['patches']):
            print('!! 未全部命中，不输出')
            return 2

        open(bpath, 'wb').write(bytes(data))
        os.chmod(bpath, 0o755)

        # ---- 清 FairPlay 票据 ----
        sc = os.path.join(appdir, 'SC_Info')
        if os.path.isdir(sc):
            shutil.rmtree(sc)
            print('已删除 SC_Info')

        # ---- 重签 ----
        if ldid and ent:
            r = subprocess.run([ldid, '-S' + ent, bpath], capture_output=True)
            if r.returncode == 0:
                print('★ 已用原 entitlements 重签')
            else:
                print('!! 重签失败: %s' % r.stderr.decode()[:200]); return 3
        else:
            print('!! 无 ldid，IPA 未签名（需自行签名后安装）')

        # ---- 重新压缩 ----
        if os.path.exists(dst):
            os.remove(dst)
        zf = zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED)
        for root_, _dirs, files in os.walk(tmp):
            for f in files:
                full = os.path.join(root_, f)
                zf.write(full, os.path.relpath(full, tmp))
        zf.close()
        print('written %s (%d B) sha256=%s' % (dst, os.path.getsize(dst),
              hashlib.sha256(open(dst, 'rb').read()).hexdigest()))
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


sys.exit(main())
