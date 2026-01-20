#!/bin/bash
# Stop CursorFX
pkill -f 'python3.*cursorfx.py' && echo "CursorFX stopped" || echo "CursorFX not running"
