"""
PROJECT: AIRGAP — Web Browser Runner
Bridges the desktop Pygame application to any web browser via HTTP streaming & input forwarding.
Access the game at: http://localhost:8080 or http://127.0.0.1:8080
"""

import sys
import os
import io
import time
import json
import threading
import socket
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer

import pygame as pg

# ---------------------------------------------------------------------------
# Virtual Input & Frame Buffer System
# ---------------------------------------------------------------------------

_virtual_keys = {}
_virtual_mouse_pos = (0, 0)
_virtual_mouse_buttons = [False, False, False]
_latest_frame = None
_frame_lock = threading.Lock()
_frame_event = threading.Event()

# Browser Key Code -> Pygame Key Mapping
KEY_MAP = {
    'KeyW': pg.K_w, 'ArrowUp': pg.K_UP,
    'KeyA': pg.K_a, 'ArrowLeft': pg.K_LEFT,
    'KeyS': pg.K_s, 'ArrowDown': pg.K_DOWN,
    'KeyD': pg.K_d, 'ArrowRight': pg.K_RIGHT,
    'Space': pg.K_SPACE,
    'Enter': pg.K_RETURN,
    'NumpadEnter': pg.K_RETURN,
    'Escape': pg.K_ESCAPE,
    'Tab': pg.K_TAB,
    'ShiftLeft': pg.K_LSHIFT, 'ShiftRight': pg.K_RSHIFT,
    'ControlLeft': pg.K_LCTRL, 'ControlRight': pg.K_RCTRL,
    'AltLeft': pg.K_LALT, 'AltRight': pg.K_RALT,
    'KeyH': pg.K_h,
    'Backspace': pg.K_BACKSPACE,
}

# Hook Pygame Key & Mouse functions so virtual web input is transparently recognized
orig_get_pressed = pg.key.get_pressed

class VirtualKeyState:
    def __init__(self, real_keys, virt_dict):
        self.real = real_keys
        self.virt = virt_dict

    def __getitem__(self, k):
        return bool(self.virt.get(k, False) or (k < len(self.real) and self.real[k]))

def custom_get_pressed():
    return VirtualKeyState(orig_get_pressed(), _virtual_keys)

pg.key.get_pressed = custom_get_pressed


# Hook Display Flip to capture frames for the web browser
orig_flip = pg.display.flip
orig_update = pg.display.update

_last_capture_time = 0
_CAPTURE_INTERVAL = 1.0 / 35.0  # Cap stream at ~35 FPS for ultra-smooth web play

def capture_screen():
    global _latest_frame, _last_capture_time
    now = time.time()
    if now - _last_capture_time < _CAPTURE_INTERVAL:
        return
    _last_capture_time = now

    surf = pg.display.get_surface()
    if surf is not None:
        buf = io.BytesIO()
        pg.image.save(surf, buf, "JPEG")
        data = buf.getvalue()
        with _frame_lock:
            _latest_frame = data
        _frame_event.set()

def custom_flip():
    orig_flip()
    capture_screen()

def custom_update(*args, **kwargs):
    orig_update(*args, **kwargs)
    capture_screen()

pg.display.flip = custom_flip
pg.display.update = custom_update


# ---------------------------------------------------------------------------
# HTML5 Web Client Interface
# ---------------------------------------------------------------------------

