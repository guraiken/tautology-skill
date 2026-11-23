from PySide6.QtWidgets import QVBoxLayout, QWidget


def build_panel(parent: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout(parent)
    layout.setContentsMargins(16, 24, 16, 6)
    layout.setSpacing(8)
    layout.addSpacing(26)
    return layout
