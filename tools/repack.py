#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重打包 IPA：把编译好的 KeepAlive.dylib 与 silence.wav 装进 App，
向主程序注入 LC_LOAD_DYLIB，并在 Info.plist 里加 UIBackgroundModes=audio。
产出新的 *_keepalive.ipa。

用法:
    python repack.py <原始ipa> <KeepAlive.dylib> <silence.wav> <输出ipa>

跨平台纯 Python，无需 mac 工具链。签名交给 TrollStore 落地时处理。
"""
import sys, os, zipfile, shutil, struct, plistlib, tempfile, subprocess

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

def main():
    if len(sys.argv) < 5:
        print(__doc__); sys.exit(1)
    src_ipa, dylib, silence, out_ipa = sys.argv[1:5]

    work = tempfile.mkdtemp(prefix='gyx_')
    try:
        # 1. 解包
        with zipfile.ZipFile(src_ipa) as z:
            z.extractall(work)
        payload = os.path.join(work, 'Payload')
        app = find_app_dir(payload)
        exe_name = None

        info = os.path.join(app, 'Info.plist')
        with open(info, 'rb') as f:
            pl = plistlib.load(f)
        exe_name = pl['CFBundleExecutable']
        exe_path = os.path.join(app, exe_name)
        print('主程序:', exe_name)

        # 2. 拷贝保活 dylib + 静音音频进 App
        shutil.copy(dylib, os.path.join(app, 'KeepAlive.dylib'))
        shutil.copy(silence, os.path.join(app, 'silence.wav'))
        print('  已放入 KeepAlive.dylib + silence.wav')

        # 3. 注入 LC_LOAD_DYLIB
        subprocess.check_call([sys.executable,
                               os.path.join(HERE, 'insert_dylib.py'),
                               '@executable_path/KeepAlive.dylib',
                               exe_path, exe_path])

        # 4. 加后台音频模式
        add_background_audio(info)

        # 5. 保留原始 _CodeSignature 不动。
        #    巨魔安装需要包内已有一份可用的签名结构；删掉会导致装不上。
        #    我们只新增文件 + 注入，签名交给巨魔安装时处理。
        print('  保留原始 _CodeSignature（巨魔安装需要）')

        # 6. 重新打包 —— 保留目录条目，尽量贴近原始 IPA 结构
        if os.path.exists(out_ipa):
            os.remove(out_ipa)
        with zipfile.ZipFile(out_ipa, 'w', zipfile.ZIP_DEFLATED) as z:
            # 顶层 Payload/ 目录条目
            zi = zipfile.ZipInfo('Payload/')
            zi.external_attr = (0o40755 << 16)
            z.writestr(zi, b'')
            for root, dirs, files in os.walk(payload):
                # 先写目录条目（以 / 结尾），和原始 IPA 一致
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
    finally:
        shutil.rmtree(work, ignore_errors=True)

if __name__ == '__main__':
    main()
