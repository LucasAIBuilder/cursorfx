# CursorFX

Mouse pointer highlighter for screen recording and streaming with OBS.

![Example](cursorfx.svg)

## Features

- Yellow circle that follows your cursor
- Pulsing ripple animation on mouse clicks
- Fully transparent and click-through overlay
- Customizable colors, sizes, and animations

## Requirements

- Linux with X11
- Python 3
- PyQt5
- python-xlib

## Installation

```bash
# Clone the repo
git clone https://github.com/LucasAIBuilder/cursorfx.git
cd cursorfx

# Install dependencies
pip install -r requirements.txt

# Optional: Install to PATH for easy access
sudo ln -s "$(pwd)/cursorfx.py" /usr/local/bin/cursorfx

# Optional: Install desktop entry and icon
cp cursorfx.desktop ~/.local/share/applications/
cp cursorfx.svg ~/.local/share/icons/
```

## Usage

```bash
# Run directly
python3 cursorfx.py

# Or if installed to PATH
cursorfx
```

## Optional keyboard overlay and control panel

The original `cursorfx.py` remains available as-is. The optional `cursorfx_keyviz.py` adds a keyboard overlay, a small control panel, and RGB color pickers for the cursor halo and the keyboard label background and text.

Run the optional overlay with:

```bash
python3 cursorfx_keyviz.py
```

The panel can independently enable or disable the mouse halo and keyboard labels. Use `Ctrl+Alt+K` to toggle key labels and `Ctrl+Alt+O` to minimize or reopen the panel. The system tray icon also reopens the panel. Selected colors are stored in the user's local application settings.

### Keyboard input privacy

The optional overlay passively observes X11 key events so it can display pressed keys. It does not consume input, write key events to disk, or transmit them. The last displayed key label stays in memory only for the configured display timeout. Avoid entering passwords or other sensitive values while key display is enabled. This add-on requires an X11 session and the X11 RECORD extension.

### Command Line Options

```
--color R,G,B        Highlight color (default: 255,220,0 yellow)
--radius N           Circle radius in pixels (default: 25)
--opacity N          Fill opacity 0.0-1.0 (default: 0.4)
--ripple-radius N    Max ripple size (default: 60)
--ripple-duration N  Ripple animation ms (default: 400)
--offset-x N         Horizontal offset (default: 0)
--offset-y N         Vertical offset, negative=up (default: -30)
```

### Examples

```bash
# Red highlight
cursorfx --color 255,100,100

# Larger, more visible
cursorfx --radius 40 --opacity 0.6

# Adjust position if circle isn't centered on cursor
cursorfx --offset-y -25
```

## Running in the Background

### Method 1: Start detached (recommended)

Run without taking over the terminal:

```bash
nohup cursorfx > /dev/null 2>&1 &
```

### Method 2: Detach after starting

If you already started it and want your terminal back:

```bash
# 1. Suspend the process
Ctrl+Z

# 2. Resume it in the background
bg

# 3. Detach it from the terminal (so it survives terminal close)
disown
```

Now you can close the terminal and CursorFX will keep running.

### Stopping CursorFX

```bash
pkill -f cursorfx.py
```

## Troubleshooting

### Circle position is off

Adjust with `--offset-x` and `--offset-y`. Negative Y moves the circle up. The default (-30) is optimized for standard arrow cursors.

### Click ripples not working

The X11 RECORD extension is required. Verify it's available:

```bash
python3 -c "from Xlib import display; d=display.Display(); print('OK' if d.has_extension('RECORD') else 'MISSING')"
```

### Wayland

This tool requires X11. On Wayland, you may need to run your session with XWayland or switch to an X11 session.

## License

MIT License - see [LICENSE](LICENSE) for details.
