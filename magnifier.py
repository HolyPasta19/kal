"""
Модуль экранной лупы для overlay-прицела.
PyQt5 реализация с захватом через mss и масштабированием через OpenCV.
"""

import sys
import time
import numpy as np
import mss
import cv2
import ctypes

# DPI awareness для корректных координат на Windows
if sys.platform == 'win32':
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from PyQt5 import QtWidgets, QtCore, QtGui


class CaptureThread(QtCore.QThread):
    """Поток захвата и обработки изображения."""
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
        
        # Проверяем CUDA
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
            
            # Учитываем смещение монитора (для мультимониторных конфигураций)
            mon_left = mon['left']
            mon_top = mon['top']
            screen_width = mon['width']
            screen_height = mon['height']
            
            # Центр монитора — для чётных размеров экрана это между пикселями
            # Захватываем так чтобы центр экрана был точно в центре захваченной области
            half = self.capture_size // 2
            
            # left = center - half, но center = width/2
            # Для width=2560, center=1280, half=100: left=1180, right=1380 (200 px)
            # Центр захвата = 1180 + 100 = 1280 ✓
            capture_left = mon_left + (screen_width - self.capture_size) // 2
            capture_top = mon_top + (screen_height - self.capture_size) // 2

            capture_region = {
                'left': capture_left,
                'top': capture_top,
                'width': self.capture_size,
                'height': self.capture_size
            }
            
            # Отладка в файл (дописываем)
            with open('magnifier_debug.txt', 'a') as f:
                f.write("\n--- CaptureThread ---\n")
                f.write(f"Monitor: {mon_left},{mon_top} {screen_width}x{screen_height}\n")
                f.write(f"Capture size: {self.capture_size}\n")
                f.write(f"Capture region: {capture_region}\n")
                f.write(f"Capture center: {capture_left + self.capture_size//2}, {capture_top + self.capture_size//2}\n")
                f.write(f"Screen center: {mon_left + screen_width//2}, {mon_top + screen_height//2}\n")
            
            interp = self.INTERPOLATION_MODES.get(self.interpolation, cv2.INTER_LINEAR)
            
            while self.running:
                t0 = time.perf_counter()
                
                # Захват
                screenshot = sct.grab(capture_region)
                img = np.array(screenshot)
                
                # BGRA -> BGR
                if img.shape[2] == 4:
                    img = img[:, :, :3]
                
                # Масштабирование
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
                
                # BGR -> RGB для QImage
                rgb = cv2.cvtColor(zoomed, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                bytes_per_line = ch * w
                
                # Создаём QImage (копируем данные чтобы избежать проблем с памятью)
                qimg = QtGui.QImage(rgb.data, w, h, bytes_per_line, QtGui.QImage.Format_RGB888).copy()
                self.frame_ready.emit(qimg)
                
                # FPS контроль
                elapsed = time.perf_counter() - t0
                target_time = 1.0 / self.current_fps
                if elapsed < target_time:
                    time.sleep(target_time - elapsed)
                else:
                    self._adapt_fps(elapsed)

    def _adapt_fps(self, frame_time):
        """Адаптирует FPS если не успеваем."""
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
    """Окно лупы — frameless, topmost, click-through."""
    
    def __init__(self, config=None):
        super().__init__()
        self.config = config or {}
        mag_config = self.config.get('magnifier', {})
        
        self.display_size = mag_config.get('display_size', 300)
        self.offset_x = mag_config.get('offset_x', 0)
        self.offset_y = mag_config.get('offset_y', 0)
        
        # Настройки окна
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool |
            QtCore.Qt.WindowTransparentForInput  # Click-through на уровне Qt
        )
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setFixedSize(self.display_size, self.display_size)
        self.setAlignment(QtCore.Qt.AlignCenter)
        
        # Без рамки, чёрный фон
        self.setStyleSheet("background-color: black;")
        
        # Поток захвата
        self.capture_thread = CaptureThread(config)
        self.capture_thread.frame_ready.connect(self.on_frame)
        
        # Позиционирование
        self._position_window()
        
        self.hide()
        
        # Click-through применим после первого показа
        self._click_through_applied = False

    def _position_window(self):
        """Позиционирует окно относительно центра экрана (синхронизировано с mss)."""
        import mss
        
        # Очищаем debug файл
        with open('magnifier_debug.txt', 'w') as f:
            f.write("--- Window Position ---\n")
        
        with mss.mss() as sct:
            mag_config = self.config.get('magnifier', {})
            monitor_index = mag_config.get('monitor_index', 1)
            try:
                mon = sct.monitors[monitor_index]
            except IndexError:
                mon = sct.monitors[1]
            
            # Позиционируем окно так чтобы его центр был ровно в центре экрана
            screen_center_x = mon['left'] + mon['width'] // 2
            screen_center_y = mon['top'] + mon['height'] // 2
            x = screen_center_x - self.display_size // 2 + self.offset_x
            y = screen_center_y - self.display_size // 2 + self.offset_y
            
            # Используем setGeometry для точного позиционирования
            self.setGeometry(x, y, self.display_size, self.display_size)
            
            # Отладка
            with open('magnifier_debug.txt', 'a') as f:
                f.write(f"Monitor: {mon['left']},{mon['top']} {mon['width']}x{mon['height']}\n")
                f.write(f"Window display_size: {self.display_size}\n")
                f.write(f"Window target position: {x}, {y}\n")
                f.write(f"Window target center: {x + self.display_size//2}, {y + self.display_size//2}\n")
                # Реальная позиция после setGeometry
                geom = self.geometry()
                f.write(f"Window actual geometry: {geom.x()}, {geom.y()}, {geom.width()}x{geom.height()}\n")
                f.write(f"Window actual center: {geom.x() + geom.width()//2}, {geom.y() + geom.height()//2}\n")

    def _set_click_through(self):
        """Делает окно прозрачным для кликов и исключает из захвата экрана (Windows)."""
        print("[MagnifierWindow] _set_click_through called")
        if sys.platform == 'win32':
            import ctypes
            
            # Сохраняем hwnd для повторного применения
            self._hwnd = int(self.effectiveWinId())
            self._apply_click_through_style()
            self._exclude_from_capture()
            
            # Повторно применяем через 500мс (Qt может перезаписать)
            QtCore.QTimer.singleShot(500, self._apply_click_through_style)
    
    def _exclude_from_capture(self):
        """Исключает окно из захвата экрана через SetWindowDisplayAffinity."""
        if not hasattr(self, '_hwnd'):
            return
        
        import ctypes
        hwnd = self._hwnd
        
        # WDA_EXCLUDEFROMCAPTURE = 0x00000011 (Windows 10 2004+)
        # WDA_MONITOR = 0x00000001 (fallback для старых версий)
        WDA_EXCLUDEFROMCAPTURE = 0x00000011
        WDA_MONITOR = 0x00000001
        
        # Пробуем сначала WDA_EXCLUDEFROMCAPTURE
        result = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        print(f"[exclude_from_capture] SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) result={result}")
        
        if not result:
            # Fallback на WDA_MONITOR
            result = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)
            print(f"[exclude_from_capture] SetWindowDisplayAffinity(WDA_MONITOR) result={result}")
    
    def _apply_click_through_style(self):
        """Применяет WS_EX_TRANSPARENT стиль."""
        if not hasattr(self, '_hwnd'):
            return
            
        import ctypes
        hwnd = self._hwnd
        
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        LWA_ALPHA = 0x02
        
        # Читаем текущий стиль
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        print(f"[click_through] hwnd={hwnd}, current style={hex(style)}")
        
        # Если WS_EX_TRANSPARENT уже есть — ничего не делаем
        if style & WS_EX_TRANSPARENT:
            print("[click_through] WS_EX_TRANSPARENT already set")
            return
        
        # Добавляем флаги
        new_style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        
        # Устанавливаем непрозрачность
        ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
        
        # Обновляем окно
        SWP_FRAMECHANGED = 0x0020
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
        
        # Проверяем
        check = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        print(f"[click_through] after apply: style={hex(check)}, has_transparent={bool(check & WS_EX_TRANSPARENT)}")

    def on_frame(self, qimg: QtGui.QImage):
        """Обновляет изображение."""
        if not qimg.isNull():
            pix = QtGui.QPixmap.fromImage(qimg)
            # Изображение уже нужного размера после cv2.resize, просто ставим
            self.setPixmap(pix)

    @QtCore.pyqtSlot()
    def toggle(self):
        """Переключает видимость."""
        if self.isVisible():
            self.capture_thread.stop()
            self.hide()
        else:
            self.capture_thread.start()
            self.show()
            self.raise_()

    @QtCore.pyqtSlot()
    def show_magnifier(self):
        """Показывает лупу."""
        if not self.isVisible():
            if not self.capture_thread.isRunning():
                self.capture_thread.start()
            self.show()
            self.raise_()
            # Применяем click-through после показа окна
            if not self._click_through_applied:
                QtCore.QTimer.singleShot(100, self._set_click_through)
                self._click_through_applied = True

    @QtCore.pyqtSlot()
    def hide_magnifier(self):
        """Скрывает лупу."""
        if self.isVisible():
            self.capture_thread.stop()
            self.hide()

    def close(self):
        """Закрывает и освобождает ресурсы."""
        self.capture_thread.stop()
        super().close()


