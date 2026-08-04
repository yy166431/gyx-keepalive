#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重打包 IPA（分两步，跨平台纯 Python，签名在 CI 的 macOS 上做）。

子命令:
  prep <src_ipa> <KeepAlive.dylib> <silence.wav> <work_dir>
      解包到 <work_dir>，放入 dylib+音频，向主程序注入 LC_LOAD_DYLIB，
      Info.plist 加 UIBackgroundModes=audio，并导出原 entitlements 到
      <work_dir>/entitlements.plist（供 codesign 用）。
      注意：不在此处签名——改过主程序后必须由 macOS 的 codesign 重签整个 app，
      否则 iOS 加载校验失败，表现为"能装但打不开/闪退"。

  pack <work_dir> <out_ipa>
      把 <work_dir>/Payload 打包成 IPA（保留目录条目，贴合原始结构）。

单机一步式（仅用于本机结构验证，产物无有效签名、不能真机跑）:
  repack.py <src_ipa> <dylib> <silence> <out_ipa>
"""
import sys, os, zipfile, shutil, plistlib, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))

def find_app_dir(payload):
    for name in os.listdir(payload):
        if name.endswith('.app'):
            return os.path.join(payload, name)
    raise RuntimeError('Payload 下找不到 .app')

def add_background_audio(info_plist_path):
    with open(info_plist_path, 'rb') as f:
        pl = plistlib.load(f)
    modes = pl.get('UIBackgroundModes', [])
    if 'audio' not in modes:
        modes.append('audio')
    pl['UIBackgroundModes'] = modes
    with open(info_plist_path, 'wb') as f:
        plistlib.dump(pl, f)
    print('  Info.plist: UIBackgroundModes =', modes)

def prep(src_ipa, dylib, silence, work):
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    with zipfile.ZipFile(src_ipa) as z:
        z.extractall(work)
    payload = os.path.join(work, 'Payload')
    app = find_app_dir(payload)

    info = os.path.join(app, 'Info.plist')
    with open(info, 'rb') as f:
        pl = plistlib.load(f)
    exe_name = pl['CFBundleExecutable']
    exe_path = os.path.join(app, exe_name)
    print('主程序:', exe_name)

    shutil.copy(dylib, os.path.join(app, 'KeepAlive.dylib'))
    shutil.copy(silence, os.path.join(app, 'silence.wav'))
    print('  已放入 KeepAlive.dylib + silence.wav')

    subprocess.check_call([sys.executable,
                           os.path.join(HERE, 'insert_dylib.py'),
                           '@executable_path/KeepAlive.dylib',
                           exe_path, exe_path])

    add_background_audio(info)
    print('  prep 完成，app 目录:', app)
    print('  下一步需在 macOS 上 codesign 整个 app，再 pack')

def pack(work, out_ipa):
    payload = os.path.join(work, 'Payload')
    if os.path.exists(out_ipa):
        os.remove(out_ipa)
    with zipfile.ZipFile(out_ipa, 'w', zipfile.ZIP_DEFLATED) as z:
        zi = zipfile.ZipInfo('Payload/')
        zi.external_attr = (0o40755 << 16)
        z.writestr(zi, b'')
        for root, dirs, files in os.walk(payload):
            for d in dirs:
                full = os.path.join(root, d)
                rel = os.path.relpath(full, work).replace(os.sep, '/') + '/'
                zi = zipfile.ZipInfo(rel)
                zi.external_attr = (0o40755 << 16)
                z.writestr(zi, b'')
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, work).replace(os.sep, '/')
                z.write(full, rel)
    print('完成 ->', out_ipa)

def main():
    a = sys.argv[1:]
    if len(a) >= 1 and a[0] == 'prep':
        prep(a[1], a[2], a[3], a[4]); return
    if len(a) >= 1 and a[0] == 'pack':
        pack(a[1], a[2]); return
    # 兼容旧的一步式（无签名，仅结构验证）
    if len(a) == 4:
        work = os.path.abspath('_repack_work')
        prep(a[0], a[1], a[2], work)
        pack(work, a[3])
        shutil.rmtree(work, ignore_errors=True)
        return
    print(__doc__); sys.exit(1)

if __name__ == '__main__':
    main()
