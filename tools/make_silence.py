#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成一个合法的静音 WAV 文件（silence.wav），供保活播放使用。
时长默认 2 秒、44.1kHz、单声道、16-bit PCM，全零采样即无声。
AVAudioPlayer 用 numberOfLoops=-1 无限循环它。

用法: python make_silence.py <输出路径> [秒数]
"""
import sys, struct, wave

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'silence.wav'
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    rate = 44100
    nframes = int(rate * seconds)
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)      # mono
        w.setsampwidth(2)      # 16-bit
        w.setframerate(rate)
        w.writeframes(b'\x00\x00' * nframes)  # 全零 = 静音
    print('已生成静音音频: %s (%.1fs, %dHz)' % (out, seconds, rate))

if __name__ == '__main__':
    main()
