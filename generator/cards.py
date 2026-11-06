from .content import Project
from .fonts import font_face
from .svg import GLOW_FILTER, document, esc
from .theme import Theme

ORBITRON = "Orbitron, 'Arial Black', sans-serif"
BODY_FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO_FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

WIDTH, HEIGHT = 410, 220
WRAP_AT = 54
MAX_LINES = 5
FIRST_LINE_Y = 70
LINE_PITCH = 18
PILL_RIGHT = 390
PILL_HEIGHT = 20
KIND_LABELS = {"app": "APP", "private": "PRIVATE"}


def wrap(text: str, limit: int = WRAP_AT) -> list[str]:
    """Greedy word wrap; a word longer than `limit` gets a line to itself."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if line and len(candidate) > limit:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _style(theme: Theme) -> str:
    # classes: ti=title, k=kind label, d=description line, g=tags
    return "".join(
        [
            font_face(500),
            font_face(700),
            f".ti{{font-family:{ORBITRON};font-weight:700;font-size:20px}}",
            f".k{{font-family:{ORBITRON};font-weight:500;font-size:10px;text-anchor:middle}}",
            f".d{{font-family:{BODY_FONT};font-size:13px}}",
            f".g{{font-family:{MONO_FONT};font-size:12px}}",
        ]
    )


def render_card(project: Project, theme: Theme) -> str:
    lines = wrap(project.description)
    if len(lines) > MAX_LINES:
        raise ValueError(
            f"{project.slug}: description wraps to {len(lines)} lines at {WRAP_AT} characters, max is {MAX_LINES}"
        )

    label = KIND_LABELS.get(project.kind)
    if label is None:
        raise ValueError(f"{project.slug}: unknown kind {project.kind!r}")
    pill_width = len(label) * 8 + 16
    pill_left = PILL_RIGHT - pill_width

    glow = ' filter="url(#glow)"' if theme.glow else ""
    body = []
    if theme.glow:
        body.append(f"<defs>{GLOW_FILTER}</defs>")
    body.append(
        f'<rect x="0.75" y="0.75" width="{WIDTH - 1.5:g}" height="{HEIGHT - 1.5:g}" rx="10" '
        f'fill="{theme.bg}" stroke="{theme.accent}" stroke-width="1.5"/>'
    )
    body.append(f'<text class="ti" x="20" y="40" fill="{theme.accent}"{glow}>{esc(project.title.upper())}</text>')
    body.append(
        f'<rect x="{pill_left}" y="12" width="{pill_width}" height="{PILL_HEIGHT}" rx="{PILL_HEIGHT // 2}" '
        f'fill="none" stroke="{theme.accent}"/>'
        f'<text class="k" x="{pill_left + pill_width / 2:g}" y="26" fill="{theme.accent}">{label}</text>'
    )
    for i, line in enumerate(lines):
        y = FIRST_LINE_Y + i * LINE_PITCH
        body.append(f'<text class="d" x="20" y="{y}" fill="{theme.text}">{esc(line)}</text>')
    body.append(f'<text class="g" x="20" y="200" fill="{theme.muted}">{esc(project.tags)}</text>')

    return document(WIDTH, HEIGHT, project.title, project.description, _style(theme), "\n".join(body))
