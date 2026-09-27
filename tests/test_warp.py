"""Zoom precision: a static dot grid pushed in 1.05 -> 1.5 must follow the ideal path with no frame-to-frame jerk.
(ffmpeg zoompan fails this: ~1.2 px/frame stair-stepping; that was the 'zoom glitch' this renderer exists to fix.)"""
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from beatcut.render import warp_rect

SW, SH, W, H, N = 576, 1024, 540, 960, 31


def test_zoom_is_subpixel_smooth():
    im = Image.new("RGB", (SW, SH), "black")
    d = ImageDraw.Draw(im)
    dots = [(x, y) for x in range(48, SW, 96) for y in range(64, SH, 128)]
    for x, y in dots:
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill="white")
    shot = {"zoom": [1.05, 1.5], "anchor": [0.48, 0.52], "fx": []}
    tracks: dict = {}
    errs = []
    for k in range(N):
        x0, y0, cw, ch = warp_rect(shot, SW, SH, W, H, k, N, 0)
        a = np.asarray(im.transform((W, H), Image.AFFINE, (cw / W, 0, x0, 0, ch / H, y0), Image.BICUBIC).convert("L"), np.float32)
        lab, n = ndimage.label(a > 100)
        cs = np.array(ndimage.center_of_mass(a, lab, range(1, n + 1)))
        for X, Y in dots:
            ix, iy = (X + 0.5 - x0) * W / cw - 0.5, (Y + 0.5 - y0) * H / ch - 0.5
            if 60 < ix < W - 60 and 60 < iy < H - 60:
                j = int(np.argmin((cs[:, 1] - ix) ** 2 + (cs[:, 0] - iy) ** 2))
                errs.append(np.hypot(cs[j, 1] - ix, cs[j, 0] - iy))
                tracks.setdefault((X, Y), {})[k] = cs[j]
    assert np.mean(errs) < 0.15
    jerks = [np.abs(np.diff(np.array([t[k] for k in range(N)]), 2, axis=0)).max() for t in tracks.values() if len(t) == N]
    assert jerks and np.median(jerks) < 0.45


def test_anchor_point_stays_put_and_punch_eases_out():
    shot = {"zoom": [1.0, 1.6], "anchor": [0.3, 0.7], "fx": []}
    for k in (0, 10, 30):
        x0, y0, cw, ch = warp_rect(shot, SW, SH, W, H, k, 31, 0)
        assert abs((0.3 * SW - x0) / cw - 0.3) < 1e-9 and abs((0.7 * SH - y0) / ch - 0.7) < 1e-9
    punch = {"zoom": [1.0, 1.0], "anchor": [0.5, 0.5], "fx": ["punch"]}
    widths = [warp_rect(punch, SW, SH, W, H, k, 30, 0)[2] for k in range(10)]
    assert widths[0] < widths[4] < widths[8] == widths[9]  # zoomed in on the hit, eases back, then steady
