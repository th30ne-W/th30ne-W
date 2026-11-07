from dataclasses import dataclass

__all__ = ["Theme", "DARK", "LIGHT", "THEMES"]


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    accent: str
    text: str
    muted: str
    glow: bool


DARK = Theme("dark", bg="#0d0d0d", accent="#00ff00", text="#e6ffe6", muted="#9fe9a8", glow=True)
LIGHT = Theme("light", bg="#eef2f1", accent="#087a37", text="#0d0d0d", muted="#2f5d43", glow=False)
THEMES = (DARK, LIGHT)
