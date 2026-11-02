r"""Teste rápido da câmara, sem o resto do programa: liga-se, tira uma imagem e grava-a.

    python -m ferramentas.testar_camara --config configuracoes\linha.json

Usa a câmara do perfil (camara.serial) em contínuo, com a exposição e o ganho do perfil, grava frame.png
na pasta atual e repõe os parâmetros originais da câmara ao sair (é partilhada).
Sem câmara à mão: $env:PYLON_CAMEMU = "1" dá uma câmara emulada do pylon (nº 0815-0000).
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\testar_camara.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modulos.m1_aquisicao import criar_fonte  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, carregar_configuracao  # noqa: E402
from nucleo.imagens import guardar_imagem  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tirar uma imagem com a câmara do perfil.")
    parser.add_argument("--config", type=Path, default=CAMINHO_POR_OMISSAO, help="perfil (com camara.serial)")
    args = parser.parse_args(argv)
    try:
        cfg = carregar_configuracao(args.config)
        cfg = dataclasses.replace(cfg, disparo=dataclasses.replace(cfg.disparo, modo="ao_vivo"))  # sem esperar sinal
        with criar_fonte(cfg) as camara:
            captura = camara.adquirir()
    except (RuntimeError, ValueError, FileNotFoundError) as erro:
        print(erro, file=sys.stderr)
        return 2
    if captura is None:
        print("A câmara não enviou nenhuma imagem a tempo (aquisicao.timeout_ms).", file=sys.stderr)
        return 1
    guardar_imagem(Path("frame.png"), captura.imagem)
    print(f"{captura.origem}: {captura.imagem.shape[1]} x {captura.imagem.shape[0]} px, gravada em frame.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
