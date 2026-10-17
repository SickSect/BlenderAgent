import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QTextEdit, QLineEdit, QPushButton, QLabel
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QThread, QMetaObject, Q_ARG
from PyQt6.QtGui import QFont

from gui.agent_starter import AgentConnector
from gui.style.chat_msg_style import get_client_chat_style, get_agent_chat_style
from gui.style.style import get_theme
from gui.worker import AgentWorker


# Если запускаешь напрямую как скрипт, возможно придется использовать: from style import get_theme

class AgentWindowChat(QMainWindow):
    def __init__(self, agent: AgentConnector):
        super().__init__()

        self.agent = agent

        # --- Настройка потоков ---
        self.thread = QThread()
        self.worker = AgentWorker(agent)
        self.worker.moveToThread(self.thread)

        # Запускаем поток
        self.thread.start()

        # Подключаем сигналы
        self.worker.response_ready.connect(self.on_agent_response)
        self.worker.error_occurred.connect(self.on_agent_error)

        # --- Настройка окна ---
        self.setWindowTitle("AI Code Agent")
        self.setGeometry(100, 100, 500, 500)

        # --- Применение стилей ---
        # Берем строку CSS из файла style.py и применяем ко всему окну
        self.setStyleSheet(get_theme("dark"))

        # --- Центральный виджет и лейаут ---
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        central_widget.setLayout(layout)

        # --- Область чата ---
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        self.chat_area.setPlaceholderText("Ожидание задачи...")
        layout.addWidget(self.chat_area, stretch=1)

        # --- Поле ввода ---
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Введите задачу и нажмите Enter...")
        layout.addWidget(self.input_field)

        # --- Кнопка отправки ---
        self.send_button = QPushButton("Отправить")
        layout.addWidget(self.send_button)

        self.chat_area.setObjectName("chatArea")
        self.input_field.setObjectName("inputField")
        self.send_button.setObjectName("sendButton")

        # --- Подключение событий ---
        self.input_field.returnPressed.connect(self.send_command)
        self.send_button.clicked.connect(self.send_command)

        # Приветственное сообщение
        self.append_to_chat("<b>Система:</b> Интерфейс готов к работе.", is_system=True)

    def send_command(self):
        text = self.input_field.text().strip()
        if not text:
            return

        # Сообщение пользователя - справа
        self.append_to_chat(get_client_chat_style(text))
        self.input_field.clear()
        # Показываем, что агент "печатает"
        self.append_to_chat("<i>Агент печатает...</i>", is_system=True)

        # Отключаем кнопку и поле ввода, пока агент думает
        self.send_button.setEnabled(False)
        self.input_field.setEnabled(False)

        # Вызываем метод в фоновом потоке
        QMetaObject.invokeMethod(
            self.worker,
            "process_message",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, text)
        )

    def  on_agent_response(self, response):
        self.send_button.setEnabled(True)
        self.input_field.setEnabled(True)

        # Ответ агента - слева
        self.append_to_chat(get_agent_chat_style(response))

        # Ставим фокус обратно в поле ввода
        self.input_field.setFocus()

    def on_agent_error(self, error_msg):
        # Включаем обратно кнопку и поле
        self.send_button.setEnabled(True)
        self.input_field.setEnabled(True)

        # Показываем ошибку
        self.append_to_chat(f"<span style='color: red;'>{error_msg}</span>", is_system=True)
        self.input_field.setFocus()

    def closeEvent(self, event):
        """Важно! Останавливаем поток при закрытии окна"""
        self.thread.quit()
        self.thread.wait()
        super().closeEvent(event)

    def append_to_chat(self, message, is_system=False):
        # Создаем виджет для сообщения с анимацией
        msg_widget = QLabel(message)
        msg_widget.setTextFormat(Qt.TextFormat.RichText)
        msg_widget.setWordWrap(True)
        msg_widget.setStyleSheet("""
            QLabel {
                background-color: transparent;
                padding: 8px 12px;
                margin: 4px 0px;
                border-radius: 8px;
            }
        """)

        # Добавляем в чат
        self.chat_area.append(message)

        # Анимация появления (применяется к последнему добавленному блоку)
        # Для простоты используем стандартный scroll
        scrollbar = self.chat_area.verticalScrollBar()

        # Анимируем прокрутку вниз
        animation = QPropertyAnimation(scrollbar, b"value")
        animation.setDuration(300)
        animation.setStartValue(scrollbar.value())
        animation.setEndValue(scrollbar.maximum())
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.start()

        # Сохраняем анимацию чтобы не удалилась
        if not hasattr(self, '_animations'):
            self._animations = []
        self._animations.append(animation)
