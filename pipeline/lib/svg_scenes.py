"""
SVG scene generators for CBSE educational shorts.

Each scene generates an inline SVG diagram with GSAP-animated elements.
Scenes are 540x540 viewBox, matching the image-card area.

scene_for_segment(seg_text, subject) → scene_type (str)
generate_svg_scene(scene_type, i, ink, ink_soft, paper) → (svg_html, [gsap_timeline_lines])
"""

import re

SCENE_TYPES = [
    "atom", "molecule", "circuit", "wave", "vector",
    "graph", "gear", "cell", "geom", "scale",
]

# keyword → scene_type (first match wins)
SCENE_KEYWORDS = [
    ("electron", "atom"), ("proton", "atom"), ("neutron", "atom"),
    ("nucleus", "atom"), ("atomic", "atom"), ("atom", "atom"),
    ("compound", "molecule"), ("bond", "molecule"), ("molecule", "molecule"),
    ("water", "molecule"), ("H2O", "molecule"), ("carbon", "molecule"),
    ("circuit", "circuit"), ("current", "circuit"), ("battery", "circuit"),
    ("voltage", "circuit"), ("resistor", "circuit"),
    ("wave", "wave"), ("frequency", "wave"), ("amplitude", "wave"),
    ("wavelength", "wave"), ("sound", "wave"), ("oscillat", "wave"),
    ("force", "vector"), ("vector", "vector"), ("acceleration", "vector"),
    ("momentum", "vector"), ("Newton", "vector"),
    ("graph", "graph"), ("chart", "graph"), ("plot", "graph"),
    ("bar", "graph"), ("data", "graph"), ("statistic", "graph"),
    ("gear", "gear"), ("machine", "gear"), ("mechanical", "gear"),
    ("work", "gear"), ("pulley", "gear"),
    ("cell", "cell"), ("nucleus", "cell"), ("membrane", "cell"),
    ("organelle", "cell"), ("mitochondria", "cell"),
    ("triangle", "geom"), ("angle", "geom"), ("geometry", "geom"),
    ("rectangle", "geom"), ("circle", "geom"), ("shape", "geom"),
    ("equation", "scale"), ("balance", "scale"), ("algebra", "scale"),
    ("formula", "scale"), ("equal", "scale"),
]

SUBJECT_SCENE = {
    "physics": "atom", "chemistry": "molecule", "biology": "cell",
    "math": "geom", "mathematics": "geom", "science": "atom",
}


def scene_for_segment(text, subject=""):
    blob = text.lower()
    for kw, sc in SCENE_KEYWORDS:
        if kw in blob:
            return sc
    s = (subject or "").lower()
    for k, v in SUBJECT_SCENE.items():
        if k in s:
            return v
    return "atom"


def _anim(id_, props, start, dur=0.6, ease="power2.out"):
    """Helper: return a fromTo GSAP line."""
    from_props = ", ".join(f'{k}: {v}' for k, v in props[0].items())
    to_props = ", ".join(f'{k}: {v}' for k, v in props[1].items())
    return f'      tl.fromTo("#{id_}", {{ {from_props} }}, {{ {to_props}, duration: {dur}, ease: "{ease}" }}, {start});'


# ---- Scene generators -------------------------------------------------------
# Each returns (svg_html, [gsap_lines])

