"""Состояние чата: JSON-файл, который читают и пишут только под блокировкой."""
from __future__ import annotations

import fcntl
import json
import threading
from contextlib import contextmanager
from pathlib import Path


class StateError(Exception):
    pass


class StateFile:
    """Файл состояния одного чата.

    Писателей бывает несколько процессов, поэтому операция идёт целиком под блокировкой:
    прочитал — сходил в сеть — записал. Замок — отдельный файл: запись подменяет файл
    состояния целиком, и замок на прежний inode следующего писателя не удержал бы.
    Внутри процесса замок повторно входимый: обработчик нажатия зовёт операции чата сам.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._local = threading.local()
        self._guard = threading.RLock()

    @contextmanager
    def locked(self):
        with self._guard:
            depth = getattr(self._local, "depth", 0)
            if depth:
                self._local.depth = depth + 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock_path.open("w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                self._local.depth = 1
                try:
                    yield
                finally:
                    self._local.depth = 0
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"{self.path}: состояние чата испорчено ({exc}); поправь файл "
                             "или удали его — бот начнёт с новой страницы") from exc

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
