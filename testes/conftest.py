"""Fixtures comuns aos testes."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def pasta_com_acentos(tmp_path: Path) -> Path:
    """Pasta temporária com espaços e acentos no nome (como as pastas do utilizador)."""
    pasta = tmp_path / "Ensaios de iluminação ç"
    pasta.mkdir()
    return pasta
