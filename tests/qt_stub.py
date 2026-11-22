"""Минимальная «заглушка» PySide6 — только чтобы проверять Python-логику интерфейса там, где Qt не установлен.

Не рисует и не показывает окон. Сигналы работают по-настоящему (синхронно), остальные методы Qt
принимают любые аргументы. Если PySide6 установлен, тесты используют его, а не эту заглушку.
"""
from __future__ import annotations

import sys
import types


class _Any:
    """Принимает любые вызовы, арифметику и сравнения."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pass

    def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return _Any()

    def __getattr__(self, name: str):
        # методы Qt — lowerCamelCase без подчёркиваний; опечатки в «своих» атрибутах (с «_») не маскируем
        # Члены перечислений Qt (WA_Foo, AlignLeft) начинаются с заглавной — их отдаём всегда.
        if name.startswith("_") or (name[:1].islower() and "_" in name.replace("raise_", "")):
            raise AttributeError(name)
        return _Any()

    def __or__(self, other):  # флаги Qt
        return _Any()

    __ror__ = __and__ = __rand__ = __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __or__
    __truediv__ = __rtruediv__ = __floordiv__ = __rfloordiv__ = __neg__ = __or__

    def __lt__(self, other) -> bool:
        return False

    __le__ = __gt__ = __ge__ = __lt__

    def __int__(self) -> int:
        return 0

    __index__ = __int__

    def __float__(self) -> float:
        return 0.0

    def __bool__(self) -> bool:
        return True

    def __iter__(self):
        return iter(())

    def __len__(self) -> int:
        return 0

    def __hash__(self) -> int:
        return id(self)


class _Meta(type):
    def __getattr__(cls, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Any()


class _Base(_Any, metaclass=_Meta):
    pass


class _BoundSignal:
    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._slots.append(slot)

    def disconnect(self, *args) -> None:  # noqa: ANN002
        pass

    def emit(self, *args) -> None:  # noqa: ANN002
        for slot in list(self._slots):
            try:
                slot(*args)
            except TypeError:
                slot()   # Qt позволяет слотам принимать меньше аргументов, чем даёт сигнал


class Signal:
    def __init__(self, *types_) -> None:  # noqa: ANN002
        self._name = ""

    def __set_name__(self, owner, name: str) -> None:
        self._name = name

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        store = obj.__dict__.setdefault("_signals", {})
        return store.setdefault(self._name, _BoundSignal())


def _make_module(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    cache: dict[str, object] = {}

    def getattr_(attr: str):
        if attr.startswith("__"):
            raise AttributeError(attr)
        if attr == "Signal":
            return Signal
        if attr in ("Slot", "Property"):
            return lambda *a, **k: (lambda f: f)
        if attr not in cache:
            cache[attr] = type(attr, (_Base,), {}) if attr[:1] == "Q" else _Any()
        return cache[attr]

    module.__getattr__ = getattr_   # type: ignore[attr-defined]
    return module


def real_qt_available() -> bool:
    """Есть ли настоящий PySide6. Безопасно вызывать и после install() (заглушка не считается)."""
    import importlib.util
    loaded = sys.modules.get("PySide6")
    if loaded is not None:
        return not getattr(loaded, "NOVA_QT_STUB", False)
    return importlib.util.find_spec("PySide6") is not None


def install() -> None:
    """Подменяет PySide6 заглушкой (если настоящий PySide6 не найден)."""
    root = _make_module("PySide6")
    root.NOVA_QT_STUB = True   # type: ignore[attr-defined]
    root.__path__ = []   # type: ignore[attr-defined]
    sys.modules["PySide6"] = root
    for sub in ("QtCore", "QtGui", "QtWidgets", "QtNetwork"):
        full = f"PySide6.{sub}"
        module = _make_module(full)
        sys.modules[full] = module
        setattr(root, sub, module)
