"""Colour grade presets, split into a source-resolution half (before the zoom warp) and an output half (after)."""
from __future__ import annotations

# Shadow lift that reproduces the approved "night" look (fitted from the delivered Seltos edit); pure black stays black.
_NIGHT_LIFT = {
    "r": "0/0 0.0157/0.0353 0.0314/0.0471 0.0471/0.0667 0.0627/0.0863 0.0941/0.1098 0.1255/0.1451 0.1882/0.2078 0.2510/0.2641 0.3765/0.3869 0.5020/0.5098 0.6275/0.6384 0.7529/0.7569 0.8784/0.8869 1/1",
    "g": "0/0 0.0157/0.0353 0.0314/0.0471 0.0471/0.0667 0.0627/0.0784 0.0941/0.1098 0.1255/0.1373 0.1882/0.2039 0.2510/0.2614 0.3765/0.3843 0.5020/0.5098 0.6275/0.6350 0.7529/0.7529 0.8784/0.8784 1/1",
    "b": "0/0 0.0157/0.0353 0.0314/0.0471 0.0471/0.0706 0.0627/0.0784 0.0941/0.1098 0.1255/0.1373 0.1882/0.2000 0.2510/0.2627 0.3765/0.3843 0.5020/0.5135 0.6275/0.6350 0.7529/0.7569 0.8784/0.8804 1/1",
}
TO_RGB = "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24"
TO_YUV = "scale=in_range=pc:out_color_matrix=bt709:out_range=tv,format=yuv420p"


def auto_gamma(mean_luma: float, grade: str) -> float:
    """Exposure lift for dark phone footage (video-range gamma, applied before the grade)."""
    if grade == "none":
        return 1.0
    g = 1.28 if mean_luma < 12 else 1.16 if mean_luma < 25 else 1.06 if mean_luma < 40 else 1.0
    return g if grade == "night" else 1 + (g - 1) * 0.6


def pre_chain(grade: str, gamma: float, hot: bool) -> list[str]:
    """Colour ops at source resolution; ends in rgb24 for the warp. hot = after the drop (more punch)."""
    lift = f"lutyuv=y='16+219*pow(clip((val-16)/219,0,1),1/{gamma:.3f})'"  # 16 stays 16: lifts shadows, keeps blacks
    if grade == "night":
        return [lift, f"eq=saturation={1.30 if hot else 1.10}", TO_RGB,
                "colorbalance=rs=-0.03:gs=-0.01:bs=0.05:rh=0.04:bh=-0.03",
                "curves=all='0/0 0.1/0.09 0.5/0.53 0.75/0.8 1/1'" if hot else "curves=all='0/0 0.1/0.09 0.5/0.53 0.9/0.93 1/1'"]
    if grade == "natural":
        return [lift, f"eq=saturation={1.12 if hot else 1.05}", TO_RGB, "curves=all='0/0 0.25/0.235 0.5/0.5 0.75/0.765 1/1'"]
    return [TO_RGB]


def post_chain(grade: str) -> list[str]:
    """Output-resolution finishing: rgb24 in, yuv420p out."""
    if grade == "night":
        m = _NIGHT_LIFT
        return [f"curves=r='{m['r']}':g='{m['g']}':b='{m['b']}'", TO_YUV,
                "vignette=angle=PI/5", "unsharp=5:5:0.3:5:5:0", "noise=c0s=4:c0f=t+u"]
    if grade == "natural":
        return [TO_YUV, "vignette=angle=PI/7", "unsharp=5:5:0.25:5:5:0", "noise=c0s=2:c0f=t+u"]
    return [TO_YUV, "unsharp=5:5:0.2:5:5:0"]
