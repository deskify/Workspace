# -*- coding: utf-8 -*-
import os
import sys
import uuid
import hashlib
import datetime
import calendar
import json
import importlib.util
import threading
import time
import ast
import operator
import customtkinter as ctk
import tkinter.font as tkfont
from tkinter import messagebox, filedialog

# --- Опциональные зависимости: приложение не должно падать, если их нет ---

try:
    import winsound
    HAS_WINSOUND = (sys.platform == "win32")
except ImportError:
    HAS_WINSOUND = False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    from plyer import notification
    HAS_PLYER = True
except ImportError:
    HAS_PLYER = False

APP_TITLE = "Deskify Workspace"
VERSION = "1.0 Beta"

# --- 1. DATA & PATH LAYER ---

class PathHelper:
    @staticmethod
    def app_dir():
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def data_dir():
        d = os.path.join(PathHelper.app_dir(), "data")
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def plugins_dir():
        pd = os.path.join(PathHelper.data_dir(), "plugins")
        os.makedirs(pd, exist_ok=True)
        return pd

    @staticmethod
    def get_path(filename):
        return os.path.join(PathHelper.data_dir(), filename)


class ConfigManager:
    DEFAULT_CONFIG = {
        "theme": "dark",
        "user_name": "Пользователь",
        "language": "ru",
        "minimize_to_tray": True,
        "sound_enabled": True,
        "sound_style": "classic",
        "autosave_interval_ms": 5000,
        "pin_enabled": False,
        "pin_hash": "",
        "categories": None,  # заполняется лениво DeskifyApp.get_categories()
    }

    @classmethod
    def load(cls):
        filepath = PathHelper.get_path("config.json")
        if not os.path.exists(filepath):
            cls.save(cls.DEFAULT_CONFIG)
            return cls.DEFAULT_CONFIG.copy()
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = cls.DEFAULT_CONFIG.copy()
                config.update(data)
                return config
        except Exception:
            return cls.DEFAULT_CONFIG.copy()

    @classmethod
    def save(cls, config_data):
        filepath = PathHelper.get_path("config.json")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


class DataManager:
    @staticmethod
    def load_json(filename, default):
        filepath = PathHelper.get_path(filename)
        if not os.path.exists(filepath):
            return default
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                val = json.load(f)
                if type(val) is type(default):
                    return val
                return default
        except Exception:
            return default

    @staticmethod
    def save_json(filename, data):
        filepath = PathHelper.get_path(filename)
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


# --- 2. LOGIC & SERVICES ---