def scene_atom(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"sa-{i}"
    # nucleus at center, 3 elliptical orbits, electron dots
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-nucleus" fill="{ink}" opacity="0">
    <circle cx="270" cy="270" r="18"/>
    <circle cx="260" cy="262" r="8" fill="{ink_soft}"/>
    <circle cx="280" cy="268" r="8" fill="{ink_soft}"/>
    <circle cx="268" cy="278" r="6" fill="{ink}"/>
  </g>
  <g fill="none" stroke="{ink_soft}" stroke-width="2" opacity="0.5">
    <ellipse id="{pref}-orb1" cx="270" cy="270" rx="110" ry="40" stroke-dasharray="0 500" stroke-dashoffset="500"/>
    <ellipse id="{pref}-orb2" cx="270" cy="270" rx="110" ry="40" transform="rotate(60 270 270)" stroke-dasharray="0 500" stroke-dashoffset="500"/>
    <ellipse id="{pref}-orb3" cx="270" cy="270" rx="110" ry="40" transform="rotate(120 270 270)" stroke-dasharray="0 500" stroke-dashoffset="500"/>
  </g>
  <g fill="{ink}" id="{pref}-electrons">
    <circle cx="195" cy="245" r="6"/>
    <circle cx="340" cy="270" r="6"/>
    <circle cx="270" cy="340" r="6"/>
  </g>
  <text x="270" y="420" text-anchor="middle" font-size="28" fill="{ink_soft}" font-family="DM Mono, monospace" opacity="0">Atom</text>
</svg>'''
    tl = [
        f'      tl.to("#{pref}-nucleus", {{ opacity: 1, scale: 1.15, transformOrigin: "50% 50%", duration: 0.5, ease: "back.out(2)" }}, {i * 3 + 0.1});',
        f'      tl.to("#{pref}-nucleus", {{ scale: 1, duration: 0.3, ease: "power2.out" }}, {i * 3 + 0.6});',
        f'      tl.to("#{pref}-orb1", {{ strokeDasharray: "500 500", strokeDashoffset: 0, duration: 0.8, ease: "power2.inOut" }}, {i * 3 + 0.2});',
        f'      tl.to("#{pref}-orb2", {{ strokeDasharray: "500 500", strokeDashoffset: 0, duration: 0.8, ease: "power2.inOut" }}, {i * 3 + 0.35});',
        f'      tl.to("#{pref}-orb3", {{ strokeDasharray: "500 500", strokeDashoffset: 0, duration: 0.8, ease: "power2.inOut" }}, {i * 3 + 0.5});',
        f'      tl.fromTo("#{pref}-electrons circle", {{ opacity: 0, scale: 0 }}, {{ opacity: 1, scale: 1, duration: 0.4, ease: "back.out(2)", stagger: 0.12 }}, {i * 3 + 0.6});',
    ]
    return svg, tl


def scene_molecule(i, ink, ink_soft, paper, dark_mode=False):
    fill_h = "#B8C0D0" if dark_mode else paper
    pref = f"sm-{i}"
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-bonds" fill="none" stroke="{ink}" stroke-width="3" opacity="0">
    <line x1="270" y1="200" x2="195" y2="310" id="{pref}-b1"/>
    <line x1="270" y1="200" x2="345" y2="310" id="{pref}-b2"/>
    <path d="M200 305 Q270 220 340 305" stroke="{ink_soft}" stroke-width="1.5" stroke-dasharray="4 4"/>
  </g>
  <g id="{pref}-atoms">
    <circle cx="270" cy="200" r="30" fill="{ink}" stroke="none"/>
    <circle cx="195" cy="310" r="22" fill="{fill_h}" stroke="{ink}" stroke-width="3"/>
    <circle cx="345" cy="310" r="22" fill="{fill_h}" stroke="{ink}" stroke-width="3"/>
  </g>
  <g font-family="DM Mono, monospace" font-size="26" fill="{ink}" text-anchor="middle" font-weight="600">
    <text x="270" y="208" fill="{fill_h}">O</text>
    <text x="195" y="317" fill="{ink}">H</text>
    <text x="345" y="317" fill="{ink}">H</text>
  </g>
  <text x="270" y="430" text-anchor="middle" font-size="22" fill="{ink_soft}" font-family="DM Mono, monospace" font-style="italic">H₂O — Water Molecule</text>
</svg>'''
    tl = [
        f'      tl.to("#{pref}-bonds", {{ opacity: 1, duration: 0.5 }}, {i * 3 + 0.1});',
        f'      tl.fromTo("#{pref}-b1", {{ scaleY: 0, transformOrigin: "270px 200px" }}, {{ scaleY: 1, duration: 0.5, ease: "power2.out" }}, {i * 3 + 0.15});',
        f'      tl.fromTo("#{pref}-b2", {{ scaleY: 0, transformOrigin: "270px 200px" }}, {{ scaleY: 1, duration: 0.5, ease: "power2.out" }}, {i * 3 + 0.3});',
        f'      tl.fromTo("#{pref}-atoms circle", {{ opacity: 0, scale: 0 }}, {{ opacity: 1, scale: 1, duration: 0.4, ease: "back.out(1.7)", stagger: 0.1 }}, {i * 3 + 0.4});',
    ]
    return svg, tl


def scene_circuit(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"sc-{i}"
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-wires" fill="none" stroke="{ink}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">
    <path d="M135 350 L135 200 L270 200" id="{pref}-w1"/>
    <path d="M270 200 L405 200 L405 350" id="{pref}-w2"/>
    <path d="M405 350 L270 350 L135 350" id="{pref}-w3"/>
  </g>
  <g id="{pref}-components">
    <rect x="105" y="350" width="60" height="80" rx="6" fill="none" stroke="{ink}" stroke-width="3"/>
    <line x1="135" y1="390" x2="135" y2="430" stroke="{ink}" stroke-width="4"/>
    <line x1="115" y1="385" x2="115" y2="415" stroke="{ink}" stroke-width="2"/>
    <line x1="155" y1="385" x2="155" y2="415" stroke="{ink}" stroke-width="2"/>
    <text x="135" y="462" text-anchor="middle" font-size="22" fill="{ink}" font-family="DM Mono, monospace" font-weight="600">+</text>
    <text x="135" y="478" text-anchor="middle" font-size="22" fill="{ink}" font-family="DM Mono, monospace" font-weight="600">-</text>
    <line x1="255" y1="200" x2="285" y2="200" stroke="{ink}" stroke-width="3"/>
    <circle cx="270" cy="185" r="20" fill="none" stroke="{ink}" stroke-width="3"/>
    <line x1="258" y1="180" x2="282" y2="180" stroke="{ink}" stroke-width="2"/>
    <circle cx="270" cy="185" r="6" fill="{ink}"/>
    <rect x="385" y="350" width="12" height="72" rx="2" fill="none" stroke="{ink}" stroke-width="3"/>
    <circle cx="391" cy="346" r="8" fill="none" stroke="{ink}" stroke-width="2"/>
    <line x1="386" y1="386" x2="396" y2="386" stroke="{ink}" stroke-width="2"/>
  </g>
  <g fill="{ink_soft}" font-family="DM Mono, monospace" font-size="22" text-anchor="middle">
    <text x="135" y="505">Battery</text>
    <text x="270" y="155">Bulb</text>
    <text x="400" y="505">Switch</text>
  </g>
</svg>'''
    tl = [
        f'      tl.fromTo("#{pref}-w1", {{ strokeDashoffset: 400, strokeDasharray: 400 }}, {{ strokeDashoffset: 0, duration: 0.5, ease: "power2.inOut" }}, {i * 3 + 0.1});',
        f'      tl.fromTo("#{pref}-w2", {{ strokeDashoffset: 300, strokeDasharray: 300 }}, {{ strokeDashoffset: 0, duration: 0.5, ease: "power2.inOut" }}, {i * 3 + 0.2});',
        f'      tl.fromTo("#{pref}-w3", {{ strokeDashoffset: 300, strokeDasharray: 300 }}, {{ strokeDashoffset: 0, duration: 0.5, ease: "power2.inOut" }}, {i * 3 + 0.3});',
        f'      tl.fromTo("#{pref}-components > *", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4, stagger: 0.08, ease: "power2.out" }}, {i * 3 + 0.3});',
        f'      tl.fromTo("#{pref}-components circle:nth-child(8)", {{ fill: "{paper}" }}, {{ fill: "{ink}", duration: 0.6, ease: "power2.inOut" }}, {i * 3 + 0.8});',
    ]
    return svg, tl


def scene_wave(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"sw-{i}"
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-wave">
    <path id="{pref}-curve" fill="none" stroke="{ink}" stroke-width="4" stroke-linecap="round"
      d="M60 340 Q120 220 180 340 T300 340 T420 340 T540 340"
      stroke-dasharray="800" stroke-dashoffset="800"/>
  </g>
  <g id="{pref}-labels" fill="{ink_soft}" font-family="DM Mono, monospace" font-size="22">
    <g id="{pref}-amp">
      <line x1="180" y1="340" x2="180" y2="290" stroke="{ink_soft}" stroke-width="2" stroke-dasharray="4 4"/>
      <path d="M180 290 L174 300 M180 290 L186 300" stroke="{ink_soft}" stroke-width="2" fill="none"/>
      <text x="195" y="318" font-style="italic">A</text>
    </g>
    <g id="{pref}-wl">
      <line x1="120" y1="370" x2="240" y2="370" stroke="{ink_soft}" stroke-width="2" stroke-dasharray="4 4"/>
      <circle cx="120" cy="370" r="4" fill="{ink_soft}"/>
      <circle cx="240" cy="370" r="4" fill="{ink_soft}"/>
      <text x="180" y="395" text-anchor="middle" font-style="italic">λ</text>
    </g>
  </g>
  <g id="{pref}-xlabel">
    <line x1="60" y1="355" x2="540" y2="355" stroke="{ink}" stroke-width="1.5" opacity="0.3"/>
    <path d="M60 360 L60 350" stroke="{ink}" stroke-width="1.5" opacity="0.3"/>
    <path d="M540 360 L540 350" stroke="{ink}" stroke-width="1.5" opacity="0.3"/>
  </g>
</svg>'''
    tl = [
        f'      tl.to("#{pref}-curve", {{ strokeDashoffset: 0, duration: 1.2, ease: "power2.inOut" }}, {i * 3 + 0.1});',
        f'      tl.fromTo("#{pref}-amp", {{ opacity: 0, x: -10 }}, {{ opacity: 1, x: 0, duration: 0.5, ease: "power2.out" }}, {i * 3 + 0.6});',
        f'      tl.fromTo("#{pref}-wl", {{ opacity: 0, x: 10 }}, {{ opacity: 1, x: 0, duration: 0.5, ease: "power2.out" }}, {i * 3 + 0.7});',
    ]
    return svg, tl


def scene_vector(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"sv-{i}"
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-grid">
    <line x1="100" y1="400" x2="440" y2="400" stroke="{ink}" stroke-width="1.5" opacity="0.15"/>
    <line x1="270" y1="120" x2="270" y2="420" stroke="{ink}" stroke-width="1.5" opacity="0.15"/>
  </g>
  <circle cx="270" cy="350" r="6" fill="{ink}" id="{pref}-origin"/>
  <g id="{pref}-arrows" fill="none" stroke-linecap="round" stroke-linejoin="round">
    <g id="{pref}-f1">
      <path d="M270 350 L370 250" stroke="{ink}" stroke-width="4"/>
      <path d="M370 250 L358 260 M370 250 L368 238" stroke="{ink}" stroke-width="3"/>
    </g>
    <g id="{pref}-f2">
      <path d="M270 350 L170 280" stroke="{ink_soft}" stroke-width="4"/>
      <path d="M170 280 L182 290 M170 280 L178 268" stroke="{ink_soft}" stroke-width="3"/>
    </g>
    <g id="{pref}-f3">
      <path d="M270 350 L340 240" stroke="{ink}" stroke-width="4" opacity="0.6"/>
      <path d="M340 240 L330 248 M340 240 L336 228" stroke="{ink}" stroke-width="3" opacity="0.6"/>
    </g>
  </g>
  <g font-family="DM Mono, monospace" font-size="24" fill="{ink}" id="{pref}-labels">
    <text x="380" y="242">F<tspan baseline-shift="sub" font-size="16">1</tspan></text>
    <text x="148" y="272" fill="{ink_soft}">F<tspan baseline-shift="sub" font-size="16">2</tspan></text>
    <text x="350" y="228">F<tspan baseline-shift="sub" font-size="16">3</tspan></text>
  </g>
</svg>'''
    tl = [
        f'      tl.fromTo("#{pref}-origin", {{ scale: 0 }}, {{ scale: 1, duration: 0.3, ease: "back.out(2)" }}, {i * 3 + 0.05});',
        f'      tl.fromTo("#{pref}-f1", {{ strokeDashoffset: 200, strokeDasharray: 200, opacity: 0 }}, {{ strokeDashoffset: 0, opacity: 1, duration: 0.6, ease: "power2.out" }}, {i * 3 + 0.15});',
        f'      tl.fromTo("#{pref}-f2", {{ strokeDashoffset: 180, strokeDasharray: 180, opacity: 0 }}, {{ strokeDashoffset: 0, opacity: 1, duration: 0.6, ease: "power2.out" }}, {i * 3 + 0.3});',
        f'      tl.fromTo("#{pref}-f3", {{ strokeDashoffset: 160, strokeDasharray: 160, opacity: 0 }}, {{ strokeDashoffset: 0, opacity: 1, duration: 0.6, ease: "power2.out" }}, {i * 3 + 0.45});',
        f'      tl.fromTo("#{pref}-labels > *", {{ opacity: 0, y: 8 }}, {{ opacity: 1, y: 0, duration: 0.4, stagger: 0.1 }}, {i * 3 + 0.5});',
    ]
    return svg, tl


def scene_graph(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"sg-{i}"
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-axes" fill="none" stroke="{ink}" stroke-width="3">
    <line x1="80" y1="400" x2="480" y2="400" id="{pref}-xaxis"/>
    <line x1="80" y1="400" x2="80" y2="100" id="{pref}-yaxis"/>
    <path d="M76 110 L80 100 L84 110" fill="{ink}" stroke="none"/>
    <path d="M470 396 L480 400 L470 404" fill="{ink}" stroke="none"/>
  </g>
  <g id="{pref}-bars" fill="{ink}" opacity="0.8">
    <rect x="120" y="320" width="60" height="80" rx="4" id="{pref}-bar1"/>
    <rect x="210" y="260" width="60" height="140" rx="4" id="{pref}-bar2"/>
    <rect x="300" y="190" width="60" height="210" rx="4" id="{pref}-bar3"/>
    <rect x="390" y="280" width="60" height="120" rx="4" id="{pref}-bar4"/>
  </g>
  <g font-family="DM Mono, monospace" font-size="20" fill="{ink_soft}" text-anchor="middle">
    <text x="150" y="440">Q1</text>
    <text x="240" y="440">Q2</text>
    <text x="330" y="440">Q3</text>
    <text x="420" y="440">Q4</text>
    <text x="55" y="250" text-anchor="end" font-size="18">Values</text>
  </g>
</svg>'''
    tl = [
        f'      tl.fromTo("#{pref}-xaxis", {{ scaleX: 0, transformOrigin: "80px 400px" }}, {{ scaleX: 1, duration: 0.4, ease: "power2.out" }}, {i * 3 + 0.1});',
        f'      tl.fromTo("#{pref}-yaxis", {{ scaleY: 0, transformOrigin: "80px 400px" }}, {{ scaleY: 1, duration: 0.4, ease: "power2.out" }}, {i * 3 + 0.15});',
        f'      tl.fromTo("#{pref}-bar1", {{ scaleY: 0, transformOrigin: "150px 400px" }}, {{ scaleY: 1, duration: 0.5, ease: "back.out(1.4)" }}, {i * 3 + 0.25});',
        f'      tl.fromTo("#{pref}-bar2", {{ scaleY: 0, transformOrigin: "240px 400px" }}, {{ scaleY: 1, duration: 0.5, ease: "back.out(1.4)" }}, {i * 3 + 0.35});',
        f'      tl.fromTo("#{pref}-bar3", {{ scaleY: 0, transformOrigin: "330px 400px" }}, {{ scaleY: 1, duration: 0.5, ease: "back.out(1.4)" }}, {i * 3 + 0.45});',
        f'      tl.fromTo("#{pref}-bar4", {{ scaleY: 0, transformOrigin: "420px 400px" }}, {{ scaleY: 1, duration: 0.5, ease: "back.out(1.4)" }}, {i * 3 + 0.55});',
    ]
    return svg, tl


def scene_geom(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"sgm-{i}"
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-triangle" fill="none" stroke="{ink}" stroke-width="4" stroke-linejoin="round">
    <path id="{pref}-shape" d="M120 370 L370 370 L370 120 Z" stroke-dasharray="700" stroke-dashoffset="700"/>
  </g>
  <g id="{pref}-marker" fill="none" stroke="{ink}" stroke-width="2">
    <path d="M355 370 L370 355 L355 340"/>
  </g>
  <g id="{pref}-labels" font-family="DM Mono, monospace" font-style="italic" font-size="28" fill="{ink}">
    <text x="245" y="410" text-anchor="middle">a</text>
    <text x="392" y="255" text-anchor="start">b</text>
    <text x="260" y="200" text-anchor="end" transform="rotate(-53 260 200)">c</text>
  </g>
  <g font-family="DM Mono, monospace" font-size="20" fill="{ink_soft}">
    <text x="80" y="395">90°</text>
    <text x="370" y="108" text-anchor="middle">θ</text>
    <text x="400" y="395" text-anchor="start">θ</text>
  </g>
  <text x="270" y="480" text-anchor="middle" font-size="24" fill="{ink}" font-family="DM Mono, monospace">Right Triangle</text>
</svg>'''
    tl = [
        f'      tl.to("#{pref}-shape", {{ strokeDashoffset: 0, duration: 1.0, ease: "power2.inOut" }}, {i * 3 + 0.1});',
        f'      tl.fromTo("#{pref}-marker", {{ opacity: 0, scale: 0 }}, {{ opacity: 1, scale: 1, duration: 0.4, ease: "back.out(1.5)" }}, {i * 3 + 0.6});',
        f'      tl.fromTo("#{pref}-labels > text", {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.4, stagger: 0.12 }}, {i * 3 + 0.5});',
    ]
    return svg, tl


def scene_scale(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"ssl-{i}"
    fill_s = "#B8C0D0" if dark_mode else paper
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-base" fill="none" stroke="{ink}" stroke-width="3">
    <polygon points="230,430 310,430 270,370" fill="{fill_s}" stroke="{ink}" stroke-width="3"/>
  </g>
  <g id="{pref}-beam" fill="none" stroke="{ink}" stroke-width="4" stroke-linecap="round">
    <line x1="100" y1="300" x2="440" y2="300" id="{pref}-beamline"/>
    <circle cx="270" cy="300" r="6" fill="{ink}"/>
  </g>
  <g id="{pref}-pans" fill="none" stroke="{ink}" stroke-width="2.5" stroke-linejoin="round">
    <path d="M100 300 L100 340 L70 380 L130 380 L100 340" id="{pref}-chain-l"/>
    <path d="M440 300 L440 340 L410 380 L470 380 L440 340" id="{pref}-chain-r"/>
    <rect x="60" y="380" width="80" height="16" rx="3" fill="{fill_s}" stroke="{ink}" stroke-width="2" id="{pref}-pan-l"/>
    <rect x="400" y="380" width="80" height="16" rx="3" fill="{fill_s}" stroke="{ink}" stroke-width="2" id="{pref}-pan-r"/>
  </g>
  <g id="{pref}-items" font-family="DM Mono, monospace" font-size="24" fill="{ink}" text-anchor="middle">
    <text x="100" y="374">x</text>
    <text x="440" y="374">3</text>
  </g>
  <text x="270" y="500" text-anchor="middle" font-size="22" fill="{ink_soft}" font-family="DM Mono, monospace" font-style="italic">Balance Scale</text>
</svg>'''
    tl = [
        f'      tl.fromTo("#{pref}-base", {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.4, ease: "power2.out" }}, {i * 3 + 0.1});',
        f'      tl.fromTo("#{pref}-beamline", {{ scaleX: 0, transformOrigin: "270px 300px" }}, {{ scaleX: 1, duration: 0.5, ease: "back.out(1.4)" }}, {i * 3 + 0.2});',
        f'      tl.fromTo("#{pref}-chain-l", {{ strokeDashoffset: 150, strokeDasharray: 150 }}, {{ strokeDashoffset: 0, duration: 0.4, ease: "power2.out" }}, {i * 3 + 0.35});',
        f'      tl.fromTo("#{pref}-chain-r", {{ strokeDashoffset: 150, strokeDasharray: 150 }}, {{ strokeDashoffset: 0, duration: 0.4, ease: "power2.out" }}, {i * 3 + 0.45});',
        f'      tl.fromTo("#{pref}-pan-l", {{ scaleY: 0, transformOrigin: "100px 388px" }}, {{ scaleY: 1, duration: 0.3, ease: "power2.out" }}, {i * 3 + 0.55});',
        f'      tl.fromTo("#{pref}-pan-r", {{ scaleY: 0, transformOrigin: "440px 388px" }}, {{ scaleY: 1, duration: 0.3, ease: "power2.out" }}, {i * 3 + 0.55});',
        f'      tl.fromTo("#{pref}-items > *", {{ opacity: 0, scale: 0 }}, {{ opacity: 1, scale: 1, duration: 0.3, ease: "back.out(2)" }}, {i * 3 + 0.7});',
    ]
    return svg, tl


def scene_cell(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"scl-{i}"
    fill_c = "#B8C0D0" if dark_mode else paper
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <g id="{pref}-wall" fill="none" stroke="{ink_soft}" stroke-width="2" opacity="0.3">
    <rect x="50" y="80" width="440" height="440" rx="20" stroke-dasharray="8 4"/>
  </g>
  <g id="{pref}-membrane" fill="none" stroke="{ink}" stroke-width="4">
    <circle cx="270" cy="240" r="120" id="{pref}-mem"/>
  </g>
  <g id="{pref}-nucleus" fill="{ink}" stroke="none" opacity="0">
    <circle cx="260" cy="220" r="35"/>
    <circle cx="275" cy="205" r="10" fill="{fill_c}" opacity="0.3"/>
  </g>
  <g id="{pref}-organelles" fill="{ink_soft}" opacity="0.6">
    <ellipse cx="210" cy="280" rx="25" ry="12" stroke="{ink_soft}" stroke-width="1.5" fill="none"/>
    <ellipse cx="330" cy="180" rx="30" ry="15" stroke="{ink_soft}" stroke-width="1.5" fill="none"/>
    <circle cx="340" cy="290" r="8" fill="{ink_soft}"/>
    <circle cx="190" cy="190" r="6" fill="{ink_soft}"/>
    <circle cx="320" cy="330" r="5" fill="{ink_soft}"/>
    <circle cx="210" cy="160" r="4" fill="{ink_soft}"/>
    <circle cx="360" cy="250" r="6" fill="{ink_soft}"/>
  </g>
  <text x="270" y="440" text-anchor="middle" font-size="24" fill="{ink}" font-family="DM Mono, monospace">Cell Structure</text>
</svg>'''
    tl = [
        f'      tl.fromTo("#{pref}-mem", {{ strokeDashoffset: 800, strokeDasharray: 800 }}, {{ strokeDashoffset: 0, duration: 1.0, ease: "power2.inOut" }}, {i * 3 + 0.1});',
        f'      tl.to("#{pref}-nucleus", {{ opacity: 1, scale: 1.1, duration: 0.5, ease: "back.out(1.6)" }}, {i * 3 + 0.4});',
        f'      tl.to("#{pref}-nucleus", {{ scale: 1, duration: 0.3, ease: "power2.out" }}, {i * 3 + 0.9});',
        f'      tl.fromTo("#{pref}-organelles > *", {{ opacity: 0, scale: 0 }}, {{ opacity: 1, scale: 1, duration: 0.3, ease: "back.out(1.4)", stagger: 0.06 }}, {i * 3 + 0.5});',
    ]
    return svg, tl


def scene_gear(i, ink, ink_soft, paper, dark_mode=False):
    pref = f"sge-{i}"
    # Simplified gear as circle with teeth pattern
    svg = f'''<svg class="scene-svg" viewBox="0 0 540 540">
  <defs>
    <g id="{pref}-tooth">
      <rect x="-4" y="-82" width="8" height="16" rx="2" fill="{ink}"/>
    </g>
  </defs>
  <g id="{pref}-gear1" transform="translate(210, 270)">
    <circle cx="0" cy="0" r="70" fill="none" stroke="{ink}" stroke-width="4"/>
    <circle cx="0" cy="0" r="50" fill="none" stroke="{ink_soft}" stroke-width="1.5" opacity="0.4"/>
    <circle cx="0" cy="0" r="12" fill="{ink}"/>
    <use href="#{pref}-tooth" transform="rotate(0)"/>
    <use href="#{pref}-tooth" transform="rotate(30)"/>
    <use href="#{pref}-tooth" transform="rotate(60)"/>
    <use href="#{pref}-tooth" transform="rotate(90)"/>
    <use href="#{pref}-tooth" transform="rotate(120)"/>
    <use href="#{pref}-tooth" transform="rotate(150)"/>
    <use href="#{pref}-tooth" transform="rotate(180)"/>
    <use href="#{pref}-tooth" transform="rotate(210)"/>
    <use href="#{pref}-tooth" transform="rotate(240)"/>
    <use href="#{pref}-tooth" transform="rotate(270)"/>
    <use href="#{pref}-tooth" transform="rotate(300)"/>
    <use href="#{pref}-tooth" transform="rotate(330)"/>
  </g>
  <g id="{pref}-gear2" transform="translate(350, 270)">
    <circle cx="0" cy="0" r="45" fill="none" stroke="{ink}" stroke-width="4"/>
    <circle cx="0" cy="0" r="30" fill="none" stroke="{ink_soft}" stroke-width="1.5" opacity="0.4"/>
    <circle cx="0" cy="0" r="8" fill="{ink}"/>
    <use href="#{pref}-tooth" transform="rotate(15) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(51) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(87) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(123) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(159) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(195) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(231) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(267) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(303) scale(0.65) translate(0, 38)"/>
    <use href="#{pref}-tooth" transform="rotate(339) scale(0.65) translate(0, 38)"/>
  </g>
  <text x="220" y="390" text-anchor="middle" font-size="22" fill="{ink}" font-family="DM Mono, monospace">Mechanical Gears</text>
</svg>'''
    tl = [
        f'      tl.fromTo("#{pref}-gear1", {{ scale: 0, opacity: 0, transformOrigin: "210px 270px" }}, {{ scale: 1, opacity: 1, duration: 0.7, ease: "back.out(1.7)" }}, {i * 3 + 0.1});',
        f'      tl.fromTo("#{pref}-gear2", {{ scale: 0, opacity: 0, transformOrigin: "350px 270px" }}, {{ scale: 1, opacity: 1, duration: 0.7, ease: "back.out(1.7)" }}, {i * 3 + 0.2});',
        f'      tl.to("#{pref}-gear1", {{ rotation: 360, transformOrigin: "210px 270px", duration: 12, ease: "none" }}, {i * 3 + 0.1});',
        f'      tl.to("#{pref}-gear2", {{ rotation: -360, transformOrigin: "350px 270px", duration: 9, ease: "none" }}, {i * 3 + 0.1});',
    ]
    return svg, tl


# Map scene_type → generator
SCENE_GENERATORS = {
    "atom": scene_atom,
    "molecule": scene_molecule,
    "circuit": scene_circuit,
    "wave": scene_wave,
    "vector": scene_vector,
    "graph": scene_graph,
    "geom": scene_geom,
    "scale": scene_scale,
    "cell": scene_cell,
    "gear": scene_gear,
}


def generate_svg_scene(scene_type, i, ink="#1A3FB0", ink_soft="#5566C8", paper="#F4F1EA", dark_mode=False):
    """Return (svg_html_string, [gsap_timeline_lines]) for the given scene type."""
    gen = SCENE_GENERATORS.get(scene_type)
    if gen is None:
        return "", []
    return gen(i, ink, ink_soft, paper, dark_mode=dark_mode)
