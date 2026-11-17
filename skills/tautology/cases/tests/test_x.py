def test_inline():
    r = calcular(10)
    assert r == calcular(10)

def test_equivalencia():
    assert normalizar("A ") == normalizar("a")

def test_ok():
    r = calcular(10)
    assert r == 20
