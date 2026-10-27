r"""MESTRE, programa principal: capta cada saco que passa, inspeciona-o e regista o resultado.

Exemplos (na raiz do projeto, com o .venv ativo):
    python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json   (fotos: cada foto é um saco)
    python main.py --fonte video --video "D:\Vídeos\linha.mp4" --disparo virtual
    python main.py --fonte spinnaker --disparo sensor --guardar nok
    python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json --depurar nok

Ciclo:
    M1 Aquisição (thread própria): fonte → disparo → passagens (uma por saco)
    Mestre: escolhe a imagem → M2 → M3..M6 → decisão     ← uma decisão por saco
    M7 Saída: registo.csv, defeitos.csv, imagens
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

import cv2

from mestre import Mestre
from modulos.m1_aquisicao import AquisicaoEmParalelo, criar_disparo, criar_fonte
from modulos.m7_saida import Saida
from nucleo.config import CAMINHO_POR_OMISSAO, RAIZ, carregar_configuracao
from nucleo.contrato import Estado, Resultado

log = logging.getLogger("main")

JANELA = "inspecao"


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspeção visual de sacos de plástico (protótipo v1).")
    parser.add_argument(
        "--fonte", choices=["pasta", "video", "webcam", "spinnaker"], default="pasta",
        help="origem das imagens (omissão: %(default)s)",
    )
    parser.add_argument(
        "--pasta", type=Path,
        help="pasta com as imagens, para --fonte pasta; inclui subpastas (p.ex. dataset\\telemovel)",
    )
    parser.add_argument("--video", type=Path, help="ficheiro de vídeo, para --fonte video")
    parser.add_argument("--indice", type=int, default=0, help="índice da webcam, para --fonte webcam (omissão: 0)")
    parser.add_argument("--serie", help="nº de série da câmara, para --fonte spinnaker (omissão: a primeira)")
    parser.add_argument(
        "--disparo", choices=["foto", "sensor", "virtual"],
        help="como se separa cada saco (omissão: DISPARO_MODO nos defines)",
    )
    parser.add_argument(
        "--config", type=Path, default=CAMINHO_POR_OMISSAO,
        help="valores a sobrepor aos defines (omissão: config.json, se existir)",
    )
    parser.add_argument(
        "--saida", type=Path, default=RAIZ / "resultados",
        help="pasta para as imagens anotadas e os registos CSV (omissão: resultados/)",
    )
    parser.add_argument(
        "--guardar", choices=["nenhuma", "nok", "todas"],
        help="que imagens anotadas guardar (omissão: SAIDA_GUARDAR_IMAGENS nos defines)",
    )
    parser.add_argument(
        "--depurar", choices=["nenhuma", "nok", "todas"],
        help="guardar a pasta de depuração (imagem de cada etapa, painel, JSON) dos sacos NOK/ERRO "
             "ou de todos (omissão: SAIDA_DEPURAR nos defines)",
    )
    parser.add_argument("--mostrar", action="store_true", help="mostrar cada saco anotado numa janela")
    parser.add_argument("--detalhe", action="store_true", help="mostrar também o traceback completo dos erros")
    return parser


def resumo_linha(resultado: Resultado, ciclo_ms: float, orcamento_ms: float) -> str:
    """Uma linha por saco na consola, p.ex.
    "saco 12 (8 imagens, 1 analisada): NOK 183 ms (+25 ms saída) | localizacao=OK relevo=NOK(2) | 2 risco".

    ciclo_ms: inspeção + registo (M7); é este o tempo que tem de caber no orçamento.
    """
    passagem = resultado.passagem
    if passagem is not None and passagem.modo != "foto":
        nome = f"saco {passagem.numero} ({passagem.num_imagens} imagens, {resultado.imagens_analisadas} analisada(s))"
    else:
        nome = Path(resultado.captura.origem).name if resultado.captura else "?"
    modulos = " ".join(f"{r.modulo}={r.resumo}" for r in resultado.relatorios)
    saida_ms = ciclo_ms - resultado.tempo_ms
    linha = f"{nome}: {resultado.estado} {resultado.tempo_ms:.0f} ms (+{saida_ms:.0f} ms saída) | {modulos}"
    if resultado.defeitos:
        linha += " | " + ", ".join(f"{n} {tipo}" for tipo, n in sorted(resultado.contagem_tipos.items()))
    if ciclo_ms > orcamento_ms:
        linha += f" | (!) acima dos {orcamento_ms:.0f} ms"
    if passagem is not None and passagem.aviso:
        linha += f" | disparo: {passagem.aviso}"
    return linha


def mostrar(imagem, ao_vivo: bool) -> bool:
    """Mostra a imagem. Devolve False se o utilizador pediu para sair (q ou Esc).

    Pasta/vídeo: espera por uma tecla para avançar. Câmara: não espera; espaço faz pausa.
    """
    cv2.namedWindow(JANELA, cv2.WINDOW_NORMAL)
    cv2.imshow(JANELA, imagem)
    tecla = cv2.waitKey(1 if ao_vivo else 0) & 0xFF
    if ao_vivo and tecla == ord(" "):
        tecla = cv2.waitKey(0) & 0xFF
    return tecla not in (ord("q"), 27)


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.detalhe else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    try:
        cfg = carregar_configuracao(args.config)
        if args.guardar:
            cfg = dataclasses.replace(cfg, saida=dataclasses.replace(cfg.saida, guardar_imagens=args.guardar))
        if args.disparo:
            cfg = dataclasses.replace(cfg, disparo=dataclasses.replace(cfg.disparo, modo=args.disparo))
        mestre = Mestre(cfg)
    except (ValueError, FileNotFoundError) as erro:  # p.ex. localizacao.fundo_referencia em falta
        log.error("%s", erro)
        return 2
    log.info("Módulos ativos: %s | disparo: %s", ", ".join(mestre.nomes), cfg.disparo.modo)
    if args.fonte in ("video", "webcam") and cfg.disparo.modo == "foto":
        log.warning("Com --fonte %s e o disparo 'foto', CADA IMAGEM conta como um saco; "
                    "para separar os sacos use --disparo virtual.", args.fonte)

    if args.fonte == "pasta" and args.pasta is None:
        log.error("Indica a pasta das imagens (e a configuração da montagem), p.ex.:\n"
                  "    python main.py --pasta dataset\\telemovel --config configuracoes\\telemovel.json")
        return 2
    try:
        fonte = criar_fonte(args.fonte, cfg, pasta=args.pasta, video=args.video,
                            indice=args.indice, numero_serie=args.serie)
    except NotImplementedError:
        log.error("A fonte '%s' ainda não está implementada (M1 aquisicao).", args.fonte)
        return 2
    except (FileNotFoundError, RuntimeError, ValueError) as erro:
        log.error("M1 aquisicao: %s", erro)
        return 2

    estados: Counter[Estado] = Counter()
    tempos: list[float] = []
    aquisicao = AquisicaoEmParalelo(fonte, criar_disparo(cfg), cfg.disparo.fila_max)
    depurar = args.depurar or cfg.saida.depurar
    pasta_resultados = None
    codigo = 0
    try:
        with aquisicao, Saida(cfg, args.saida, mestre.nomes, depurar=depurar) as saida:
            pasta_resultados = saida.pasta
            log.info("Resultados em %s", saida.pasta)
            if saida.depuracao is not None:
                log.info("Depuração ligada (%s): as imagens de cada etapa gastam tempo e disco.", depurar)
            for passagem in aquisicao:
                inicio = perf_counter()
                # Os módulos só produzem as imagens das etapas se forem precisas para a depuração.
                resultado = mestre.inspecionar_passagem(passagem, guardar_intermedios=saida.depuracao is not None)
                saida.registar(resultado)
                # O registo (CSV, imagens, depuração) também gasta tempo do saco seguinte.
                ciclo_ms = (perf_counter() - inicio) * 1000
                estados[resultado.estado] += 1
                tempos.append(ciclo_ms)
                print(resumo_linha(resultado, ciclo_ms, cfg.geral.orcamento_ms))
                if args.mostrar:
                    imagem = saida.anotar(resultado)
                    if imagem is not None and not mostrar(imagem, fonte.ao_vivo):
                        break
    except KeyboardInterrupt:
        log.info("Interrompido pelo utilizador.")
    except ValueError as erro:  # p.ex. disparo "sensor" com uma fonte sem sensor
        log.error("%s", erro)
        return 2
    except RuntimeError as erro:  # p.ex. a câmara desligou-se a meio: mostrar o que já foi feito
        log.error("M1 aquisicao parou: %s", erro)
        log.debug("Traceback completo:", exc_info=erro)
        codigo = 1
    finally:
        if args.mostrar:
            cv2.destroyAllWindows()

    total = sum(estados.values())
    print(f"\nSacos inspecionados: {total}  " + "  ".join(f"{e}: {n}" for e, n in sorted(estados.items())))
    print(f"Imagens recebidas: {aquisicao.imagens}; passagens descartadas por atraso da análise: "
          f"{aquisicao.descartadas}")
    if tempos:
        print(f"Tempo por saco (inspeção + saída): médio {sum(tempos) / len(tempos):.0f} ms, "
              f"máximo {max(tempos):.0f} ms (orçamento {cfg.geral.orcamento_ms:.0f} ms)")
    if pasta_resultados is not None and (pasta_resultados / "depuracao").exists():
        print(f"Depuração (um painel.jpg por saco): {pasta_resultados / 'depuracao'}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
