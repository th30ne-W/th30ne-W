import random
from xml.sax.saxutils import escape

from .theme import Theme

GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾅﾆﾇﾈﾉﾊﾋﾌﾍﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789:=*+"

RAIN_FONT = "13px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
LINE_HEIGHT = 15

GLOW_FILTER = (
    '<filter id="glow" x="-20%" y="-20%" width="140%" height="140%">'
    '<feGaussianBlur stdDeviation="2" result="b"/>'
    '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
    "</filter>"
)


def esc(text: str) -> str:
    return escape(text)


def document(width: int, height: int, title: str, desc: str, style: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="t d">\n'
        f'<title id="t">{esc(title)}</title>\n'
        f'<desc id="d">{esc(desc)}</desc>\n'
        f"<style>{style}</style>\n"
        f"{body}\n"
        "</svg>\n"
    )


def _num(value: float) -> str:
    return f"{round(value, 2):g}"


def rain_columns(
    rng: random.Random,
    count: int,
    width: int,
    height: int,
    glyphs_per_column: int,
    pitch: float,
    animated: bool,
    theme: Theme,
) -> str:
    """Matrix rain: a <style> block followed by `count` glyph columns.

    `width` is the canvas width the columns are laid across (count * pitch, give or
    take); column x positions derive from `pitch` alone. `height` sets how far animated
    columns fall and the range still columns are scattered over.
    """
    trail = glyphs_per_column * LINE_HEIGHT
    rules = [f".c{{font:{RAIN_FONT};text-anchor:middle}}", f".h{{fill:{theme.text}}}"]
    rules += [f".o{n}{{fill-opacity:{n / 10:g}}}" for n in range(1, 10)]
    if animated:
        rules.append(".c{animation-name:f;animation-timing-function:linear;animation-iteration-count:infinite}")
        rules.append(f"@keyframes f{{from{{transform:translateY(-{trail}px)}}to{{transform:translateY({height + 20}px)}}}}")

    fade_steps = max(glyphs_per_column - 1, 1)
    columns = []
    for i in range(count):
        x = _num(i * pitch + pitch / 2)
        if animated:
            duration = round(rng.uniform(4.5, 9.5), 1)
            delay = round(rng.uniform(0, duration), 1)
            attrs = f'style="animation-duration:{duration:.1f}s;animation-delay:-{delay:.1f}s"'
        else:
            attrs = f'transform="translate(0 {round(rng.uniform(-trail, height))})"'
        spans = []
        for j in range(glyphs_per_column):
            glyph = rng.choice(GLYPHS)
            if j == glyphs_per_column - 1:
                cls = "h"
            else:
                cls = f"o{min(1 + j * 9 // fade_steps, 9)}"
            spans.append(f'<tspan x="{x}" dy="{LINE_HEIGHT}" class="{cls}">{esc(glyph)}</tspan>')
        columns.append(f'<text class="c" fill="{theme.accent}" {attrs}>{"".join(spans)}</text>')
    return f"<style>{''.join(rules)}</style>\n" + "\n".join(columns)
