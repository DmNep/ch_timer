#!/usr/bin/env pythonw
# -*- coding: utf-8 -*-
"""Шахматный таймер для Windows.

Одни часы идут — те, чей сейчас ход. Вторые стоят.
Если активная сторона впереди больше чем на 30 минут, её циферблат
мигает и звучит сигнал: сделать ход и переключить на соперника.

Запуск: «Шахматный таймер.bat» или  pyw -3 chess_timer.py
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox

try:
    import winsound
except ImportError:  # не Windows
    winsound = None


# Сигнал, когда одна сторона впереди больше чем на столько секунд.
THRESHOLD_SECONDS = 30 * 60
BEEP_EVERY_MS = 2800

BG = "#14161c"
BANNER_BG = "#1c1f27"
TEXT = "#f4f6fb"
MUTED = "#9aa3b5"
GREEN = "#3ecf8e"
AMBER = "#e6a23c"
RED = "#e23b3b"
RED_DIM = "#6e1616"


def format_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def leader(white_seconds: float, black_seconds: float, threshold: float = THRESHOLD_SECONDS) -> str | None:
    """Кто впереди больше чем на threshold. Сравнение по целым секундам, как на циферблате."""
    gap = int(white_seconds) - int(black_seconds)
    if gap > threshold:
        return "white"
    if -gap > threshold:
        return "black"
    return None


def signal_state(white_seconds: float, black_seconds: float, active: str | None, paused: bool) -> str:
    """off — разрыв в норме; warn — впереди другая сторона; blink — пауза на лидере; alert — лидер думает."""
    who = leader(white_seconds, black_seconds)
    if who is None:
        return "off"
    if active != who:
        return "warn"
    if paused:
        return "blink"
    return "alert"


class Clock:
    def __init__(self) -> None:
        self._elapsed = 0.0
        self._started_at: float | None = None

    def start(self) -> None:
        if self._started_at is None:
            self._started_at = time.monotonic()

    def stop(self) -> None:
        if self._started_at is not None:
            self._elapsed += time.monotonic() - self._started_at
            self._started_at = None

    def reset(self) -> None:
        self._elapsed = 0.0
        self._started_at = None

    def read(self) -> float:
        if self._started_at is None:
            return self._elapsed
        return self._elapsed + (time.monotonic() - self._started_at)


SIDE_COLORS = {
    "white": {
        "idle": ("#f3efe6", "#1a1c22", "#6d6458", "#f3efe6"),
        "held": ("#f3efe6", "#1a1c22", "#6d6458", "#f3efe6"),
        "active": ("#fffdf8", "#12141a", "#178a45", GREEN),
        "paused": ("#f3efe6", "#1a1c22", "#8a5a10", AMBER),
        "warn": ("#f3efe6", "#1a1c22", "#8a5a10", AMBER),
        "alert_on": (RED, "#ffffff", "#ffffff", "#ffd0d0"),
        "alert_off": (RED_DIM, "#ffffff", "#ffffff", RED),
    },
    "black": {
        "idle": ("#1e2128", TEXT, MUTED, "#1e2128"),
        "held": ("#1e2128", TEXT, MUTED, "#1e2128"),
        "active": ("#2a3140", TEXT, "#8dffb8", GREEN),
        "paused": ("#1e2128", TEXT, AMBER, AMBER),
        "warn": ("#1e2128", TEXT, AMBER, AMBER),
        "alert_on": (RED, "#ffffff", "#ffffff", "#ffd0d0"),
        "alert_off": ("#4a1010", "#ffffff", "#ffffff", RED),
    },
}

STATE_TEXT = {
    "idle": "ожидание",
    "held": "стоит",
    "active": "●  идёт время",
    "paused": "пауза",
    "warn": "впереди",
    "alert_on": "●  сделайте ход",
    "alert_off": "●  сделайте ход",
}


class SidePanel:
    def __init__(self, parent: tk.Misc, title: str, on_click) -> None:
        self.frame = tk.Frame(parent, highlightthickness=5, bd=0, cursor="hand2")
        self.title = tk.Label(self.frame, text=title, font=("Segoe UI", 16, "bold"), cursor="hand2")
        self.time = tk.Label(self.frame, text="00:00:00", font=("Consolas", 64, "bold"), cursor="hand2")
        self.state = tk.Label(self.frame, text=STATE_TEXT["idle"], font=("Segoe UI", 14), cursor="hand2")
        self.title.pack(side="top", pady=(28, 0))
        self.state.pack(side="bottom", pady=(0, 26))
        self.time.pack(side="top", expand=True)
        self._look = None
        self._shown = None
        for widget in (self.frame, self.title, self.time, self.state):
            widget.bind("<Button-1>", lambda _event: on_click())

    def paint(self, side: str, mode: str, time_text: str, time_font: tuple) -> None:
        look = (side, mode, time_font)
        if look != self._look:
            self._look = look
            bg, fg, sub, border = SIDE_COLORS[side][mode]
            self.frame.configure(bg=bg, highlightbackground=border, highlightcolor=border)
            self.title.configure(bg=bg, fg=fg)
            self.time.configure(bg=bg, fg=fg, font=time_font, text=time_text)
            self.state.configure(bg=bg, fg=sub, text=STATE_TEXT[mode])
            self._shown = time_text
        elif time_text != self._shown:
            self._shown = time_text
            self.time.configure(text=time_text)


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _error_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text[:1500], "Шахматный таймер", 0x10)


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_uint),
    ]


class ChessTimerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.clocks = {"white": Clock(), "black": Clock()}
        self.active: str | None = None
        self.paused = False
        self._signal = "off"
        self._blink_on = False
        self._style_key = None
        self._time_size = 64
        self._tick_job: str | None = None
        self._blink_job: str | None = None
        self._beep_job: str | None = None
        self._beep_lock = threading.Lock()
        self._build()
        self._tick()
        self._blink()

    def _build(self) -> None:
        root = self.root
        root.title("Шахматный таймер")
        root.configure(bg=BG)
        root.minsize(920, 600)
        self._center(980, 660)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.bind("<space>", self._on_space)

        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=22, pady=16)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        tk.Label(
            outer,
            text="Шахматный таймер",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 20, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 12))

        clocks = tk.Frame(outer, bg=BG)
        clocks.grid(row=1, column=0, sticky="nsew")
        clocks.columnconfigure(0, weight=1)
        clocks.columnconfigure(1, weight=0, minsize=168)
        clocks.columnconfigure(2, weight=1)
        clocks.rowconfigure(0, weight=1)

        self.white = SidePanel(clocks, "БЕЛЫЕ", self._on_white)
        self.black = SidePanel(clocks, "ЧЁРНЫЕ", self._on_black)
        self.white.frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.black.frame.grid(row=0, column=2, sticky="nsew", padx=(8, 0))

        gap_host = tk.Frame(clocks, bg=BG)
        gap_host.grid(row=0, column=1, sticky="nsew")
        gap_box = tk.Frame(gap_host, bg=BG)
        gap_box.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(gap_box, text="РАЗРЫВ", bg=BG, fg=MUTED, font=("Segoe UI", 11, "bold")).pack()
        self.gap_value = tk.Label(gap_box, text="00:00:00", bg=BG, fg=TEXT, font=("Consolas", 22, "bold"))
        self.gap_value.pack(pady=(6, 6))
        self.gap_note = tk.Label(gap_box, text="порог 30:00", bg=BG, fg=MUTED, font=("Segoe UI", 11))
        self.gap_note.pack()

        self.banner_frame = tk.Frame(outer, bg=BANNER_BG, height=88)
        self.banner_frame.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        self.banner_frame.grid_propagate(False)
        self.banner = tk.Label(
            self.banner_frame,
            text="",
            bg=BANNER_BG,
            fg=TEXT,
            font=("Segoe UI", 13, "bold"),
            wraplength=860,
            justify="center",
        )
        self.banner.pack(fill="both", expand=True, padx=16, pady=8)

        buttons = tk.Frame(outer, bg=BG)
        buttons.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        for column in range(4):
            buttons.columnconfigure(column, weight=1)
        self.white_btn = self._button(buttons, "Белые", self._on_white)
        self.black_btn = self._button(buttons, "Чёрные", self._on_black)
        self.pause_btn = self._button(buttons, "Пауза", self._toggle_pause)
        self.reset_btn = self._button(buttons, "Полный сброс", self._on_reset)
        self.white_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.black_btn.grid(row=0, column=1, sticky="ew", padx=6)
        self.pause_btn.grid(row=0, column=2, sticky="ew", padx=6)
        self.reset_btn.grid(row=0, column=3, sticky="ew", padx=(6, 0))

        tk.Label(
            outer,
            text="Пробел — пауза или продолжение. Нажатие на циферблат включает эту сторону.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=4, column=0, sticky="ew", pady=(12, 0))

        root.bind("<Configure>", self._on_resize)
        self._apply_style("off", None, False)
        self.white.paint("white", "idle", "00:00:00", ("Consolas", self._time_size, "bold"))
        self.black.paint("black", "idle", "00:00:00", ("Consolas", self._time_size, "bold"))

    def _button(self, parent: tk.Misc, text: str, command) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 12, "bold"),
            relief="flat",
            bd=0,
            highlightthickness=2,
            cursor="hand2",
            takefocus=0,
            padx=8,
            pady=12,
        )

    def _center(self, width: int, height: int) -> None:
        self.root.update_idletasks()
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        size = max(42, min(92, event.width // 18))
        if size != self._time_size:
            self._time_size = size
            self._style_key = None
        self.banner.configure(wraplength=max(280, event.width - 90))

    def _on_white(self) -> None:
        self._activate("white")

    def _on_black(self) -> None:
        self._activate("black")

    def _on_space(self, _event: tk.Event) -> str:
        self._toggle_pause()
        return "break"

    def _activate(self, side: str) -> None:
        self.root.focus_set()
        other = "black" if side == "white" else "white"
        self.clocks[other].stop()
        self.clocks[side].start()
        self.active = side
        self.paused = False
        self._render()

    def _toggle_pause(self) -> None:
        self.root.focus_set()
        if self.active is None:
            return
        clock = self.clocks[self.active]
        if self.paused:
            clock.start()
            self.paused = False
        else:
            clock.stop()
            self.paused = True
        self._render()

    def _on_reset(self) -> None:
        self.root.focus_set()
        white = self.clocks["white"].read()
        black = self.clocks["black"].read()
        if self.active is None and white == 0 and black == 0:
            return
        if not messagebox.askyesno(
            "Полный сброс",
            "Обнулить время белых и чёрных?",
            parent=self.root,
            default=messagebox.NO,
        ):
            return
        self.clocks["white"].reset()
        self.clocks["black"].reset()
        self.active = None
        self.paused = False
        self._render()

    def _tick(self) -> None:
        self._render()
        self._tick_job = self.root.after(100, self._tick)

    def _blink(self) -> None:
        self._blink_on = not self._blink_on
        self._blink_job = self.root.after(480, self._blink)

    def _render(self) -> None:
        white = self.clocks["white"].read()
        black = self.clocks["black"].read()
        who = leader(white, black)
        signal = signal_state(white, black, self.active, self.paused)
        self._sync_sound(signal)
        phase = self._blink_on if signal in ("blink", "alert") else False
        style_key = (self.active, self.paused, who, signal, phase, self._time_size)
        if style_key != self._style_key:
            self._style_key = style_key
            self._apply_style(signal, who, phase)
        font = ("Consolas", self._time_size, "bold")
        self.white.paint("white", self._panel_mode("white", who, signal, phase), format_hms(white), font)
        self.black.paint("black", self._panel_mode("black", who, signal, phase), format_hms(black), font)
        gap = abs(int(white) - int(black))
        gap_text = format_hms(gap)
        if self.gap_value.cget("text") != gap_text:
            self.gap_value.configure(text=gap_text)
        note = self._gap_note(white, black)
        if self.gap_note.cget("text") != note:
            self.gap_note.configure(text=note)
        banner = self._banner_text(signal, who, gap)
        if self.banner.cget("text") != banner:
            self.banner.configure(text=banner)

    def _panel_mode(self, side: str, who: str | None, signal: str, phase: bool) -> str:
        if signal in ("blink", "alert") and side == who:
            return "alert_on" if phase else "alert_off"
        if signal == "warn" and side == who:
            return "warn"
        if self.active == side and not self.paused:
            return "active"
        if self.active == side and self.paused:
            return "paused"
        if self.active is not None:
            return "held"
        return "idle"

    def _gap_note(self, white: float, black: float) -> str:
        if int(white) == 0 and int(black) == 0:
            return "порог 30:00"
        if int(white) == int(black):
            return "наравне"
        if white > black:
            return "белые впереди"
        return "чёрные впереди"

    def _banner_text(self, signal: str, who: str | None, gap: int) -> str:
        if signal == "off":
            if self.active is None:
                return "Включите время той стороны, которая думает над ходом."
            if self.paused:
                return "Пауза. Время обеих сторон стоит."
            running = "белых" if self.active == "white" else "чёрных"
            return f"Идёт время {running}."
        assert who is not None
        name = "Белые" if who == "white" else "Чёрные"
        other = "чёрных" if who == "white" else "белых"
        shown = format_hms(gap)
        if signal == "alert":
            return (
                f"{name} ушли вперёд больше, чем на 30 минут ({shown}). "
                f"Сделайте ход и переключите на {other}."
            )
        if signal == "blink":
            return f"Пауза. {name} впереди на {shown}. Сделайте ход и переключите на {other}."
        if self.active == "white":
            return f"{name} впереди на {shown}. Сейчас идёт время белых."
        if self.active == "black":
            return f"{name} впереди на {shown}. Сейчас идёт время чёрных."
        return f"{name} впереди на {shown}."

    def _apply_style(self, signal: str, who: str | None, phase: bool) -> None:
        if signal in ("blink", "alert"):
            banner_bg = RED if phase else RED_DIM
            banner_fg = "#ffffff"
            gap_fg = "#ff8d8d"
            title = "Сделайте ход — шахматный таймер"
        elif signal == "warn":
            banner_bg = "#3a2e14"
            banner_fg = "#ffd58a"
            gap_fg = AMBER
            title = "Шахматный таймер"
        else:
            banner_bg = BANNER_BG
            banner_fg = TEXT
            gap_fg = TEXT
            title = "Шахматный таймер"
        self.root.title(title)
        self.banner_frame.configure(bg=banner_bg)
        self.banner.configure(bg=banner_bg, fg=banner_fg)
        self.gap_value.configure(fg=gap_fg)
        self._style_side_button(self.white_btn, "white", signal, who, phase)
        self._style_side_button(self.black_btn, "black", signal, who, phase)
        if self.active is None:
            self._style_button(self.pause_btn, "#2a2e38", "#6a7080", "#2a2e38")
            self.pause_btn.configure(text="Пауза", state="disabled", disabledforeground="#6a7080")
        elif self.paused:
            self._style_button(self.pause_btn, "#4a3b1c", "#ffd58a", AMBER)
            self.pause_btn.configure(text="Продолжить", state="normal")
        else:
            self._style_button(self.pause_btn, "#243044", TEXT, "#243044")
            self.pause_btn.configure(text="Пауза", state="normal")
        self._style_button(self.reset_btn, "#3a2c32", "#f0d0d4", "#3a2c32")

    def _style_side_button(self, button: tk.Button, side: str, signal: str, who: str | None, phase: bool) -> None:
        if signal in ("blink", "alert") and who == side:
            self._style_button(button, RED if phase else RED_DIM, "#ffffff", "#ffd0d0")
        elif self.active == side and side == "white":
            self._style_button(button, "#f7f4ef", "#16181d", GREEN)
        elif self.active == side and side == "black":
            self._style_button(button, "#101216", TEXT, GREEN)
        else:
            self._style_button(button, "#2a2e38", TEXT, "#2a2e38")

    def _style_button(self, button: tk.Button, bg: str, fg: str, border: str) -> None:
        button.configure(
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
            highlightbackground=border,
            highlightcolor=border,
        )

    def _sync_sound(self, signal: str) -> None:
        if signal == "alert" and self._signal != "alert":
            self._beep()
            self._schedule_beep()
            self._flash(True)
        elif signal != "alert" and self._signal == "alert":
            self._stop_sound()
            self._flash(False)
        self._signal = signal

    def _schedule_beep(self) -> None:
        self._cancel_beep_job()
        self._beep_job = self.root.after(BEEP_EVERY_MS, self._beep_cycle)

    def _beep_cycle(self) -> None:
        self._beep_job = None
        if self._signal != "alert":
            return
        self._beep()
        self._schedule_beep()

    def _cancel_beep_job(self) -> None:
        if self._beep_job is not None:
            self.root.after_cancel(self._beep_job)
            self._beep_job = None

    def _stop_sound(self) -> None:
        self._cancel_beep_job()

    def _beep(self) -> None:
        if winsound is None or not self._beep_lock.acquire(blocking=False):
            return

        def run() -> None:
            try:
                winsound.Beep(880, 150)
                time.sleep(0.05)
                winsound.Beep(698, 190)
            except RuntimeError:
                try:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except RuntimeError:
                    pass
            finally:
                self._beep_lock.release()

        threading.Thread(target=run, daemon=True).start()

    def _flash(self, start: bool) -> None:
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            info = FLASHWINFO()
            info.cbSize = ctypes.sizeof(FLASHWINFO)
            info.hwnd = hwnd
            # FLASHW_ALL | FLASHW_TIMERNOFG, пока сигнал жив; FLASHW_STOP, когда он погас.
            info.dwFlags = 0xF if start else 0
            info.uCount = 0
            info.dwTimeout = 0
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass

    def _on_close(self) -> None:
        self._signal = "off"
        self._stop_sound()
        self._flash(False)
        for job in (self._tick_job, self._blink_job):
            if job is not None:
                self.root.after_cancel(job)
        self.root.destroy()


def main() -> None:
    _enable_dpi()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ch.timer.chess")
    except Exception:
        pass
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        _error_box("Не удалось открыть окно:\n" + str(exc))
        return

    def report(exc_type, exc, tb) -> None:
        _error_box("".join(traceback.format_exception(exc_type, exc, tb)))

    root.report_callback_exception = report
    ChessTimerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
    sys.exit(0)
