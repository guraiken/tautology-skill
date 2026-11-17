def test_antes_depois():
    antes = ler()
    mudar()
    assert ler() == antes