class MagnifierOverlay:
    """
    Обёртка для совместимости с существующим кодом.
    Можно использовать как раньше: magnifier.toggle_visibility(), magnifier.close()
    """
    
    def __init__(self, config=None, master=None):
        self.config = config or {}
        self.app = None
        self.window = None
        self._app_created = False

    def create_window(self, parent=None):
        """Создаёт окно лупы."""
        if self.window is None:
            self.window = MagnifierWindow(self.config)

    def toggle_visibility(self):
        """Переключает видимость."""
        if self.window:
            self.window.toggle()

    def show(self):
        """Показывает лупу."""
        if self.window:
            self.window.show_magnifier()

    def hide(self):
        """Скрывает лупу."""
        if self.window:
            self.window.hide_magnifier()

    def close(self):
        """Закрывает лупу."""
        if self.window:
            self.window.close()
            self.window = None
        if self.app and self._app_created:
            self.app.quit()

    def run(self):
        """Запуск в standalone режиме."""
        self.app = QtWidgets.QApplication.instance()
        if self.app is None:
            self.app = QtWidgets.QApplication(sys.argv)
            self._app_created = True
        
        self.create_window()
        self.window.show_magnifier()
        
        if self._app_created:
            self.app.exec_()

def load_config():
    """Загружает конфиг из файла."""
    import json
    default_config = {
        'magnifier': {
            'capture_size': 150,
            'display_size': 300,
            'zoom': 2.0,
            'target_fps': 60,
            'interpolation': 'linear',
            'offset_x': 0,
            'offset_y': 0,
            'border_color': '#00ff00',
            'border_width': 2,
            'use_cuda': False,
            'monitor_index': 1
        }
    }
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            file_config = json.load(f)
            # Мержим с дефолтами
            if 'magnifier' in file_config:
                default_config['magnifier'].update(file_config['magnifier'])
            return default_config
    except:
        return default_config


