"""Formatação de números no padrão brasileiro."""
from __future__ import annotations


def _br(txt: str) -> str:
    return txt.replace(",", "§").replace(".", ",").replace("§", ".")


def dec(v: float | None, casas: int = 2) -> str:
    return "—" if v is None else _br(f"{v:,.{casas}f}")


def brl(v: float | None, casas: int = 2) -> str:
    return "—" if v is None else f"R$ {dec(v, casas)}"


def pct(v: float | None, casas: int = 1, sinal: bool = False) -> str:
    if v is None:
        return "—"
    return _br(f"{v * 100:{'+' if sinal else ''}.{casas}f}") + "%"


def mi(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1e9:
        return f"R$ {dec(v / 1e9, 1)} bi"
    if v >= 1e6:
        return f"R$ {dec(v / 1e6, 1)} mi"
    return f"R$ {dec(v / 1e3, 0)} mil"
