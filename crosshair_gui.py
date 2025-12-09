import sys
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--magnifier-ipc', action='store_true', help='Magnifier IPC mode')
args, unknown = parser.parse_known_args()

if args.magnifier_ipc:
    import time
    import numpy as np
    import mss
    import cv2
    import ctypes
    import threading
    import json
    
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    
    from PyQt5 import QtWidgets, QtCore, QtGui
    
    def load_magnifier_config():
        default_config = {
            'magnifier': {
                'capture_size': 150,
                'display_size': 300,
                'zoom': 2.0,
                'target_fps': 60,
                'interpolation': 'linear',
                'offset_x': 0,
                'offset_y': 0,
                'use_cuda': False,
                'monitor_index': 1
            }
        }
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                if 'magnifier' in file_config:
                    default_config['magnifier'].update(file_config['magnifier'])
                return default_config
        except:
            return default_config
    
    class CaptureThread(QtCore.QThread):
        frame_ready = QtCore.pyqtSignal(QtGui.QImage)
        
        INTERPOLATION_MODES = {
            'nearest': cv2.INTER_NEAREST,
            'linear': cv2.INTER_LINEAR,
            'cubic': cv2.INTER_CUBIC,
            'lanczos': cv2.INTER_LANCZOS4,
        }
        
        def __init__(self, config=None):
            super().__init__()
            config = config or {}
            mag_config = config.get('magnifier', {})
            
            self.capture_size = mag_config.get('capture_size', 200)
            self.display_size = mag_config.get('display_size', 300)
            self.target_fps = mag_config.get('target_fps', 60)
            self.interpolation = mag_config.get('interpolation', 'linear')
            self.monitor_index = mag_config.get('monitor_index', 1)
            self.use_cuda = mag_config.get('use_cuda', False)
            
            self.running = False
            self.current_fps = self.target_fps
            self.frame_times = []
        
        def run(self):
            self.running = True
            
            cuda_available = False
            if self.use_cuda:
                try:
                    if cv2.cuda.getCudaEnabledDeviceCount() > 0:
                        cuda_available = True
                except Exception:
                    pass
            
            with mss.mss() as sct:
                try:
                    mon = sct.monitors[self.monitor_index]
                except IndexError:
                    mon = sct.monitors[1]
                
                mon_left, mon_top = mon['left'], mon['top']
                screen_width, screen_height = mon['width'], mon['height']
                
                capture_left = mon_left + (screen_width - self.capture_size) // 2
                capture_top = mon_top + (screen_height - self.capture_size) // 2
                
                capture_region = {
                    'left': capture_left,
                    'top': capture_top,
                    'width': self.capture_size,
                    'height': self.capture_size
                }
                
                interp = self.INTERPOLATION_MODES.get(self.interpolation, cv2.INTER_LINEAR)
                
                while self.running:
                    t0 = time.perf_counter()
                    
                    screenshot = sct.grab(capture_region)
                    img = np.array(screenshot)
                    
                    if img.shape[2] == 4:
                        img = img[:, :, :3]
                    
                    if cuda_available:
                        try:
                            gpu_mat = cv2.cuda_GpuMat()
                            gpu_mat.upload(img)
                            resized = cv2.cuda.resize(gpu_mat, (self.display_size, self.display_size), interpolation=interp)
                            zoomed = resized.download()
                        except Exception:
                            zoomed = cv2.resize(img, (self.display_size, self.display_size), interpolation=interp)
                    else:
                        zoomed = cv2.resize(img, (self.display_size, self.display_size), interpolation=interp)
                    
                    rgb = cv2.cvtColor(zoomed, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    bytes_per_line = ch * w
                    
                    qimg = QtGui.QImage(rgb.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888).copy()
                    self.frame_ready.emit(qimg)
                    
                    elapsed = time.perf_counter() - t0
                    target_time = 1.0 / self.current_fps
                    if elapsed < target_time:
                        time.sleep(target_time - elapsed)
                    else:
                        self._adapt_fps(elapsed)
        
        def _adapt_fps(self, frame_time):
            self.frame_times.append(frame_time)
            if len(self.frame_times) > 10:
                self.frame_times.pop(0)
            
            avg_time = sum(self.frame_times) / len(self.frame_times)
            if avg_time > 1.0 / 30:
                self.current_fps = max(15, self.current_fps - 5)
            elif avg_time < 1.0 / 50 and self.current_fps < self.target_fps:
                self.current_fps = min(self.target_fps, self.current_fps + 5)
        
        def stop(self):
            self.running = False
            self.wait(2000)
    
    class MagnifierWindow(QtWidgets.QLabel):
        def __init__(self, config=None):
            super().__init__()
            self.config = config or {}
            mag_config = self.config.get('magnifier', {})
            
            self.display_size = mag_config.get('display_size', 300)
            self.offset_x = mag_config.get('offset_x', 0)
            self.offset_y = mag_config.get('offset_y', 0)
            
            self.setWindowFlags(
                QtCore.Qt.FramelessWindowHint |
                QtCore.Qt.WindowStaysOnTopHint |
                QtCore.Qt.Tool |
                QtCore.Qt.WindowTransparentForInput
            )
            self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
            self.setFixedSize(self.display_size, self.display_size)
            self.setAlignment(QtCore.Qt.AlignCenter)
            self.setStyleSheet("background-color: black;")
            
            self.capture_thread = CaptureThread(config)
            self.capture_thread.frame_ready.connect(self.on_frame)
            
            self._position_window()
            self.hide()
            self._click_through_applied = False
        
        def _position_window(self):
            import mss
            with mss.mss() as sct:
                mag_config = self.config.get('magnifier', {})
                monitor_index = mag_config.get('monitor_index', 1)
                try:
                    mon = sct.monitors[monitor_index]
                except IndexError:
                    mon = sct.monitors[1]
                
                screen_center_x = mon['left'] + mon['width'] // 2
                screen_center_y = mon['top'] + mon['height'] // 2
                x = screen_center_x - self.display_size // 2 + self.offset_x
                y = screen_center_y - self.display_size // 2 + self.offset_y
                
                self.setGeometry(x, y, self.display_size, self.display_size)
        
        def _set_click_through(self):
            if sys.platform == 'win32':
                self._hwnd = int(self.effectiveWinId())
                self._apply_click_through_style()
                self._exclude_from_capture()
                QtCore.QTimer.singleShot(500, self._apply_click_through_style)
        
        def _exclude_from_capture(self):
            if not hasattr(self, '_hwnd'):
                return
            
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            WDA_MONITOR = 0x00000001
            
            result = ctypes.windll.user32.SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE)
            if not result:
                ctypes.windll.user32.SetWindowDisplayAffinity(self._hwnd, WDA_MONITOR)
        
        def _apply_click_through_style(self):
            if not hasattr(self, '_hwnd'):
                return
            
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            
            style = ctypes.windll.user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
            if style & WS_EX_TRANSPARENT:
                return
            
            new_style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, new_style)
            ctypes.windll.user32.SetLayeredWindowAttributes(self._hwnd, 0, 255, 0x02)
            ctypes.windll.user32.SetWindowPos(self._hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
        
        def on_frame(self, qimg: QtGui.QImage):
            if not qimg.isNull():
                self.setPixmap(QtGui.QPixmap.fromImage(qimg))
        
        @QtCore.pyqtSlot()
        def show_magnifier(self):
            if not self.isVisible():
                if not self.capture_thread.isRunning():
                    self.capture_thread.start()
                self.show()
                self.raise_()
                if not self._click_through_applied:
                    QtCore.QTimer.singleShot(100, self._set_click_through)
                    self._click_through_applied = True
        
        @QtCore.pyqtSlot()
        def hide_magnifier(self):
            if self.isVisible():
                self.capture_thread.stop()
                self.hide()
        
        def close(self):
            self.capture_thread.stop()
            super().close()
    
    config = load_magnifier_config()
    app = QtWidgets.QApplication(sys.argv)
    magnifier = MagnifierWindow(config)
    
    def ipc_reader():
        while True:
            try:
                line = sys.stdin.readline().strip()
                if line == 'show':
                    QtCore.QMetaObject.invokeMethod(magnifier, 'show_magnifier', QtCore.Qt.QueuedConnection)
                elif line == 'hide':
                    QtCore.QMetaObject.invokeMethod(magnifier, 'hide_magnifier', QtCore.Qt.QueuedConnection)
                elif line == 'quit' or not line:
                    app.quit()
                    break
            except:
                break
    
    ipc_thread = threading.Thread(target=ipc_reader, daemon=True)
    ipc_thread.start()
    
    sys.exit(app.exec_())

import tkinter as tk
from tkinter import ttk
import json
import os
from PIL import Image, ImageTk, ImageDraw
from pynput import keyboard, mouse
import threading
import ctypes


class CrosshairApp:
    def __init__(self):
        self.config = self.load_config()
        self.overlay = None
        self.magnifier_process = None
        self.magnifier_visible = False
        self.listener = None
        self.mouse_listener = None
        
        self.root = tk.Tk()
        self.root.title("Crosshair Settings")
        self.root.geometry("650x500")
        self.root.resizable(True, True)
        self.root.minsize(550, 450)
        try:
            self.root.iconbitmap('Sprite-0001.ico')
        except:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)
        
        self.crosshair_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.crosshair_tab, text="Прицел")
        self.create_crosshair_tab()
        
        self.magnifier_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.magnifier_tab, text="Лупа")
        self.create_magnifier_tab()
        
        self.hotkey_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.hotkey_tab, text="Хоткеи")
        self.create_hotkey_tab()
        
        self.start_overlay()
        self.setup_hotkeys()

    def load_config(self):
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {
                "hotkeys": {"toggle": "f1"},
                "crosshair": {
                    "type": "cross",
                    "r": 0, "g": 255, "b": 0,
                    "alpha": 255,
                    "size": 15,
                    "thickness": 2,
                    "gap": 5,
                    "dot_size": 2
                }
            }

    def save_config(self):
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)
        self.update_overlay()

    def create_crosshair_tab(self):
        main_container = ttk.Frame(self.crosshair_tab)
        main_container.pack(fill='both', expand=True, padx=10, pady=10)
        
        frame = ttk.Frame(main_container)
        frame.pack(side='left', fill='both', expand=True)
        
        ttk.Label(frame, text="Цвет (RGB):").grid(row=0, column=0, sticky='w', pady=5)
        
        self.r_var = tk.DoubleVar(value=self.config['crosshair']['r'])
        self.g_var = tk.DoubleVar(value=self.config['crosshair']['g'])
        self.b_var = tk.DoubleVar(value=self.config['crosshair']['b'])
        
        ttk.Label(frame, text="R:").grid(row=1, column=0, sticky='w')
        ttk.Scale(frame, from_=0, to=255, variable=self.r_var,
                 command=lambda v: self.on_color_change()).grid(row=1, column=1, sticky='ew', padx=5)
        self.r_label = ttk.Label(frame, text=str(int(self.r_var.get())))
        self.r_label.grid(row=1, column=2)
        
        ttk.Label(frame, text="G:").grid(row=2, column=0, sticky='w')
        ttk.Scale(frame, from_=0, to=255, variable=self.g_var,
                 command=lambda v: self.on_color_change()).grid(row=2, column=1, sticky='ew', padx=5)
        self.g_label = ttk.Label(frame, text=str(int(self.g_var.get())))
        self.g_label.grid(row=2, column=2)
        
        ttk.Label(frame, text="B:").grid(row=3, column=0, sticky='w')
        ttk.Scale(frame, from_=0, to=255, variable=self.b_var,
                 command=lambda v: self.on_color_change()).grid(row=3, column=1, sticky='ew', padx=5)
        self.b_label = ttk.Label(frame, text=str(int(self.b_var.get())))
        self.b_label.grid(row=3, column=2)
        
        ttk.Label(frame, text="Прозрачность:").grid(row=4, column=0, sticky='w', pady=(10,0))
        self.alpha_var = tk.DoubleVar(value=self.config['crosshair']['alpha'])
        ttk.Scale(frame, from_=0, to=255, variable=self.alpha_var,
                 command=lambda v: self.on_slider_move()).grid(row=4, column=1, sticky='ew', padx=5, pady=(10,0))
        self.alpha_label = ttk.Label(frame, text=f"{self.alpha_var.get():.2f}")
        self.alpha_label.grid(row=4, column=2, pady=(10,0))
        
        self.width_label_widget = ttk.Label(frame, text="Ширина:")
        self.width_label_widget.grid(row=5, column=0, sticky='w', pady=(10,0))
        self.thickness_var = tk.DoubleVar(value=self.config['crosshair']['thickness'])
        self.width_scale = ttk.Scale(frame, from_=0.5, to=10, variable=self.thickness_var,
                 command=lambda v: self.on_slider_move())
        self.width_scale.grid(row=5, column=1, sticky='ew', padx=5, pady=(10,0))
        self.thickness_label = ttk.Label(frame, text=f"{self.thickness_var.get():.2f}")
        self.thickness_label.grid(row=5, column=2, pady=(10,0))
        
        self.length_label_widget = ttk.Label(frame, text="Длина:")
        self.length_label_widget.grid(row=6, column=0, sticky='w')
        self.size_var = tk.DoubleVar(value=self.config['crosshair']['size'])
        self.length_scale = ttk.Scale(frame, from_=5, to=50, variable=self.size_var,
                 command=lambda v: self.on_slider_move())
        self.length_scale.grid(row=6, column=1, sticky='ew', padx=5)
        self.size_label = ttk.Label(frame, text=f"{self.size_var.get():.2f}")
        self.size_label.grid(row=6, column=2)
        
        self.gap_label_widget = ttk.Label(frame, text="Отступ:")
        self.gap_label_widget.grid(row=7, column=0, sticky='w')
        self.gap_var = tk.DoubleVar(value=self.config['crosshair']['gap'])
        self.gap_scale = ttk.Scale(frame, from_=0, to=20, variable=self.gap_var,
                 command=lambda v: self.on_slider_move())
        self.gap_scale.grid(row=7, column=1, sticky='ew', padx=5)
        self.gap_label = ttk.Label(frame, text=f"{self.gap_var.get():.2f}")
        self.gap_label.grid(row=7, column=2)
        
        self.dot_size_label_widget = ttk.Label(frame, text="Размер точки:")
        self.dot_size_label_widget.grid(row=8, column=0, sticky='w')
        self.dot_size_var = tk.DoubleVar(value=self.config['crosshair'].get('dot_size', 2))
        self.dot_size_scale = ttk.Scale(frame, from_=1, to=10, variable=self.dot_size_var,
                 command=lambda v: self.on_slider_move())
        self.dot_size_scale.grid(row=8, column=1, sticky='ew', padx=5)
        self.dot_size_label = ttk.Label(frame, text=f"{self.dot_size_var.get():.2f}")
        self.dot_size_label.grid(row=8, column=2)
        
        frame.columnconfigure(1, weight=1)
        
        samples_frame = ttk.LabelFrame(main_container, text="Типы прицелов", padding=10)
        samples_frame.pack(side='right', fill='y', padx=(10,0))
        
        self.crosshair_type_var = tk.StringVar(value=self.config['crosshair']['type'])
        
        types = [
            ("dot", "dot"), ("circle", "circle"), ("circle_dot", "circle_dot"),
            ("cross", "cross"), ("chevron", "chevron"), ("cross_no_dot", "cross_no_dot")
        ]
        
        self.preview_canvases = []
        for i, (label, value) in enumerate(types):
            row, col = i // 2, i % 2
            btn_frame = ttk.Frame(samples_frame)
            btn_frame.grid(row=row, column=col, padx=5, pady=5)
            preview = tk.Canvas(btn_frame, width=60, height=60, bg='white',
                              highlightthickness=2, highlightbackground='gray', cursor='hand2')
            preview.pack()
            self.preview_canvases.append((preview, value))
            self.draw_preview(preview, value)
            preview.bind('<Button-1>', lambda e, v=value: self.select_crosshair_type(v))
        
        self.update_previews()
        self.update_slider_visibility()

    def create_hotkey_tab(self):
        frame = ttk.Frame(self.hotkey_tab, padding=20)
        frame.pack(fill='both', expand=True)
        
        ttk.Label(frame, text="Хоткей для включения/выключения прицела:").pack(anchor='w', pady=(0,5))
        
        hotkey_frame = ttk.Frame(frame)
        hotkey_frame.pack(fill='x', pady=(0,15))
        
        self.hotkey_var = tk.StringVar(value=self.config['hotkeys']['toggle'])
        self.hotkey_entry = ttk.Entry(hotkey_frame, textvariable=self.hotkey_var, width=30, state='readonly')
        self.hotkey_entry.pack(side='left', padx=(0,10))
        
        self.record_btn = ttk.Button(hotkey_frame, text="Записать", command=self.record_hotkey)
        self.record_btn.pack(side='left')
        
        ttk.Label(frame, text="Хоткей для включения/выключения лупы:").pack(anchor='w', pady=(15,5))
        
        magnifier_hotkey_frame = ttk.Frame(frame)
        magnifier_hotkey_frame.pack(fill='x', pady=(0,15))
        
        self.magnifier_hotkey_var = tk.StringVar(value=self.config['hotkeys'].get('magnifier', 'f1'))
        self.magnifier_hotkey_entry = ttk.Entry(magnifier_hotkey_frame, textvariable=self.magnifier_hotkey_var, width=30, state='readonly')
        self.magnifier_hotkey_entry.pack(side='left', padx=(0,10))
        
        self.magnifier_record_btn = ttk.Button(magnifier_hotkey_frame, text="Записать", command=self.record_magnifier_hotkey)
        self.magnifier_record_btn.pack(side='left')
        
        ttk.Label(frame, text="ESC для отмены записи хоткея", foreground='gray').pack(anchor='w', pady=10)
        
        self.recording = False
        self.recording_magnifier = False
        self.hotkey_listener = None
    
    def create_magnifier_tab(self):
        """Создает вкладку настроек лупы."""
        frame = ttk.Frame(self.magnifier_tab, padding=20)
        frame.pack(fill='both', expand=True)
        
        mag_config = self.config.get('magnifier', {})
        
        ttk.Label(frame, text="Размер окна лупы (px):").grid(row=0, column=0, sticky='w', pady=5)
        self.display_size_var = tk.IntVar(value=mag_config.get('display_size', 300))
        ttk.Scale(frame, from_=100, to=1000, variable=self.display_size_var,
                 command=lambda v: self.on_magnifier_change()).grid(row=0, column=1, sticky='ew', padx=5)
        self.display_size_label = ttk.Label(frame, text=str(self.display_size_var.get()))
        self.display_size_label.grid(row=0, column=2)
        
        ttk.Label(frame, text="Кратность приближения:").grid(row=1, column=0, sticky='w', pady=5)
        self.zoom_var = tk.DoubleVar(value=mag_config.get('zoom', 2.0))
        ttk.Scale(frame, from_=1.0, to=8.0, variable=self.zoom_var,
                 command=lambda v: self.on_magnifier_change()).grid(row=1, column=1, sticky='ew', padx=5)
        self.zoom_label = ttk.Label(frame, text=f"{self.zoom_var.get():.1f}x")
        self.zoom_label.grid(row=1, column=2)
        
        ttk.Label(frame, text="FPS:").grid(row=2, column=0, sticky='w', pady=5)
        self.fps_var = tk.IntVar(value=mag_config.get('target_fps', 60))
        ttk.Scale(frame, from_=15, to=120, variable=self.fps_var,
                 command=lambda v: self.on_magnifier_change()).grid(row=2, column=1, sticky='ew', padx=5)
        self.fps_label = ttk.Label(frame, text=str(self.fps_var.get()))
        self.fps_label.grid(row=2, column=2)
        
        frame.columnconfigure(1, weight=1)
        
        status_frame = ttk.LabelFrame(frame, text="Статус", padding=10)
        status_frame.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(20,0))
        
        self.magnifier_status_var = tk.StringVar(value="Не запущена")
        ttk.Label(status_frame, textvariable=self.magnifier_status_var).pack(anchor='w')
        
        info_frame = ttk.LabelFrame(frame, text="Информация", padding=10)
        info_frame.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(10,0))
        
        info_text = """Лупа захватывает область экрана вокруг центра и отображает увеличенное изображение.

• Размер области захвата = Размер окна / Кратность
• Большая кратность = сильнее приближение
• Используйте хоткей для включения/выключения"""
        
        ttk.Label(info_frame, text=info_text, justify='left').pack(anchor='w')
    
    def on_magnifier_change(self):
        """Обработчик изменения настроек лупы."""
        display_size = int(self.display_size_var.get())
        zoom = round(self.zoom_var.get(), 1)
        fps = int(self.fps_var.get())
        
        self.display_size_label.config(text=str(display_size))
        self.zoom_label.config(text=f"{zoom:.1f}x")
        self.fps_label.config(text=str(fps))
        
        capture_size = int(display_size / zoom)
        
        if 'magnifier' not in self.config:
            self.config['magnifier'] = {}
        
        self.config['magnifier']['display_size'] = display_size
        self.config['magnifier']['capture_size'] = capture_size
        self.config['magnifier']['zoom'] = zoom
        self.config['magnifier']['target_fps'] = fps
        
        self.save_config()
        
        if self.magnifier_process:
            if hasattr(self, '_magnifier_restart_timer') and self._magnifier_restart_timer:
                self.root.after_cancel(self._magnifier_restart_timer)
            self._magnifier_restart_timer = self.root.after(500, self._restart_magnifier_debounced)
    
    def _restart_magnifier_debounced(self):
        """Перезапускает лупу после debounce."""
        self._magnifier_restart_timer = None
        if self.magnifier_process:
            was_visible = self.magnifier_visible
            self.stop_magnifier()
            self.start_magnifier()
            if was_visible:
                self.toggle_magnifier()
    
    def record_magnifier_hotkey(self):
        """Записывает хоткей для лупы."""
        if self.recording_magnifier:
            return
        
        if self.listener:
            self.listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        
        self.recording_magnifier = True
        self.magnifier_record_btn.config(state='disabled')
        self.magnifier_hotkey_var.set("Нажмите клавишу или кнопку мыши...")
        
        pressed_keys = set()
        main_key = [None]
        temp_mouse_listener = [None]
        temp_keyboard_listener = [None]
        
        def get_key_name(key):
            try:
                if hasattr(key, 'vk') and key.vk:
                    vk = key.vk
                    if 65 <= vk <= 90: return chr(vk).lower()
                    if 48 <= vk <= 57: return chr(vk)
                    if 112 <= vk <= 123: return f'f{vk - 111}'
                    if vk == 189: return '-'
                    if vk == 187: return '='
                    if vk in (220, 226): return '\\'
                if hasattr(key, 'char') and key.char:
                    return key.char.lower()
                if hasattr(key, 'name') and key.name:
                    return key.name.lower()
            except:
                pass
            return None
        
        def save_hotkey():
            hotkey_parts = []
            if 'ctrl' in pressed_keys:
                hotkey_parts.append('ctrl')
            if 'alt' in pressed_keys:
                hotkey_parts.append('alt')
            if main_key[0]:
                hotkey_parts.append(main_key[0])
            
            if hotkey_parts:
                if temp_keyboard_listener[0]:
                    temp_keyboard_listener[0].stop()
                if temp_mouse_listener[0]:
                    temp_mouse_listener[0].stop()
                
                hotkey_str = '+'.join(hotkey_parts)
                self.magnifier_hotkey_var.set(hotkey_str)
                self.config['hotkeys']['magnifier'] = hotkey_str
                self.save_config()
                self.setup_hotkeys()
                self.stop_magnifier_recording()
                return True
            return False
        
        def on_press(key):
            if not self.recording_magnifier:
                return False
            try:
                if hasattr(key, 'name') and key.name == 'esc':
                    if temp_keyboard_listener[0]:
                        temp_keyboard_listener[0].stop()
                    if temp_mouse_listener[0]:
                        temp_mouse_listener[0].stop()
                    self.magnifier_hotkey_var.set(self.config['hotkeys'].get('magnifier', 'f1'))
                    self.setup_hotkeys()
                    self.stop_magnifier_recording()
                    return False
                
                if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    pressed_keys.add('ctrl')
                elif key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                    pressed_keys.add('alt')
                else:
                    key_name = get_key_name(key)
                    if key_name and key_name not in ['ctrl', 'alt', 'shift']:
                        main_key[0] = key_name
            except:
                pass
        
        def on_release(key):
            if not self.recording_magnifier:
                return False
            try:
                is_ctrl = key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r
                is_alt = key == keyboard.Key.alt_l or key == keyboard.Key.alt_r
                
                should_save = False
                if not is_ctrl and not is_alt and main_key[0]:
                    should_save = True
                elif (is_ctrl or is_alt) and not main_key[0]:
                    should_save = True
                
                if should_save:
                    if save_hotkey():
                        return False
                
                if is_ctrl:
                    pressed_keys.discard('ctrl')
                elif is_alt:
                    pressed_keys.discard('alt')
            except:
                pass
        
        def on_click(x, y, button, pressed):
            if not self.recording_magnifier or not pressed:
                return
            try:
                if button == mouse.Button.left:
                    main_key[0] = 'mouse1'
                elif button == mouse.Button.right:
                    main_key[0] = 'mouse2'
                elif button == mouse.Button.middle:
                    main_key[0] = 'mouse3'
                elif hasattr(button, 'value') and button.value == 8:
                    main_key[0] = 'mouse4'
                elif hasattr(button, 'value') and button.value == 9:
                    main_key[0] = 'mouse5'
                
                if save_hotkey():
                    return False
            except:
                pass
        
        def on_scroll(x, y, dx, dy):
            if not self.recording_magnifier:
                return
            try:
                if dy > 0:
                    main_key[0] = 'scroll_up'
                elif dy < 0:
                    main_key[0] = 'scroll_down'
                
                if save_hotkey():
                    return False
            except:
                pass
        
        temp_keyboard_listener[0] = keyboard.Listener(on_press=on_press, on_release=on_release)
        temp_keyboard_listener[0].start()
        
        temp_mouse_listener[0] = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
        temp_mouse_listener[0].start()
    
    def stop_magnifier_recording(self):
        """Останавливает запись хоткея лупы."""
        self.recording_magnifier = False
        self.magnifier_record_btn.config(state='normal')

    def draw_preview(self, canvas, crosshair_type):
        cx, cy = 30, 30
        size, thickness, gap = 12, 2, 4
        color = 'black'
        
        if crosshair_type == 'dot':
            canvas.create_oval(cx - size//2, cy - size//2, cx + size//2, cy + size//2, fill=color, outline=color)
        elif crosshair_type == 'circle':
            canvas.create_oval(cx - size, cy - size, cx + size, cy + size, outline=color, width=thickness)
        elif crosshair_type == 'circle_dot':
            canvas.create_oval(cx - size, cy - size, cx + size, cy + size, outline=color, width=thickness)
            canvas.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=color, outline=color)
        elif crosshair_type == 'cross':
            canvas.create_line(cx, cy - gap - size, cx, cy - gap, fill=color, width=thickness)
            canvas.create_line(cx, cy + gap, cx, cy + gap + size, fill=color, width=thickness)
            canvas.create_line(cx - gap - size, cy, cx - gap, cy, fill=color, width=thickness)
            canvas.create_line(cx + gap, cy, cx + gap + size, cy, fill=color, width=thickness)
            canvas.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=color, outline=color)
        elif crosshair_type == 'chevron':
            canvas.create_line(cx, cy - size//2, cx - size, cy + size//2, fill=color, width=thickness)
            canvas.create_line(cx, cy - size//2, cx + size, cy + size//2, fill=color, width=thickness)
        elif crosshair_type == 'cross_no_dot':
            canvas.create_line(cx, cy - gap - size, cx, cy - gap, fill=color, width=thickness)
            canvas.create_line(cx, cy + gap, cx, cy + gap + size, fill=color, width=thickness)
            canvas.create_line(cx - gap - size, cy, cx - gap, cy, fill=color, width=thickness)
            canvas.create_line(cx + gap, cy, cx + gap + size, cy, fill=color, width=thickness)

    def update_previews(self):
        r, g, b = int(self.r_var.get()), int(self.g_var.get()), int(self.b_var.get())
        color = f'#{r:02x}{g:02x}{b:02x}'
        for canvas, crosshair_type in self.preview_canvases:
            canvas.delete('all')
            self.draw_preview_colored(canvas, crosshair_type, color)

    def draw_preview_colored(self, canvas, crosshair_type, color):
        cx, cy = 30, 30
        size, thickness, gap = 12, 2, 4
        
        if crosshair_type == 'dot':
            canvas.create_oval(cx - size//2, cy - size//2, cx + size//2, cy + size//2, fill=color, outline=color)
        elif crosshair_type == 'circle':
            canvas.create_oval(cx - size, cy - size, cx + size, cy + size, outline=color, width=thickness)
        elif crosshair_type == 'circle_dot':
            canvas.create_oval(cx - size, cy - size, cx + size, cy + size, outline=color, width=thickness)
            canvas.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=color, outline=color)
        elif crosshair_type == 'cross':
            canvas.create_line(cx, cy - gap - size, cx, cy - gap, fill=color, width=thickness)
            canvas.create_line(cx, cy + gap, cx, cy + gap + size, fill=color, width=thickness)
            canvas.create_line(cx - gap - size, cy, cx - gap, cy, fill=color, width=thickness)
            canvas.create_line(cx + gap, cy, cx + gap + size, cy, fill=color, width=thickness)
            canvas.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, fill=color, outline=color)
        elif crosshair_type == 'chevron':
            canvas.create_line(cx, cy - size//2, cx - size, cy + size//2, fill=color, width=thickness)
            canvas.create_line(cx, cy - size//2, cx + size, cy + size//2, fill=color, width=thickness)
        elif crosshair_type == 'cross_no_dot':
            canvas.create_line(cx, cy - gap - size, cx, cy - gap, fill=color, width=thickness)
            canvas.create_line(cx, cy + gap, cx, cy + gap + size, fill=color, width=thickness)
            canvas.create_line(cx - gap - size, cy, cx - gap, cy, fill=color, width=thickness)
            canvas.create_line(cx + gap, cy, cx + gap + size, cy, fill=color, width=thickness)

    def on_color_change(self):
        r = int(self.r_var.get())
        g = int(self.g_var.get())
        b = int(self.b_var.get())
        
        self.r_label.config(text=str(r))
        self.g_label.config(text=str(g))
        self.b_label.config(text=str(b))
        
        self.config['crosshair']['r'] = r
        self.config['crosshair']['g'] = g
        self.config['crosshair']['b'] = b
        self.update_previews()
        self.save_config()

    def on_slider_move(self):
        self.size_label.config(text=f"{self.size_var.get():.2f}")
        self.thickness_label.config(text=f"{self.thickness_var.get():.2f}")
        self.gap_label.config(text=f"{self.gap_var.get():.2f}")
        self.dot_size_label.config(text=f"{self.dot_size_var.get():.2f}")
        self.alpha_label.config(text=f"{self.alpha_var.get():.2f}")
        
        self.config['crosshair']['size'] = round(self.size_var.get(), 2)
        self.config['crosshair']['thickness'] = round(self.thickness_var.get(), 2)
        self.config['crosshair']['gap'] = round(self.gap_var.get(), 2)
        self.config['crosshair']['dot_size'] = round(self.dot_size_var.get(), 2)
        self.config['crosshair']['alpha'] = round(self.alpha_var.get(), 2)
        self.save_config()

    def select_crosshair_type(self, value):
        self.crosshair_type_var.set(value)
        self.on_type_change()

    def on_type_change(self):
        self.config['crosshair']['type'] = self.crosshair_type_var.get()
        self.update_slider_visibility()
        self.save_config()

    def update_slider_visibility(self):
        crosshair_type = self.crosshair_type_var.get()
        
        types_with_dot = ['cross', 'circle_dot']
        types_without_length = ['dot']
        types_with_circle = ['circle', 'circle_dot']
        types_without_gap = ['chevron', 'circle', 'circle_dot', 'dot']
        types_without_width = ['dot']
        
        if crosshair_type in types_without_width:
            self.width_label_widget.grid_remove()
            self.width_scale.grid_remove()
            self.thickness_label.grid_remove()
        else:
            self.width_label_widget.grid()
            self.width_scale.grid()
            self.thickness_label.grid()
        
        if crosshair_type in types_with_dot or crosshair_type == 'dot':
            self.dot_size_label_widget.grid()
            self.dot_size_scale.grid()
            self.dot_size_label.grid()
        else:
            self.dot_size_label_widget.grid_remove()
            self.dot_size_scale.grid_remove()
            self.dot_size_label.grid_remove()
        
        if crosshair_type in types_without_length:
            self.length_label_widget.grid_remove()
            self.length_scale.grid_remove()
            self.size_label.grid_remove()
        else:
            if crosshair_type in types_with_circle:
                self.length_label_widget.config(text="Размер окружности:")
                self.length_scale.config(from_=1, to=50)
            else:
                self.length_label_widget.config(text="Длина:")
                self.length_scale.config(from_=5, to=50)
            self.length_label_widget.grid()
            self.length_scale.grid()
            self.size_label.grid()
        
        if crosshair_type in types_without_gap:
            self.gap_label_widget.grid_remove()
            self.gap_scale.grid_remove()
            self.gap_label.grid_remove()
        else:
            self.gap_label_widget.grid()
            self.gap_scale.grid()
            self.gap_label.grid()

    def record_hotkey(self):
        if self.recording:
            return
        
        if self.listener:
            self.listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        
        self.recording = True
        self.record_btn.config(state='disabled')
        self.hotkey_var.set("Нажмите клавишу или кнопку мыши...")
        
        pressed_keys = set()
        main_key = [None]
        temp_mouse_listener_obj = [None]
        temp_keyboard_listener_obj = [None]
        
        def get_key_name(key):
            try:
                if hasattr(key, 'vk') and key.vk:
                    vk = key.vk
                    if 65 <= vk <= 90: return chr(vk).lower()
                    if 48 <= vk <= 57: return chr(vk)
                    if 112 <= vk <= 123: return f'f{vk - 111}'
                    if vk == 189: return '-'
                    if vk == 187: return '='
                    if vk in (220, 226): return '\\'
                if hasattr(key, 'char') and key.char:
                    return key.char.lower()
                if hasattr(key, 'name') and key.name:
                    return key.name.lower()
            except:
                pass
            return None
        
        def save_hotkey():
            hotkey_parts = []
            if 'ctrl' in pressed_keys:
                hotkey_parts.append('ctrl')
            if 'alt' in pressed_keys:
                hotkey_parts.append('alt')
            if main_key[0]:
                hotkey_parts.append(main_key[0])
            
            if hotkey_parts:
                if temp_keyboard_listener_obj[0]:
                    temp_keyboard_listener_obj[0].stop()
                if temp_mouse_listener_obj[0]:
                    temp_mouse_listener_obj[0].stop()
                
                hotkey_str = '+'.join(hotkey_parts)
                self.hotkey_var.set(hotkey_str)
                self.config['hotkeys']['toggle'] = hotkey_str
                self.save_config()
                self.setup_hotkeys()
                self.stop_recording()
                return True
            return False
        
        def on_press(key):
            if not self.recording:
                return False
            try:
                if hasattr(key, 'name') and key.name == 'esc':
                    if temp_keyboard_listener_obj[0]:
                        temp_keyboard_listener_obj[0].stop()
                    if temp_mouse_listener_obj[0]:
                        temp_mouse_listener_obj[0].stop()
                    self.hotkey_var.set(self.config['hotkeys']['toggle'])
                    self.setup_hotkeys()
                    self.stop_recording()
                    return False
                
                if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    pressed_keys.add('ctrl')
                elif key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                    pressed_keys.add('alt')
                else:
                    key_name = get_key_name(key)
                    if key_name and key_name not in ['ctrl', 'alt', 'shift']:
                        main_key[0] = key_name
            except:
                pass
        
        def on_release(key):
            if not self.recording:
                return False
            try:
                is_ctrl = key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r
                is_alt = key == keyboard.Key.alt_l or key == keyboard.Key.alt_r
                
                should_save = False
                if not is_ctrl and not is_alt and main_key[0]:
                    should_save = True
                elif (is_ctrl or is_alt) and not main_key[0]:
                    should_save = True
                
                if should_save:
                    if save_hotkey():
                        return False
                
                if is_ctrl:
                    pressed_keys.discard('ctrl')
                elif is_alt:
                    pressed_keys.discard('alt')
            except:
                pass
        
        def on_click(x, y, button, pressed):
            if not self.recording or not pressed:
                return
            try:
                if button == mouse.Button.left:
                    main_key[0] = 'mouse1'
                elif button == mouse.Button.right:
                    main_key[0] = 'mouse2'
                elif button == mouse.Button.middle:
                    main_key[0] = 'mouse3'
                elif hasattr(button, 'value') and button.value == 8:
                    main_key[0] = 'mouse4'
                elif hasattr(button, 'value') and button.value == 9:
                    main_key[0] = 'mouse5'
                
                if save_hotkey():
                    return False
            except:
                pass
        
        def on_scroll(x, y, dx, dy):
            if not self.recording:
                return
            try:
                if dy > 0:
                    main_key[0] = 'scroll_up'
                elif dy < 0:
                    main_key[0] = 'scroll_down'
                
                if save_hotkey():
                    return False
            except:
                pass
        
        temp_keyboard_listener_obj[0] = keyboard.Listener(on_press=on_press, on_release=on_release)
        temp_keyboard_listener_obj[0].start()
        
        temp_mouse_listener_obj[0] = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
        temp_mouse_listener_obj[0].start()

    def stop_recording(self):
        self.recording = False
        self.record_btn.config(state='normal')
        if self.hotkey_listener:
            self.hotkey_listener.stop()
            self.hotkey_listener = None

    def setup_hotkeys(self):
        toggle_str = self.config['hotkeys']['toggle']
        toggle_parts = toggle_str.split('+')
        
        magnifier_str = self.config['hotkeys'].get('magnifier', 'f1')
        magnifier_parts = magnifier_str.split('+')
        
        pressed_modifiers = set()
        
        def get_key_name(key):
            try:
                if hasattr(key, 'vk') and key.vk:
                    vk = key.vk
                    if 65 <= vk <= 90: return chr(vk).lower()
                    if 48 <= vk <= 57: return chr(vk)
                    if 112 <= vk <= 123: return f'f{vk - 111}'
                    if vk == 189: return '-'
                    if vk == 187: return '='
                    if vk in (220, 226): return '\\'
                if hasattr(key, 'char') and key.char:
                    return key.char.lower()
                if hasattr(key, 'name') and key.name:
                    return key.name.lower()
            except:
                pass
            return None
        
        def check_hotkey(key_name, hotkey_parts):
            required_modifiers = set()
            main_key = None
            for part in hotkey_parts:
                if part in ['ctrl', 'alt']:
                    required_modifiers.add(part)
                else:
                    main_key = part
            
            if not required_modifiers:
                return main_key == key_name
            else:
                return main_key == key_name and required_modifiers.issubset(pressed_modifiers)
        
        def on_press(key):
            try:
                if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    pressed_modifiers.add('ctrl')
                elif key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                    pressed_modifiers.add('alt')
                
                key_name = get_key_name(key)
                if key_name:
                    if check_hotkey(key_name, toggle_parts):
                        self.toggle_overlay()
                    elif check_hotkey(key_name, magnifier_parts):
                        self.toggle_magnifier()
            except:
                pass
        
        def on_release(key):
            try:
                if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    pressed_modifiers.discard('ctrl')
                elif key == keyboard.Key.alt_l or key == keyboard.Key.alt_r:
                    pressed_modifiers.discard('alt')
            except:
                pass
        
        def on_click(x, y, button, pressed):
            if not pressed:
                return
            try:
                mouse_name = None
                if button == mouse.Button.left:
                    mouse_name = 'mouse1'
                elif button == mouse.Button.right:
                    mouse_name = 'mouse2'
                elif button == mouse.Button.middle:
                    mouse_name = 'mouse3'
                elif hasattr(button, 'value') and button.value == 8:
                    mouse_name = 'mouse4'
                elif hasattr(button, 'value') and button.value == 9:
                    mouse_name = 'mouse5'
                
                if mouse_name:
                    if check_hotkey(mouse_name, toggle_parts):
                        self.toggle_overlay()
                    elif check_hotkey(mouse_name, magnifier_parts):
                        self.toggle_magnifier()
            except:
                pass
        
        def on_scroll(x, y, dx, dy):
            try:
                scroll_name = 'scroll_up' if dy > 0 else 'scroll_down'
                if check_hotkey(scroll_name, toggle_parts):
                    self.toggle_overlay()
                elif check_hotkey(scroll_name, magnifier_parts):
                    self.toggle_magnifier()
            except:
                pass
        
        if self.listener:
            self.listener.stop()
        self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.listener.start()
        
        if hasattr(self, 'mouse_listener') and self.mouse_listener:
            self.mouse_listener.stop()
        self.mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
        self.mouse_listener.start()

    def start_overlay(self):
        self.overlay = CrosshairOverlay(self.config)
        threading.Thread(target=self.overlay.run, daemon=True).start()

    def start_magnifier(self):
        """Запускает процесс лупы (скрытым)."""
        import subprocess
        self.magnifier_visible = False
        try:
            self.magnifier_process = subprocess.Popen(
                [sys.executable, __file__, '--magnifier-ipc'],
                stdin=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            self._update_magnifier_ui()
        except Exception as e:
            print(f"Ошибка запуска лупы: {e}")
            self.magnifier_process = None
            self._update_magnifier_ui()
    
    def stop_magnifier(self):
        """Останавливает процесс лупы."""
        if self.magnifier_process:
            try:
                self.magnifier_process.stdin.write(b'quit\n')
                self.magnifier_process.stdin.flush()
                self.magnifier_process.wait(timeout=1)
            except:
                try:
                    self.magnifier_process.terminate()
                except:
                    pass
            self.magnifier_process = None
            self.magnifier_visible = False
            self._update_magnifier_ui()
    
    def _update_magnifier_ui(self):
        """Обновляет UI в зависимости от статуса процесса."""
        if self.magnifier_process:
            if self.magnifier_visible:
                self.magnifier_status_var.set("Запущена (видима)")
            else:
                self.magnifier_status_var.set("Запущена (скрыта)")
        else:
            self.magnifier_status_var.set("Не запущена")

    def update_overlay(self):
        if self.overlay:
            self.overlay.update_crosshair(self.config)

    def toggle_overlay(self):
        if self.overlay:
            self.overlay.toggle_visibility()

    def toggle_magnifier(self):
        """Переключает видимость лупы через IPC (хоткей)."""
        if not self.magnifier_process:
            self.start_magnifier()
            if not self.magnifier_process:
                return
        
        try:
            if self.magnifier_visible:
                self.magnifier_process.stdin.write(b'hide\n')
                self.magnifier_process.stdin.flush()
                self.magnifier_visible = False
            else:
                self.magnifier_process.stdin.write(b'show\n')
                self.magnifier_process.stdin.flush()
                self.magnifier_visible = True
            self._update_magnifier_ui()
        except Exception as e:
            print(f"Ошибка IPC: {e}")
            self.magnifier_process = None
            self._update_magnifier_ui()

    def on_closing(self):
        if self.listener:
            self.listener.stop()
        if hasattr(self, 'mouse_listener') and self.mouse_listener:
            self.mouse_listener.stop()
        if self.overlay:
            self.overlay.close()
        self.stop_magnifier()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


class CrosshairOverlay:
    def __init__(self, config):
        self.config = config
        self.visible = True
        self.root = None
        self.canvas = None
        self.window_size = 200

    def run(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.transparent_color = '#ff00ff'
        self.root.attributes('-transparentcolor', self.transparent_color)
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - self.window_size) // 2
        y = (screen_height - self.window_size) // 2
        
        self.root.geometry(f'{self.window_size}x{self.window_size}+{x}+{y}')
        self.root.config(bg=self.transparent_color)
        
        self.canvas = tk.Canvas(self.root, width=self.window_size, height=self.window_size,
                               bg=self.transparent_color, highlightthickness=0)
        self.canvas.pack()
        
        self.set_click_through()
        self.draw_crosshair()
        self.root.mainloop()
    
    def _force_topmost(self):
        """Принудительно устанавливает окно поверх всех через WinAPI."""
        try:
            hwnd = int(self.root.wm_frame(), 16)
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, 
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except:
            pass

    def set_click_through(self):
        try:
            self.root.update_idletasks()
            hwnd = int(self.root.wm_frame(), 16)
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0020)
        except Exception as e:
            print(f"Не удалось сделать окно click-through: {e}")

    def update_crosshair(self, config):
        self.config = config
        if self.canvas:
            self.root.after(0, self._update_and_lift)
    
    def _update_and_lift(self):
        """Обновляет прицел и поднимает окно наверх."""
        self.draw_crosshair()
        self._force_topmost()

    def toggle_visibility(self):
        if not self.root:
            return
        if self.visible:
            self.root.attributes('-alpha', 0.0)
            self.visible = False
        else:
            alpha = self.config['crosshair'].get('alpha', 255) / 255.0
            self.root.attributes('-alpha', alpha)
            self.visible = True

    def draw_crosshair(self):
        if not self.canvas:
            return
        self.canvas.delete('all')
        
        center = self.window_size // 2
        cfg = self.config['crosshair']
        
        if self.visible:
            alpha = cfg.get('alpha', 255) / 255.0
            self.root.attributes('-alpha', alpha)
        
        crosshair_type = cfg.get('type', 'cross')
        r, g, b = int(cfg.get('r', 0)), int(cfg.get('g', 255)), int(cfg.get('b', 0))
        color = f'#{r:02x}{g:02x}{b:02x}'
        
        size = round(cfg.get('size', 15), 1)
        thickness = max(0.5, round(cfg.get('thickness', 2), 1))
        gap = round(cfg.get('gap', 5), 1)
        dot_size = round(cfg.get('dot_size', 2), 1)
        
        if crosshair_type == 'dot':
            self.canvas.create_oval(center - dot_size, center - dot_size,
                                   center + dot_size, center + dot_size,
                                   fill=color, outline=color)
        elif crosshair_type == 'circle':
            radius = max(thickness, size)
            self.canvas.create_oval(center - radius, center - radius,
                                   center + radius, center + radius,
                                   outline=color, width=thickness)
        elif crosshair_type == 'circle_dot':
            radius = max(thickness, size)
            self.canvas.create_oval(center - radius, center - radius,
                                   center + radius, center + radius,
                                   outline=color, width=thickness)
            self.canvas.create_oval(center - dot_size, center - dot_size,
                                   center + dot_size, center + dot_size,
                                   fill=color, outline=color)
        elif crosshair_type == 'cross':
            self.canvas.create_line(center, center - gap - size, center, center - gap, fill=color, width=thickness)
            self.canvas.create_line(center, center + gap, center, center + gap + size, fill=color, width=thickness)
            self.canvas.create_line(center - gap - size, center, center - gap, center, fill=color, width=thickness)
            self.canvas.create_line(center + gap, center, center + gap + size, center, fill=color, width=thickness)
            self.canvas.create_oval(center - dot_size, center - dot_size,
                                   center + dot_size, center + dot_size,
                                   fill=color, outline=color)
        elif crosshair_type == 'chevron':
            self.canvas.create_line(center - size, center + size, center, center, fill=color, width=thickness)
            self.canvas.create_line(center, center, center + size, center + size, fill=color, width=thickness)
        elif crosshair_type == 'cross_no_dot':
            self.canvas.create_line(center, center - gap - size, center, center - gap, fill=color, width=thickness)
            self.canvas.create_line(center, center + gap, center, center + gap + size, fill=color, width=thickness)
            self.canvas.create_line(center - gap - size, center, center - gap, center, fill=color, width=thickness)
            self.canvas.create_line(center + gap, center, center + gap + size, center, fill=color, width=thickness)

    def close(self):
        if self.root:
            self.root.quit()


if __name__ == '__main__':
    app = CrosshairApp()
    app.run()
