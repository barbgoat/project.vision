"""Correr os comandos do projeto (main.py, ferramentas) e ler os CSV de resultados."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

from nucleo.config import RAIZ


def correr(*argumentos: str | Path) -> subprocess.CompletedProcess:
    """Corre um comando do projeto (p.ex. "main.py", "--help") com o Python deste ambiente, na raiz."""
    return subprocess.run(
        [sys.executable, *map(str, argumentos)], cwd=RAIZ, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=300,
    )


def ler_csv(caminho: Path) -> list[dict[str, str]]:
    """Linhas de um CSV do M7 (separador ';', utf-8 com BOM)."""
    return list(csv.DictReader(caminho.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))


def execucao(pasta_saida: Path) -> Path:
    """A pasta <saida>/<data_hora>/ criada por uma execução do main.py."""
    pastas = [p for p in pasta_saida.iterdir() if p.is_dir()]
    assert len(pastas) == 1, pastas
    return pastas[0]
