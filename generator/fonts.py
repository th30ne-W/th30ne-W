import base64
from functools import cache
from pathlib import Path

__all__ = ["font_face"]

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
WEIGHTS = (500, 700)


@cache
def font_face(weight: int) -> str:
    """Return an @font-face rule embedding the Orbitron latin subset for `weight`."""
    if weight not in WEIGHTS:
        raise ValueError(f"unsupported Orbitron weight {weight!r}; expected one of {WEIGHTS}")
    data = (FONTS_DIR / f"orbitron-{weight}.woff2").read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return (
        f"@font-face{{font-family:Orbitron;font-weight:{weight};"
        f'src:url(data:font/woff2;base64,{b64}) format("woff2")}}'
    )
