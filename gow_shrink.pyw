from __future__ import annotations

import codecs
import importlib
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, font as tkfont, messagebox

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
GAMES_FILE = os.path.join(ROOT_DIR, 'tool', 'games.json')
SETTINGS_FILE = os.path.join(os.path.expanduser('~'), '.gow-shrink.json')

C = {
    'bg': '#0e1014',
    'side': '#121419',
    'side_hover': '#181b22',
    'card': '#171a21',
    'line': '#262b36',
    'line_hover': '#353b49',
    'text': '#e8eaf0',
    'muted': '#8a92a6',
    'faint': '#5c6475',
    'accent': '#5b8cff',
    'accent_hover': '#7aa2ff',
    'accent_soft': '#1c2744',
    'ok': '#3ecf8e',
    'ok_soft': '#12352a',
    'warn': '#f5b74e',
    'warn_soft': '#3a2d14',
    'err': '#ff6b6b',
    'err_soft': '#3d1d22',
    'console': '#0a0c10',
    'button': '#262b36',
    'button_hover': '#303644',
    'disabled': '#1f232c',
    'disabled_text': '#4b5262',
}

SCALE = 1.0


def px(v: float) -> int:
    return int(round(v * SCALE))


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    text: str
    saved: str
    args: tuple[str, ...]
    requires: tuple[str, ...]
    repeat_warning: str


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    exe: str
    requires: tuple[str, ...]
    install_dirs: tuple[str, ...]
    steps: tuple[Step, ...]


def load_games(path: str = GAMES_FILE) -> list[Game]:
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    games = []
    for key, g in raw.items():
        app = g.get('app')
        if not app:
            continue
        steps = tuple(Step(s['id'], s['title'], s.get('text', ''), s.get('saved', ''), tuple(s['args']),
                           tuple(s.get('requires', ())), s.get('repeat_warning', ''))
                      for s in app.get('steps', ()))
        games.append(Game(key, app.get('name', g.get('title', key)), g['exe'], tuple(app.get('requires', ())),
                          tuple(os.path.normpath(d) for d in app.get('install_dirs', ())), steps))
    return games


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def python_exe() -> str:
    exe = sys.executable
    if os.path.basename(exe).lower() == 'pythonw.exe':
        alt = os.path.join(os.path.dirname(exe), 'python.exe')
        if os.path.isfile(alt):
            return alt
    return exe


def missing_modules(names) -> list[str]:
    return [n for n in dict.fromkeys(names) if importlib.util.find_spec(n) is None]


