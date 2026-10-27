r"""Testa SÓ o disparo (M1): que passagens (sacos) encontra numa sequência de imagens.

    python -m ferramentas.testar_disparo --video "D:\Vídeos\linha.mp4"
    python -m ferramentas.testar_disparo --pasta dataset\blackfly\sequencia --disparo virtual --guardar resultados\disparo

Para cada passagem mostra: número, instante de início, duração, nº de
imagens, as candidatas (com a distância do saco ao centro) e os avisos. Com
--guardar, grava as candidatas de cada passagem (passagem_003_1.jpg, ...),
para confirmar à vista que a melhor imagem tem o saco inteiro e centrado.

O que confirmar:
    - uma passagem por saco (nem sacos juntos numa, nem um saco partido em duas);
    - a 1.ª candidata tem o saco inteiro e perto do centro;
    - imagens por passagem suficientes (ver DISPARO_FPS nos defines).
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\testar_disparo.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modulos.m1_aquisicao import criar_disparo, criar_fonte  # noqa: E402
from nucleo.config import CAMINHO_POR_OMISSAO, carregar_configuracao  # noqa: E402
from nucleo.contrato import Passagem  # noqa: E402
from nucleo.imagens import guardar_imagem  # noqa: E402


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Testar só o disparo (separação dos sacos) numa sequência de imagens.")
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument("--video", type=Path, help="ficheiro de vídeo")
    origem.add_argument("--pasta", type=Path, help="pasta com uma sequência de imagens (por ordem alfabética)")
    parser.add_argument(
        "--disparo", choices=["sensor", "virtual"], default="virtual",
        help="modo de disparo (omissão: %(default)s; 'sensor' precisa da câmara)",
    )
    parser.add_argument(
        "--config", type=Path, default=CAMINHO_POR_OMISSAO,
        help="valores a sobrepor aos defines (omissão: config.json, se existir)",
    )
    parser.add_argument("--guardar", type=Path, help="pasta onde gravar as imagens candidatas de cada passagem")
    return parser


def descrever(passagem: Passagem, inicio_fonte: float) -> str:
    candidatas = ", ".join(
        f"{Path(c.origem).name} ({Passagem.texto_pontuacao(p)})"
        for c, p in zip(passagem.candidatas, passagem.pontuacoes)
    )
    linha = (f"passagem {passagem.numero:3d}: t={passagem.inicio - inicio_fonte:7.2f} s, "
             f"{passagem.duracao_ms:5.0f} ms, {passagem.num_imagens:3d} imagens | candidatas: {candidatas}")
    if passagem.aviso:
        linha += f" | AVISO: {passagem.aviso}"
    return linha


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    try:
        cfg = carregar_configuracao(args.config)
        cfg = dataclasses.replace(cfg, disparo=dataclasses.replace(cfg.disparo, modo=args.disparo))
        fonte = criar_fonte("video" if args.video else "pasta", cfg, pasta=args.pasta, video=args.video)
    except (ValueError, FileNotFoundError, RuntimeError) as erro:
        logging.getLogger("testar_disparo").error("%s", erro)
        return 2

    disparo = criar_disparo(cfg)
    passagens: list[Passagem] = []
    inicio_fonte: float | None = None
    imagens = 0

    def contar(capturas):
        nonlocal inicio_fonte, imagens
        for captura in capturas:
            inicio_fonte = captura.instante if inicio_fonte is None else inicio_fonte
            imagens += 1
            yield captura

    try:
        with fonte:
            for passagem in disparo.passagens(contar(fonte)):
                passagens.append(passagem)
                print(descrever(passagem, inicio_fonte or passagem.inicio))
                if args.guardar:
                    for i, captura in enumerate(passagem.candidatas, start=1):
                        guardar_imagem(args.guardar / f"passagem_{passagem.numero:03d}_{i}.jpg", captura.imagem)
    except ValueError as erro:  # p.ex. "sensor" sem câmara
        logging.getLogger("testar_disparo").error("%s", erro)
        return 2

    print(f"\n{imagens} imagens, {len(passagens)} passagens (sacos), "
          f"{disparo.ignoradas} ignoradas por terem menos de {cfg.disparo.imagens_min} imagem(ns).")
    if passagens:
        por_passagem = [p.num_imagens for p in passagens]
        print(f"Imagens por passagem: mín. {min(por_passagem)}, média {sum(por_passagem) / len(por_passagem):.1f}, "
              f"máx. {max(por_passagem)}")
        com_aviso = [p.numero for p in passagens if p.aviso]
        if com_aviso:
            print(f"Passagens com aviso: {com_aviso}")
    if args.guardar:
        print(f"Candidatas gravadas em {args.guardar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
