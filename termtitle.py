#!/usr/bin/env python3
"""
termtitle.py

Generate a retro terminal-style intro video from a text script.

Usage:
    python -m TermTitle <script.txt> <output.mp4> [options]
    python TermTitle.py <script.txt> <output.mp4> [options]

Common command-line options:
    --background <file>             Optional starting background image. If omitted, starts black.
    --size <WIDTHxHEIGHT>           Output size. Default: 640x480.
    --fps <number>                  Frames per second. Default: 30.
    --font <file>                   Starting/default font file.
    --font-size <number>            Starting/default font size. Default: 26.
    --color <name or hex>           Starting/default text color. Default: green.
    --cursor <style>                Starting/default cursor style: block, underline, bar, none.
    --chars-per-second <number>     Starting/default typing speed. Default: 14.
    --end-hold <seconds>            Hold time after the script ends. Default: 2.
    --beep-frequency <hz>           Starting/default beep pitch. Default: 880.
    --beep-volume <0-1>             Beep volume. Default: 0.12.
    --beep-duration <seconds>       Beep length. Default: 0.035.
    --bg-transition <type>          Default background transition: cut, pixelize, raster, scroll.
    --bg-transition-duration <sec>  Default background transition duration. Default: 1.
    --raster-line-height <pixels>   Raster block size. Default: 8.
    --scroll-step <pixels>          Scroll transition step size. Default: 16.
    --shutdown-duration <seconds>   Default CRT shutdown duration. Default: 1.2.
    --no-audio                      Disable generated beeps.
    --no-flicker                    Disable subtle CRT brightness flicker.
    --preview-frames                Save first/middle/last preview frames as PNGs.

Script commands:
    [bg <file or color> <transition> <duration>]
        Set or change the background. An image file or solid color must be specified.
        A transition type and duration in seconds may optionally be specified.
        Example: [bg image.jpg raster 2.5]

    [newbg <file or color> <transition> <duration>]
        Alias for [bg ...].

    [clear]
        Clear all visible text.

    [pause <seconds>]
        Pause with the current screen visible.
        Example: [pause 1.5]

    [speed <chars-per-second>]
        Change typing speed.
        Example: [speed 20]

    [font <file>]
        Change font. Paths are resolved relative to the script file first.
        Example: [font fonts/VT323-Regular.ttf]

    [fontsize <number>]
        Change font size.
        Example: [fontsize 40]

    [beepfreq <hz>]
        Change beep pitch.
        Example: [beepfreq 700]

    [cursor <style> <color>]
        Change cursor style and optionally text/cursor color.
        Styles: _, underline, |, bar, block, none.
        Example: [cursor _ amber]

    [color <name or hex>]
        Change text/cursor color without changing cursor style.
        Example: [color cyan]

    [scroll down]
        Text begins near the top and advances downward. This is the default.

    [scroll up]
        Text is bottom-anchored and scrolls upward as new lines are added.

    [shutdown <seconds>]
        Clear text, hide cursor, collapse the screen to a horizontal white line,
        then fade to black. Duration is optional.
        Example: [shutdown 1.8]

Supported color names:
    green, brightgreen, white, amber, orange, red, blue, cyan, magenta,
    purple, yellow, black

Dependencies:
    pip install pillow moviepy numpy

MoviePy requires ffmpeg. Recent MoviePy installs usually handle this, but if export
fails, install ffmpeg and make sure it is on your PATH.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    from moviepy import AudioFileClip, VideoClip
except ImportError:
    try:
        from moviepy.editor import AudioFileClip, VideoClip
    except ImportError as exc:
        raise SystemExit(
            "MoviePy is required. Install dependencies with:\n"
            "    pip install pillow moviepy numpy\n"
        ) from exc


# -----------------------------
# Constants and color helpers
# -----------------------------

VALID_TRANSITIONS = {"cut", "pixelize", "raster", "scroll"}

NAMED_COLORS = {
    "green": "80ff80",
    "brightgreen": "a0ffa0",
    "white": "ffffff",
    "amber": "ffbf40",
    "orange": "ff9a33",
    "red": "ff5a5a",
    "blue": "7aa8ff",
    "cyan": "66ffff",
    "magenta": "ff66ff",
    "purple": "c080ff",
    "yellow": "ffff80",
    "black": "000000",
}


def parse_hex_color(value: str) -> Tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError("Color must be a 6-digit hex value or known color name, like 80ff80 or amber")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def parse_color_value(value: str) -> Tuple[int, int, int]:
    raw = value.strip().lower().replace(" ", "")
    raw = NAMED_COLORS.get(raw, raw)
    return parse_hex_color(raw)


def is_color_token(value: str) -> bool:
    raw = value.strip().lower().lstrip("#")
    return value.strip().lower() in NAMED_COLORS or bool(re.fullmatch(r"[0-9a-fA-F]{6}", raw))


def normalize_cursor_style(value: str) -> str:
    value = value.strip().lower()
    if value in {"_", "underline"}:
        return "underline"
    if value in {"|", "bar"}:
        return "bar"
    if value in {"block", "box", "█"}:
        return "block"
    if value in {"none", "off"}:
        return "none"
    raise ValueError("Cursor style must be one of: _, underline, |, bar, block, none")


# -----------------------------
# Data model
# -----------------------------

@dataclass
class Event:
    type: str
    text: str = ""
    seconds: float = 0.0
    speed: Optional[float] = None
    bg_path: Optional[str] = None
    bg_color: Optional[str] = None
    bg_transition: Optional[str] = None
    bg_duration: Optional[float] = None
    font_path: Optional[str] = None
    font_size: Optional[int] = None
    beep_frequency: Optional[float] = None
    color_value: Optional[str] = None
    cursor_style: Optional[str] = None
    scroll_mode: Optional[str] = None


@dataclass
class StyleState:
    font_path: Optional[str]
    font_size: int
    beep_frequency: float
    speed: float
    color: Tuple[int, int, int]
    cursor_style: str
    scroll_mode: str


@dataclass
class RenderState:
    buffer: str
    cursor_visible: bool
    background: Image.Image
    font_path: Optional[str]
    font_size: int
    color: Tuple[int, int, int]
    cursor_style: str
    scroll_mode: str


# -----------------------------
# Script parsing
# -----------------------------

COMMAND_RE = re.compile(r"^\s*\[(\w+)(?:\s+([^\]]+))?\]\s*(?:#.*)?$")


def parse_bg_args(arg: str, default_transition: str, default_duration: float) -> Tuple[Optional[str], Optional[str], str, float]:
    parts = arg.split()
    if not parts:
        raise ValueError("[bg ...] requires an image filename or color name")

    bg_target = parts[0]
    transition = default_transition
    duration = default_duration

    if len(parts) >= 2:
        transition = parts[1].lower()
    if transition not in VALID_TRANSITIONS:
        raise ValueError(
            f"Invalid background transition '{transition}'. "
            f"Valid options: {', '.join(sorted(VALID_TRANSITIONS))}"
        )

    if len(parts) >= 3:
        try:
            duration = float(parts[2])
        except ValueError as exc:
            raise ValueError(f"Invalid background transition duration: {parts[2]}") from exc
        if duration < 0:
            raise ValueError("Background transition duration cannot be negative")

    if len(parts) > 3:
        raise ValueError("Too many arguments for [bg ...]. Use: [bg image.jpg raster 1.0] or [bg black pixelize 3]")

    if is_color_token(bg_target):
        return None, bg_target, transition, duration

    return bg_target, None, transition, duration


def parse_script(
    path: Path,
    default_speed: float,
    default_transition: str,
    default_transition_duration: float,
    default_font_size: int,
    default_beep_frequency: float,
    default_color_value: str,
    default_cursor_style: str,
    default_shutdown_duration: float,
) -> List[Event]:
    if not path.exists():
        raise FileNotFoundError(f"Script file not found: {path}")

    events: List[Event] = []
    text_accum: List[str] = []

    current_speed = default_speed
    current_font_path: Optional[str] = None
    current_font_size = default_font_size
    current_beep_frequency = default_beep_frequency
    current_color_value = default_color_value
    current_cursor_style = normalize_cursor_style(default_cursor_style)
    current_scroll_mode = "down"

    def flush_text() -> None:
        nonlocal text_accum
        if text_accum:
            events.append(
                Event(
                    type="type",
                    text="".join(text_accum),
                    speed=current_speed,
                    font_path=current_font_path,
                    font_size=current_font_size,
                    beep_frequency=current_beep_frequency,
                    color_value=current_color_value,
                    cursor_style=current_cursor_style,
                    scroll_mode=current_scroll_mode,
                )
            )
            text_accum = []

    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line_no_newline = raw_line.rstrip("\n")
            match = COMMAND_RE.match(line_no_newline)

            if not match:
                text_accum.append(raw_line)
                continue

            command = match.group(1).lower()
            arg = (match.group(2) or "").strip()

            if command in {"bg", "newbg"}:
                flush_text()
                bg_path, bg_color, transition, duration = parse_bg_args(
                    arg,
                    default_transition,
                    default_transition_duration,
                )
                events.append(
                    Event(
                        type="bg",
                        bg_path=bg_path,
                        bg_color=bg_color,
                        bg_transition=transition,
                        bg_duration=duration,
                    )
                )

            elif command == "clear":
                flush_text()
                events.append(Event(type="clear"))

            elif command == "pause":
                flush_text()
                try:
                    seconds = float(arg)
                except ValueError as exc:
                    raise ValueError(f"Invalid pause value: [{command} {arg}]") from exc
                if seconds < 0:
                    raise ValueError("Pause duration cannot be negative")
                events.append(Event(type="pause", seconds=seconds))

            elif command == "speed":
                flush_text()
                try:
                    current_speed = float(arg)
                except ValueError as exc:
                    raise ValueError(f"Invalid speed value: [{command} {arg}]") from exc
                if current_speed <= 0:
                    raise ValueError("Speed must be greater than zero")
                events.append(Event(type="speed", speed=current_speed))

            elif command == "font":
                flush_text()
                if not arg:
                    raise ValueError("[font ...] requires a font filename")
                current_font_path = arg
                events.append(Event(type="font", font_path=current_font_path))

            elif command == "fontsize":
                flush_text()
                try:
                    current_font_size = int(arg)
                except ValueError as exc:
                    raise ValueError(f"Invalid fontsize value: [{command} {arg}]") from exc
                if current_font_size <= 0:
                    raise ValueError("Font size must be greater than zero")
                events.append(Event(type="fontsize", font_size=current_font_size))

            elif command == "beepfreq":
                flush_text()
                try:
                    current_beep_frequency = float(arg)
                except ValueError as exc:
                    raise ValueError(f"Invalid beepfreq value: [{command} {arg}]") from exc
                if current_beep_frequency <= 0:
                    raise ValueError("Beep frequency must be greater than zero")
                events.append(Event(type="beepfreq", beep_frequency=current_beep_frequency))

            elif command == "cursor":
                flush_text()
                parts = arg.split()
                if not parts:
                    raise ValueError("[cursor ...] requires a cursor style, for example [cursor _ green]")
                current_cursor_style = normalize_cursor_style(parts[0])
                if len(parts) >= 2:
                    current_color_value = parts[1]
                    parse_color_value(current_color_value)
                if len(parts) > 2:
                    raise ValueError("Too many arguments for [cursor ...]. Use: [cursor _ green]")
                events.append(Event(type="cursor", cursor_style=current_cursor_style, color_value=current_color_value))

            elif command in {"color", "textcolor"}:
                flush_text()
                if not arg:
                    raise ValueError("[color ...] requires a color name or hex value")
                current_color_value = arg
                parse_color_value(current_color_value)
                events.append(Event(type="color", color_value=current_color_value))

            elif command == "scroll":
                flush_text()
                mode = arg.strip().lower()
                if mode not in {"up", "down"}:
                    raise ValueError("[scroll ...] must be either [scroll up] or [scroll down]")
                current_scroll_mode = mode
                events.append(Event(type="scroll", scroll_mode=current_scroll_mode))

            elif command == "shutdown":
                flush_text()
                if arg:
                    try:
                        seconds = float(arg)
                    except ValueError as exc:
                        raise ValueError(f"Invalid shutdown value: [{command} {arg}]") from exc
                    if seconds < 0:
                        raise ValueError("Shutdown duration cannot be negative")
                else:
                    seconds = default_shutdown_duration
                events.append(Event(type="shutdown", seconds=seconds))

            else:
                text_accum.append(raw_line)

    flush_text()
    return events


# -----------------------------
# Asset loading
# -----------------------------

def resolve_asset_path(asset: str, script_path: Path) -> Path:
    p = Path(asset)

    if p.is_absolute() and p.exists():
        return p

    candidate = script_path.parent / asset
    if candidate.exists():
        return candidate

    if p.exists():
        return p

    raise FileNotFoundError(f"Asset not found: {asset}")


def make_solid_background(color_value: str, size: Tuple[int, int]) -> Image.Image:
    return Image.new("RGB", size, parse_color_value(color_value))


def fit_background(path: Path, size: Tuple[int, int]) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(f"Background image not found: {path}")

    img = Image.open(path).convert("RGB")
    target_w, target_h = size
    src_w, src_h = img.size

    src_aspect = src_w / src_h
    target_aspect = target_w / target_h

    if src_aspect > target_aspect:
        new_w = int(src_h * target_aspect)
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / target_aspect)
        top = (src_h - new_h) // 2
        img = img.crop((0, top, src_w, top + new_h))

    return img.resize(size, Image.Resampling.LANCZOS).convert("RGB")


def load_font(font_path: Optional[str], font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_path:
        fp = Path(font_path)
        if not fp.exists():
            raise FileNotFoundError(f"Font file not found: {font_path}")
        return ImageFont.truetype(str(fp), font_size)

    bundled = Path(__file__).parent / "fonts" / "VT323-Regular.ttf"
    if bundled.exists():
        return ImageFont.truetype(str(bundled), font_size)

    candidates = ["DejaVuSansMono.ttf", "Consolas.ttf", "Courier New.ttf", "cour.ttf"]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, font_size)
        except OSError:
            pass

    return ImageFont.load_default()


# -----------------------------
# Text layout
# -----------------------------

def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def measure_mono(font: ImageFont.ImageFont) -> Tuple[int, int]:
    img = Image.new("RGB", (200, 100))
    draw = ImageDraw.Draw(img)
    w, h = text_size(draw, "M", font)
    _, h2 = text_size(draw, "Mg", font)
    return max(1, w), max(1, h, h2)


def wrap_buffer(buffer: str, max_cols: int) -> List[str]:
    lines: List[str] = []

    for paragraph in buffer.split("\n"):
        if paragraph == "":
            lines.append("")
            continue

        current = ""
        words = re.split(r"(\s+)", paragraph)

        for token in words:
            if token == "":
                continue

            while len(token) > max_cols:
                remaining = max_cols - len(current)
                if remaining <= 0:
                    lines.append(current.rstrip())
                    current = ""
                    remaining = max_cols
                current += token[:remaining]
                token = token[remaining:]
                if len(current) >= max_cols:
                    lines.append(current.rstrip())
                    current = ""

            if len(current) + len(token) <= max_cols:
                current += token
            else:
                lines.append(current.rstrip())
                current = token.lstrip()

        lines.append(current.rstrip())

    return lines


# -----------------------------
# Background transitions
# -----------------------------

def transition_pixelize(old_bg: Image.Image, new_bg: Image.Image, progress: float) -> Image.Image:
    progress = max(0.0, min(1.0, progress))
    w, h = old_bg.size

    if progress < 0.5:
        src = old_bg
        p = progress / 0.5
        blocks = int(1 + p * 28)
    else:
        src = new_bg
        p = (progress - 0.5) / 0.5
        blocks = int(29 - p * 28)

    blocks = max(1, blocks)
    small_w = max(1, w // blocks)
    small_h = max(1, h // blocks)
    chunky = src.resize((small_w, small_h), Image.Resampling.BILINEAR)
    return chunky.resize((w, h), Image.Resampling.NEAREST).convert("RGB")


def transition_raster(old_bg: Image.Image, new_bg: Image.Image, progress: float, block_size: int) -> Image.Image:
    progress = max(0.0, min(1.0, progress))
    w, h = old_bg.size
    block_size = max(1, block_size)

    canvas = old_bg.copy().convert("RGB")
    cols = math.ceil(w / block_size)
    rows = math.ceil(h / block_size)
    total_blocks = max(1, cols * rows)
    blocks_to_draw = min(total_blocks, int(math.ceil(progress * total_blocks)))

    for block_index in range(blocks_to_draw):
        row = block_index // cols
        col = block_index % cols
        x0 = col * block_size
        y0 = row * block_size
        x1 = min(w, x0 + block_size)
        y1 = min(h, y0 + block_size)
        region = new_bg.crop((x0, y0, x1, y1))
        canvas.paste(region, (x0, y0))

    return canvas


def transition_scroll(old_full: Image.Image, new_full: Image.Image, progress: float, step: int) -> Image.Image:
    progress = max(0.0, min(1.0, progress))
    w, h = old_full.size
    step = max(1, step)

    raw_offset = int(progress * h)
    offset = min(h, (raw_offset // step) * step)
    canvas = Image.new("RGB", (w, h), (0, 0, 0))

    if offset < h:
        old_region = old_full.crop((0, offset, w, h))
        canvas.paste(old_region, (0, 0))

    if offset > 0:
        new_region = new_full.crop((0, 0, w, offset))
        canvas.paste(new_region, (0, h - offset))

    return canvas


def transition_shutdown_frame(screen_img: Image.Image, progress: float) -> Image.Image:
    progress = max(0.0, min(1.0, progress))
    w, h = screen_img.size
    center_y = h // 2
    canvas = Image.new("RGB", (w, h), (0, 0, 0))

    collapse_portion = 0.78
    fade_portion = 1.0 - collapse_portion

    if progress < collapse_portion:
        p = progress / collapse_portion
        p = p * p
        collapsed_h = max(1, int(round((1.0 - p) * h)))

        if collapsed_h > 2:
            shrunk = screen_img.resize((w, collapsed_h), Image.Resampling.BILINEAR)
            y = (h - collapsed_h) // 2
            canvas.paste(shrunk, (0, y))
        else:
            line_thickness = 2
            y0 = max(0, center_y - line_thickness // 2)
            y1 = min(h, y0 + line_thickness)
            canvas.paste(Image.new("RGB", (w, y1 - y0), (255, 255, 255)), (0, y0))
    else:
        p = (progress - collapse_portion) / max(0.0001, fade_portion)
        brightness = int(round(255 * (1.0 - p)))
        if brightness > 0:
            line_thickness = 2
            y0 = max(0, center_y - line_thickness // 2)
            y1 = min(h, y0 + line_thickness)
            canvas.paste(Image.new("RGB", (w, y1 - y0), (brightness, brightness, brightness)), (0, y0))

    return canvas


# -----------------------------
# Frame rendering
# -----------------------------

def add_scanlines(img: Image.Image, opacity: int = 36) -> Image.Image:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = img.size
    for y in range(0, h, 2):
        draw.line((0, y, w, y), fill=(0, 0, 0, opacity))
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def add_vignette(img: Image.Image, strength: int = 110) -> Image.Image:
    w, h = img.size
    x = np.linspace(-1, 1, w)
    y = np.linspace(-1, 1, h)
    xv, yv = np.meshgrid(x, y)
    radius = np.sqrt(xv * xv + yv * yv)
    mask = np.clip((radius - 0.35) / 0.85, 0, 1)
    alpha = (mask * strength).astype(np.uint8)

    vignette = Image.new("RGBA", img.size, (0, 0, 0, 0))
    vignette.putalpha(Image.fromarray(alpha))
    black = Image.new("RGBA", img.size, (0, 0, 0, 255))
    clear = Image.new("RGBA", img.size, (0, 0, 0, 0))
    vignette_layer = Image.composite(black, clear, vignette.getchannel("A"))
    return Image.alpha_composite(img.convert("RGBA"), vignette_layer)


def render_terminal_overlay(
    buffer: str,
    cursor_visible: bool,
    size: Tuple[int, int],
    font: ImageFont.ImageFont,
    color: Tuple[int, int, int],
    margin_x: int,
    margin_y: int,
    line_spacing: int,
    cursor_style: str,
    scroll_mode: str,
) -> Image.Image:
    w, h = size
    char_w, char_h = measure_mono(font)
    max_cols = max(1, (w - 2 * margin_x) // char_w)
    max_lines = max(1, (h - 2 * margin_y) // (char_h + line_spacing))

    lines = wrap_buffer(buffer, max_cols)[-max_lines:]
    text_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(text_layer)

    text_color = (*color, 235)
    line_height = char_h + line_spacing

    if scroll_mode == "up":
        y = h - margin_y - (len(lines) * line_height)
        y = max(margin_y, y)
    else:
        y = margin_y

    last_line_width = 0

    for line in lines:
        draw.text((margin_x, y), line, font=font, fill=text_color)
        last_line_width = text_size(draw, line, font)[0]
        y += line_height

    if cursor_visible and cursor_style != "none":
        cursor_x = margin_x + last_line_width
        if scroll_mode == "up":
            first_y = h - margin_y - (len(lines) * line_height)
            first_y = max(margin_y, first_y)
            cursor_y = first_y + (len(lines) - 1) * line_height if lines else h - margin_y - char_h
        else:
            cursor_y = margin_y + (len(lines) - 1) * line_height if lines else margin_y

        cursor_w = max(2, char_w)
        cursor_h = max(8, char_h)

        if cursor_style == "underline":
            draw.rectangle(
                (cursor_x, cursor_y + cursor_h - 3, cursor_x + cursor_w, cursor_y + cursor_h),
                fill=text_color,
            )
        elif cursor_style == "bar":
            draw.rectangle((cursor_x, cursor_y, cursor_x + 2, cursor_y + cursor_h), fill=text_color)
        else:
            draw.rectangle(
                (cursor_x, cursor_y + 2, cursor_x + cursor_w, cursor_y + cursor_h + 2),
                fill=(*color, 150),
            )

    return text_layer


def composite_terminal_frame(
    background: Image.Image,
    buffer: str,
    cursor_visible: bool,
    font: ImageFont.ImageFont,
    color: Tuple[int, int, int],
    margin_x: int,
    margin_y: int,
    line_spacing: int,
    glow_radius: float,
    cursor_style: str,
    scroll_mode: str,
    frame_index: int,
    flicker: bool,
) -> np.ndarray:
    base = background.convert("RGBA")
    size = base.size

    text_layer = render_terminal_overlay(
        buffer=buffer,
        cursor_visible=cursor_visible,
        size=size,
        font=font,
        color=color,
        margin_x=margin_x,
        margin_y=margin_y,
        line_spacing=line_spacing,
        cursor_style=cursor_style,
        scroll_mode=scroll_mode,
    )

    glow_layer = text_layer.filter(ImageFilter.GaussianBlur(radius=glow_radius))
    glow_layer2 = text_layer.filter(ImageFilter.GaussianBlur(radius=glow_radius * 2.2))

    combined = Image.alpha_composite(base, glow_layer2)
    combined = Image.alpha_composite(combined, glow_layer)
    combined = Image.alpha_composite(combined, text_layer)
    combined = add_scanlines(combined)
    combined = add_vignette(combined)

    arr = np.asarray(combined.convert("RGB")).astype(np.float32)

    if flicker:
        factor = 0.97 + 0.03 * math.sin(frame_index * 0.73)
        arr *= factor

    return np.clip(arr, 0, 255).astype(np.uint8)


def render_rgb_terminal_image(
    background: Image.Image,
    buffer: str,
    cursor_visible: bool,
    font: ImageFont.ImageFont,
    color: Tuple[int, int, int],
    margin_x: int,
    margin_y: int,
    line_spacing: int,
    glow_radius: float,
    cursor_style: str,
    scroll_mode: str,
) -> Image.Image:
    arr = composite_terminal_frame(
        background=background,
        buffer=buffer,
        cursor_visible=cursor_visible,
        font=font,
        color=color,
        margin_x=margin_x,
        margin_y=margin_y,
        line_spacing=line_spacing,
        glow_radius=glow_radius,
        cursor_style=cursor_style,
        scroll_mode=scroll_mode,
        frame_index=0,
        flicker=False,
    )
    return Image.fromarray(arr).convert("RGB")


# -----------------------------
# Timeline construction
# -----------------------------

def cursor_on(frame_number: int, fps: int) -> bool:
    return int((frame_number / fps) * 2) % 2 == 0


def make_plain_state(
    states: List[RenderState],
    fps: int,
    buffer: str,
    background: Image.Image,
    style: StyleState,
) -> None:
    states.append(
        RenderState(
            buffer=buffer,
            cursor_visible=cursor_on(len(states), fps),
            background=background,
            font_path=style.font_path,
            font_size=style.font_size,
            color=style.color,
            cursor_style=style.cursor_style,
            scroll_mode=style.scroll_mode,
        )
    )


def build_timeline(
    events: Iterable[Event],
    fps: int,
    default_speed: float,
    end_hold: float,
    initial_background: Image.Image,
    initial_font_path: Optional[str],
    initial_font_size: int,
    initial_beep_frequency: float,
    initial_color: Tuple[int, int, int],
    initial_cursor_style: str,
    script_path: Path,
    output_size: Tuple[int, int],
    margin_x: int,
    margin_y: int,
    line_spacing: int,
    glow_radius: float,
    raster_line_height: int,
    scroll_step: int,
) -> Tuple[List[RenderState], List[Tuple[float, float]], float]:
    states: List[RenderState] = []
    beep_events: List[Tuple[float, float]] = []

    buffer = ""
    background = initial_background
    style = StyleState(
        font_path=initial_font_path,
        font_size=initial_font_size,
        beep_frequency=initial_beep_frequency,
        speed=default_speed,
        color=initial_color,
        cursor_style=normalize_cursor_style(initial_cursor_style),
        scroll_mode="down",
    )

    font_cache: Dict[Tuple[Optional[str], int], ImageFont.ImageFont] = {}

    def get_font(font_path: Optional[str], font_size: int) -> ImageFont.ImageFont:
        key = (font_path, font_size)
        if key not in font_cache:
            if font_path:
                resolved = resolve_asset_path(font_path, script_path)
                font_cache[key] = load_font(str(resolved), font_size)
            else:
                font_cache[key] = load_font(None, font_size)
        return font_cache[key]

    def add_plain_frame() -> None:
        make_plain_state(states, fps, buffer, background, style)

    for event in events:
        if event.type == "bg":
            if event.bg_color:
                new_bg = make_solid_background(event.bg_color, output_size)
            elif event.bg_path:
                new_bg = fit_background(resolve_asset_path(event.bg_path, script_path), output_size)
            else:
                continue

            transition = (event.bg_transition or "cut").lower()
            duration = event.bg_duration if event.bg_duration is not None else 0.0

            if transition == "cut" or duration <= 0:
                background = new_bg
                add_plain_frame()
                continue

            frame_count = max(1, int(round(duration * fps)))

            if transition == "scroll":
                trans_font = get_font(style.font_path, style.font_size)
                old_full = render_rgb_terminal_image(
                    background=background,
                    buffer=buffer,
                    cursor_visible=cursor_on(len(states), fps),
                    font=trans_font,
                    color=style.color,
                    margin_x=margin_x,
                    margin_y=margin_y,
                    line_spacing=line_spacing,
                    glow_radius=glow_radius,
                    cursor_style=style.cursor_style,
                    scroll_mode=style.scroll_mode,
                )
                new_full = render_rgb_terminal_image(
                    background=new_bg,
                    buffer=buffer,
                    cursor_visible=cursor_on(len(states), fps),
                    font=trans_font,
                    color=style.color,
                    margin_x=margin_x,
                    margin_y=margin_y,
                    line_spacing=line_spacing,
                    glow_radius=glow_radius,
                    cursor_style=style.cursor_style,
                    scroll_mode=style.scroll_mode,
                )

                for i in range(frame_count):
                    progress = i / max(1, frame_count - 1)
                    transition_bg = transition_scroll(old_full, new_full, progress, scroll_step)
                    states.append(
                        RenderState(
                            buffer="",
                            cursor_visible=False,
                            background=transition_bg,
                            font_path=style.font_path,
                            font_size=style.font_size,
                            color=style.color,
                            cursor_style=style.cursor_style,
                            scroll_mode=style.scroll_mode,
                        )
                    )
            else:
                for i in range(frame_count):
                    progress = i / max(1, frame_count - 1)
                    if transition == "pixelize":
                        transition_bg = transition_pixelize(background, new_bg, progress)
                    elif transition == "raster":
                        transition_bg = transition_raster(background, new_bg, progress, raster_line_height)
                    else:
                        transition_bg = new_bg

                    states.append(
                        RenderState(
                            buffer=buffer,
                            cursor_visible=cursor_on(len(states), fps),
                            background=transition_bg,
                            font_path=style.font_path,
                            font_size=style.font_size,
                            color=style.color,
                            cursor_style=style.cursor_style,
                            scroll_mode=style.scroll_mode,
                        )
                    )

            background = new_bg

        elif event.type == "clear":
            buffer = ""
            add_plain_frame()

        elif event.type == "speed":
            if event.speed and event.speed > 0:
                style.speed = event.speed

        elif event.type == "font":
            style.font_path = event.font_path

        elif event.type == "fontsize":
            if event.font_size and event.font_size > 0:
                style.font_size = event.font_size

        elif event.type == "beepfreq":
            if event.beep_frequency and event.beep_frequency > 0:
                style.beep_frequency = event.beep_frequency

        elif event.type == "cursor":
            if event.cursor_style:
                style.cursor_style = normalize_cursor_style(event.cursor_style)
            if event.color_value:
                style.color = parse_color_value(event.color_value)

        elif event.type == "color":
            if event.color_value:
                style.color = parse_color_value(event.color_value)

        elif event.type == "scroll":
            if event.scroll_mode in {"up", "down"}:
                style.scroll_mode = event.scroll_mode

        elif event.type == "shutdown":
            duration = max(0.0, event.seconds)
            buffer = ""
            shutdown_font = get_font(style.font_path, style.font_size)
            screen_img = render_rgb_terminal_image(
                background=background,
                buffer="",
                cursor_visible=False,
                font=shutdown_font,
                color=style.color,
                margin_x=margin_x,
                margin_y=margin_y,
                line_spacing=line_spacing,
                glow_radius=glow_radius,
                cursor_style="none",
                scroll_mode=style.scroll_mode,
            )

            frame_count = max(1, int(round(duration * fps)))
            for i in range(frame_count):
                progress = i / max(1, frame_count - 1)
                shutdown_bg = transition_shutdown_frame(screen_img, progress)
                states.append(
                    RenderState(
                        buffer="",
                        cursor_visible=False,
                        background=shutdown_bg,
                        font_path=style.font_path,
                        font_size=style.font_size,
                        color=style.color,
                        cursor_style="none",
                        scroll_mode=style.scroll_mode,
                    )
                )

            background = Image.new("RGB", output_size, (0, 0, 0))
            style.cursor_style = "none"
            buffer = ""

        elif event.type == "pause":
            frame_count = max(1, int(round(event.seconds * fps)))
            for _ in range(frame_count):
                add_plain_frame()

        elif event.type == "type":
            if event.font_path is not None:
                style.font_path = event.font_path
            if event.font_size is not None:
                style.font_size = event.font_size
            if event.beep_frequency is not None:
                style.beep_frequency = event.beep_frequency
            if event.color_value is not None:
                style.color = parse_color_value(event.color_value)
            if event.cursor_style is not None:
                style.cursor_style = normalize_cursor_style(event.cursor_style)
            if event.scroll_mode is not None:
                style.scroll_mode = event.scroll_mode
            if event.speed and event.speed > 0:
                style.speed = event.speed

            frames_per_char = max(1, int(round((1.0 / style.speed) * fps)))

            for ch in event.text:
                buffer += ch
                current_time = len(states) / fps
                if ch not in {" ", "\t", "\n", "\r"}:
                    beep_events.append((current_time, style.beep_frequency))
                for _ in range(frames_per_char):
                    add_plain_frame()

        else:
            raise ValueError(f"Unknown event type: {event.type}")

    for _ in range(max(1, int(round(end_hold * fps)))):
        add_plain_frame()

    total_duration = len(states) / fps
    return states, beep_events, total_duration


# -----------------------------
# Audio generation
# -----------------------------

def write_beep_audio(
    path: Path,
    beep_events: List[Tuple[float, float]],
    duration: float,
    sample_rate: int = 44100,
    beep_duration: float = 0.035,
    volume: float = 0.12,
) -> None:
    total_samples = max(1, int(math.ceil(duration * sample_rate)))
    audio = np.zeros(total_samples, dtype=np.float32)
    beep_samples = max(1, int(beep_duration * sample_rate))
    fade_len = max(1, int(0.006 * sample_rate))
    beep_cache: Dict[float, np.ndarray] = {}

    for bt, frequency in beep_events:
        frequency = round(float(frequency), 3)
        if frequency not in beep_cache:
            t = np.arange(beep_samples, dtype=np.float32) / sample_rate
            wave1 = np.sin(2 * np.pi * frequency * t)
            wave2 = 0.35 * np.sin(2 * np.pi * frequency * 1.5 * t)
            beep = (wave1 + wave2).astype(np.float32)

            envelope = np.ones(beep_samples, dtype=np.float32)
            envelope[:fade_len] = np.linspace(0, 1, fade_len)
            envelope[-fade_len:] = np.linspace(1, 0, fade_len)
            beep *= envelope * volume
            beep_cache[frequency] = beep

        beep = beep_cache[frequency]
        start = int(bt * sample_rate)
        end = min(total_samples, start + beep_samples)
        if start < total_samples and end > start:
            audio[start:end] += beep[: end - start]

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.95:
        audio *= 0.95 / peak

    pcm = (audio * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


# -----------------------------
# Command-line interface
# -----------------------------

def parse_size(value: str) -> Tuple[int, int]:
    match = re.match(r"^(\d+)x(\d+)$", value.lower().strip())
    if not match:
        raise argparse.ArgumentTypeError("Size must be WIDTHxHEIGHT, for example 640x480")
    w, h = int(match.group(1)), int(match.group(2))
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("Width and height must be greater than zero")
    return w, h


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a retro terminal intro MP4 from a script.")
    parser.add_argument("script", type=Path, help="Text script file with terminal markup commands")
    parser.add_argument("output", type=Path, help="Output MP4 filename")

    parser.add_argument("--background", type=Path, default=None, help="Optional initial background image. Defaults to black.")
    parser.add_argument("--size", type=parse_size, default=(640, 480), help="Output size, default: 640x480")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second, default: 30")
    parser.add_argument("--font", default=None, help="Initial/default .ttf/.otf font file")
    parser.add_argument("--font-size", type=int, default=26, help="Initial/default font size, default: 26")
    parser.add_argument("--color", default="80ff80", help="Initial/default text color name or hex RGB, default: green")
    parser.add_argument("--chars-per-second", type=float, default=14.0, help="Default typing speed, default: 14")
    parser.add_argument("--end-hold", type=float, default=2.0, help="Seconds to hold after script ends, default: 2")
    parser.add_argument("--margin-x", type=int, default=42, help="Left/right text margin, default: 42")
    parser.add_argument("--margin-y", type=int, default=44, help="Top/bottom text margin, default: 44")
    parser.add_argument("--line-spacing", type=int, default=6, help="Extra pixels between lines, default: 6")
    parser.add_argument("--glow-radius", type=float, default=2.0, help="Text glow blur radius, default: 2")
    parser.add_argument(
        "--cursor",
        choices=["block", "underline", "bar", "none"],
        default="block",
        help="Initial/default cursor style, default: block",
    )
    parser.add_argument("--no-flicker", action="store_true", help="Disable subtle CRT brightness flicker")
    parser.add_argument("--no-audio", action="store_true", help="Disable generated beep audio")
    parser.add_argument("--beep-frequency", type=float, default=880.0, help="Initial/default beep frequency in Hz")
    parser.add_argument("--beep-volume", type=float, default=0.12, help="Beep volume, default: 0.12")
    parser.add_argument("--beep-duration", type=float, default=0.035, help="Beep duration in seconds, default: 0.035")
    parser.add_argument(
        "--bg-transition",
        choices=sorted(VALID_TRANSITIONS),
        default="cut",
        help="Default [bg] transition, default: cut",
    )
    parser.add_argument("--bg-transition-duration", type=float, default=1.0, help="Default [bg] duration, default: 1")
    parser.add_argument("--raster-line-height", type=int, default=8, help="Raster block size in pixels, default: 8")
    parser.add_argument("--scroll-step", type=int, default=16, help="Scroll step size in pixels, default: 16")
    parser.add_argument("--shutdown-duration", type=float, default=1.2, help="Default CRT shutdown duration in seconds, default: 1.2")
    parser.add_argument("--preview-frames", action="store_true", help="Render first/middle/last frames as PNG previews")

    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.fps <= 0:
        parser.error("--fps must be greater than zero")
    if args.font_size <= 0:
        parser.error("--font-size must be greater than zero")
    if args.chars_per_second <= 0:
        parser.error("--chars-per-second must be greater than zero")
    if args.end_hold < 0:
        parser.error("--end-hold cannot be negative")
    if args.bg_transition_duration < 0:
        parser.error("--bg-transition-duration cannot be negative")
    if args.raster_line_height <= 0:
        parser.error("--raster-line-height must be greater than zero")
    if args.scroll_step <= 0:
        parser.error("--scroll-step must be greater than zero")
    if args.shutdown_duration < 0:
        parser.error("--shutdown-duration cannot be negative")
    if not 0 <= args.beep_volume <= 1:
        parser.error("--beep-volume must be between 0 and 1")
    if args.beep_frequency <= 0:
        parser.error("--beep-frequency must be greater than zero")


def make_initial_background(args: argparse.Namespace) -> Image.Image:
    if args.background is not None:
        return fit_background(args.background, args.size)
    return Image.new("RGB", args.size, (0, 0, 0))


def attach_audio(video: VideoClip, audio_clip: AudioFileClip) -> VideoClip:
    if hasattr(video, "with_audio"):
        return video.with_audio(audio_clip)
    return video.set_audio(audio_clip)


def write_video_file(video: VideoClip, output: Path, fps: int, has_audio: bool) -> None:
    kwargs = {
        "fps": fps,
        "codec": "libx264",
        "preset": "medium",
        "threads": os.cpu_count() or 4,
    }

    if has_audio:
        kwargs["audio_codec"] = "aac"
    else:
        kwargs["audio"] = False

    video.write_videofile(str(output), **kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    initial_color = parse_color_value(args.color)
    initial_cursor = normalize_cursor_style(args.cursor)

    events = parse_script(
        path=args.script,
        default_speed=args.chars_per_second,
        default_transition=args.bg_transition,
        default_transition_duration=args.bg_transition_duration,
        default_font_size=args.font_size,
        default_beep_frequency=args.beep_frequency,
        default_color_value=args.color,
        default_cursor_style=initial_cursor,
        default_shutdown_duration=args.shutdown_duration,
    )

    if not events:
        raise SystemExit("Script contains no renderable content.")

    initial_background = make_initial_background(args)

    initial_font_path: Optional[str] = args.font
    if initial_font_path:
        initial_font_path = str(resolve_asset_path(initial_font_path, args.script))

    states, beep_events, duration = build_timeline(
        events=events,
        fps=args.fps,
        default_speed=args.chars_per_second,
        end_hold=args.end_hold,
        initial_background=initial_background,
        initial_font_path=initial_font_path,
        initial_font_size=args.font_size,
        initial_beep_frequency=args.beep_frequency,
        initial_color=initial_color,
        initial_cursor_style=initial_cursor,
        script_path=args.script,
        output_size=args.size,
        margin_x=args.margin_x,
        margin_y=args.margin_y,
        line_spacing=args.line_spacing,
        glow_radius=args.glow_radius,
        raster_line_height=args.raster_line_height,
        scroll_step=args.scroll_step,
    )

    font_cache: Dict[Tuple[Optional[str], int], ImageFont.ImageFont] = {}

    def get_font_for_state(state: RenderState) -> ImageFont.ImageFont:
        key = (state.font_path, state.font_size)
        if key not in font_cache:
            if state.font_path:
                font_cache[key] = load_font(str(resolve_asset_path(state.font_path, args.script)), state.font_size)
            else:
                font_cache[key] = load_font(None, state.font_size)
        return font_cache[key]

    def make_frame(t: float) -> np.ndarray:
        frame_index = min(len(states) - 1, max(0, int(t * args.fps)))
        state = states[frame_index]
        font = get_font_for_state(state)

        return composite_terminal_frame(
            background=state.background,
            buffer=state.buffer,
            cursor_visible=state.cursor_visible and state.cursor_style != "none",
            font=font,
            color=state.color,
            margin_x=args.margin_x,
            margin_y=args.margin_y,
            line_spacing=args.line_spacing,
            glow_radius=args.glow_radius,
            cursor_style=state.cursor_style,
            scroll_mode=state.scroll_mode,
            frame_index=frame_index,
            flicker=not args.no_flicker,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)

    try:
        video = VideoClip(frame_function=make_frame, duration=duration)
    except TypeError:
        video = VideoClip(make_frame, duration=duration)

    temp_audio_path: Optional[Path] = None
    audio_clip: Optional[AudioFileClip] = None

    try:
        if not args.no_audio:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                temp_audio_path = Path(tmp.name)

            write_beep_audio(
                path=temp_audio_path,
                beep_events=beep_events,
                duration=duration,
                volume=args.beep_volume,
                beep_duration=args.beep_duration,
            )
            audio_clip = AudioFileClip(str(temp_audio_path))
            video = attach_audio(video, audio_clip)

        if args.preview_frames:
            preview_indices = sorted(set([0, len(states) // 2, len(states) - 1]))
            for idx in preview_indices:
                png_path = args.output.with_name(f"{args.output.stem}_preview_{idx:05d}.png")
                Image.fromarray(make_frame(idx / args.fps)).save(png_path)
                print(f"Wrote preview: {png_path}")

        print(f"Rendering {duration:.2f}s video at {args.size[0]}x{args.size[1]}, {args.fps} fps...")
        write_video_file(video, args.output, args.fps, has_audio=not args.no_audio)
        print(f"Done: {args.output}")
        return 0

    finally:
        if audio_clip is not None:
            audio_clip.close()
        video.close()
        if temp_audio_path and temp_audio_path.exists():
            try:
                temp_audio_path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
