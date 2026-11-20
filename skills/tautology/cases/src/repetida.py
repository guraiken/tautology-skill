import json


def flag(text, key, default=False):
    try:
        return bool(json.loads(text).get(key, default))
    except ValueError:
        return default
    return default


def total(itens):
    soma = sum(item.preco for item in itens)
    soma = sum(item.preco for item in itens)
    return soma