HTML_CLIENT = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PROJECT: AIRGAP — Web Console</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: #080c14;
            color: #e2e8f0;
            font-family: 'Segoe UI', Roboto, -apple-system, sans-serif;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            overflow-x: hidden;
            user-select: none;
        }
        header {
            width: 100%;
            background: linear-gradient(180deg, #0f172a 0%, #080c14 100%);
            border-bottom: 2px solid #1e293b;
            padding: 12px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .logo-badge {
            background: #ef4444;
            color: #ffffff;
            font-weight: 900;
            font-size: 11px;
            padding: 4px 8px;
            border-radius: 4px;
            letter-spacing: 1.5px;
        }
        .title {
            font-size: 18px;
            font-weight: 800;
            letter-spacing: 2px;
            color: #38bdf8;
        }
        .status {
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 12px;
            color: #4ade80;
            background: #052e16;
            padding: 5px 12px;
            border-radius: 20px;
            border: 1px solid #166534;
        }
        .pulse {
            width: 8px;
            height: 8px;
            background: #4ade80;
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(1.3); }
            100% { opacity: 1; transform: scale(1); }
        }
        #game-container {
            margin-top: 14px;
            position: relative;
            background: #000000;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.8), 0 0 20px rgba(56, 189, 248, 0.15);
            border: 2px solid #334155;
            max-width: 95vw;
        }
        #viewport {
            display: block;
            width: 1280px;
            max-width: 95vw;
            height: auto;
            aspect-ratio: 1280 / 640;
            object-fit: contain;
            cursor: default;
            user-select: none;
            -webkit-user-select: none;
            -webkit-user-drag: none;
        }
        /* Controls Overlay */
        .controls-bar {
            margin-top: 16px;
            margin-bottom: 24px;
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 10px;
            max-width: 1280px;
            width: 95vw;
        }
        .btn {
            background: #1e293b;
            color: #f1f5f9;
            border: 1px solid #475569;
            padding: 10px 18px;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 700;
            cursor: default;
            user-select: none;
            -webkit-user-select: none;
            -webkit-user-drag: none;
            transition: all 0.1s ease;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        }
        .btn:active, .btn.pressed {
            background: #38bdf8;
            color: #0f172a;
            transform: translateY(2px);
            box-shadow: 0 1px 2px rgba(0,0,0,0.5);
        }
        .btn-red { border-color: #ef4444; color: #f87171; }
        .btn-red:active, .btn-red.pressed { background: #ef4444; color: white; }
        .btn-orange { border-color: #f97316; color: #fb923c; }
        .btn-orange:active, .btn-orange.pressed { background: #f97316; color: white; }
        .btn-cyan { border-color: #06b6d4; color: #22d3ee; }
        .btn-cyan:active, .btn-cyan.pressed { background: #06b6d4; color: white; }

        .shortcuts-hint {
            color: #94a3b8;
            font-size: 13px;
            margin-top: 6px;
            text-align: center;
        }
        .badge {
            background: #334155;
            color: #cbd5e1;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <span class="logo-badge">LABORATORY CONSOLE</span>
            <span class="title">PROJECT: AIRGAP</span>
        </div>
        <div class="status">
            <div class="pulse"></div>
            <span>CONECTADO AL NÚCLEO LOCAL</span>
        </div>
    </header>

    <div id="game-container">
        <img id="viewport" src="/stream" alt="PROJECT AIRGAP Feed" tabindex="0" draggable="false">
    </div>

    <div class="shortcuts-hint">
        🎮 <b>Controles de Teclado:</b>
        <span class="badge">WASD / Flechas</span> Moverse &bull;
        <span class="badge">ESPACIO</span> Interactuar / Entrar en Ducto &bull;
        <span class="badge">ALT</span> Salto entre Nodos (IA) &bull;
        <span class="badge">ENTER</span> Revocar Acceso &bull;
        <span class="badge">CTRL</span> Pulso EM &bull;
        <span class="badge">SHIFT</span> Sabotaje GPU &bull;
        <span class="badge">TAB</span> Mapa
    </div>

    <div style="display:flex; gap:12px; justify-content:center; margin: 12px 0;">
        <button class="btn btn-cyan" style="background: #059669; font-size:15px; border-color:#34d399;" onclick="quickPlay()">?? ENTRAR A PARTIDA (FREEPLAY)</button>
        <button class="btn btn-orange" onclick="quickOnline()">?? ENTRAR A MULTIJUGADOR</button>
    </div>
    <div class="controls-bar">
        <button class="btn btn-cyan" data-code="KeyW">⬆ W</button>
        <button class="btn btn-cyan" data-code="KeyA">⬅ A</button>
        <button class="btn btn-cyan" data-code="KeyS">⬇ S</button>
        <button class="btn btn-cyan" data-code="KeyD">➡ D</button>
        <button class="btn" data-code="Space">⚡ ESPACIO</button>
        <button class="btn btn-red" data-code="Enter">🔒 REVOCAR (ENTER)</button>
        <button class="btn btn-orange" data-code="ControlLeft">💡 PULSO EM (CTRL)</button>
        <button class="btn btn-orange" data-code="ShiftLeft">⚠ MELTDOWN (SHIFT)</button>
        <button class="btn" data-code="AltLeft">🌀 SALTO (ALT)</button>
        <button class="btn" data-code="Tab">🗺 MAPA (TAB)</button>
        <button class="btn" data-code="Escape">ESC</button>
    </div>

    <script>
        const viewport = document.getElementById('viewport');
        viewport.focus();
        viewport.addEventListener('dragstart', function(e) { e.preventDefault(); });

        function quickPlay() {
            sendInput('mousedown', 'MouseLeft', 640, 260);
            setTimeout(function() { sendInput('mouseup', 'MouseLeft', 640, 260); }, 100);
            setTimeout(function() {
                sendInput('mousedown', 'MouseLeft', 640, 160);
                setTimeout(function() { sendInput('mouseup', 'MouseLeft', 640, 160); }, 100);
            }, 400);
            setTimeout(function() {
                sendInput('down', 'Enter');
                setTimeout(function() { sendInput('up', 'Enter'); }, 100);
            }, 800);
        }

        function quickOnline() {
            sendInput('mousedown', 'MouseLeft', 640, 330);
            setTimeout(function() { sendInput('mouseup', 'MouseLeft', 640, 330); }, 100);
        }

        function sendInput(type, code, x, y) {
            let url = /input?type=&code=;
            if (x !== undefined && y !== undefined) {
                url += &x=&y=;
            }
            navigator.sendBeacon ? navigator.sendBeacon(url) : fetch(url, {method: 'GET', keepalive: true});
        }

        // Global Keyboard Listeners
        window.addEventListener('keydown', (e) => {
            if (['Space', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Tab'].includes(e.code)) {
                e.preventDefault();
            }
            sendInput('down', e.code);
        });

        window.addEventListener('keyup', (e) => {
            sendInput('up', e.code);
        });

        // Mouse clicks on viewport
        viewport.addEventListener('mousedown', (e) => {
            const rect = viewport.getBoundingClientRect();
            const scaleX = 1280 / rect.width;
            const scaleY = 640 / rect.height;
            const x = Math.round((e.clientX - rect.left) * scaleX);
            const y = Math.round((e.clientY - rect.top) * scaleY);
            sendInput('mousedown', 'MouseLeft', x, y);
        });

        viewport.addEventListener('mouseup', (e) => {
            const rect = viewport.getBoundingClientRect();
            const scaleX = 1280 / rect.width;
            const scaleY = 640 / rect.height;
            const x = Math.round((e.clientX - rect.left) * scaleX);
            const y = Math.round((e.clientY - rect.top) * scaleY);
            sendInput('mouseup', 'MouseLeft', x, y);
        });

        // Touch / Click Buttons
        document.querySelectorAll('.btn[data-code]').forEach(btn => {
            const code = btn.dataset.code;
            const press = (e) => {
                e.preventDefault();
                btn.classList.add('pressed');
                sendInput('down', code);
            };
            const release = (e) => {
                e.preventDefault();
                btn.classList.remove('pressed');
                sendInput('up', code);
            };

            btn.addEventListener('mousedown', press);
            btn.addEventListener('mouseup', release);
            btn.addEventListener('mouseleave', release);
            btn.addEventListener('touchstart', press, {passive: false});
            btn.addEventListener('touchend', release, {passive: false});
        });
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP Request Handler (Streaming & Input API)
# ---------------------------------------------------------------------------

class WebGameHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_CLIENT.encode("utf-8"))

        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()

            try:
                while True:
                    _frame_event.wait(timeout=0.1)
                    _frame_event.clear()
                    with _frame_lock:
                        frame = _latest_frame

                    if frame:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("utf-8"))
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass

        elif path == "/input":
            params = parse_qs(parsed.query)
            evt_type = params.get("type", [""])[0]
            code = params.get("code", [""])[0]

            if evt_type == "mousedown" or evt_type == "mouseup":
                try:
                    x = int(params.get("x", [0])[0])
                    y = int(params.get("y", [0])[0])
                    if evt_type == "mousedown":
                        pg.event.post(pg.event.Event(pg.MOUSEBUTTONDOWN, pos=(x, y), button=1))
                    else:
                        pg.event.post(pg.event.Event(pg.MOUSEBUTTONUP, pos=(x, y), button=1))
                except Exception:
                    pass

            if code in KEY_MAP:
                pg_key = KEY_MAP[code]
                if evt_type == "down":
                    _virtual_keys[pg_key] = True
                    pg.event.post(pg.event.Event(pg.KEYDOWN, key=pg_key, unicode=code[-1:] if len(code) == 4 and code.startswith('Key') else ''))
                elif evt_type == "up":
                    _virtual_keys[pg_key] = False
                    pg.event.post(pg.event.Event(pg.KEYUP, key=pg_key))

            self.send_response(204)
            self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()


def start_web_server(port=8080):
    server = ThreadingHTTPServer(('0.0.0.0', port), WebGameHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server


if __name__ == "__main__":
    PORT = 8080
    start_web_server(PORT)

    local_ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    print(f"[WEB] Servidor activo en http://localhost:{PORT} y http://{local_ip}:{PORT}")

    from game import Game

    while True:
        g = Game()
        g.menu.game_intro()
        del g
