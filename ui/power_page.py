"""Страница «Электропитание»: схемы питания Windows — переключить, добавить, переименовать, удалить, настроить."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFrame, QHBoxLayout, QInputDialog, QLineEdit, QMenu, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app_context import AppContext
from system.power import BALANCED, HIGH_PERFORMANCE, POWER_SAVER, ULTIMATE, PowerError, PowerPlan
from ui.item_dialogs import confirm
from ui.widgets import Card, EmptyState, HLine, Page, clear_layout, make_label, setting_row
from ui.workers import AsyncRunner

KIND_TITLES = {
    "ultimate": "Максимальная производительность", "high": "Высокая производительность",
    "balanced": "Сбалансированная", "saver": "Экономия энергии", "custom": "Своя схема",
}
# схемы, которые Windows умеет создавать по идентификатору, даже если их нет в списке
BUILTIN_BASES = (
    (ULTIMATE, "Максимальная производительность (встроенная в Windows)", "NOVA — Максимальная"),
    (HIGH_PERFORMANCE, "Высокая производительность (встроенная)", "NOVA — Высокая"),
    (BALANCED, "Сбалансированная (встроенная)", "NOVA — Сбалансированная"),
    (POWER_SAVER, "Экономия энергии (встроенная)", "NOVA — Экономия"),
)
AUTO_PLAN = ""


def _error_text(error: Exception) -> str:
    return str(error) if isinstance(error, PowerError) else "Не удалось выполнить команду Windows (подробности в nova.log)."


class NewPlanDialog(QDialog):
    """Добавление схемы: копия выбранной схемы под новым названием."""

    def __init__(self, plans: list[PowerPlan], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Добавить схему питания")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(make_label("Добавить схему питания", "h2"))
        lay.addWidget(make_label("Будет создана копия выбранной схемы — её параметры потом можно изменить на этой странице.",
                                 "caption", wrap=True))
        lay.addWidget(make_label("На основе", "h3"))
        self.base = QComboBox()
        listed = {p.guid for p in plans}
        for plan in plans:
            self.base.addItem(f"{plan.name}", (plan.guid, f"{plan.name} — копия"))
        for guid, title, suggestion in BUILTIN_BASES:
            if guid not in listed:
                self.base.addItem(title, (guid, suggestion))
        self.base.currentIndexChanged.connect(self._suggest)
        lay.addWidget(self.base)
        lay.addWidget(make_label("Название", "h3"))
        self.name = QLineEdit()
        lay.addWidget(self.name)
        self.error = make_label("", "warn", wrap=True)
        self.error.hide()
        lay.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Добавить")
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self._suggest()

    def _suggest(self) -> None:
        data = self.base.currentData()
        if isinstance(data, (tuple, list)) and len(data) > 1:
            self.name.setText(str(data[1]))

    def _accept(self) -> None:
        if not self.name.text().strip():
            self.error.setText("Введите название схемы.")
            self.error.show()
            return
        self.accept()

    def result_values(self) -> tuple[str, str]:
        """(название, GUID схемы-основы)"""
        data = self.base.currentData()
        return self.name.text().strip(), str(data[0])


class PowerPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Электропитание", "Схемы питания Windows: переключайте, добавляйте, переименовывайте и удаляйте. "
                                           "В режиме производительности NOVA сама включает мощную схему на время игры "
                                           "и после выхода возвращает прежнюю.")
        self._ctx, self._power = ctx, ctx.power
        self._runner = AsyncRunner(self)
        self._plans: list[PowerPlan] = []
        self._params_guid = ""
        self._loaded = False

        if not self._power.available:
            card = Card()
            card.body.addWidget(EmptyState("power", "Доступно только в Windows",
                                           "Схемы электропитания управляются командой powercfg, которой нет на этой системе."))
            self.body.addWidget(card)
            self.finish()
            return

        # ---- режим производительности ----
        boost = Card(spacing=6)
        boost.body.addWidget(make_label("Схема для режима производительности", "h2"))
        self.plan_combo = QComboBox()
        self.plan_combo.setMinimumWidth(300)
        self.plan_combo.activated.connect(self._on_boost_plan)
        boost.body.addWidget(setting_row(
            "Какую схему включать при запуске игры",
            "«Автоматически» — самая мощная из доступных (Максимальная, затем Высокая). Если такой схемы нет, NOVA "
            "создаст свою копию встроенной схемы.", self.plan_combo))
        boost.body.addWidget(HLine())
        status_row = QHBoxLayout()
        self.boost_status = make_label("", "muted", wrap=True)
        self.btn_boost_off = QPushButton("Выключить сейчас")
        self.btn_boost_off.setToolTip("Вернуть прежнюю схему питания и настройки Windows, игра при этом не закрывается")
        self.btn_boost_off.clicked.connect(self._stop_boost)
        status_row.addWidget(self.boost_status, 1)
        status_row.addWidget(self.btn_boost_off)
        boost.body.addLayout(status_row)
        self.body.addWidget(boost)

        # ---- схемы ----
        plans = Card(spacing=8)
        head = QHBoxLayout()
        head.addWidget(make_label("Схемы питания", "h2"), 1)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.reload)
        add = QPushButton("Добавить схему…")
        add.setObjectName("primary")
        add.clicked.connect(self._add_plan)
        head.addWidget(refresh)
        head.addWidget(add)
        plans.body.addLayout(head)
        self.plans_box = QVBoxLayout()
        self.plans_box.setSpacing(8)
        plans.body.addLayout(self.plans_box)
        self.state_label = make_label("Загрузка схем…", "muted", wrap=True)
        plans.body.addWidget(self.state_label)
        self.body.addWidget(plans)

        # ---- параметры выбранной схемы ----
        self.params = Card(spacing=6)
        self.params_title = make_label("", "h2")
        self.params.body.addWidget(self.params_title)
        self.cpu_min = self._spin(0, 100, " %")
        self.cpu_max = self._spin(0, 100, " %")
        self.display_min = self._spin(0, 1440, " мин", never=True)
        self.sleep_min = self._spin(0, 1440, " мин", never=True)
        self.params.body.addWidget(setting_row(
            "Минимальное состояние процессора", "Ниже этого значения процессор не снижает частоту. "
                                                "100 % держит максимальную частоту постоянно: больше нагрев и расход энергии.",
            self.cpu_min))
        self.params.body.addWidget(HLine())
        self.params.body.addWidget(setting_row(
            "Максимальное состояние процессора", "100 % — процессор может работать на полной скорости. "
                                                 "Меньшее значение ограничивает производительность.", self.cpu_max))
        self.params.body.addWidget(HLine())
        self.params.body.addWidget(setting_row("Выключать дисплей через", "При питании от сети. «Никогда» — не выключать.",
                                               self.display_min))
        self.params.body.addWidget(HLine())
        self.params.body.addWidget(setting_row("Переходить в сон через", "При питании от сети. «Никогда» — не засыпать.",
                                               self.sleep_min))
        buttons = QHBoxLayout()
        apply = QPushButton("Применить")
        apply.setObjectName("primary")
        apply.clicked.connect(self._apply_params)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.params.hide)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(apply)
        self.params.body.addLayout(buttons)
        self.params.body.addWidget(make_label(
            "Изменяются значения для питания от сети. Параметры работы от батареи NOVA не трогает.", "caption", wrap=True))
        self.params.hide()
        self.body.addWidget(self.params)
        self.finish()

        ctx.library.boost_changed.connect(self._on_boost_changed)
        ctx.settings.changed.connect(lambda key: self._fill_combo() if key == "boost_plan" else None)
        self._refresh_boost()

    @staticmethod
    def _spin(low: int, high: int, suffix: str, never: bool = False) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSuffix(suffix)
        spin.setFixedWidth(130)
        if never:
            spin.setSpecialValueText("Никогда")
        return spin

    # ------------------------------------------------------------------ данные
    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        if self._power.available and not self._loaded:
            self.reload()
        self._refresh_boost()

    def reload(self) -> None:
        self._runner.submit(self._power.plans, self._on_plans)

    def _on_plans(self, plans: list[PowerPlan] | None, error: Exception | None) -> None:
        if error is not None or plans is None:
            self.state_label.setText("Не удалось получить список схем: " + (_error_text(error) if error else "пустой ответ"))
            self.state_label.show()
            return
        self._loaded = True
        self._plans = sorted(plans, key=lambda p: (not p.active, -p.performance_rank, p.name.lower()))
        self.state_label.setVisible(not plans)
        if not plans:
            self.state_label.setText("Windows не вернула ни одной схемы питания.")
        clear_layout(self.plans_box)
        for plan in self._plans:
            self.plans_box.addWidget(self._plan_row(plan))
        self._fill_combo()
        if self._params_guid and all(p.guid != self._params_guid for p in self._plans):
            self.params.hide()

    def _plan_row(self, plan: PowerPlan) -> QWidget:
        row = QFrame()
        row.setObjectName("cardAlt")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(10)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(make_label(plan.name, "h3"))
        kind = KIND_TITLES.get(plan.kind, "Своя схема")
        texts.addWidget(make_label(kind if not plan.is_builtin else f"{kind} · встроенная схема Windows", "caption"))
        lay.addLayout(texts, 1)
        if plan.active:
            lay.addWidget(make_label("● Активна", "ok"))
        else:
            use = QPushButton("Сделать активной")
            use.clicked.connect(lambda _c=False, p=plan: self._activate(p))
            lay.addWidget(use)
        tune = QPushButton("Настроить")
        tune.clicked.connect(lambda _c=False, p=plan: self._open_params(p))
        lay.addWidget(tune)
        more = QPushButton("⋯")
        more.setObjectName("dots")
        more.setFixedSize(40, 36)
        more.clicked.connect(lambda _c=False, p=plan, b=more: self._plan_menu(p, b))
        lay.addWidget(more)
        return row

    def _plan_menu(self, plan: PowerPlan, button: QPushButton) -> None:
        menu = QMenu(self)
        menu.addAction("Переименовать…").triggered.connect(lambda: self._rename(plan))
        remove = menu.addAction("Удалить…")
        remove.setEnabled(not plan.active)
        remove.setToolTip("Активную схему удалить нельзя" if plan.active else "")
        remove.triggered.connect(lambda: self._delete(plan))
        menu.exec(button.mapToGlobal(QPoint(0, button.height() + 4)))

    def _fill_combo(self) -> None:
        saved = str(self._ctx.settings.get("boost_plan") or AUTO_PLAN).lower()
        self.plan_combo.blockSignals(True)
        self.plan_combo.clear()
        self.plan_combo.addItem("Автоматически (самая мощная)", AUTO_PLAN)
        for plan in self._plans:
            self.plan_combo.addItem(plan.name, plan.guid)
        index = self.plan_combo.findData(saved)
        self.plan_combo.setCurrentIndex(max(index, 0))
        self.plan_combo.blockSignals(False)

    def _on_boost_plan(self, _index: int) -> None:
        self._ctx.settings.set("boost_plan", str(self.plan_combo.currentData() or AUTO_PLAN))

    # ------------------------------------------------------------------ режим производительности
    def _refresh_boost(self) -> None:
        active = self._ctx.library.boost_active
        names = ", ".join(self._ctx.library.running_titles)
        if active:
            self.boost_status.setText("Режим производительности включён" + (f" ({names})." if names else "."))
        else:
            self.boost_status.setText("Режим производительности сейчас выключен. Он включается при запуске игры из NOVA "
                                      "(настройка — на странице «Оптимизация»).")
        self.btn_boost_off.setVisible(active)

    def _on_boost_changed(self, _active: bool) -> None:
        self._refresh_boost()
        if self.isVisible():
            self.reload()          # схема питания могла смениться — обновляем отметку «Активна»

    def _stop_boost(self) -> None:
        self._ctx.library.stop_boost_now()

    # ------------------------------------------------------------------ действия
    def _run(self, job: Callable[[], Any], done_text: str, after: Callable[[Any], None] | None = None) -> None:
        notice = self._ctx.library.notice

        def finished(result: Any, error: Exception | None) -> None:
            if error is not None:
                notice.emit("error", _error_text(error))
            else:
                notice.emit("info", done_text)
                if after is not None:
                    after(result)
            self.reload()

        self._runner.submit(job, finished)

    def _activate(self, plan: PowerPlan) -> None:
        if self._ctx.library.boost_active:
            self._ctx.library.notice.emit(
                "warning", "Сейчас включён режим производительности — после выхода из игры NOVA вернёт прежнюю схему, "
                           "если вы её не меняли. Чтобы поменять схему насовсем, сначала нажмите «Выключить сейчас».")
        self._run(lambda: self._power.set_active(plan.guid), f"Включена схема «{plan.name}».")

    def _rename(self, plan: PowerPlan) -> None:
        text, ok = QInputDialog.getText(self, "Переименовать схему", "Новое название:", QLineEdit.EchoMode.Normal, plan.name)
        text = text.strip()
        if ok and text and text != plan.name:
            self._run(lambda: self._power.rename(plan.guid, text), f"Схема переименована: «{text}».")

    def _delete(self, plan: PowerPlan) -> None:
        extra = ("\n\nЭто встроенная схема Windows — её можно вернуть кнопкой «Добавить схему…»." if plan.is_builtin else "")
        if not confirm(self, "Удалить схему питания?", f"Схема «{plan.name}» будет удалена из Windows.{extra}", "Удалить схему"):
            return
        if str(self._ctx.settings.get("boost_plan")).lower() == plan.guid:
            self._ctx.settings.set("boost_plan", AUTO_PLAN)
        self._run(lambda: self._power.delete(plan.guid), f"Схема «{plan.name}» удалена.")

    def _add_plan(self) -> None:
        dialog = NewPlanDialog(self._plans, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, base = dialog.result_values()
        self._run(lambda: self._power.create(name, base), f"Схема «{name}» добавлена.")

    # ------------------------------------------------------------------ параметры схемы
    def _open_params(self, plan: PowerPlan) -> None:
        self._params_guid = plan.guid
        self.params_title.setText(f"Параметры схемы «{plan.name}»")
        self.params.show()

        def read() -> tuple[tuple[int, int], tuple[int, int]]:
            return self._power.cpu_state(plan.guid), self._power.timeouts(plan.guid)

        def loaded(values: Any, error: Exception | None) -> None:
            if error is not None or values is None:
                self._ctx.library.notice.emit("error", "Не удалось прочитать параметры схемы: "
                                              + (_error_text(error) if error else "пустой ответ"))
                self.params.hide()
                return
            (cmin, cmax), (display, sleep) = values
            self.cpu_min.setValue(cmin)
            self.cpu_max.setValue(cmax)
            self.display_min.setValue(display)
            self.sleep_min.setValue(sleep)

        self._runner.submit(read, loaded)

    def _apply_params(self) -> None:
        guid = self._params_guid
        cmin, cmax = self.cpu_min.value(), self.cpu_max.value()
        display, sleep = self.display_min.value(), self.sleep_min.value()
        if cmin > cmax:
            self._ctx.library.notice.emit("warning", "Минимальное состояние процессора не может быть больше максимального.")
            return

        def job() -> None:
            self._power.set_cpu_state(guid, cmin, cmax)
            self._power.set_timeouts(guid, display, sleep)
            self._power.apply_to_active(guid)

        self._run(job, "Параметры схемы сохранены.")