def find_game_root(path: str, exe: str) -> str | None:
    if not path:
        return None
    d = os.path.abspath(path)
    for _ in range(5):
        if os.path.isfile(os.path.join(d, exe)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def round_rect(c: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r,
           x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
           x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Button(tk.Canvas):
    KINDS = {
        'primary': ('accent', 'accent_hover', '#ffffff'),
        'secondary': ('button', 'button_hover', 'text'),
        'ghost': (None, 'button', 'muted'),
    }

    def __init__(self, parent, text: str, command, kind: str = 'secondary', font=None, height: int = 34,
                 min_width: int = 0):
        super().__init__(parent, bg=parent['bg'], highlightthickness=0, bd=0, cursor='hand2')
        self.text, self.command, self.kind, self.font = text, command, kind, font
        self.enabled, self.hover, self.pressed = True, False, False
        self.h, self.min_width = px(height), px(min_width)
        self._resize()
        self.bind('<Enter>', lambda _: self._set(hover=True))
        self.bind('<Leave>', lambda _: self._set(hover=False, pressed=False))
        self.bind('<ButtonPress-1>', lambda _: self._set(pressed=True))
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Configure>', lambda _: self._draw())

    def _resize(self) -> None:
        self.configure(width=max(self.min_width, self.font.measure(self.text) + px(32)), height=self.h)

    def _set(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)
        self._draw()

    def _release(self, e) -> None:
        inside = 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height()
        self._set(pressed=False)
        if inside and self.enabled and self.command:
            self.command()

    def _draw(self) -> None:
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        base, hover, fg = self.KINDS[self.kind]
        if not self.enabled:
            fill, color = (C['disabled'] if base else None), C['disabled_text']
        else:
            fill = C[hover] if (self.hover or self.pressed) else (C[base] if base else None)
            color = fg if fg.startswith('#') else C[fg]
        if fill:
            round_rect(self, 0, 0, w - 1, h - 1, px(9), fill=fill, outline=fill)
        self.create_text(w / 2, h / 2, text=self.text, fill=color, font=self.font)
        self.configure(cursor='hand2' if self.enabled else 'arrow')

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self.enabled:
            self.enabled = enabled
            self._draw()

    def set_text(self, text: str) -> None:
        if text != self.text:
            self.text = text
            self._resize()
            self._draw()


class Card(tk.Canvas):
    def __init__(self, parent, pad: int = 20, radius: int = 14):
        super().__init__(parent, bg=parent['bg'], highlightthickness=0, bd=0, height=px(40))
        self.fill, self.outline = C['card'], C['line']
        self.pad, self.radius = px(pad), px(radius)
        self.body = tk.Frame(self, bg=self.fill)
        self.win = self.create_window(self.pad, self.pad, window=self.body, anchor='nw')
        self.bind('<Configure>', lambda _: self._draw())
        self.body.bind('<Configure>', lambda _: self._fit())

    def _fit(self) -> None:
        h = self.body.winfo_reqheight() + 2 * self.pad
        if int(float(self['height'])) != h:
            self.configure(height=h)

    def _draw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        self.delete('bg')
        round_rect(self, 1, 1, w - 2, h - 2, self.radius, fill=self.fill, outline=self.outline, tags='bg')
        self.tag_lower('bg')
        self.itemconfigure(self.win, width=max(1, w - 2 * self.pad))


class Switch(tk.Canvas):
    def __init__(self, parent, var: tk.BooleanVar, command=None):
        super().__init__(parent, bg=parent['bg'], highlightthickness=0, bd=0, width=px(40), height=px(22),
                         cursor='hand2')
        self.var, self.command = var, command
        self.bind('<Button-1>', self._toggle)
        var.trace_add('write', lambda *_: self._draw())
        self._draw()

    def _toggle(self, _=None) -> None:
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self) -> None:
        self.delete('all')
        w, h = px(40), px(22)
        on = self.var.get()
        track = C['accent'] if on else C['line_hover']
        round_rect(self, 0, 0, w - 1, h - 1, h / 2, fill=track, outline=track)
        r = h / 2 - px(3)
        cx = w - h / 2 if on else h / 2
        self.create_oval(cx - r, h / 2 - r, cx + r, h / 2 + r, fill='#ffffff', outline='#ffffff')


class Pill(tk.Canvas):
    STYLES = {
        'running': ('accent', 'accent_soft'),
        'done': ('ok', 'ok_soft'),
        'failed': ('err', 'err_soft'),
    }

    def __init__(self, parent, font):
        super().__init__(parent, bg=parent['bg'], highlightthickness=0, bd=0, height=px(24), width=1)
        self.font = font

    def set(self, text: str, style: str = '') -> None:
        self.delete('all')
        if not text:
            self.configure(width=1)
            return
        fg, bg = (C[k] for k in self.STYLES[style])
        w, h = self.font.measure(text) + px(22), px(24)
        self.configure(width=w)
        round_rect(self, 0, 0, w - 1, h - 1, h / 2, fill=bg, outline=bg)
        self.create_text(w / 2, h / 2, text=text, fill=fg, font=self.font)


class Bar(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, bg=parent['bg'], highlightthickness=0, bd=0, height=px(4))
        self.frac = 0.0
        self.bind('<Configure>', lambda _: self._draw())

    def set(self, frac: float) -> None:
        self.frac = max(0.0, min(1.0, frac))
        self._draw()

    def _draw(self) -> None:
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        round_rect(self, 0, 0, w - 1, h - 1, h / 2, fill=C['line'], outline=C['line'])
        if self.frac > 0:
            round_rect(self, 0, 0, max(h, (w - 1) * self.frac), h - 1, h / 2, fill=C['accent'], outline=C['accent'])


class ScrollList(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=parent['bg'])
        self.canvas = tk.Canvas(self, bg=parent['bg'], highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)
        self.inner = tk.Frame(self.canvas, bg=parent['bg'])
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.inner.bind('<Configure>', lambda _: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.bind_all('<MouseWheel>', self._wheel, add='+')

    def _wheel(self, e) -> None:
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and w is not self:
            w = w.master
        if w is self and self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units')


class NavItem(tk.Frame):
    def __init__(self, parent, game: Game, fonts, on_click):
        super().__init__(parent, bg=C['side'], cursor='hand2')
        self.game = game
        self.selected = self.hover = False
        self.bar = tk.Frame(self, bg=C['side'], width=px(3))
        self.bar.pack(side='left', fill='y')
        self.inner = tk.Frame(self, bg=C['side'], padx=px(14), pady=px(10))
        self.inner.pack(side='left', fill='both', expand=True)
        self.inner.columnconfigure(0, weight=1)
        self.name = tk.Label(self.inner, text=game.name, font=fonts['nav'], fg=C['muted'], bg=C['side'],
                             anchor='w')
        self.name.grid(row=0, column=0, sticky='ew')
        self.sub = tk.Label(self.inner, text='', font=fonts['tiny'], fg=C['faint'], bg=C['side'], anchor='w')
        self.sub.grid(row=1, column=0, sticky='ew')
        self.dot = tk.Canvas(self.inner, width=px(8), height=px(8), bg=C['side'], highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=1, rowspan=2, padx=(px(8), 0))
        for w in (self, self.bar, self.inner, self.name, self.sub, self.dot):
            w.bind('<Button-1>', lambda _: on_click(game.key))
            w.bind('<Enter>', lambda _: self._hover(True))
            w.bind('<Leave>', lambda _: self._hover(False))

    def _hover(self, on: bool) -> None:
        self.hover = on
        self._paint()

    def update_state(self, selected: bool, sub: str, dot: str) -> None:
        self.selected = selected
        self.sub.configure(text=sub)
        self.dot.delete('all')
        s = px(8)
        self.dot.create_oval(0, 0, s - 1, s - 1, fill=C[dot], outline=C[dot])
        self._paint()

    def _paint(self) -> None:
        bg = C['accent_soft'] if self.selected else (C['side_hover'] if self.hover else C['side'])
        for w in (self, self.inner, self.name, self.sub, self.dot):
            w.configure(bg=bg)
        self.bar.configure(bg=C['accent'] if self.selected else bg)
        self.name.configure(fg=C['text'] if self.selected or self.hover else C['muted'])


class App:
    def __init__(self, root: tk.Tk, games: list[Game]):
        self.root = root
        self.games = {g.key: g for g in games}
        families = set(tkfont.families(root))
        text = 'Segoe UI Variable Text' if 'Segoe UI Variable Text' in families else 'Segoe UI'
        display = 'Segoe UI Variable Display' if 'Segoe UI Variable Display' in families else text
        mono = 'Cascadia Mono' if 'Cascadia Mono' in families else 'Consolas'
        self.f = {
            'base': tkfont.Font(root, family=text, size=10),
            'small': tkfont.Font(root, family=text, size=9),
            'tiny': tkfont.Font(root, family=text, size=8),
            'bold': tkfont.Font(root, family=text, size=10, weight='bold'),
            'nav': tkfont.Font(root, family=text, size=10, weight='bold'),
            'step': tkfont.Font(root, family=text, size=11, weight='bold'),
            'brand': tkfont.Font(root, family=display, size=15, weight='bold'),
            'title': tkfont.Font(root, family=display, size=20, weight='bold'),
            'section': tkfont.Font(root, family=text, size=8, weight='bold'),
            'mono': tkfont.Font(root, family=mono, size=9),
        }
        root.configure(bg=C['bg'])

        s = load_settings()
        saved_dirs = s.get('dirs') if isinstance(s.get('dirs'), dict) else {}
        self.dirs = {g.key: saved_dirs.get(g.key) or self._autodetect(g) for g in games}
        first = s.get('game') if s.get('game') in self.games else games[0].key
        self.game = tk.StringVar(value=first)
        self.use_backup = tk.BooleanVar(value=bool(s.get('backup', False)))
        self.backup_dir = s.get('backup_dir', '')
        self.status = {(g.key, st.id): 'idle' for g in games for st in g.steps}

        self.proc: subprocess.Popen | None = None
        self.running: tuple[str, str] | None = None
        self.on_done = None
        self.out_queue: queue.Queue = queue.Queue()
        self.rows: dict[str, dict] = {}
        self.nav: dict[str, NavItem] = {}
        self.console_open = True

        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self._sidebar()
        self._content()

        self.game.trace_add('write', lambda *_: self._refresh())
        self._refresh()
        root.protocol('WM_DELETE_WINDOW', self._close)
        root.after(50, self._poll)

    def _autodetect(self, g: Game) -> str:
        return next((d for d in g.install_dirs if find_game_root(d, g.exe) == d), '')

    def _label(self, parent, text='', font='base', fg='text', **kw) -> tk.Label:
        return tk.Label(parent, text=text, font=self.f[font], fg=C[fg], bg=parent['bg'], **kw)

    def _wrap(self, label: tk.Label) -> tk.Label:
        label.bind('<Configure>', lambda e: label.configure(wraplength=max(1, e.width)))
        return label

    def _sidebar(self) -> None:
        side = tk.Frame(self.root, bg=C['side'], width=px(250))
        side.grid(row=0, column=0, sticky='ns')
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(3, weight=1)
        tk.Frame(self.root, bg=C['line'], width=1).grid(row=0, column=0, sticky='nse')
        head = tk.Frame(side, bg=C['side'], padx=px(20), pady=px(22))
        head.grid(row=0, column=0, sticky='ew')
        self._label(head, 'gow-shrink', 'brand').pack(anchor='w')
        self._label(head, 'Install size reduction', 'small', 'faint').pack(anchor='w')
        self._label(side, 'GAMES', 'section', 'faint').grid(row=1, column=0, sticky='w', padx=px(20),
                                                            pady=(px(6), px(6)))
        self.nav_list = ScrollList(side)
        self.nav_list.grid(row=3, column=0, sticky='nsew', pady=(0, px(12)))
        foot = tk.Frame(side, bg=C['side'], padx=px(20), pady=px(14))
        foot.grid(row=4, column=0, sticky='ew')
        self._label(foot, f'{len(self.games)} supported games', 'tiny', 'faint').pack(anchor='w')

    def _content(self) -> None:
        main = tk.Frame(self.root, bg=C['bg'], padx=px(32), pady=px(26))
        main.grid(row=0, column=1, sticky='nsew')
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)
        self.main = main

        head = tk.Frame(main, bg=C['bg'])
        head.grid(row=0, column=0, sticky='ew')
        head.columnconfigure(0, weight=1)
        self.title = self._label(head, '', 'title', anchor='w')
        self.title.grid(row=0, column=0, sticky='w')
        self.exe_label = self._label(head, '', 'small', 'faint')
        self.exe_label.grid(row=0, column=1, sticky='e')

        folder = Card(main, pad=16)
        folder.grid(row=1, column=0, sticky='ew', pady=(px(16), 0))
        b = folder.body
        b.columnconfigure(1, weight=1)
        self.folder_dot = tk.Canvas(b, width=px(10), height=px(10), bg=b['bg'], highlightthickness=0, bd=0)
        self.folder_dot.grid(row=0, column=0, rowspan=2, padx=(px(2), px(14)))
        self.folder_state = self._label(b, '', 'bold', anchor='w')
        self.folder_state.grid(row=0, column=1, sticky='ew')
        self.folder_path = self._wrap(self._label(b, '', 'small', 'muted', anchor='w', justify='left'))
        self.folder_path.grid(row=1, column=1, sticky='ew')
        self.folder_button = Button(b, 'Change folder', self._choose_dir, 'secondary', self.f['small'], height=30)
        self.folder_button.grid(row=0, column=2, rowspan=2, padx=(px(12), 0))

        sec = tk.Frame(main, bg=C['bg'])
        sec.grid(row=2, column=0, sticky='ew', pady=(px(24), px(10)))
        self._label(sec, 'STEPS', 'section', 'faint').pack(side='left')
        self._label(sec, 'Run each step once, then start the game to check it.', 'small', 'faint').pack(side='right')
        self.steps_card = Card(main)
        self.steps_card.grid(row=3, column=0, sticky='ew')
        sb = self.steps_card.body
        sb.columnconfigure(1, weight=1)
        sb.columnconfigure(2, minsize=px(104))
        sb.columnconfigure(3, minsize=px(84))

        opt = Card(main, pad=16)
        opt.grid(row=4, column=0, sticky='ew', pady=(px(14), 0))
        ob = opt.body
        ob.columnconfigure(1, weight=1)
        Switch(ob, self.use_backup, self._update_backup).grid(row=0, column=0, rowspan=2, padx=(0, px(14)))
        self._label(ob, 'Keep the original files', 'bold').grid(row=0, column=1, sticky='w')
        self.backup_label = self._wrap(self._label(ob, '', 'small', 'muted', anchor='w', justify='left'))
        self.backup_label.grid(row=1, column=1, sticky='ew')
        self.backup_button = Button(ob, 'Choose folder', self._choose_backup, 'ghost', self.f['small'], height=28)
        self.backup_button.grid(row=0, column=2, rowspan=2, padx=(px(10), 0))

        out = tk.Frame(main, bg=C['bg'])
        out.grid(row=5, column=0, sticky='nsew', pady=(px(16), 0))
        out.columnconfigure(0, weight=1)
        out.rowconfigure(1, weight=1)
        bar = tk.Frame(out, bg=C['bg'])
        bar.grid(row=0, column=0, sticky='ew', pady=(0, px(6)))
        self.status_label = self._label(bar, 'Ready', 'small', 'muted')
        self.status_label.pack(side='left')
        self.console_button = Button(bar, 'Hide output', self._toggle_console, 'ghost', self.f['small'], height=26)
        self.console_button.pack(side='right')
        self.stop_button = Button(bar, 'Stop', self._stop, 'ghost', self.f['small'], height=26)
        self.console = tk.Frame(out, bg=C['console'], highlightthickness=1, highlightbackground=C['line'])
        self.console.grid(row=1, column=0, sticky='nsew')
        self.log = tk.Text(self.console, height=8, wrap='word', font=self.f['mono'], bg=C['console'],
                           fg='#c9cfdc', insertbackground='#c9cfdc', relief='flat', bd=0, highlightthickness=0,
                           padx=px(14), pady=px(10), state='disabled', selectbackground='#2a3142')
        self.log.pack(fill='both', expand=True)
        for tag, color in (('cmd', 'accent_hover'), ('ok', 'ok'), ('fail', 'err'), ('dim', 'faint')):
            self.log.tag_configure(tag, foreground=C[color])
        self._update_backup()

    def _ready(self, g: Game) -> bool:
        return find_game_root(self.dirs.get(g.key, ''), g.exe) is not None

    def _refresh(self) -> None:
        self._refresh_nav()
        self._refresh_header()
        self._build_steps()
        self._update_buttons()
        self._save()

    def _refresh_nav(self) -> None:
        order = sorted(self.games.values(), key=lambda g: not self._ready(g))
        for g in order:
            item = self.nav.get(g.key)
            if item is None:
                item = self.nav[g.key] = NavItem(self.nav_list.inner, g, self.f, self._select)
            item.pack_forget()
        for g in order:
            self.nav[g.key].pack(fill='x', padx=px(10), pady=px(1))
            done = sum(self.status[(g.key, st.id)] == 'done' for st in g.steps)
            if not self.dirs.get(g.key):
                sub, dot = 'Folder not set', 'faint'
            elif not self._ready(g):
                sub, dot = f'{g.exe} not found', 'err'
            else:
                sub, dot = (f'{done} of {len(g.steps)} steps done' if done else f'{len(g.steps)} steps'), 'ok'
            self.nav[g.key].update_state(g.key == self.game.get(), sub, dot)

    def _refresh_header(self) -> None:
        g = self.games[self.game.get()]
        self.title.configure(text=g.name)
        self.exe_label.configure(text=g.exe)
        d = self.dirs.get(g.key, '')
        self.folder_dot.delete('all')
        if self._ready(g):
            state, color = 'Game folder found', 'ok'
        elif d:
            state, color = f'{g.exe} was not found in this folder', 'err'
        else:
            state, color = 'Choose the game folder', 'warn'
        s = px(10)
        self.folder_dot.create_oval(0, 0, s - 1, s - 1, fill=C[color], outline=C[color])
        self.folder_state.configure(text=state, fg=C[color] if color != 'ok' else C['text'])
        self.folder_path.configure(text=d or f'Select the folder that contains {g.exe}.')

    def _build_steps(self) -> None:
        body = self.steps_card.body
        for child in body.winfo_children():
            child.destroy()
        self.rows.clear()
        g = self.games[self.game.get()]
        r = 0
        missing = missing_modules(g.requires + tuple(n for st in g.steps for n in st.requires))
        if missing:
            note = tk.Frame(body, bg=C['warn_soft'], padx=px(14), pady=px(10))
            note.grid(row=r, column=0, columnspan=5, sticky='ew', pady=(0, px(16)))
            note.columnconfigure(0, weight=1)
            tk.Label(note, text=f'Missing Python package: {", ".join(missing)}', font=self.f['bold'],
                     fg=C['warn'], bg=note['bg']).grid(row=0, column=0, sticky='w')
            tk.Label(note, text=f'{python_exe()} -m pip install {" ".join(missing)}', font=self.f['mono'],
                     fg=C['muted'], bg=note['bg'], anchor='w').grid(row=1, column=0, sticky='w')
            btn = Button(note, 'Install', lambda m=missing: self._install(m), 'primary', self.f['bold'], height=30)
            btn.grid(row=0, column=1, rowspan=2, padx=(px(12), 0))
            self.rows['_install'] = {'button': btn}
            r += 1
        if not g.steps:
            self._label(body, 'No steps are defined for this game yet.', 'small', 'muted').grid(
                row=r, column=0, columnspan=5, sticky='w')
        for i, st in enumerate(g.steps):
            if i:
                tk.Frame(body, bg=C['line'], height=1).grid(row=r, column=0, columnspan=5, sticky='ew',
                                                            pady=px(14))
                r += 1
            num = tk.Canvas(body, width=px(30), height=px(30), bg=body['bg'], highlightthickness=0, bd=0)
            num.grid(row=r, column=0, rowspan=2, sticky='n', padx=(0, px(14)))
            self._label(body, st.title, 'step').grid(row=r, column=1, sticky='w')
            self._wrap(self._label(body, st.text, 'small', 'muted', anchor='w', justify='left')).grid(
                row=r + 1, column=1, sticky='ew')
            if st.saved:
                self._label(body, f'≈ {st.saved}', 'bold', 'ok').grid(row=r, column=2, rowspan=2, sticky='e',
                                                                          padx=(px(12), px(12)))
            pill = Pill(body, self.f['small'])
            pill.grid(row=r, column=3, rowspan=2, padx=(0, px(12)))
            btn = Button(body, 'Run', lambda s=st: self._run_step(s), 'primary', self.f['bold'], min_width=104)
            btn.grid(row=r, column=4, rowspan=2)
            bar = Bar(body)
            bar.grid(row=r + 2, column=1, columnspan=4, sticky='ew', pady=(px(10), 0))
            info = self._label(body, '', 'small', 'faint', anchor='w')
            info.grid(row=r + 3, column=1, columnspan=4, sticky='ew', pady=(px(4), 0))
            bar.grid_remove()
            info.grid_remove()
            self.rows[st.id] = {'num': num, 'pill': pill, 'button': btn, 'bar': bar, 'info': info, 'n': i + 1}
            self._paint_row(g.key, st.id)
            r += 4

    def _paint_row(self, game: str, step: str) -> None:
        row = self.rows.get(step)
        if row is None or game != self.game.get():
            return
        state = self.status[(game, step)]
        num, s = row['num'], px(30)
        num.delete('all')
        if state == 'done':
            num.create_oval(1, 1, s - 1, s - 1, fill=C['ok_soft'], outline=C['ok_soft'])
            num.create_text(s / 2, s / 2, text='✓', fill=C['ok'], font=self.f['bold'])
        else:
            color = C['accent'] if state == 'running' else C['line_hover']
            num.create_oval(1, 1, s - 1, s - 1, outline=color, width=px(1.5))
            num.create_text(s / 2, s / 2, text=str(row['n']), fill=C['text'] if state == 'running' else C['muted'],
                            font=self.f['bold'])
        row['pill'].set(*{'idle': ('',), 'running': ('Running', 'running'), 'done': ('Done', 'done'),
                          'failed': ('Failed', 'failed')}[state])
        row['button'].set_text('Run again' if state in ('done', 'failed') else 'Run')
        if state == 'running':
            row['bar'].grid()
            row['info'].grid()
        elif state != 'failed':
            row['bar'].grid_remove()
            row['info'].grid_remove()

    def _update_buttons(self) -> None:
        busy = self.proc is not None
        g = self.games[self.game.get()]
        ok_game = self._ready(g) and not missing_modules(g.requires)
        for st in g.steps:
            row = self.rows.get(st.id)
            if row:
                row['button'].set_enabled(not busy and ok_game and not missing_modules(st.requires))
        if '_install' in self.rows:
            self.rows['_install']['button'].set_enabled(not busy)
        self.folder_button.set_enabled(not busy)
        self.backup_button.set_enabled(not busy and self.use_backup.get())
        if busy:
            self.stop_button.pack(side='right', padx=(0, px(6)))
        else:
            self.stop_button.pack_forget()

    def _update_backup(self) -> None:
        if not self.use_backup.get():
            text = 'Off: the original files are replaced or deleted, as in the quick start.'
        elif self.backup_dir:
            text = (f'Originals are moved to {self.backup_dir}\\<game>-<step>. Undo a step with: '
                    f'python -m tool restore --backup "{self.backup_dir}\\<game>-<step>"')
        else:
            text = 'Choose a folder on a drive with enough free space for the originals.'
        self.backup_label.configure(text=text)
        self.backup_button.set_enabled(self.proc is None and self.use_backup.get())
        self._save()

    def _select(self, key: str) -> None:
        if key == self.game.get():
            return
        if self.proc is not None:
            self._set_status('Wait until the running step finishes.', 'warn')
            return
        self.game.set(key)

    def _choose_dir(self) -> None:
        g = self.games[self.game.get()]
        d = filedialog.askdirectory(title=f'Select the {g.name} folder', initialdir=self.dirs.get(g.key) or None)
        if not d:
            return
        root = find_game_root(os.path.normpath(d), g.exe)
        if root is None:
            other = next((o for o in self.games.values() if o.key != g.key and find_game_root(d, o.exe)), None)
            hint = (f'This is the {other.name} folder.' if other else
                    f'{g.exe} was not found in this folder or its parents.')
            messagebox.showerror('Game folder', f'{hint}\n\nSelect the folder that contains {g.exe}.')
            return
        self.dirs[g.key] = root
        self._refresh()

    def _choose_backup(self) -> None:
        d = filedialog.askdirectory(title='Select a folder for the original files', initialdir=self.backup_dir or None)
        if d:
            self.backup_dir = os.path.normpath(d)
            self._update_backup()

    def _run_step(self, st: Step) -> None:
        g = self.games[self.game.get()]
        args = [a.replace('{game_dir}', self.dirs[g.key]) for a in st.args] + ['--game', g.key]
        if self.use_backup.get():
            if not self.backup_dir:
                self._choose_backup()
                if not self.backup_dir:
                    return
            target = os.path.join(self.backup_dir, f'{g.key}-{st.id}')
            args += ['--backup', target]
            where = f'The original files are moved to:\n{target}'
        else:
            where = 'No backup: the original files are replaced or deleted and cannot be restored.'
        again = ''
        if st.repeat_warning and self.status[(g.key, st.id)] == 'done':
            again = f'\n\nThis step already ran. {st.repeat_warning}'
        if not messagebox.askyesno(f'{g.name} · {st.title}',
                                   f'{st.text}\n\n{where}{again}\n\nStart the game after this step to check it. '
                                   f'Continue?', icon='warning'):
            return
        self.running = (g.key, st.id)
        self.status[self.running] = 'running'
        self._paint_row(*self.running)
        self.rows[st.id]['bar'].set(0)
        self.rows[st.id]['info'].configure(text='Starting...', fg=C['faint'])
        self._spawn([python_exe(), '-u', '-m', 'tool', *args], st.title, self._step_done)

    def _step_done(self, code: int) -> None:
        key, self.running = self.running, None
        if key is None:
            return
        self.status[key] = 'done' if code == 0 else 'failed'
        self._paint_row(*key)
        row = self.rows.get(key[1])
        if row and code != 0 and key[0] == self.game.get():
            row['info'].configure(text='See the output below for details.', fg=C['err'])
            row['bar'].grid_remove()
        self._refresh_nav()
        if code == 0:
            self._set_status('Done. Start the game and check it before running the next step.', 'ok')
        else:
            self._set_status(f'Failed (exit code {code}).', 'err')
            if not self.console_open:
                self._toggle_console()

    def _install(self, names: list[str]) -> None:
        def done(code: int) -> None:
            importlib.invalidate_caches()
            self._build_steps()
            self._set_status('Installed.' if code == 0 else 'Install failed.', 'ok' if code == 0 else 'err')

        self._spawn([python_exe(), '-m', 'pip', 'install', *names], 'Installing ' + ', '.join(names), done)

    def _spawn(self, cmd: list[str], label: str, on_done) -> None:
        if self.proc is not None:
            return
        shown = ' '.join(f'"{a}"' if ' ' in a or '(' in a else a for a in cmd[1:] if a != '-u')
        self._write(f'\n$ python {shown}\n')
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            self.proc = subprocess.Popen(cmd, cwd=ROOT_DIR, env=env, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)
        except OSError as e:
            self._write(f'failed to start: {e}\n')
            on_done(1)
            return
        self.on_done = on_done
        self._set_status(f'{label}...', 'accent')
        self._update_buttons()
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def _reader(self, proc: subprocess.Popen) -> None:
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        pending = ''
        while True:
            chunk = proc.stdout.read1(4096)
            text = pending + decoder.decode(chunk, final=not chunk)
            pending = ''
            if chunk and text.endswith('\r'):
                text, pending = text[:-1], '\r'
            if text:
                self.out_queue.put(text)
            if not chunk:
                break
        self.out_queue.put((proc.wait(),))

    def _poll(self) -> None:
        try:
            while True:
                item = self.out_queue.get_nowait()
                if isinstance(item, tuple):
                    self._finished(item[0])
                else:
                    self._write(item)
                    self._progress(item)
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _progress(self, text: str) -> None:
        if not self.running or self.running[0] != self.game.get():
            return
        row = self.rows.get(self.running[1])
        lines = [ln.strip() for ln in re.split(r'[\r\n]', text) if ln.strip()]
        if not row or not lines:
            return
        m = re.search(r'(\d{1,3}(?:\.\d+)?)%', lines[-1])
        if m:
            row['bar'].set(float(m.group(1)) / 100)
        row['info'].configure(text=lines[-1][:140], fg=C['faint'])

    def _finished(self, code: int) -> None:
        self.proc = None
        self._write(f'[exit code {code}]\n')
        done, self.on_done = self.on_done, None
        if done:
            done(code)
        self._update_buttons()

    def _set_status(self, text: str, color: str = 'muted') -> None:
        self.status_label.configure(text=text, fg=C[color])

    def _toggle_console(self) -> None:
        self.console_open = not self.console_open
        if self.console_open:
            self.console.grid()
        else:
            self.console.grid_remove()
        self.console_button.set_text('Hide output' if self.console_open else 'Show output')

    def _write(self, text: str) -> None:
        self.log.configure(state='normal')
        first = int(self.log.index('end-1c').split('.')[0])
        for i, part in enumerate(text.replace('\r\n', '\n').split('\r')):
            if i:
                self.log.delete('end-1c linestart', 'end-1c')
            self.log.insert('end', part)
        last = int(self.log.index('end-1c').split('.')[0])
        for n in range(first, last + 1):
            start, end = f'{n}.0', f'{n}.end'
            line = self.log.get(start, end)
            for tag in ('cmd', 'ok', 'fail', 'dim'):
                self.log.tag_remove(tag, start, end)
            tag = ('cmd' if line.startswith('$ ') else
                   'fail' if ('[FAIL]' in line or 'Error' in line or 'error:' in line
                              or (line.startswith('[exit code') and line != '[exit code 0]')) else
                   'ok' if '[OK]' in line or '[RESTORED]' in line or line == '[exit code 0]' else
                   'dim' if line.startswith('[SKIP]') else None)
            if tag:
                self.log.tag_add(tag, start, end)
        self.log.see('end')
        self.log.configure(state='disabled')

    def _stop(self) -> None:
        if self.proc and messagebox.askyesno(
                'Stop', 'Stop the running step? A file that is being written may be left unfinished.',
                icon='warning'):
            self.proc.terminate()

    def _save(self) -> None:
        save_settings({'game': self.game.get(), 'dirs': self.dirs, 'backup': self.use_backup.get(),
                       'backup_dir': self.backup_dir})

    def _close(self) -> None:
        if self.proc is not None:
            if not messagebox.askyesno('Quit', 'A step is still running. Stop it and quit?', icon='warning'):
                return
            self.proc.terminate()
        self._save()
        self.root.destroy()


def setup_window(root: tk.Tk) -> None:
    global SCALE
    SCALE = max(1.0, root.winfo_fpixels('1i') / 96)
    root.title('gow-shrink')
    root.geometry(f'{px(1040)}x{px(820)}')
    root.minsize(px(900), px(680))
    if os.name == 'nt':
        try:
            import ctypes
            root.update()
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except (AttributeError, OSError):
            pass


def main() -> int:
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    games = load_games()
    if not games:
        root.withdraw()
        messagebox.showerror('gow-shrink', f'No games with an "app" section in {GAMES_FILE}.')
        return 1
    setup_window(root)
    App(root, games)
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
