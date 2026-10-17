from PyQt6.QtCore import QObject, QThread, pyqtSignal

from PyQt6.QtCore import QMetaObject, Q_ARG


class AgentWorker(QObject):
    response_ready = pyqtSignal(str)  # "ответ готов"
    error_occurred = pyqtSignal(str)  # "произошла ошибка"

    def __init__(self, agent):
        super().__init__()
        self.agent = agent

    def process_message(self, text):
        """Этот метод выполнится в фоновом потоке"""
        try:
            # Здесь вызываем медленный метод агента
            response = self.agent.send_msg(text)
            self.response_ready.emit(response)
        except Exception as e:
            self.error_occurred.emit(f"Ошибка: {str(e)}")