"""QThread workers so network checks and installs never block the UI thread."""
from PySide6.QtCore import QThread, Signal

from .models import ApplyChange, BrowserInfo, Dependency, Env
from .manager import DependencyManager


class CheckWorker(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(list)   # list[Dependency]
    failed = Signal(str)
    browser_checked = Signal(object)  # BrowserInfo

    def __init__(self, manager: DependencyManager, deps: list[Dependency], env: Env, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._deps = deps
        self._env = env

    def run(self):
        try:
            result = self._manager.check(self._deps, self._env, self.progress.emit)
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
        try:
            self.browser_checked.emit(self._manager.browser_status())
        except Exception as e:  # noqa: BLE001
            self.browser_checked.emit(BrowserInfo(error=str(e)))


class ApplyWorker(QThread):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, manager: DependencyManager, changes: list[ApplyChange], env: Env,
                 install_browser: bool, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._changes = changes
        self._env = env
        self._install_browser = install_browser

    def run(self):
        try:
            result = self._manager.apply(
                self._changes, self._env,
                on_log=self.log.emit,
                on_progress=self.progress.emit,
                install_browser=self._install_browser,
            )
            self.finished_ok.emit(result)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class BrowserWorker(QThread):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, manager: DependencyManager, parent=None):
        super().__init__(parent)
        self._manager = manager

    def run(self):
        try:
            ok, message = self._manager.update_browser(
                on_log=self.log.emit,
                on_progress=self.progress.emit,
            )
            if ok:
                self.finished_ok.emit(message)
            else:
                self.failed.emit(message)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
