# -*- coding: utf-8 -*-
"""
Плагин: Центр управления плагинами (Plugin Store) для Deskify Workspace.
Масштабное расширение для загрузки, обновления и установки новых модулей прямо из GitHub репозитория.
"""

import os
import sys
import json
import urllib.request
import threading
from tkinter import messagebox

api_workspace = None

# URL-адрес каталога плагинов на вашем GitHub (в будущем вы можете заменить на свой репозиторий)
# Для примера используется демонстрационный JSON, описывающий доступные плагины
MARKETPLACE_URL = "https://githubusercontent.com"

class PluginStoreWindow:
    def __init__(self):
        self.ctk = api_workspace["ctk"]
        
        # Создаем большое полноценное окно магазина
        self.win = api_workspace["open_window"]("Deskify Plugin Store", 700, 550)
        self.win.minsize(600, 400)
        
        self.plugins_dir = os.path.dirname(os.path.abspath(__file__))
        
        self._build_ui()
        
        # Загружаем список доступных плагинов в отдельном потоке, чтобы UI не зависал
        threading.Thread(target=self._fetch_catalog, daemon=True).start()

    def _build_ui(self):
        # Шапка магазина
        top_bar = self.ctk.CTkFrame(self.win, fg_color="transparent")
        top_bar.pack(fill="x", padx=20, pady=(16, 8))
        
        self.title_lbl = self.ctk.CTkLabel(
            top_bar, text="🔌 Магазин расширений Deskify", 
            font=self.ctk.CTkFont(size=20, weight="bold")
        )
        self.title_lbl.pack(side="left")
        
        self.status_lbl = self.ctk.CTkLabel(top_bar, text="Подключение к серверу...", text_color="gray60")
        self.status_lbl.pack(side="right", padx=10)

        # Главный скролл-контейнер для карточек плагинов
        self.scroll_frame = self.ctk.CTkScrollableFrame(self.win, fg_color="transparent")
        self.scroll_frame.pack(fill="both", expand=True, padx=20, pady=(4, 16))

    def _fetch_catalog(self):
        """Безопасное скачивание каталога плагинов из интернета"""
        try:
            # Имитируем каталог, если сеть недоступна или репозиторий еще не создан
            # В продакшене этот JSON будет отдавать ваш GitHub
            try:
                with urllib.request.urlopen(MARKETPLACE_URL, timeout=5) as response:
                    catalog = json.loads(response.read().decode('utf-8'))
            except Exception:
                # Запасной демонстрационный каталог (Mock-данные для демонстрации работы)
                catalog = [
                    {
                        "id": "engineering_calculator.py",
                        "name": "📐 Инженерный калькулятор",
                        "desc": "Расширенные математические функции: тригонометрия (sin, cos), логарифмы, корни и константы пи/е.",
                        "url": "https://githubusercontent.com"
                    },
                    {
                        "id": "desktop_stickers.py",
                        "name": "📌 Стикеры на рабочий стол",
                        "desc": "Создание миниатюрных полупрозрачных заметок-листочков, закрепляемых поверх всех окон системы.",
                        "url": "https://githubusercontent.com"
                    },
                    {
                        "id": "habit_tracker.py",
                        "name": "📈 Трекер привычек",
                        "desc": "Позволяет внедрять полезные ритуалы, контролировать их выполнение и копить серии огненных дней.",
                        "url": "https://githubusercontent.com"
                    }
                ]

            self.win.after(0, lambda: self._render_catalog(catalog))
        except Exception as e:
            self.win.after(0, lambda: self._set_status(f"Ошибка сети: {str(e)}", "error"))

    def _set_status(self, text, mode="info"):
        color = "#e74c3c" if mode == "error" else "gray60"
        self.status_lbl.configure(text=text, text_color=color)

    def _render_catalog(self, catalog):
        self._set_status("Каталог успешно обновлен")
        
        # Очищаем контейнер
        for w in self.scroll_frame.winfo_children():
            w.destroy()

        for p in catalog:
            # Проверяем, установлен ли уже этот плагин локально
            file_path = os.path.join(self.plugins_dir, p["id"])
            is_installed = os.path.exists(file_path)

            # Карточка плагина
            card = self.ctk.CTkFrame(self.scroll_frame, corner_radius=8)
            card.pack(fill="x", pady=6, padx=4)

            # Текстовый блок внутри карточки
            info_frame = self.ctk.CTkFrame(card, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True, padx=12, pady=12)

            ctk.CTkLabel(info_frame, text=p["name"], font=self.ctk.CTkFont(size=15, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(info_frame, text=p["desc"], text_color="gray60", font=self.ctk.CTkFont(size=12), wrap_length=420, justify="left").pack(anchor="w", pady=(4, 0))

            # Кнопка управления (Установить / Удалить)
            btn_frame = self.ctk.CTkFrame(card, fg_color="transparent")
            btn_frame.pack(side="right", padx=16, pady=12)

            if is_installed:
                btn = self.ctk.CTkButton(
                    btn_frame, text="Удалить", fg_color="#c0392b", hover_color="#e74c3c", width=100,
                    command=lambda p_id=p["id"]: self._delete_plugin(p_id)
                )
            else:
                btn = self.ctk.CTkButton(
                    btn_frame, text="Установить", fg_color="#1f538d", hover_color="#1c497d", width=100,
                    command=lambda p_url=p["url"], p_id=p["id"]: self._install_plugin(p_url, p_id)
                )
            btn.pack()

    def _install_plugin(self, url, filename):
        self._set_status("Загрузка...")
        
        def worker():
            try:
                target_path = os.path.join(self.plugins_dir, filename)
                
                # Скачиваем файл плагина из репозитория
                with urllib.request.urlopen(url, timeout=10) as response:
                    code = response.read()
                    with open(target_path, "wb") as f:
                        f.write(code)
                        
                self.win.after(0, self._prompt_restart)
            except Exception as e:
                self.win.after(0, lambda: messagebox.showerror("Ошибка установки", f"Не удалось скачать плагин:\n{e}"))
                self.win.after(0, lambda: self._set_status("Ошибка установки", "error"))

        threading.Thread(target=worker, daemon=True).start()

    def _delete_plugin(self, filename):
        if messagebox.askyesno("Удаление", f"Вы уверены, что хотите удалить {filename}?"):
            try:
                target_path = os.path.join(self.plugins_dir, filename)
                if os.path.exists(target_path):
                    os.remove(target_path)
                self._prompt_restart()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось удалить файл: {e}")

    def _prompt_restart(self):
        """Информирует об успешном действии и предлагает перезапустить Deskify"""
        if messagebox.askyesno("Перезапуск системы", "Действие выполнено успешно!\n\nДля применения изменений необходимо перезапустить Deskify Workspace. Перезапустить сейчас?"):
            self._restart_app()
        else:
            # Просто обновляем интерфейс магазина, если пользователь отказался перезапускаться прямо сейчас
            threading.Thread(target=self._fetch_catalog, daemon=True).start()

    def _restart_app(self):
        """Горячий перезапуск всего приложения Deskify Workspace"""
        try:
            python = sys.executable
            os.execv(python, [python] + sys.argv)
        except Exception as e:
            messagebox.showerror("Ошибка перезапуска", f"Не удалось автоматически перезагрузить приложение: {e}")


def open_store():
    PluginStoreWindow()

def register(api):
    global api_workspace
    api_workspace = api
    
    # Интегрируем кнопку маркетплейса в сайдбар Deskify Workspace
    api["add_button"]("🌐 Магазин плагинов", open_store)