class SafeCalculator:
    ALLOWED_OPERATORS = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos
    }

    @classmethod
    def evaluate(cls, expression):
        expression = (expression or "").strip()
        if not expression:
            return ""
        try:
            node = ast.parse(expression, mode='eval').body
            res = cls._eval_node(node)
            if isinstance(res, float):
                res = round(res, 8)
                if res == int(res) and abs(res) < 1e15:
                    res = int(res)
            return res
        except ZeroDivisionError:
            return "Деление на 0"
        except Exception:
            return "Ошибка"

    @classmethod
    def _eval_node(cls, node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return node.value
        elif isinstance(node, ast.BinOp):
            left = cls._eval_node(node.left)
            right = cls._eval_node(node.right)
            op_type = type(node.op)
            if op_type in cls.ALLOWED_OPERATORS:
                return cls.ALLOWED_OPERATORS[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            operand = cls._eval_node(node.operand)
            op_type = type(node.op)
            if op_type in cls.ALLOWED_OPERATORS:
                return cls.ALLOWED_OPERATORS[op_type](operand)
        raise ValueError("Недопустимое выражение")


class NotificationService:
    @staticmethod
    def notify(title, message):
        if HAS_PLYER:
            try:
                notification.notify(title=title, message=message, app_name=APP_TITLE, timeout=5)
                return
            except Exception:
                pass
        messagebox.showinfo(title, message)


class SoundService:
    """
    Звуковой сервис без тяжёлых зависимостей.
    На Windows использует winsound (короткие тональные сигналы).
    На остальных ОС использует системный сигнал Tk (root.bell()) как безопасный
    кроссплатформенный запасной вариант. Управляется настройками
    "sound_enabled" (вкл/выкл) и "sound_style" (набор тонов) из конфига.
    """
    app = None  # ссылка на главное окно (нужна для конфига и .bell())

    STYLES = {
        "classic": {
            "success": [(880, 80), (1180, 110)],
            "error": [(220, 180)],
            "delete": [(500, 60), (350, 80)],
            "notify": [(740, 100), (990, 100), (1320, 140)],
            "click": [(600, 35)],
        },
        "soft": {
            "success": [(660, 90), (880, 90)],
            "error": [(300, 150)],
            "delete": [(420, 70)],
            "notify": [(520, 120), (660, 120)],
            "click": [(500, 30)],
        },
    }

    @classmethod
    def bind_app(cls, app):
        cls.app = app

    @classmethod
    def _enabled(cls):
        if cls.app is None:
            return False
        return bool(cls.app.config.get("sound_enabled", True))

    @classmethod
    def _table(cls):
        style = cls.app.config.get("sound_style", "classic") if cls.app else "classic"
        return cls.STYLES.get(style, cls.STYLES["classic"])

    @classmethod
    def _play(cls, key):
        if not cls._enabled():
            return
        tones = cls._table().get(key, [])
        if not tones:
            return

        def worker():
            if HAS_WINSOUND:
                try:
                    for freq, dur in tones:
                        winsound.Beep(int(freq), int(dur))
                except Exception:
                    pass
            elif cls.app is not None:
                try:
                    cls.app.after(0, cls.app.bell)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    @classmethod
    def success(cls):
        cls._play("success")

    @classmethod
    def error(cls):
        cls._play("error")

    @classmethod
    def delete(cls):
        cls._play("delete")

    @classmethod
    def notify_sound(cls):
        cls._play("notify")

    @classmethod
    def click(cls):
        cls._play("click")


# --- 3. MAIN APPLICATION ---

RUS_MONTHS = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
RUS_WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DEFAULT_CATEGORIES = [["Работа", "#e74c3c"], ["Учёба", "#3498db"], ["Личное", "#2ecc71"], ["Другое", "#f1c40f"]]
CATEGORY_PALETTE = ["#e74c3c", "#3498db", "#2ecc71", "#f1c40f", "#9b59b6", "#1abc9c", "#e67e22", "#95a5a6"]

PRIORITIES = {"🔴 Высокий": 3, "🟡 Средний": 2, "🟢 Низкий": 1}

REPEAT_OPTIONS = {
    "Не повторять": "none",
    "Каждый день": "daily",
    "Каждую неделю": "weekly",
    "Каждый месяц": "monthly",
}
REPEAT_LABELS = {"daily": "🔁 ежедн.", "weekly": "🔁 еженед.", "monthly": "🔁 ежемес."}

NAV_ITEMS = [
    ("today", "🏠 Сегодня"),
    ("calendar", "📅 Календарь"),
    ("notepad", "📝 Блокнот"),
    ("todo", "☑️ Задачи"),
    ("pomodoro", "🍅 Помодоро"),
    ("reminders", "🔔 Напоминания"),
    ("calculator", "🧮 Калькулятор"),
    ("clock", "🕒 Часы"),
]


class DeskifyApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.config = ConfigManager.load()
        self.apply_theme()
        SoundService.bind_app(self)

        self.title(f"{APP_TITLE} v{VERSION}")
        self.geometry("1150x720")
        self.minsize(980, 650)

        # Шрифты для форматирования текста в блокноте (создаются после root)
        self._bold_font = tkfont.Font(size=14, weight="bold")
        self._italic_font = tkfont.Font(size=14, slant="italic")

        # Данные
        self.notes_by_date = DataManager.load_json("notes_by_date.json", {})
        self.notes_docs = DataManager.load_json(
            "notes_docs.json",
            [{"id": "1", "title": "Заметка 1", "content": "Привет! Это твой многофайловый блокнот.", "formatting": []}]
        )
        self.tasks = DataManager.load_json("tasks.json", [])
        self.reminders = DataManager.load_json("reminders.json", [])

        if not isinstance(self.notes_by_date, dict):
            self.notes_by_date = {}
        if not isinstance(self.notes_docs, list) or not self.notes_docs:
            self.notes_docs = [{"id": "1", "title": "Заметка 1", "content": "", "formatting": []}]
        if not isinstance(self.tasks, list):
            self.tasks = []
        if not isinstance(self.reminders, list):
            self.reminders = []

        # Миграция старых данных на новые поля
        for t in self.tasks:
            if isinstance(t, dict):
                t.setdefault("subtasks", [])
                t.setdefault("repeat", "none")
        for r in self.reminders:
            if isinstance(r, dict):
                r.setdefault("repeat", "none")
        for d in self.notes_docs:
            if isinstance(d, dict):
                d.setdefault("formatting", [])

        self.active_tab = None
        self.active_doc_id = self.notes_docs[0]["id"]
        self.search_var = ctk.StringVar()
        self.nav_buttons = {}

        # Календарь: текущий месяц/неделя (чтобы не сбрасывать вид после действий)
        today = datetime.date.today()
        self.cal_year = today.year
        self.cal_month = today.month
        self.cal_view_mode = "month"
        self.cal_week_anchor = today
        self.cal_day_widgets = {}
        self._drag_state = None

        # Переменные Помодоро
        self.pomo_time_left = 25 * 60
        self.pomo_running = False
        self.pomo_mode = "work"  # work, short_break, long_break
        self.pomo_timer_id = None

        self._toast_label = None
        self._toast_after_id = None
        self.clock_window = None

        self._build_layout()
        self._bind_hotkeys()
        self._load_plugins()

        # Фоновый сервис напоминаний
        self.stop_bg_event = threading.Event()
        self.bg_thread = threading.Thread(target=self._background_reminder_service, daemon=True)
        self.bg_thread.start()

        # Инициализация трея (если доступен pystray/Pillow)
        self.tray_icon = None
        if HAS_TRAY:
            self._setup_tray()

        self.protocol("WM_DELETE_WINDOW", self.on_close_request)

        if self.config.get("pin_enabled") and self.config.get("pin_hash"):
            self.withdraw()
            self._show_lock_screen()
        else:
            self.show_tab("today")

        # Периодическое автосохранение (защита от потери данных в блокноте)
        self._schedule_autosave()

    def apply_theme(self):
        theme = self.config.get("theme", "dark")
        ctk.set_appearance_mode("Dark" if theme == "dark" else "Light")

    # --- КАТЕГОРИИ (настраиваемые пользователем) ---

    def get_categories(self):
        cats = self.config.get("categories")
        if not isinstance(cats, list) or not cats:
            cats = [list(c) for c in DEFAULT_CATEGORIES]
            self.config["categories"] = cats
        return cats

    def get_category_color(self, name):
        for n, c in self.get_categories():
            if n == name:
                return c
        return "#ffffff"

    # --- АВТОСОХРАНЕНИЕ ---

    def _schedule_autosave(self):
        if self.active_tab == "notepad":
            self._save_current_doc()
        interval = max(2000, int(self.config.get("autosave_interval_ms", 5000)))
        self.after(interval, self._schedule_autosave)

    # --- TOAST-УВЕДОМЛЕНИЯ (обратная связь в интерфейсе, с опциональной отменой) ---

    def _toast(self, message, kind="info", undo_cb=None):
        colors = {"info": "#1f538d", "success": "#219150", "error": "#c0392b"}
        if self._toast_after_id:
            try:
                self.after_cancel(self._toast_after_id)
            except Exception:
                pass
        if self._toast_label is not None:
            try:
                self._toast_label.destroy()
            except Exception:
                pass

        container = ctk.CTkFrame(self, fg_color=colors.get(kind, colors["info"]), corner_radius=8)
        ctk.CTkLabel(
            container, text=message, text_color="white",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left", padx=(14, 6), pady=8)
        if undo_cb:
            ctk.CTkButton(
                container, text="Отменить", width=90, height=26, fg_color="transparent",
                border_width=1, border_color="white", text_color="white", hover_color="#00000033",
                command=lambda: (undo_cb(), self._hide_toast())
            ).pack(side="left", padx=(0, 10), pady=4)
        else:
            ctk.CTkLabel(container, text="", width=4).pack(side="left")

        container.place(relx=0.5, rely=0.95, anchor="s")
        self._toast_label = container
        timeout = 6000 if undo_cb else 2200
        self._toast_after_id = self.after(timeout, self._hide_toast)

    def _hide_toast(self):
        if self._toast_label is not None:
            try:
                self._toast_label.destroy()
            except Exception:
                pass
        self._toast_label = None
        self._toast_after_id = None

    # --- УДАЛЕНИЕ С ВОЗМОЖНОСТЬЮ ОТМЕНЫ (мягкое удаление, без блокирующих диалогов) ---

    def _soft_delete(self, item, source_list, save_fn, refresh_fn, message):
        try:
            source_list.remove(item)
        except ValueError:
            return
        save_fn()
        refresh_fn()
        SoundService.delete()

        def undo():
            source_list.append(item)
            save_fn()
            refresh_fn()
            SoundService.success()
            self._toast("Восстановлено", "success")

        self._toast(message, "error", undo_cb=undo)

    # --- PIN-ЗАЩИТА ---

    def _show_lock_screen(self):
        lock = ctk.CTkToplevel(self)
        lock.title("Deskify — вход")
        lock.geometry("320x220")
        lock.resizable(False, False)
        lock.protocol("WM_DELETE_WINDOW", lambda: self._lock_quit(lock))
        lock.grab_set()

        ctk.CTkLabel(lock, text="🔒 Введите PIN-код", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(24, 10))
        pin_var = ctk.StringVar()
        entry = ctk.CTkEntry(lock, textvariable=pin_var, show="•", justify="center", font=ctk.CTkFont(size=18))
        entry.pack(pady=6, padx=30, fill="x")
        entry.focus()

        err_lbl = ctk.CTkLabel(lock, text="", text_color="#e74c3c")
        err_lbl.pack()

        def try_unlock(event=None):
            val = pin_var.get().strip()
            if hashlib.sha256(val.encode()).hexdigest() == self.config.get("pin_hash"):
                lock.destroy()
                self.deiconify()
                self.show_tab("today")
            else:
                err_lbl.configure(text="Неверный PIN")
                SoundService.error()
                pin_var.set("")

        entry.bind("<Return>", try_unlock)
        ctk.CTkButton(lock, text="Войти", command=try_unlock).pack(pady=10)

    def _lock_quit(self, lock_win):
        if messagebox.askyesno("Выход", "Закрыть приложение?"):
            lock_win.destroy()
            self.full_quit()

    # --- TRAY INTEGRATION ---

    def _create_tray_image(self):
        width, height = 64, 64
        image = Image.new('RGB', (width, height), color=(31, 83, 141))
        d = ImageDraw.Draw(image)
        d.rectangle([8, 8, width - 8, height - 8], outline=(255, 255, 255), width=3)
        return image

    def _setup_tray(self):
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Открыть Deskify", self.show_from_tray, default=True),
                pystray.MenuItem("Выход", self.full_quit)
            )
            self.tray_icon = pystray.Icon("Deskify", self._create_tray_image(), APP_TITLE, menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            self.tray_icon = None

    def hide_to_tray(self):
        self._save_current_doc()
        self.withdraw()
        if HAS_PLYER:
            NotificationService.notify("Deskify", "Приложение свернуто в трей.")

    def show_from_tray(self, icon=None, item=None):
        self.after(0, self._restore_window)

    def _restore_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def on_close_request(self):
        """Обрабатывает нажатие на крестик окна с учётом настройки 'minimize_to_tray'."""
        if HAS_TRAY and self.tray_icon is not None and self.config.get("minimize_to_tray", True):
            self.hide_to_tray()
        else:
            if messagebox.askyesno("Выход", "Закрыть приложение полностью?"):
                self.full_quit()

    # --- UI STRUCTURE ---

    def _build_layout(self):
        top_bar = ctk.CTkFrame(self, corner_radius=0, height=55)
        top_bar.pack(fill="x", side="top")

        title_box = ctk.CTkFrame(top_bar, fg_color="transparent")
        title_box.pack(side="left", padx=20, pady=6)
        ctk.CTkLabel(title_box, text=f"⚡ {APP_TITLE}", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        user_name = self.config.get("user_name", "").strip()
        if user_name:
            ctk.CTkLabel(
                title_box, text=f"Привет, {user_name}!",
                font=ctk.CTkFont(size=11), text_color="gray60"
            ).pack(anchor="w")

        ctk.CTkButton(top_bar, text="Найти", width=70, fg_color="#1f538d", command=self.perform_search).pack(side="right", padx=(6, 20), pady=12)
        self.search_entry = ctk.CTkEntry(top_bar, textvariable=self.search_var, placeholder_text="Поиск везде... (Ctrl+F)", width=220)
        self.search_entry.pack(side="right", padx=6, pady=12)
        self.search_entry.bind("<Return>", lambda e: self.perform_search())

        main_container = ctk.CTkFrame(self, corner_radius=0)
        main_container.pack(fill="both", expand=True)

        self.sidebar = ctk.CTkFrame(main_container, width=220, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(self.sidebar, text="МЕНЮ", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray60").pack(anchor="w", pady=(14, 4), padx=16)

        nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        nav_frame.pack(fill="x", padx=8)

        for tab_key, label in NAV_ITEMS:
            self._add_nav_btn(nav_frame, tab_key, label)

        ctk.CTkLabel(self.sidebar, text="ПЛАГИНЫ", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray60").pack(anchor="w", pady=(12, 4), padx=16)
        self.plugins_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.plugins_frame.pack(fill="x", padx=8)

        bottom_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        bottom_frame.pack(side="bottom", fill="x", padx=10, pady=12)

        ctk.CTkButton(bottom_frame, text="⚙️ Настройки", fg_color="transparent", border_width=1, command=lambda: self.show_tab("settings")).pack(fill="x", pady=2)
        if HAS_TRAY:
            ctk.CTkButton(bottom_frame, text="Свернуть в трей", fg_color="gray25", hover_color="gray35", command=self.hide_to_tray).pack(fill="x", pady=2)
        ctk.CTkButton(bottom_frame, text="Выход", fg_color="#c0392b", hover_color="#e74c3c", command=self.full_quit).pack(fill="x", pady=2)

        self.workspace = ctk.CTkFrame(main_container, corner_radius=10)
        self.workspace.pack(side="right", fill="both", expand=True, padx=12, pady=12)

    def _add_nav_btn(self, parent, tab_key, label):
        btn = ctk.CTkButton(
            parent, text=label, anchor="w", fg_color="transparent",
            text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
            height=34, font=ctk.CTkFont(size=13),
            command=lambda: self.show_tab(tab_key)
        )
        btn.pack(fill="x", pady=1)
        self.nav_buttons[tab_key] = btn

    def _highlight_nav(self, tab_key):
        """Подсвечивает активный пункт меню, чтобы пользователь всегда видел, где он находится."""
        for key, btn in self.nav_buttons.items():
            if key == tab_key:
                btn.configure(fg_color="#1f538d", text_color="white", hover_color="#1c497d")
            else:
                btn.configure(fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"))

    def _bind_hotkeys(self):
        self.bind("<Control-n>", lambda e: self.show_tab("notepad"))
        self.bind("<Control-t>", lambda e: self.show_tab("todo"))
        self.bind("<Control-r>", lambda e: self.show_tab("reminders"))
        self.bind("<Control-f>", lambda e: self.search_entry.focus())

    def clear_workspace(self):
        for child in list(self.workspace.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

    def show_tab(self, tab_key, **kwargs):
        # Сохраняем текущую заметку блокнота перед уходом с вкладки, чтобы не терять правки
        if self.active_tab == "notepad" and tab_key != "notepad":
            self._save_current_doc()

        self.active_tab = tab_key
        self.clear_workspace()
        if tab_key in self.nav_buttons:
            self._highlight_nav(tab_key)

        if tab_key == "today":
            self._render_today()
        elif tab_key == "calendar":
            self._render_calendar(kwargs.get("year"), kwargs.get("month"))
        elif tab_key == "notepad":
            self._render_notepad()
        elif tab_key == "todo":
            self._render_todo(filter_q=kwargs.get("filter_q"))
        elif tab_key == "pomodoro":
            self._render_pomodoro()
        elif tab_key == "reminders":
            self._render_reminders()
        elif tab_key == "calculator":
            self._render_calculator()
        elif tab_key == "clock":
            self._render_clock()
        elif tab_key == "settings":
            self._render_settings()
        elif tab_key == "search":
            self._render_search(kwargs.get("query", ""))

    # --- 0. Сегодня (сводка) ---

    def _render_today(self):
        today = datetime.date.today()
        ds = today.strftime("%Y-%m-%d")

        wrap = ctk.CTkScrollableFrame(self.workspace, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(
            wrap, text=f"Сегодня, {today.day} {RUS_MONTHS[today.month].lower()} {today.year}",
            font=ctk.CTkFont(size=20, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(wrap, text=today.strftime("%A").capitalize(), text_color="gray60").pack(anchor="w", pady=(0, 16))

        today_tasks = [t for t in self.tasks if isinstance(t, dict) and t.get("due_date") == ds]
        ctk.CTkLabel(wrap, text=f"☑️ Задачи на сегодня ({len(today_tasks)})", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(6, 4))
        if today_tasks:
            for t in sorted(today_tasks, key=lambda x: (x.get("done", False), -PRIORITIES.get(x.get("priority", "🟡 Средний"), 1))):
                row = ctk.CTkFrame(wrap)
                row.pack(fill="x", pady=2)
                var = ctk.BooleanVar(value=t.get("done", False))

                def toggle(t=t, v=var):
                    t["done"] = v.get()
                    DataManager.save_json("tasks.json", self.tasks)
                    if t["done"]:
                        SoundService.success()
                        self._apply_task_repeat(t)
                    self.show_tab("today")

                ctk.CTkCheckBox(row, text=t.get("text", ""), variable=var, command=toggle).pack(side="left", padx=8, pady=6, fill="x", expand=True)
        else:
            ctk.CTkLabel(wrap, text="На сегодня задач нет 🎉", text_color="gray60").pack(anchor="w", padx=4, pady=4)

        today_reminders = [r for r in self.reminders if isinstance(r, dict) and r.get("date") == ds]
        ctk.CTkLabel(wrap, text=f"🔔 Напоминания на сегодня ({len(today_reminders)})", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(20, 4))
        if today_reminders:
            for r in sorted(today_reminders, key=lambda x: x.get("time", "")):
                ctk.CTkLabel(wrap, text=f"⏰ {r.get('time')} — {r.get('text')}", anchor="w").pack(anchor="w", padx=4, pady=2)
        else:
            ctk.CTkLabel(wrap, text="Напоминаний нет", text_color="gray60").pack(anchor="w", padx=4, pady=4)

        actions = ctk.CTkFrame(wrap, fg_color="transparent")
        actions.pack(fill="x", pady=(24, 0))
        ctk.CTkButton(actions, text="📝 Заметка на сегодня", command=lambda: self._open_day_modal(ds)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(actions, text="🍅 Начать Помодоро", fg_color="gray30", command=lambda: self.show_tab("pomodoro")).pack(side="left", padx=8)

    # --- 1. Календарь ---

    def _render_calendar(self, year=None, month=None):
        today = datetime.date.today()
        year, month = year or today.year, month or today.month
        self.cal_year, self.cal_month = year, month

        top = ctk.CTkFrame(self.workspace, fg_color="transparent")
        top.pack(fill="x", pady=(10, 0), padx=16)

        def nav(delta):
            if self.cal_view_mode == "week":
                self.cal_week_anchor = self.cal_week_anchor + datetime.timedelta(days=7 * delta)
                self.show_tab("calendar", year=self.cal_week_anchor.year, month=self.cal_week_anchor.month)
            else:
                y, m = year, month + delta
                if m < 1:
                    y -= 1
                    m = 12
                elif m > 12:
                    y += 1
                    m = 1
                self.show_tab("calendar", year=y, month=m)

        def go_today():
            self.cal_week_anchor = today
            self.show_tab("calendar", year=today.year, month=today.month)

        ctk.CTkButton(top, text="◄", width=36, command=lambda: nav(-1)).pack(side="left")

        if self.cal_view_mode == "week":
            wk_start = self.cal_week_anchor - datetime.timedelta(days=self.cal_week_anchor.weekday())
            wk_end = wk_start + datetime.timedelta(days=6)
            label_text = f"{wk_start.day} {RUS_MONTHS[wk_start.month]} – {wk_end.day} {RUS_MONTHS[wk_end.month]} {wk_end.year}"
        else:
            label_text = f"{RUS_MONTHS[month]} {year}"
        ctk.CTkLabel(top, text=label_text, font=ctk.CTkFont(size=18, weight="bold")).pack(side="left", expand=True)

        ctk.CTkButton(top, text="►", width=36, command=lambda: nav(1)).pack(side="right")
        ctk.CTkButton(top, text="Сегодня", width=80, fg_color="transparent", border_width=1, command=go_today).pack(side="right", padx=6)

        switch_row = ctk.CTkFrame(self.workspace, fg_color="transparent")
        switch_row.pack(fill="x", padx=16, pady=(8, 0))

        def set_view(mode):
            self.cal_view_mode = mode
            self.show_tab("calendar", year=self.cal_year, month=self.cal_month)

        ctk.CTkButton(switch_row, text="Месяц", width=80,
                      fg_color="#1f538d" if self.cal_view_mode == "month" else "gray30",
                      command=lambda: set_view("month")).pack(side="left", padx=(0, 4))
        ctk.CTkButton(switch_row, text="Неделя", width=80,
                      fg_color="#1f538d" if self.cal_view_mode == "week" else "gray30",
                      command=lambda: set_view("week")).pack(side="left")
        ctk.CTkLabel(switch_row, text="Совет: перетащите день с задачами на другой день, чтобы перенести срок",
                     text_color="gray60", font=ctk.CTkFont(size=11)).pack(side="left", padx=12)

        if self.cal_view_mode == "week":
            week_container = ctk.CTkFrame(self.workspace, fg_color="transparent")
            week_container.pack(expand=True, fill="both", padx=16, pady=10)
            self._render_calendar_week(week_container)
            return

        grid = ctk.CTkFrame(self.workspace, fg_color="transparent")
        grid.pack(expand=True, fill="both", padx=16, pady=10)
        self.cal_day_widgets = {}

        for i, d in enumerate(RUS_WD):
            wd_color = "#e74c3c" if i >= 5 else "gray60"
            ctk.CTkLabel(grid, text=d, font=ctk.CTkFont(weight="bold", size=13), text_color=wd_color).grid(row=0, column=i, sticky="nsew", padx=2, pady=4)

        cal = calendar.Calendar(firstweekday=0)
        month_days = cal.monthdayscalendar(year, month)

        tasks_by_date = {}
        for t in self.tasks:
            d = t.get("due_date")
            if d:
                tasks_by_date.setdefault(d, []).append(t)

        for r, week in enumerate(month_days, start=1):
            for c, day in enumerate(week):
                if day != 0:
                    ds = f"{year:04d}-{month:02d}-{day:02d}"
                    has_note = ds in self.notes_by_date
                    day_tasks = tasks_by_date.get(ds, [])
                    is_today = (year == today.year and month == today.month and day == today.day)

                    indicators = []
                    if has_note:
                        indicators.append("📝")
                    if day_tasks:
                        indicators.append(f"☑️{len(day_tasks)}")
                    ind_str = " ".join(indicators)

                    btn_text = f"{day}\n{ind_str}" if ind_str else str(day)
                    if is_today:
                        fg = "#1f538d"
                    elif has_note or day_tasks:
                        fg = ("gray65", "gray30")
                    else:
                        fg = ("gray85", "gray25")

                    btn = ctk.CTkButton(grid, text=btn_text, fg_color=fg, font=ctk.CTkFont(size=12, weight="bold"),
                                         border_width=2 if is_today else 0, border_color="#3498db")
                    btn.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
                    self.cal_day_widgets[btn] = ds
                    btn.bind("<ButtonPress-1>", lambda e, dstr=ds: self._cal_press(e, dstr))
                    btn.bind("<B1-Motion>", lambda e, dstr=ds: self._cal_motion(e, dstr))
                    btn.bind("<ButtonRelease-1>", lambda e, dstr=ds: self._cal_release(e, dstr))

        for i in range(len(month_days) + 1):
            grid.rowconfigure(i, weight=1)
        for j in range(7):
            grid.columnconfigure(j, weight=1)

    def _render_calendar_week(self, container):
        wk_start = self.cal_week_anchor - datetime.timedelta(days=self.cal_week_anchor.weekday())
        days = [wk_start + datetime.timedelta(days=i) for i in range(7)]
        today = datetime.date.today()

        tasks_by_date = {}
        for t in self.tasks:
            d = t.get("due_date")
            if d:
                tasks_by_date.setdefault(d, []).append(t)

        cols = ctk.CTkFrame(container, fg_color="transparent")
        cols.pack(fill="both", expand=True)

        for i, day in enumerate(days):
            ds = day.strftime("%Y-%m-%d")
            is_today = (day == today)
            col = ctk.CTkFrame(cols, corner_radius=8, fg_color="#1f538d" if is_today else ("gray85", "gray20"))
            col.grid(row=0, column=i, sticky="nsew", padx=3, pady=3)
            cols.columnconfigure(i, weight=1)
            cols.rowconfigure(0, weight=1)

            head_color = "white" if is_today else ("gray10", "gray90")
            ctk.CTkLabel(col, text=RUS_WD[i], font=ctk.CTkFont(size=11, weight="bold"), text_color=head_color).pack(pady=(8, 0))
            ctk.CTkLabel(col, text=str(day.day), font=ctk.CTkFont(size=20, weight="bold"), text_color=head_color).pack()

            day_tasks = tasks_by_date.get(ds, [])
            tasks_box = ctk.CTkScrollableFrame(col, fg_color="transparent", height=280)
            tasks_box.pack(fill="both", expand=True, padx=4, pady=6)
            for t in day_tasks:
                mark = "✓" if t.get("done") else "○"
                ctk.CTkLabel(
                    tasks_box, text=f"{mark} {t.get('text', '')}", anchor="w",
                    text_color=head_color, font=ctk.CTkFont(size=11),
                    wraplength=110, justify="left"
                ).pack(anchor="w", pady=1)

            if ds in self.notes_by_date:
                ctk.CTkLabel(col, text="📝", text_color=head_color).pack()

            ctk.CTkButton(
                col, text="+", width=28, height=24, fg_color="transparent",
                border_width=1, border_color=head_color, text_color=head_color,
                command=lambda dstr=ds: self._open_day_modal(dstr)
            ).pack(pady=(0, 8))

    # --- Drag & drop задач между днями (месячный вид) ---

    def _resolve_day_widget(self, widget):
        hops = 0
        while widget is not None and hops < 6:
            if widget in self.cal_day_widgets:
                return widget
            widget = getattr(widget, "master", None)
            hops += 1
        return None

    def _cal_press(self, event, date_str):
        self._drag_state = {"start": (event.x_root, event.y_root), "source": date_str, "moved": False}

    def _cal_motion(self, event, date_str):
        st = self._drag_state
        if not st or st["source"] != date_str:
            return
        sx, sy = st["start"]
        if abs(event.x_root - sx) > 6 or abs(event.y_root - sy) > 6:
            st["moved"] = True

    def _cal_release(self, event, date_str):
        st = self._drag_state
        self._drag_state = None
        if not st or st["source"] != date_str:
            return
        if not st["moved"]:
            self._open_day_modal(date_str)
            return
        target_widget = self.winfo_containing(event.x_root, event.y_root)
        resolved = self._resolve_day_widget(target_widget)
        target_date = self.cal_day_widgets.get(resolved)
        if target_date and target_date != date_str:
            self._move_tasks_between_days(date_str, target_date)
        else:
            self.show_tab("calendar", year=self.cal_year, month=self.cal_month)

    def _move_tasks_between_days(self, from_date, to_date):
        day_tasks = [t for t in self.tasks if isinstance(t, dict) and t.get("due_date") == from_date]
        if not day_tasks:
            self.show_tab("calendar", year=self.cal_year, month=self.cal_month)
            return

        if len(day_tasks) == 1:
            day_tasks[0]["due_date"] = to_date
            DataManager.save_json("tasks.json", self.tasks)
            SoundService.success()
            self._toast(f"Задача перенесена на {to_date}", "success")
            self.show_tab("calendar", year=self.cal_year, month=self.cal_month)
            return

        win = ctk.CTkToplevel(self)
        win.title("Выберите задачу для переноса")
        win.geometry("360x340")
        win.grab_set()
        win.transient(self)
        ctk.CTkLabel(win, text=f"Перенести на {to_date}:", font=ctk.CTkFont(weight="bold")).pack(pady=10)
        scroll = ctk.CTkScrollableFrame(win)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for t in day_tasks:
            def move(t=t):
                t["due_date"] = to_date
                DataManager.save_json("tasks.json", self.tasks)
                win.destroy()
                SoundService.success()
                self._toast(f"Задача перенесена на {to_date}", "success")
                self.show_tab("calendar", year=self.cal_year, month=self.cal_month)

            ctk.CTkButton(scroll, text=t.get("text", ""), anchor="w", command=move).pack(fill="x", pady=3)

        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), self.show_tab("calendar", year=self.cal_year, month=self.cal_month)))

    def _open_day_modal(self, date_str):
        win = ctk.CTkToplevel(self)
        win.title(f"События за {date_str}")
        win.geometry("480x560")
        win.grab_set()
        win.transient(self)

        ctk.CTkLabel(win, text=f"📅 {date_str}", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)

        tasks_container = ctk.CTkFrame(win)
        tasks_container.pack(fill="x", padx=16, pady=5)

        def refresh_day_tasks():
            for c in list(tasks_container.winfo_children()):
                c.destroy()
            day_tasks = [t for t in self.tasks if t.get("due_date") == date_str]
            ctk.CTkLabel(tasks_container, text="Задачи на день:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=4)
            if day_tasks:
                for t in day_tasks:
                    status = "✓" if t.get("done") else "○"
                    ctk.CTkLabel(tasks_container, text=f"{status} {t.get('text')}", text_color="gray80").pack(anchor="w", padx=16, pady=2)
            else:
                ctk.CTkLabel(tasks_container, text="Пока нет задач на этот день", text_color="gray60").pack(anchor="w", padx=16, pady=2)

        refresh_day_tasks()

        quick_add = ctk.CTkFrame(win, fg_color="transparent")
        quick_add.pack(fill="x", padx=16, pady=(0, 10))
        quick_entry = ctk.CTkEntry(quick_add, placeholder_text="Новая задача на этот день...")
        quick_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        def quick_add_task():
            txt = quick_entry.get().strip()
            if not txt:
                return
            cats = self.get_categories()
            self.tasks.append({
                "text": txt, "category": cats[0][0] if cats else "Другое",
                "priority": "🟡 Средний", "due_date": date_str, "done": False,
                "subtasks": [], "repeat": "none"
            })
            DataManager.save_json("tasks.json", self.tasks)
            quick_entry.delete(0, "end")
            refresh_day_tasks()
            SoundService.success()

        quick_entry.bind("<Return>", lambda e: quick_add_task())
        ctk.CTkButton(quick_add, text="+", width=36, command=quick_add_task).pack(side="left")

        ctk.CTkLabel(win, text="Заметка:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(6, 2))
        txt = ctk.CTkTextbox(win, wrap="word")
        txt.pack(expand=True, fill="both", padx=16, pady=(0, 10))
        txt.insert("1.0", self.notes_by_date.get(date_str, ""))

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(pady=(0, 12))

        def close_and_refresh():
            win.destroy()
            self.show_tab("calendar", year=self.cal_year, month=self.cal_month)

        def save():
            val = txt.get("1.0", "end").strip()
            if val:
                self.notes_by_date[date_str] = val
            else:
                self.notes_by_date.pop(date_str, None)
            DataManager.save_json("notes_by_date.json", self.notes_by_date)
            SoundService.success()
            self._toast("Заметка к дню сохранена", "success")
            close_and_refresh()

        win.protocol("WM_DELETE_WINDOW", close_and_refresh)
        ctk.CTkButton(btn_row, text="Закрыть", fg_color="gray30", width=100, command=close_and_refresh).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Сохранить заметку", width=160, command=save).pack(side="left", padx=6)

    # --- 2. Блокнот ---

    def _get_raw_textbox(self):
        """Достаём настоящий tkinter.Text из CTkTextbox, чтобы работать с тегами форматирования."""
        return getattr(self.doc_textbox, "_textbox", self.doc_textbox)

    def _toggle_format(self, tag):
        raw = self._get_raw_textbox()
        try:
            sel_start, sel_end = raw.tag_ranges("sel")[:2]
        except (ValueError, IndexError):
            self._toast("Сначала выделите текст", "info")
            return
        has_any = bool(raw.tag_nextrange(tag, sel_start, sel_end))
        if has_any:
            raw.tag_remove(tag, sel_start, sel_end)
        else:
            raw.tag_add(tag, sel_start, sel_end)

    def _render_notepad(self):
        # Вызывается и напрямую из _add_new_doc/_delete_doc/_switch_doc (не через show_tab),
        # поэтому чистим workspace здесь явно, иначе виджеты дублируются друг на друге.
        self.clear_workspace()

        container = ctk.CTkFrame(self.workspace, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        left_panel = ctk.CTkFrame(container, width=220)
        left_panel.pack(side="left", fill="y", padx=(0, 10))
        left_panel.pack_propagate(False)

        ctk.CTkButton(left_panel, text="+ Новая заметка", fg_color="#1f538d", command=self._add_new_doc).pack(fill="x", padx=8, pady=8)

        self.docs_scroll = ctk.CTkScrollableFrame(left_panel)
        self.docs_scroll.pack(fill="both", expand=True, padx=4, pady=4)

        right_panel = ctk.CTkFrame(container)
        right_panel.pack(side="right", fill="both", expand=True)

        if not self.notes_docs:
            self.notes_docs = [{"id": "1", "title": "Заметка 1", "content": "", "formatting": []}]
            self.active_doc_id = "1"

        active_doc = next((d for d in self.notes_docs if d["id"] == self.active_doc_id), self.notes_docs[0])
        self.active_doc_id = active_doc["id"]

        top_editor = ctk.CTkFrame(right_panel, fg_color="transparent")
        top_editor.pack(fill="x", padx=12, pady=8)

        self.doc_title_entry = ctk.CTkEntry(top_editor, font=ctk.CTkFont(size=16, weight="bold"))
        self.doc_title_entry.insert(0, active_doc.get("title", "Без названия"))
        self.doc_title_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        def save_with_feedback():
            self._save_current_doc()
            SoundService.success()
            self._toast("Заметка сохранена", "success")

        ctk.CTkButton(top_editor, text="Сохранить", width=100, command=save_with_feedback).pack(side="right")

        toolbar = ctk.CTkFrame(right_panel, fg_color="transparent")
        toolbar.pack(fill="x", padx=12, pady=(0, 4))
        ctk.CTkButton(toolbar, text="Ж", width=32, font=ctk.CTkFont(weight="bold"), fg_color="gray30",
                      command=lambda: self._toggle_format("bold")).pack(side="left", padx=2)
        ctk.CTkButton(toolbar, text="К", width=32, font=ctk.CTkFont(slant="italic"), fg_color="gray30",
                      command=lambda: self._toggle_format("italic")).pack(side="left", padx=2)
        ctk.CTkButton(toolbar, text="Ч", width=32, fg_color="gray30",
                      command=lambda: self._toggle_format("underline")).pack(side="left", padx=2)
        ctk.CTkLabel(toolbar, text="Выделите текст и нажмите кнопку (или Ctrl+B / Ctrl+I / Ctrl+U)",
                     text_color="gray60", font=ctk.CTkFont(size=11)).pack(side="left", padx=10)

        self.doc_textbox = ctk.CTkTextbox(right_panel, wrap="word", font=ctk.CTkFont(size=14))
        self.doc_textbox.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.doc_textbox.insert("1.0", active_doc.get("content", ""))

        raw = self._get_raw_textbox()
        try:
            raw.tag_configure("bold", font=self._bold_font)
            raw.tag_configure("italic", font=self._italic_font)
            raw.tag_configure("underline", underline=True)
            for fmt in active_doc.get("formatting", []):
                try:
                    raw.tag_add(fmt["tag"], fmt["start"], fmt["end"])
                except Exception:
                    pass

            def make_handler(tag):
                def handler(event):
                    self._toggle_format(tag)
                    return "break"
                return handler

            raw.bind("<Control-b>", make_handler("bold"))
            raw.bind("<Control-i>", make_handler("italic"))
            raw.bind("<Control-u>", make_handler("underline"))
        except Exception:
            pass

        self._update_docs_list()

    def _update_docs_list(self):
        for child in list(self.docs_scroll.winfo_children()):
            child.destroy()

        for doc in self.notes_docs:
            is_active = doc["id"] == self.active_doc_id
            bg_color = "#1f538d" if is_active else "transparent"

            frame = ctk.CTkFrame(self.docs_scroll, fg_color=bg_color, corner_radius=6)
            frame.pack(fill="x", pady=2, padx=2)

            btn = ctk.CTkButton(
                frame, text=doc.get("title", "Заметка"), anchor="w",
                fg_color="transparent", hover_color="gray30" if not is_active else "#1c497d",
                text_color="white" if is_active else ("gray10", "gray90"),
                height=32,
                command=lambda d_id=doc["id"]: self._switch_doc(d_id)
            )
            btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

            if len(self.notes_docs) > 1:
                del_btn = ctk.CTkButton(
                    frame, text="✕", width=24, height=24, fg_color="transparent",
                    hover_color="#c0392b",
                    command=lambda d_id=doc["id"], title=doc.get("title", "Заметка"): self._delete_doc(d_id, title)
                )
                del_btn.pack(side="right", padx=4, pady=4)

    def _switch_doc(self, doc_id):
        self._save_current_doc()
        self.active_doc_id = doc_id
        self._render_notepad()

    def _add_new_doc(self):
        self._save_current_doc()
        new_id = uuid.uuid4().hex[:10]
        new_doc = {"id": new_id, "title": f"Заметка {len(self.notes_docs) + 1}", "content": "", "formatting": []}
        self.notes_docs.append(new_doc)
        self.active_doc_id = new_id
        DataManager.save_json("notes_docs.json", self.notes_docs)
        self._render_notepad()
        SoundService.success()

    def _delete_doc(self, doc_id, title):
        if len(self.notes_docs) <= 1:
            return
        doc = next((d for d in self.notes_docs if d["id"] == doc_id), None)
        if doc is None:
            return
        idx = self.notes_docs.index(doc)
        self.notes_docs.remove(doc)
        was_active = (self.active_doc_id == doc_id)
        if was_active:
            self.active_doc_id = self.notes_docs[0]["id"]
        DataManager.save_json("notes_docs.json", self.notes_docs)
        self._render_notepad()
        SoundService.delete()

        def undo():
            self.notes_docs.insert(min(idx, len(self.notes_docs)), doc)
            if was_active:
                self.active_doc_id = doc_id
            DataManager.save_json("notes_docs.json", self.notes_docs)
            self._render_notepad()
            SoundService.success()

        self._toast(f"Заметка «{title}» удалена", "error", undo_cb=undo)

    def _save_current_doc(self):
        if hasattr(self, 'doc_textbox') and self.doc_textbox.winfo_exists():
            raw = self._get_raw_textbox()
            formatting = []
            for tag in ("bold", "italic", "underline"):
                try:
                    ranges = raw.tag_ranges(tag)
                except Exception:
                    ranges = []
                for i in range(0, len(ranges), 2):
                    formatting.append({"tag": tag, "start": str(ranges[i]), "end": str(ranges[i + 1])})

            for d in self.notes_docs:
                if d["id"] == self.active_doc_id:
                    d["title"] = self.doc_title_entry.get().strip() or "Без названия"
                    # Без .strip(): диапазоны форматирования привязаны к исходным индексам
                    # виджета, обрезка текста сдвинула бы их и сломала форматирование.
                    d["content"] = self.doc_textbox.get("1.0", "end-1c")
                    d["formatting"] = formatting
                    break
            DataManager.save_json("notes_docs.json", self.notes_docs)

    # --- 3. Список задач (To-Do) ---

    def _apply_task_repeat(self, task):
        repeat = task.get("repeat", "none")
        if repeat == "none" or not task.get("due_date"):
            return
        try:
            d = datetime.datetime.strptime(task["due_date"], "%Y-%m-%d").date()
        except ValueError:
            return
        if repeat == "daily":
            d = d + datetime.timedelta(days=1)
        elif repeat == "weekly":
            d = d + datetime.timedelta(days=7)
        elif repeat == "monthly":
            month = d.month + 1
            year = d.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            last_day = calendar.monthrange(year, month)[1]
            d = datetime.date(year, month, min(d.day, last_day))
        else:
            return
        task["due_date"] = d.strftime("%Y-%m-%d")
        task["done"] = False
        DataManager.save_json("tasks.json", self.tasks)
        self._toast(f"Повторяющаяся задача перенесена на {task['due_date']}", "info")

    def _open_edit_task(self, task):
        win = ctk.CTkToplevel(self)
        win.title("Редактировать задачу")
        win.geometry("420x560")
        win.grab_set()
        win.transient(self)

        ctk.CTkLabel(win, text="Текст задачи", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(14, 2))
        text_entry = ctk.CTkEntry(win)
        text_entry.insert(0, task.get("text", ""))
        text_entry.pack(fill="x", padx=16)

        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=10)

        ctk.CTkLabel(body, text="Приоритет").pack(anchor="w")
        prio_var = ctk.StringVar(value=task.get("priority", "🟡 Средний"))
        ctk.CTkOptionMenu(body, variable=prio_var, values=list(PRIORITIES.keys())).pack(fill="x", pady=4)

        cat_names = [c[0] for c in self.get_categories()] or ["Другое"]
        cat_var = ctk.StringVar(value=task.get("category") if task.get("category") in cat_names else cat_names[0])
        ctk.CTkLabel(body, text="Категория").pack(anchor="w", pady=(8, 0))
        ctk.CTkOptionMenu(body, variable=cat_var, values=cat_names).pack(fill="x", pady=4)

        ctk.CTkLabel(body, text="Срок (ГГГГ-ММ-ДД)").pack(anchor="w", pady=(8, 0))
        date_entry = ctk.CTkEntry(body)
        date_entry.insert(0, task.get("due_date") or "")
        date_entry.pack(fill="x", pady=4)

        ctk.CTkLabel(body, text="Повтор").pack(anchor="w", pady=(8, 0))
        current_repeat_label = next((k for k, v in REPEAT_OPTIONS.items() if v == task.get("repeat", "none")), "Не повторять")
        repeat_var = ctk.StringVar(value=current_repeat_label)
        ctk.CTkOptionMenu(body, variable=repeat_var, values=list(REPEAT_OPTIONS.keys())).pack(fill="x", pady=4)

        ctk.CTkLabel(win, text="Подзадачи", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=16, pady=(6, 2))
        sub_scroll = ctk.CTkScrollableFrame(win, height=120)
        sub_scroll.pack(fill="x", padx=16)

        subtasks = [dict(s) for s in task.get("subtasks", [])]  # рабочая копия

        def refresh_subtasks():
            for c in list(sub_scroll.winfo_children()):
                c.destroy()
            for i, st in enumerate(subtasks):
                r = ctk.CTkFrame(sub_scroll, fg_color="transparent")
                r.pack(fill="x", pady=1)
                v = ctk.BooleanVar(value=st.get("done", False))

                def on_toggle(i=i, v=v):
                    subtasks[i]["done"] = v.get()

                ctk.CTkCheckBox(r, text=st.get("text", ""), variable=v, command=on_toggle).pack(side="left", fill="x", expand=True)

                def remove(i=i):
                    subtasks.pop(i)
                    refresh_subtasks()

                ctk.CTkButton(r, text="✕", width=24, height=24, fg_color="transparent", hover_color="#c0392b", command=remove).pack(side="right")

        refresh_subtasks()

        add_row = ctk.CTkFrame(win, fg_color="transparent")
        add_row.pack(fill="x", padx=16, pady=(4, 10))
        sub_entry = ctk.CTkEntry(add_row, placeholder_text="Новый пункт...")
        sub_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        def add_subtask():
            txt = sub_entry.get().strip()
            if not txt:
                return
            subtasks.append({"text": txt, "done": False})
            sub_entry.delete(0, "end")
            refresh_subtasks()

        sub_entry.bind("<Return>", lambda e: add_subtask())
        ctk.CTkButton(add_row, text="+", width=36, command=add_subtask).pack(side="left")

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=14)

        def save():
            txt = text_entry.get().strip()
            if not txt:
                messagebox.showerror("Ошибка", "Текст задачи не может быть пустым")
                return
            due_raw = date_entry.get().strip()
            if due_raw:
                try:
                    datetime.datetime.strptime(due_raw, "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("Ошибка", "Используйте формат даты ГГГГ-ММ-ДД")
                    return
            task["text"] = txt
            task["priority"] = prio_var.get()
            task["category"] = cat_var.get()
            task["due_date"] = due_raw or None
            task["repeat"] = REPEAT_OPTIONS.get(repeat_var.get(), "none")
            task["subtasks"] = subtasks
            DataManager.save_json("tasks.json", self.tasks)
            win.destroy()
            SoundService.success()
            self._toast("Задача обновлена", "success")
            if self.active_tab == "todo":
                self._update_todo_list()
            elif self.active_tab == "today":
                self.show_tab("today")

        def delete_from_edit():
            win.destroy()
            self._soft_delete(
                task, self.tasks,
                lambda: DataManager.save_json("tasks.json", self.tasks),
                lambda: self._update_todo_list() if self.active_tab == "todo" else self.show_tab(self.active_tab),
                f"Задача «{task.get('text', '')}» удалена"
            )

        ctk.CTkButton(btn_row, text="Удалить", fg_color="#c0392b", hover_color="#e74c3c", command=delete_from_edit).pack(side="left")
        ctk.CTkButton(btn_row, text="Отмена", fg_color="gray30", command=win.destroy).pack(side="right", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Сохранить", command=save).pack(side="right")

    def _render_todo(self, filter_q=None):
        top = ctk.CTkFrame(self.workspace, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(top, text="Управление задачами", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        done_count = sum(1 for t in self.tasks if isinstance(t, dict) and t.get("done"))
        ctk.CTkLabel(
            top, text=f"Выполнено {done_count} из {len(self.tasks)}",
            font=ctk.CTkFont(size=12), text_color="gray60"
        ).pack(side="right")

        inputs = ctk.CTkFrame(self.workspace)
        inputs.pack(fill="x", padx=16, pady=(0, 10))

        entry = ctk.CTkEntry(inputs, placeholder_text="Что нужно сделать?", font=ctk.CTkFont(size=13))
        entry.pack(side="left", fill="x", expand=True, padx=6, pady=8)

        prio_var = ctk.StringVar(value="🟡 Средний")
        ctk.CTkOptionMenu(inputs, variable=prio_var, values=list(PRIORITIES.keys()), width=110).pack(side="left", padx=3)

        cat_names = [c[0] for c in self.get_categories()] or ["Другое"]
        cat_var = ctk.StringVar(value=cat_names[0])
        ctk.CTkOptionMenu(inputs, variable=cat_var, values=cat_names, width=100).pack(side="left", padx=3)

        date_entry = ctk.CTkEntry(inputs, width=90, placeholder_text="ГГГГ-ММ-ДД")
        date_entry.pack(side="left", padx=3)

        repeat_var = ctk.StringVar(value="Не повторять")
        ctk.CTkOptionMenu(inputs, variable=repeat_var, values=list(REPEAT_OPTIONS.keys()), width=120).pack(side="left", padx=3)

        def add_task():
            text = entry.get().strip()
            if not text:
                return
            due_raw = date_entry.get().strip()
            if due_raw:
                try:
                    datetime.datetime.strptime(due_raw, "%Y-%m-%d")
                except ValueError:
                    messagebox.showerror("Ошибка", "Используйте формат даты ГГГГ-ММ-ДД")
                    SoundService.error()
                    return
            self.tasks.append({
                "text": text, "category": cat_var.get(),
                "priority": prio_var.get(), "due_date": due_raw or None, "done": False,
                "subtasks": [], "repeat": REPEAT_OPTIONS.get(repeat_var.get(), "none")
            })
            DataManager.save_json("tasks.json", self.tasks)
            self._update_todo_list()
            entry.delete(0, "end")
            date_entry.delete(0, "end")
            entry.focus()
            SoundService.success()
            self._toast("Задача добавлена", "success")

        entry.bind("<Return>", lambda e: add_task())
        date_entry.bind("<Return>", lambda e: add_task())
        ctk.CTkButton(inputs, text="Добавить", width=90, command=add_task).pack(side="left", padx=6)

        self.todo_scroll = ctk.CTkScrollableFrame(self.workspace)
        self.todo_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self._update_todo_list(filter_q=filter_q)

    def _update_todo_list(self, filter_q=None):
        for child in list(self.todo_scroll.winfo_children()):
            child.destroy()

        sorted_tasks = sorted(
            self.tasks,
            key=lambda x: (x.get("done", False), -PRIORITIES.get(x.get("priority", "🟡 Средний"), 1))
        )

        visible_count = 0
        for task in sorted_tasks:
            if not isinstance(task, dict):
                continue
            if filter_q and filter_q.lower() not in task.get("text", "").lower():
                continue
            visible_count += 1

            row = ctk.CTkFrame(self.todo_scroll)
            row.pack(fill="x", pady=3, padx=2)

            color = self.get_category_color(task.get("category"))
            ctk.CTkLabel(row, text="▌", text_color=color, font=ctk.CTkFont(size=16, weight="bold")).pack(side="left", padx=(8, 2))

            prio_icon = task.get("priority", "🟡 Средний").split()[0]
            ctk.CTkLabel(row, text=prio_icon).pack(side="left", padx=2)

            chk_var = ctk.BooleanVar(value=task.get("done", False))

            def toggle_done(t=task, v=chk_var):
                t["done"] = v.get()
                DataManager.save_json("tasks.json", self.tasks)
                if t["done"]:
                    SoundService.success()
                    self._apply_task_repeat(t)
                self._update_todo_list(filter_q)

            chk = ctk.CTkCheckBox(row, text=task.get("text", ""), variable=chk_var, command=toggle_done, font=ctk.CTkFont(size=13))
            chk.pack(side="left", fill="x", expand=True, padx=6, pady=8)

            subtasks = task.get("subtasks") or []
            if subtasks:
                done_n = sum(1 for s in subtasks if s.get("done"))
                ctk.CTkLabel(row, text=f"🧩{done_n}/{len(subtasks)}", font=ctk.CTkFont(size=11), text_color="gray60").pack(side="left", padx=4)

            repeat_label = REPEAT_LABELS.get(task.get("repeat", "none"))
            if repeat_label:
                ctk.CTkLabel(row, text=repeat_label, font=ctk.CTkFont(size=11), text_color="gray60").pack(side="left", padx=4)

            if task.get("due_date"):
                ctk.CTkLabel(row, text=f"📅 {task.get('due_date')}", font=ctk.CTkFont(size=11), text_color="gray60").pack(side="left", padx=6)

            if task.get("done"):
                chk.configure(text_color="gray50")

            ctk.CTkButton(row, text="✎", width=28, height=28, fg_color="transparent",
                          command=lambda t=task: self._open_edit_task(t)).pack(side="right", padx=2)

            def delete_item(t=task):
                self._soft_delete(
                    t, self.tasks,
                    lambda: DataManager.save_json("tasks.json", self.tasks),
                    lambda: self._update_todo_list(filter_q),
                    f"Задача «{t.get('text', '')}» удалена"
                )

            ctk.CTkButton(row, text="✕", width=28, height=28, fg_color="transparent", hover_color="#c0392b", command=delete_item).pack(side="right", padx=6)

        if visible_count == 0:
            msg = "Ничего не найдено" if filter_q else "Список задач пуст — добавьте первую задачу выше"
            ctk.CTkLabel(self.todo_scroll, text=msg, text_color="gray60", font=ctk.CTkFont(size=13)).pack(pady=30)

    # --- 4. Помодоро таймер ---

    def _render_pomodoro(self):
        f = ctk.CTkFrame(self.workspace, fg_color="transparent")
        f.pack(expand=True)

        modes_frame = ctk.CTkFrame(f, fg_color="transparent")
        modes_frame.pack(pady=(0, 20))

        ctk.CTkButton(modes_frame, text="Работа (25 мин)", command=lambda: self._set_pomo_mode("work", 25)).pack(side="left", padx=5)
        ctk.CTkButton(modes_frame, text="Короткий перерыв (5 мин)", command=lambda: self._set_pomo_mode("short_break", 5)).pack(side="left", padx=5)
        ctk.CTkButton(modes_frame, text="Длинный перерыв (15 мин)", command=lambda: self._set_pomo_mode("long_break", 15)).pack(side="left", padx=5)

        mode_labels = {"work": "Режим: работа", "short_break": "Режим: короткий перерыв", "long_break": "Режим: длинный перерыв"}
        self.pomo_mode_label = ctk.CTkLabel(f, text=mode_labels.get(self.pomo_mode, ""), text_color="gray60", font=ctk.CTkFont(size=13))
        self.pomo_mode_label.pack()

        self.pomo_label = ctk.CTkLabel(f, font=ctk.CTkFont(size=80, weight="bold"), text=self._format_pomo_time())
        self.pomo_label.pack(pady=10)

        ctrl_frame = ctk.CTkFrame(f, fg_color="transparent")
        ctrl_frame.pack(pady=10)

        self.pomo_btn = ctk.CTkButton(
            ctrl_frame, text="Пауза" if self.pomo_running else "Старт", font=ctk.CTkFont(size=16, weight="bold"),
            width=120, height=40, fg_color="#e67e22" if self.pomo_running else "#2ecc71",
            hover_color="#27ae60", command=self._toggle_pomo
        )
        self.pomo_btn.pack(side="left", padx=8)

        ctk.CTkButton(ctrl_frame, text="Сброс", font=ctk.CTkFont(size=16), width=100, height=40, fg_color="gray30", command=self._reset_pomo).pack(side="left", padx=8)

    def _format_pomo_time(self):
        m = self.pomo_time_left // 60
        s = self.pomo_time_left % 60
        return f"{m:02d}:{s:02d}"

    def _mode_minutes(self, mode):
        return {"work": 25, "short_break": 5, "long_break": 15}.get(mode, 25)

    def _set_pomo_mode(self, mode, minutes):
        self.pomo_running = False
        self.pomo_mode = mode
        self.pomo_time_left = minutes * 60
        if hasattr(self, 'pomo_label') and self.pomo_label.winfo_exists():
            self.pomo_label.configure(text=self._format_pomo_time())
            self.pomo_btn.configure(text="Старт", fg_color="#2ecc71")
            mode_labels = {"work": "Режим: работа", "short_break": "Режим: короткий перерыв", "long_break": "Режим: длинный перерыв"}
            self.pomo_mode_label.configure(text=mode_labels.get(mode, ""))

    def _toggle_pomo(self):
        self.pomo_running = not self.pomo_running
        if self.pomo_running:
            self.pomo_btn.configure(text="Пауза", fg_color="#e67e22")
            self._pomo_tick()
        else:
            self.pomo_btn.configure(text="Старт", fg_color="#2ecc71")

    def _reset_pomo(self):
        self.pomo_running = False
        self.pomo_time_left = self._mode_minutes(self.pomo_mode) * 60
        if hasattr(self, 'pomo_label') and self.pomo_label.winfo_exists():
            self.pomo_label.configure(text=self._format_pomo_time())
            self.pomo_btn.configure(text="Старт", fg_color="#2ecc71")

    def _pomo_tick(self):
        if self.pomo_running and self.pomo_time_left > 0:
            self.pomo_time_left -= 1
            if hasattr(self, 'pomo_label') and self.pomo_label.winfo_exists():
                self.pomo_label.configure(text=self._format_pomo_time())
            self.after(1000, self._pomo_tick)
        elif self.pomo_time_left == 0 and self.pomo_running:
            self.pomo_running = False
            SoundService.notify_sound()
            NotificationService.notify("Deskify Pomodoro", "Время вышло! Сделайте перерыв или вернитесь к работе.")
            self._reset_pomo()

    # --- 5. Напоминания ---

    def _render_reminders(self):
        top = ctk.CTkFrame(self.workspace, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(top, text="Напоминания", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        inputs = ctk.CTkFrame(self.workspace)
        inputs.pack(fill="x", padx=16, pady=(0, 10))

        now = datetime.datetime.now()
        date_entry = ctk.CTkEntry(inputs, width=100, placeholder_text="ГГГГ-ММ-ДД")
        date_entry.insert(0, now.strftime("%Y-%m-%d"))
        date_entry.pack(side="left", padx=6, pady=10)

        hours_opt = [f"{i:02d}" for i in range(24)]
        mins_opt = [f"{i:02d}" for i in range(0, 60, 5)]

        h_var = ctk.StringVar(value=now.strftime("%H"))
        m_var = ctk.StringVar(value="00")

        ctk.CTkOptionMenu(inputs, variable=h_var, values=hours_opt, width=65).pack(side="left", padx=2)
        ctk.CTkLabel(inputs, text=":").pack(side="left")
        ctk.CTkOptionMenu(inputs, variable=m_var, values=mins_opt, width=65).pack(side="left", padx=2)

        repeat_var = ctk.StringVar(value="Не повторять")
        ctk.CTkOptionMenu(inputs, variable=repeat_var, values=list(REPEAT_OPTIONS.keys()), width=120).pack(side="left", padx=4)

        text_entry = ctk.CTkEntry(inputs, placeholder_text="О чём напомнить?", font=ctk.CTkFont(size=13))
        text_entry.pack(side="left", fill="x", expand=True, padx=6, pady=10)

        def add_reminder():
            d = date_entry.get().strip()
            t = f"{h_var.get()}:{m_var.get()}"
            txt = text_entry.get().strip()
            if not txt:
                return
            try:
                reminder_dt = datetime.datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
                if reminder_dt < datetime.datetime.now() - datetime.timedelta(minutes=1):
                    if not messagebox.askyesno("Напоминание в прошлом", "Указанное время уже прошло. Всё равно добавить?"):
                        return
                item = {"date": d, "time": t, "text": txt, "repeat": REPEAT_OPTIONS.get(repeat_var.get(), "none")}
                self.reminders.append(item)
                DataManager.save_json("reminders.json", self.reminders)
                self._update_reminders_list()
                text_entry.delete(0, "end")
                text_entry.focus()
                SoundService.success()
                self._toast("Напоминание добавлено", "success")
            except ValueError:
                messagebox.showerror("Ошибка", "Используйте формат даты ГГГГ-ММ-ДД")
                SoundService.error()

        text_entry.bind("<Return>", lambda e: add_reminder())
        ctk.CTkButton(inputs, text="Добавить", width=90, command=add_reminder).pack(side="left", padx=6)

        self.rem_scroll = ctk.CTkScrollableFrame(self.workspace)
        self.rem_scroll.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self._update_reminders_list()

    def _update_reminders_list(self):
        for child in list(self.rem_scroll.winfo_children()):
            child.destroy()

        def sort_key(r):
            try:
                return datetime.datetime.strptime(f"{r.get('date')} {r.get('time')}", "%Y-%m-%d %H:%M")
            except Exception:
                return datetime.datetime.max

        valid_reminders = [r for r in self.reminders if isinstance(r, dict)]
        now = datetime.datetime.now()

        for rem in sorted(valid_reminders, key=sort_key):
            is_past = False
            try:
                dt = datetime.datetime.strptime(f"{rem.get('date')} {rem.get('time')}", "%Y-%m-%d %H:%M")
                is_past = dt < now and rem.get("repeat", "none") == "none"
            except Exception:
                pass

            row = ctk.CTkFrame(self.rem_scroll)
            row.pack(fill="x", pady=3, padx=2)

            time_str = f"⏰ {rem.get('date')} в {rem.get('time')}"
            if is_past:
                time_str += " (прошло)"
            ctk.CTkLabel(row, text=time_str, font=ctk.CTkFont(weight="bold", size=12),
                         text_color="gray50" if is_past else "#3498db").pack(side="left", padx=10)

            text_kwargs = {"text_color": "gray50"} if is_past else {}
            ctk.CTkLabel(row, text=rem.get('text'), font=ctk.CTkFont(size=13), anchor="w", **text_kwargs).pack(side="left", fill="x", expand=True, padx=8, pady=8)

            repeat_label = REPEAT_LABELS.get(rem.get("repeat", "none"))
            if repeat_label:
                ctk.CTkLabel(row, text=repeat_label, font=ctk.CTkFont(size=11), text_color="gray60").pack(side="left", padx=4)

            def delete_item(r=rem):
                self._soft_delete(
                    r, self.reminders,
                    lambda: DataManager.save_json("reminders.json", self.reminders),
                    self._update_reminders_list, "Напоминание удалено"
                )

            ctk.CTkButton(row, text="✕", width=28, height=28, fg_color="transparent", hover_color="#c0392b", command=delete_item).pack(side="right", padx=6)

        if not valid_reminders:
            ctk.CTkLabel(self.rem_scroll, text="Напоминаний пока нет", text_color="gray60", font=ctk.CTkFont(size=13)).pack(pady=30)

    def _advance_reminder(self, reminder):
        try:
            d = datetime.datetime.strptime(reminder["date"], "%Y-%m-%d").date()
        except Exception:
            return
        repeat = reminder.get("repeat", "none")
        if repeat == "daily":
            d += datetime.timedelta(days=1)
        elif repeat == "weekly":
            d += datetime.timedelta(days=7)
        elif repeat == "monthly":
            month = d.month + 1
            year = d.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            last_day = calendar.monthrange(year, month)[1]
            d = datetime.date(year, month, min(d.day, last_day))
        else:
            return
        reminder["date"] = d.strftime("%Y-%m-%d")
        DataManager.save_json("reminders.json", self.reminders)
        if self.active_tab == "reminders":
            self._update_reminders_list()

    # --- 6. Калькулятор ---

    def _render_calculator(self):
        frame = ctk.CTkFrame(self.workspace, fg_color="transparent")
        frame.pack(expand=True, padx=40, pady=20)

        disp_var = ctk.StringVar()
        entry = ctk.CTkEntry(frame, textvariable=disp_var, font=ctk.CTkFont(size=26, weight="bold"), justify="right", height=55, width=320)
        entry.pack(fill="x", pady=(0, 12))
        entry.focus()

        keys = [
            ["C", "⌫", "(", ")"],
            ["7", "8", "9", "/"],
            ["4", "5", "6", "*"],
            ["1", "2", "3", "-"],
            ["0", ".", "=", "+"],
        ]
        grid = ctk.CTkFrame(frame, fg_color="transparent")
        grid.pack()

        def press(k):
            if k == "=":
                res = SafeCalculator.evaluate(disp_var.get())
                if res in ("Ошибка", "Деление на 0"):
                    SoundService.error()
                disp_var.set(str(res))
            elif k == "C":
                disp_var.set("")
            elif k == "⌫":
                disp_var.set(disp_var.get()[:-1])
            else:
                disp_var.set(disp_var.get() + k)

        for r, row in enumerate(keys):
            for c, k in enumerate(row):
                if k == "=":
                    btn_color = "#1f538d"
                elif k in ("C", "⌫"):
                    btn_color = "#c0392b"
                else:
                    btn_color = ("gray25", "gray35")
                ctk.CTkButton(grid, text=k, font=ctk.CTkFont(size=18, weight="bold"), width=70, height=50, fg_color=btn_color,
                              command=lambda key=k: press(key)).grid(row=r, column=c, padx=4, pady=4)

        entry.bind("<Return>", lambda e: press("="))
        entry.bind("<KP_Enter>", lambda e: press("="))
        entry.bind("<Escape>", lambda e: press("C"))

    # --- 7. Часы ---

    def _render_clock(self):
        f = ctk.CTkFrame(self.workspace, fg_color="transparent")
        f.pack(expand=True)

        lbl_time = ctk.CTkLabel(f, font=ctk.CTkFont(size=72, weight="bold"))
        lbl_time.pack()

        lbl_date = ctk.CTkLabel(f, font=ctk.CTkFont(size=18), text_color="gray60")
        lbl_date.pack(pady=8)

        ctk.CTkButton(f, text="⧉ Открыть в отдельном окне", fg_color="gray30", command=self._open_clock_window).pack(pady=(12, 0))

        def update():
            if lbl_time.winfo_exists():
                now = datetime.datetime.now()
                lbl_time.configure(text=now.strftime("%H:%M:%S"))
                lbl_date.configure(text=now.strftime("%A, %d %B %Y"))
                self.after(1000, update)
        update()

    def _open_clock_window(self):
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.lift()
            self.clock_window.focus_force()
            return

        win = ctk.CTkToplevel(self)
        win.title("Часы — Deskify")
        win.geometry("300x170")
        win.minsize(220, 130)
        self.clock_window = win

        topmost_var = ctk.BooleanVar(value=True)
        win.attributes("-topmost", True)

        lbl = ctk.CTkLabel(win, font=ctk.CTkFont(size=36, weight="bold"))
        lbl.pack(expand=True, pady=(16, 0))
        lbl_d = ctk.CTkLabel(win, font=ctk.CTkFont(size=12), text_color="gray60")
        lbl_d.pack()

        def toggle_topmost():
            win.attributes("-topmost", topmost_var.get())

        ctk.CTkCheckBox(win, text="Поверх всех окон", variable=topmost_var, command=toggle_topmost).pack(pady=8)

        def tick():
            if not win.winfo_exists():
                return
            now = datetime.datetime.now()
            lbl.configure(text=now.strftime("%H:%M:%S"))
            lbl_d.configure(text=now.strftime("%d %B %Y"))
            win.after(1000, tick)

        tick()

    # --- 8. Настройки ---

    def _render_settings(self):
        f = ctk.CTkScrollableFrame(self.workspace, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=24, pady=20)

        ctk.CTkLabel(f, text="Настройки", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=(0, 16))

        ctk.CTkLabel(f, text="Ваше имя", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(4, 2))
        name_entry = ctk.CTkEntry(f, width=260)
        name_entry.insert(0, self.config.get("user_name", ""))
        name_entry.pack(anchor="w", pady=(0, 16))

        ctk.CTkLabel(f, text="Оформление", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(4, 2))
        theme_var = ctk.StringVar(value=self.config.get("theme", "dark"))
        ctk.CTkRadioButton(f, text="Тёмная тема", variable=theme_var, value="dark").pack(anchor="w", pady=4)
        ctk.CTkRadioButton(f, text="Светлая тема", variable=theme_var, value="light").pack(anchor="w", pady=4)

        ctk.CTkLabel(f, text="Звук", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(16, 2))
        sound_var = ctk.BooleanVar(value=self.config.get("sound_enabled", True))
        ctk.CTkCheckBox(f, text="Звуковые уведомления включены", variable=sound_var).pack(anchor="w", pady=4)
        style_var = ctk.StringVar(value=self.config.get("sound_style", "classic"))
        style_labels = {"classic": "Классический", "soft": "Мягкий"}
        ctk.CTkOptionMenu(f, variable=style_var, values=list(style_labels.values()), width=160).pack(anchor="w", pady=(0, 4))
        # переводим отображаемое значение обратно в ключ при сохранении
        style_reverse = {v: k for k, v in style_labels.items()}
        style_var.set(style_labels.get(self.config.get("sound_style", "classic"), "Классический"))

        ctk.CTkLabel(f, text="Поведение", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(16, 2))
        if HAS_TRAY:
            tray_var = ctk.BooleanVar(value=self.config.get("minimize_to_tray", True))
            ctk.CTkCheckBox(f, text="Сворачивать в трей при закрытии окна", variable=tray_var).pack(anchor="w", pady=4)
        else:
            tray_var = None
            ctk.CTkLabel(
                f, text="⚠ Иконка в трее недоступна: не установлены пакеты pystray/Pillow.",
                text_color="#e67e22", font=ctk.CTkFont(size=12)
            ).pack(anchor="w", pady=4)

        if not HAS_PLYER:
            ctk.CTkLabel(
                f, text="ℹ Для системных уведомлений установите пакет plyer (сейчас используются обычные окна).",
                text_color="gray60", font=ctk.CTkFont(size=12)
            ).pack(anchor="w", pady=(4, 0))

        # --- Категории задач ---
        ctk.CTkLabel(f, text="Категории задач", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(20, 4))
        cat_list_frame = ctk.CTkFrame(f, fg_color="transparent")
        cat_list_frame.pack(fill="x")

        def refresh_cats():
            for c in list(cat_list_frame.winfo_children()):
                c.destroy()
            for name, color in self.get_categories():
                row = ctk.CTkFrame(cat_list_frame, fg_color="transparent")
                row.pack(fill="x", pady=2)
                ctk.CTkLabel(row, text="●", text_color=color, font=ctk.CTkFont(size=16)).pack(side="left", padx=(0, 6))
                ctk.CTkLabel(row, text=name).pack(side="left", fill="x", expand=True)

                def remove_cat(n=name):
                    cats = self.get_categories()
                    if len(cats) <= 1:
                        messagebox.showerror("Ошибка", "Должна остаться хотя бы одна категория")
                        return
                    self.config["categories"] = [c for c in cats if c[0] != n]
                    ConfigManager.save(self.config)
                    refresh_cats()

                ctk.CTkButton(row, text="✕", width=24, height=24, fg_color="transparent", hover_color="#c0392b", command=remove_cat).pack(side="right")

        refresh_cats()

        add_cat_row = ctk.CTkFrame(f, fg_color="transparent")
        add_cat_row.pack(fill="x", pady=(4, 20))
        cat_name_entry = ctk.CTkEntry(add_cat_row, placeholder_text="Название категории", width=160)
        cat_name_entry.pack(side="left", padx=(0, 6))
        color_var = ctk.StringVar(value=CATEGORY_PALETTE[0])
        ctk.CTkOptionMenu(add_cat_row, variable=color_var, values=CATEGORY_PALETTE, width=90).pack(side="left", padx=6)

        def add_cat():
            name = cat_name_entry.get().strip()
            if not name:
                return
            cats = self.get_categories()
            if any(c[0] == name for c in cats):
                messagebox.showerror("Ошибка", "Такая категория уже есть")
                return
            cats.append([name, color_var.get()])
            self.config["categories"] = cats
            ConfigManager.save(self.config)
            cat_name_entry.delete(0, "end")
            refresh_cats()
            SoundService.success()

        ctk.CTkButton(add_cat_row, text="+ Добавить", command=add_cat).pack(side="left")

        # --- PIN-защита ---
        ctk.CTkLabel(f, text="Защита доступа", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(4, 2))
        pin_status = "включена" if self.config.get("pin_enabled") else "выключена"
        ctk.CTkLabel(f, text=f"PIN-защита сейчас {pin_status}.", text_color="gray60").pack(anchor="w")

        pin_row = ctk.CTkFrame(f, fg_color="transparent")
        pin_row.pack(fill="x", pady=6)
        pin_entry = ctk.CTkEntry(pin_row, placeholder_text="Новый PIN-код (4+ цифр)", show="•", width=200)
        pin_entry.pack(side="left", padx=(0, 8))

        def set_pin():
            val = pin_entry.get().strip()
            if len(val) < 4 or not val.isdigit():
                messagebox.showerror("Ошибка", "PIN должен содержать минимум 4 цифры")
                return
            self.config["pin_enabled"] = True
            self.config["pin_hash"] = hashlib.sha256(val.encode()).hexdigest()
            ConfigManager.save(self.config)
            SoundService.success()
            self._toast("PIN-код установлен", "success")
            self.show_tab("settings")

        def clear_pin():
            self.config["pin_enabled"] = False
            self.config["pin_hash"] = ""
            ConfigManager.save(self.config)
            self._toast("PIN-защита отключена", "info")
            self.show_tab("settings")

        ctk.CTkButton(pin_row, text="Установить", width=100, command=set_pin).pack(side="left", padx=4)
        if self.config.get("pin_enabled"):
            ctk.CTkButton(pin_row, text="Отключить", width=100, fg_color="#c0392b", command=clear_pin).pack(side="left", padx=4)

        # --- Резервное копирование ---
        ctk.CTkLabel(f, text="Резервное копирование", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(20, 4))
        backup_row = ctk.CTkFrame(f, fg_color="transparent")
        backup_row.pack(fill="x", pady=(0, 20))
        ctk.CTkButton(backup_row, text="Экспортировать данные", command=self._export_backup).pack(side="left", padx=(0, 8))
        ctk.CTkButton(backup_row, text="Импортировать данные", fg_color="gray30", command=self._import_backup).pack(side="left")

        def save():
            self.config["user_name"] = name_entry.get().strip()
            self.config["theme"] = theme_var.get()
            self.config["sound_enabled"] = bool(sound_var.get())
            self.config["sound_style"] = style_reverse.get(style_var.get(), "classic")
            if tray_var is not None:
                self.config["minimize_to_tray"] = bool(tray_var.get())
            ConfigManager.save(self.config)
            self.apply_theme()
            SoundService.success()
            self._toast("Настройки сохранены", "success")
            self.show_tab("settings")  # перерисовать шапку с обновлённым именем

        ctk.CTkButton(f, text="Сохранить", width=140, command=save).pack(anchor="w", pady=10)

        ctk.CTkLabel(f, text=f"{APP_TITLE} v{VERSION}", text_color="gray50", font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(20, 0))

    def _export_backup(self):
        self._save_current_doc()
        path = filedialog.asksaveasfilename(
            title="Экспорт данных Deskify", defaultextension=".json",
            filetypes=[("JSON файл", "*.json")], initialfile="deskify_backup.json"
        )
        if not path:
            return
        backup = {
            "config": self.config,
            "tasks": self.tasks,
            "reminders": self.reminders,
            "notes_docs": self.notes_docs,
            "notes_by_date": self.notes_by_date,
            "exported_at": datetime.datetime.now().isoformat(),
            "app_version": VERSION,
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(backup, fh, ensure_ascii=False, indent=2)
            SoundService.success()
            self._toast("Резервная копия сохранена", "success")
        except Exception as e:
            messagebox.showerror("Ошибка экспорта", str(e))
            SoundService.error()

    def _import_backup(self):
        path = filedialog.askopenfilename(title="Импорт данных Deskify", filetypes=[("JSON файл", "*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            messagebox.showerror("Ошибка импорта", f"Не удалось прочитать файл: {e}")
            SoundService.error()
            return

        if not messagebox.askyesno("Импорт данных", "Импорт заменит текущие задачи, заметки и напоминания. Продолжить?"):
            return

        if isinstance(data.get("tasks"), list):
            self.tasks = data["tasks"]
        if isinstance(data.get("reminders"), list):
            self.reminders = data["reminders"]
        if isinstance(data.get("notes_docs"), list) and data.get("notes_docs"):
            self.notes_docs = data["notes_docs"]
        if isinstance(data.get("notes_by_date"), dict):
            self.notes_by_date = data["notes_by_date"]
        if isinstance(data.get("config"), dict):
            merged = ConfigManager.DEFAULT_CONFIG.copy()
            merged.update(data["config"])
            self.config = merged

        for t in self.tasks:
            if isinstance(t, dict):
                t.setdefault("subtasks", [])
                t.setdefault("repeat", "none")
        for r in self.reminders:
            if isinstance(r, dict):
                r.setdefault("repeat", "none")
        for d in self.notes_docs:
            if isinstance(d, dict):
                d.setdefault("formatting", [])
        self.active_doc_id = self.notes_docs[0]["id"]

        DataManager.save_json("tasks.json", self.tasks)
        DataManager.save_json("reminders.json", self.reminders)
        DataManager.save_json("notes_docs.json", self.notes_docs)
        DataManager.save_json("notes_by_date.json", self.notes_by_date)
        ConfigManager.save(self.config)
        self.apply_theme()

        SoundService.success()
        self._toast("Данные импортированы", "success")
        self.show_tab("today")

    # --- SEARCH & THREADS ---

    def perform_search(self):
        q = self.search_var.get().strip()
        if not q:
            return
        self.show_tab("search", query=q)

    def _render_search(self, query):
        top = ctk.CTkFrame(self.workspace, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(top, text=f"Результаты поиска: «{query}»", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="✕ Закрыть", fg_color="transparent", border_width=1,
                      command=lambda: self.show_tab("today")).pack(side="right")

        scroll = ctk.CTkScrollableFrame(self.workspace)
        scroll.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        q_low = query.lower()
        found_any = False

        matched_tasks = [t for t in self.tasks if isinstance(t, dict) and q_low in t.get("text", "").lower()]
        if matched_tasks:
            found_any = True
            ctk.CTkLabel(scroll, text=f"☑️ Задачи ({len(matched_tasks)})", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(4, 4))
            for t in matched_tasks:
                ctk.CTkButton(
                    scroll, text=t.get("text", ""), anchor="w", fg_color="transparent", hover_color="gray30",
                    command=lambda: self.show_tab("todo", filter_q=query)
                ).pack(fill="x", pady=1)

        matched_docs = [d for d in self.notes_docs if q_low in d.get("title", "").lower() or q_low in d.get("content", "").lower()]
        if matched_docs:
            found_any = True
            ctk.CTkLabel(scroll, text=f"📝 Заметки блокнота ({len(matched_docs)})", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(16, 4))
            for d in matched_docs:
                snippet = d.get("content", "").replace("\n", " ")[:80]

                def open_doc(doc_id=d["id"]):
                    self.active_doc_id = doc_id
                    self.show_tab("notepad")

                ctk.CTkButton(
                    scroll, text=f"{d.get('title')} — {snippet}", anchor="w", fg_color="transparent",
                    hover_color="gray30", command=open_doc
                ).pack(fill="x", pady=1)

        matched_days = [(ds, txt) for ds, txt in self.notes_by_date.items() if q_low in txt.lower()]
        if matched_days:
            found_any = True
            ctk.CTkLabel(scroll, text=f"📅 Заметки по дням ({len(matched_days)})", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(16, 4))
            for ds, txt in sorted(matched_days):
                snippet = txt.replace("\n", " ")[:80]
                ctk.CTkButton(
                    scroll, text=f"{ds} — {snippet}", anchor="w", fg_color="transparent",
                    hover_color="gray30", command=lambda dstr=ds: self._open_day_modal(dstr)
                ).pack(fill="x", pady=1)

        if not found_any:
            ctk.CTkLabel(scroll, text="Ничего не найдено", text_color="gray60").pack(pady=40)
            SoundService.error()

    def _background_reminder_service(self):
        notified = set()
        while not self.stop_bg_event.is_set():
            now = datetime.datetime.now()
            # Итерируем по копии списка: он может изменяться в главном потоке
            # (добавление/удаление напоминаний) параллельно с этим фоновым потоком.
            for r in list(self.reminders):
                if not isinstance(r, dict):
                    continue
                try:
                    key = f"{r.get('date')}_{r.get('time')}_{r.get('text')}"
                    dt = datetime.datetime.strptime(f"{r.get('date')} {r.get('time')}", "%Y-%m-%d %H:%M")
                    if now <= dt <= now + datetime.timedelta(seconds=45) and key not in notified:
                        self.after(0, SoundService.notify_sound)
                        self.after(0, lambda title="Deskify", msg=r.get("text"): NotificationService.notify(title, msg))
                        notified.add(key)
                        if r.get("repeat", "none") != "none":
                            self.after(0, lambda rem=r: self._advance_reminder(rem))
                except Exception:
                    continue
            if self.stop_bg_event.wait(timeout=10):
                break

    def full_quit(self, icon=None, item=None):
        self._save_current_doc()
        self.stop_bg_event.set()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    # --- PLUGINS API ---

    def _load_plugins(self):
        pd = PathHelper.plugins_dir()
        api = {
            "add_button": self.add_plugin_button,
            "add_plugin_button": self.add_plugin_button,
            "open_window": lambda title, w=400, h=300: self._create_plugin_window(title, w, h),
            "get_tasks": lambda: self.tasks,
            "ctk": ctk
        }
        try:
            plugin_files = [f for f in os.listdir(pd) if f.endswith(".py")]
        except Exception:
            plugin_files = []

        for file in plugin_files:
            try:
                spec = importlib.util.spec_from_file_location(f"plugin_{file}", os.path.join(pd, file))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register(api)
            except Exception as e:
                self._log_plugin_error(file, e)

    def _log_plugin_error(self, filename, error):
        print(f"Ошибка загрузки плагина {filename}: {error}")
        try:
            log_path = PathHelper.get_path("plugin_errors.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {filename}: {error}\n")
        except Exception:
            pass

    def add_plugin_button(self, name, callback):
        btn = ctk.CTkButton(self.plugins_frame, text=name, anchor="w", fg_color="gray20", hover_color="gray30", height=32, command=callback)
        btn.pack(fill="x", pady=2)

    def _create_plugin_window(self, title, width, height):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry(f"{width}x{height}")
        win.grab_set()
        return win


if __name__ == "__main__":
    app = DeskifyApp()
    app.mainloop()
