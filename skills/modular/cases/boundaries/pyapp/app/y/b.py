from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.x.a import Thing


def f():
    from app.x import a
    return a
