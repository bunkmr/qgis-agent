"""生成 QGIS Agent 插件图标"""

import os
import struct
import zlib


def create_png(width, height, pixels):
    """创建 PNG 图片

    pixels: 字节数组，每行宽度*4字节(RGBA)
    """
    def write_chunk(chunk_type, data):
        chunk = chunk_type + data
        return struct.pack('>I', len(data)) + chunk + struct.pack('>I', zlib.crc32(chunk) & 0xffffffff)

    # PNG signature
    signature = b'\x89PNG\r\n\x1a\n'

    # IHDR chunk
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    ihdr = write_chunk(b'IHDR', ihdr_data)

    # IDAT chunk
    raw_data = b''
    for y in range(height):
        raw_data += b'\x00'  # filter none
        raw_data += pixels[y * width * 4:(y + 1) * width * 4]
    compressed = zlib.compress(raw_data)
    idat = write_chunk(b'IDAT', compressed)

    # IEND chunk
    iend = write_chunk(b'IEND', b'')

    return signature + ihdr + idat + iend


def generate_icon():
    """生成64x64图标"""
    w, h = 64, 64
    pixels = bytearray(w * h * 4)

    def set_pixel(x, y, r, g, b, a=255):
        if 0 <= x < w and 0 <= y < h:
            idx = (y * w + x) * 4
            pixels[idx] = r
            pixels[idx +1] = g
            pixels[idx +2] = b
            pixels[idx +3] = a

    def draw_rounded_rect(x1, y1, x2, y2, r, cr, cg, cb):
        for y in range(y1, y2 +1):
            for x in range(x1, x2 +1):
                # Check corners
                if x < x1 +r and y < y1 +r:
                    dx, dy = x - (x1 +r), y - (y1 +r)
                    if dx *dx + dy *dy > r *r:
                        continue
                elif x > x2 -r and y < y1 +r:
                    dx, dy = x - (x2 -r), y - (y1 +r)
                    if dx *dx + dy *dy > r *r:
                        continue
                elif x < x1 +r and y > y2 -r:
                    dx, dy = x - (x1 +r), y - (y2 -r)
                    if dx *dx + dy *dy > r *r:
                        continue
                elif x > x2 -r and y > y2 -r:
                    dx, dy = x - (x2 -r), y - (y2 -r)
                    if dx *dx + dy *dy > r *r:
                        continue
                set_pixel(x, y, cr, cg, cb)

    def draw_line(x1, y1, x2, y2, r, g, b, a=128):
        dx = x2 - x1
        dy = y2 - y1
        steps = max(abs(dx), abs(dy))
        if steps == 0:
            set_pixel(x1, y1, r, g, b, a)
            return
        for i in range(steps +1):
            x = x1 + dx * i // steps
            y = y1 + dy * i // steps
            set_pixel(x, y, r, g, b, a)

    def fill_triangle(x1, y1, x2, y2, x3, y3, r, g, b, a):
        """Simple triangle fill using scanline"""
        ys = sorted([(y1, x1), (y2, x2), (y3, x3)], key=lambda p: p[0])
        if ys[0][0] == ys[2][0]:
            return
        for y in range(ys[0][0], ys[2][0] +1):
            if y < 0 or y >= h:
                continue
            xa = xb = 0
            for i in range(2):
                if y >= ys[i][0] and y <= ys[i +1][0]:
                    t = (y - ys[i][0]) / (ys[i +1][0] - ys[i][0]) if ys[i +1][0] != ys[i][0] else 0
                    xa = int(ys[i][1] + t * (ys[i +1][1] - ys[i][1]))
            if y >= ys[0][0] and y <= ys[2][0]:
                t = (y - ys[0][0]) / (ys[2][0] - ys[0][0]) if ys[2][0] != ys[0][0] else 0
                xb = int(ys[0][1] + t * (ys[2][1] - ys[0][1]))
            x_min, x_max = min(xa, xb), max(xa, xb)
            for x in range(max(0, x_min), min(w, x_max +1)):
                set_pixel(x, y, r, g, b, a)

    def fill_circle(cx, cy, rad, r, g, b, a=255):
        for y in range(max(0, cy -rad), min(h, cy +rad +1)):
            for x in range(max(0, cx -rad), min(w, cx +rad +1)):
                if (x -cx) *(x -cx) + (y -cy) *(y -cy) <= rad *rad:
                    set_pixel(x, y, r, g, b, a)

    # 背景 (圆角矩形)
    draw_rounded_rect(2, 2, 61, 61, 10, 46, 134, 193)

    # 地图网格线
    for y in range(12, 56, 8):
        draw_line(8, y, 56, y, 93, 173, 226, 60)
    for x in range(12, 56, 8):
        draw_line(x, 8, x, 56, 93, 173, 226, 60)

    # 地图多边形 (地形 - 三角形组合)
    fill_triangle(14, 48, 22, 30, 30, 38, 130, 224, 170, 100)
    fill_triangle(22, 30, 30, 38, 38, 26, 130, 224, 170, 100)
    fill_triangle(30, 38, 38, 26, 46, 34, 130, 224, 170, 100)
    fill_triangle(38, 26, 46, 34, 50, 48, 130, 224, 170, 100)
    draw_line(14, 48, 22, 30, 39, 174, 96)
    draw_line(22, 30, 30, 38, 39, 174, 96)
    draw_line(30, 38, 38, 26, 39, 174, 96)
    draw_line(38, 26, 46, 34, 39, 174, 96)
    draw_line(46, 34, 50, 48, 39, 174, 96)

    # 节点
    fill_circle(22, 30, 3, 244, 208, 63)
    fill_circle(30, 38, 2, 244, 208, 63)
    fill_circle(38, 26, 2, 244, 208, 63)
    fill_circle(46, 34, 2, 244, 208, 63)

    # AI 星星 (火花)
    star_cx, star_cy = 38, 18
    for dx, dy in [(0, -5), (0, 5), (-5, 0), (5, 0), (-3, -3), (3, 3), (-3, 3), (3, -3)]:
        fill_circle(star_cx +dx, star_cy +dy, 1, 249, 231, 159)

    # "AI" 文字 - 简单像素文字
    # A
    for i in range(4):
        set_pixel(24 +i, 52 -i, 255, 255, 255, 200)
        set_pixel(24 +i, 52 +i, 255, 255, 255, 200)
    for x in range(24, 28):
        set_pixel(x, 50, 255, 255, 255, 200)
    # I
    for y in range(49, 53):
        set_pixel(32, y, 255, 255, 255, 200)
    set_pixel(30, 52, 255, 255, 255, 200)
    set_pixel(34, 52, 255, 255, 255, 200)

    return create_png(w, h, bytes(pixels))


if __name__ == "__main__":
    png_data = generate_icon()
    icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
    with open(icon_path, "wb") as f:
        f.write(png_data)
    print(f"图标已生成: {icon_path}")
