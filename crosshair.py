import tkinter as tk
import sys
import json
import os
from PIL import Image, ImageTk
from pynput import keyboard

class CrosshairOverlay:
    def __init__(self):
        self.config = self.load_config()
        self.visible = True
        
        self.root = tk.Tk()
        
        # Настройка окна
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-transparentcolor', 'black')
        
        # Размер окна с прицелом
        self.window_size = 100
        
        # Получаем размер экрана и центрируем окно
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - self.window_size) // 2
        y = (screen_height - self.window_size) // 2
        
        self.root.geometry(f'{self.window_size}x{self.window_size}+{x}+{y}')
        self.root.config(bg='black')
        
        # Создаем canvas для рисования
        self.canvas = tk.Canvas(
            self.root,
            width=self.window_size,
            height=self.window_size,
            bg='black',
            highlightthickness=0
        )
        self.canvas.pack()
        
        # Рисуем прицел
        self.draw_crosshair()
        
        # Запускаем слушатель клавиатуры
        self.setup_hotkeys()
        
    def load_config(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print("config.json не найден, используются настройки по умолчанию")
            return {
                "hotkeys": {"toggle": "F1", "exit": "Escape"},
                "crosshair": {
                    "type": "cross",
                    "color": "lime",
                    "size": 15,
                    "thickness": 2,
                    "gap": 5
                }
            }
    
    def setup_hotkeys(self):
        def on_press(key):
            try:
                key_name = key.name if hasattr(key, 'name') else str(key).replace("'", "")
                
                if key_name.lower() == self.config['hotkeys']['toggle'].lower():
                    self.toggle_visibility()
                elif key_name.lower() == self.config['hotkeys']['exit'].lower():
                    self.close()
            except AttributeError:
                pass
        
        listener = keyboard.Listener(on_press=on_press)
        listener.start()
    
    def toggle_visibility(self):
        if self.visible:
            self.root.withdraw()
            self.visible = False
        else:
            self.root.deiconify()
            self.visible = True
    
    def draw_crosshair(self):
        self.canvas.delete('all')
        center = self.window_size // 2
        
        cfg = self.config['crosshair']
        crosshair_type = cfg['type']
        color = cfg['color']
        size = cfg['size']
        thickness = cfg['thickness']
        gap = cfg['gap']
        
        if crosshair_type == 'cross':
            self.draw_cross(center, size, gap, thickness, color)
        elif crosshair_type == 'dot':
            self.draw_dot(center, size, color)
        elif crosshair_type == 'circle':
            self.draw_circle(center, size, thickness, color)

    
    def draw_cross(self, center, length, gap, thickness, color):
        # Верхняя линия
        self.canvas.create_line(
            center, center - gap - length,
            center, center - gap,
            fill=color, width=thickness
        )
        
        # Нижняя линия
        self.canvas.create_line(
            center, center + gap,
            center, center + gap + length,
            fill=color, width=thickness
        )
        
        # Левая линия
        self.canvas.create_line(
            center - gap - length, center,
            center - gap, center,
            fill=color, width=thickness
        )
        
        # Правая линия
        self.canvas.create_line(
            center + gap, center,
            center + gap + length, center,
            fill=color, width=thickness
        )
        
        # Центральная точка
        self.canvas.create_oval(
            center - 1, center - 1,
            center + 1, center + 1,
            fill=color, outline=color
        )
    
    def draw_dot(self, center, size, color):
        self.canvas.create_oval(
            center - size // 2, center - size // 2,
            center + size // 2, center + size // 2,
            fill=color, outline=color
        )
    
    def draw_circle(self, center, radius, thickness, color):
        self.canvas.create_oval(
            center - radius, center - radius,
            center + radius, center + radius,
            outline=color, width=thickness
        )
        # Центральная точка
        self.canvas.create_oval(
            center - 1, center - 1,
            center + 1, center + 1,
            fill=color, outline=color
        )
    
    
    def close(self):
        self.root.destroy()
        sys.exit(0)
    
    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    app = CrosshairOverlay()
    app.run()
