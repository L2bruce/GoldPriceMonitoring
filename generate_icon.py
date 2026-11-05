"""
macOS 应用图标生成脚本

规范说明：
在 macOS Big Sur (macOS 11+) 及之后版本中，macOS 统一了应用图标的设计网格 (Icon Grid)：
- 1024x1024 的画布尺寸下，圆角矩形主体尺寸为 824x824 像素（占画布宽高的约 80.5%）；
- 主体居中放置（上下左右留白各约 100 像素），并配有微弱柔和的环境光阴影；
- 如果直接将图标主体以 1024x1024 铺满画布，在 Finder、Launchpad 等处会比系统原生应用大整整一圈。

本脚本读取满幅原图 `build/icon_raw.png`，按 macOS 规范生成缩放后的 824x824 主体与阴影，
并输出 1024x1024 规范图标、全套分辨率 iconset 以及编译后的 `build/icon.icns`。
"""
import os
import shutil
import subprocess
from PIL import Image, ImageFilter
import numpy as np


def generate_standard_macos_icon():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    build_dir = os.path.join(base_dir, 'build')
    raw_path = os.path.join(build_dir, 'icon_raw.png')
    src_path = os.path.join(build_dir, 'icon_1024.png')

    if not os.path.exists(raw_path):
        if os.path.exists(src_path):
            shutil.copy2(src_path, raw_path)
        else:
            raise FileNotFoundError(f"找不到图标源文件: {raw_path}")

    src_image = Image.open(raw_path).convert('RGBA')

    canvas_size = 1024
    body_size = 824  # macOS 官方网格标准主体大小 (824x824)

    # 高质量下采样到 824x824
    body = src_image.resize((body_size, body_size), Image.Resampling.LANCZOS)

    offset_x = (canvas_size - body_size) // 2  # 100
    offset_y = (canvas_size - body_size) // 2  # 100

    # 创建符合 macOS 设计语言的环境光投影
    shadow_offset_y = 10
    shadow_blur = 18
    shadow_alpha = 0.28

    alpha_body = body.split()[3]
    shadow_mask = Image.new('L', (canvas_size, canvas_size), 0)
    shadow_mask.paste(alpha_body, (offset_x, offset_y + shadow_offset_y))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur))

    shadow_mask_arr = np.array(shadow_mask).astype(np.float32) * shadow_alpha
    shadow_mask_img = Image.fromarray(shadow_mask_arr.astype(np.uint8))

    shadow = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
    black = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 255))
    shadow.paste(black, (0, 0), shadow_mask_img)

    # 合成 1024x1024 主画布
    canvas = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
    canvas = Image.alpha_composite(canvas, shadow)
    canvas.paste(body, (offset_x, offset_y), body)

    # 保存新的 icon_1024.png
    canvas.save(src_path, 'PNG')
    print(f"==> 保存规范的 1024x1024 图标: {src_path}")

    # 生成各分辨率的 icon.iconset
    iconset_dir = os.path.join(build_dir, 'icon.iconset')
    if os.path.exists(iconset_dir):
        shutil.rmtree(iconset_dir)
    os.makedirs(iconset_dir, exist_ok=True)

    specs = [
        ('icon_16x16.png', 16),
        ('icon_16x16@2x.png', 32),
        ('icon_32x32.png', 32),
        ('icon_32x32@2x.png', 64),
        ('icon_128x128.png', 128),
        ('icon_128x128@2x.png', 256),
        ('icon_256x256.png', 256),
        ('icon_256x256@2x.png', 512),
        ('icon_512x512.png', 512),
        ('icon_512x512@2x.png', 1024),
    ]

    for filename, sz in specs:
        resized = canvas.resize((sz, sz), Image.Resampling.LANCZOS)
        resized.save(os.path.join(iconset_dir, filename), 'PNG')

    print(f"==> 已生成全部 10 种分辨率 iconset")

    # 编译 icns
    icns_path = os.path.join(build_dir, 'icon.icns')
    subprocess.run(['iconutil', '-c', 'icns', iconset_dir, '-o', icns_path], check=True)
    print(f"==> 编译完成: {icns_path}")


if __name__ == '__main__':
    generate_standard_macos_icon()