if __name__ == '__main__':
    import argparse
    import threading
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-hotkey', action='store_true', help='Запуск без хоткеев')
    parser.add_argument('--ipc', action='store_true', help='IPC режим (команды через stdin)')
    args = parser.parse_args()
    
    config = load_config()
    
    app = QtWidgets.QApplication(sys.argv)
    magnifier = MagnifierWindow(config)
    
    if args.ipc:
        # IPC режим — читаем команды из stdin
        def ipc_reader():
            while True:
                try:
                    line = sys.stdin.readline().strip()
                    if line == 'show':
                        QtCore.QMetaObject.invokeMethod(
                            magnifier, 'show_magnifier',
                            QtCore.Qt.QueuedConnection
                        )
                    elif line == 'hide':
                        QtCore.QMetaObject.invokeMethod(
                            magnifier, 'hide_magnifier',
                            QtCore.Qt.QueuedConnection
                        )
                    elif line == 'quit' or not line:
                        app.quit()
                        break
                except:
                    break
        
        ipc_thread = threading.Thread(target=ipc_reader, daemon=True)
        ipc_thread.start()
        
        sys.exit(app.exec_())
    
    elif args.no_hotkey:
        # Режим без хоткеев — просто показываем лупу
        magnifier.show_magnifier()
        sys.exit(app.exec_())
    
    else:
        # Режим с хоткеями для standalone запуска
        from pynput import keyboard
        
        def on_press(key):
            try:
                key_char = getattr(key, 'char', None)
                if key_char == '-':
                    QtCore.QMetaObject.invokeMethod(
                        magnifier, 'toggle',
                        QtCore.Qt.QueuedConnection
                    )
                elif hasattr(key, 'name') and key.name == 'esc':
                    magnifier.close()
                    app.quit()
                    return False
            except Exception as e:
                print(f"Error: {e}")

        listener = keyboard.Listener(on_press=on_press)
        listener.start()

        print("Лупа запущена. Минус (-) - показать/скрыть, ESC - выход")
        magnifier.show_magnifier()
        
        sys.exit(app.exec_())
