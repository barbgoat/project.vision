r"""Calcula a montagem da câmara (lente, distância, FPS, exposição) a partir das características dela.

    python -m ferramentas.calcular_montagem                    # lente e distância dos defines (LENTE_*)
    python -m ferramentas.calcular_montagem --distancia 350    # a mesma lente, mais longe
    python -m ferramentas.calcular_montagem --focal 16 --distancia 600
    python -m ferramentas.calcular_montagem --campo 300 --distancia 450   # que lente preciso?
    python -m ferramentas.calcular_montagem --velocidade 800 --fps 24 --exposicao 80

Mostra: campo de visão e px/mm (e se bate com GERAL_PX_POR_MM), profundidade
de campo, o erro causado pela altura do saco, se o saco cabe em qualquer
rotação, quantas imagens terá o saco inteiro à vista, o arrasto do
movimento, o débito na rede GigE e a memória por imagem. Nada é alterado:
os valores a adotar passam-se à mão para nucleo/defines.py.

A lente é aproximada como uma lente fina: campo = sensor × (distância − focal) / focal,
com a distância medida da lente ao tapete. Depois de montar, medir sempre o
px/mm a sério com uma régua.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

if __package__ in (None, ""):  # corrido como ficheiro: python ferramentas\calcular_montagem.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nucleo.config import CAMINHO_POR_OMISSAO, carregar_configuracao  # noqa: E402

DEBITO_GIGE_MB_S = 120.0  # débito útil de uma ligação Gigabit Ethernet
BYTES_POR_PIXEL = {"BayerRG8": 1.0, "BayerRG12p": 1.5, "BGR8": 3.0}


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calcular a montagem da câmara a partir das suas características.")
    parser.add_argument("--focal", type=float, help="distância focal da lente (mm); omissão: LENTE_FOCAL_MM")
    parser.add_argument("--distancia", type=float, help="distância da lente ao tapete (mm); omissão: LENTE_DISTANCIA_MM")
    parser.add_argument("--abertura", type=float, help="abertura (número F); omissão: LENTE_ABERTURA_F")
    parser.add_argument("--espessura", type=float, default=5.0,
                        help="altura do saco pousado, para estimar o erro nas medidas (mm; omissão: %(default)s)")
    parser.add_argument("--campo", type=float, help="largura de campo pretendida, ao longo dos 2448 px (mm)")
    parser.add_argument("--velocidade", type=float, help="velocidade do tapete (mm/s); omissão: GERAL_VELOCIDADE_MM_S")
    parser.add_argument("--fps", type=float, help="imagens por segundo; omissão: DISPARO_FPS")
    parser.add_argument("--exposicao", type=float, help="exposição (µs); omissão: AQUISICAO_EXPOSICAO_US")
    parser.add_argument(
        "--config", type=Path, default=CAMINHO_POR_OMISSAO,
        help="valores a sobrepor aos defines (omissão: config.json, se existir)",
    )
    return parser


def aviso(condicao_ok: bool) -> str:
    return "OK " if condicao_ok else "(!)"


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    cfg = carregar_configuracao(args.config)
    cam = cfg.camara
    velocidade = args.velocidade or cfg.geral.velocidade_mm_s
    fps = args.fps or cfg.disparo.fps
    exposicao_us = args.exposicao or cfg.aquisicao.exposicao_us
    sensor_l = cam.largura_px * cam.pixel_um / 1000  # mm
    sensor_a = cam.altura_px * cam.pixel_um / 1000

    lente = cfg.lente
    focal = args.focal or lente.focal_mm
    distancia = args.distancia or lente.distancia_mm
    abertura = args.abertura or lente.abertura_f
    diagonal_sensor = math.hypot(sensor_l, sensor_a)

    print(f"Câmara: {cam.modelo}")
    print(f"  {cam.largura_px} x {cam.altura_px} px, píxel de {cam.pixel_um} µm, sensor {sensor_l:.2f} x {sensor_a:.2f} mm, "
          f"até {cam.fps_max:g} FPS ({cam.formato_pixel})")
    if not args.focal:
        print(f"Lente: {lente.modelo}, {lente.focal_mm:g} mm, F{abertura:g}")
        print(f"  {aviso(lente.circulo_imagem_mm >= diagonal_sensor)} círculo de imagem de {lente.circulo_imagem_mm:g} mm "
              f"para um sensor com {diagonal_sensor:.1f} mm de diagonal")

    # --- Campo de visão ----------------------------------------------------------------
    print("\nCampo de visão")
    if args.campo:
        campo_l = args.campo
        origem = f"campo pedido de {args.campo:g} mm"
        if args.distancia:
            focal = sensor_l * args.distancia / (args.campo + sensor_l)
            print(f"  Para {args.campo:g} mm a {args.distancia:g} mm do tapete: lente de ~{focal:.1f} mm "
                  "(escolher a focal comercial mais próxima, p.ex. 8, 12, 16, 25 mm, e ajustar a distância)")
        else:
            distancia = focal * (args.campo / sensor_l + 1)
            print(f"  Com a lente de {focal:g} mm: a {distancia:.0f} mm do tapete")
    else:
        campo_l = sensor_l * (distancia - focal) / focal
        origem = f"lente de {focal:g} mm a {distancia:g} mm do tapete"
    px_mm = cam.largura_px / campo_l
    campo_a = cam.altura_px / px_mm
    print(f"  {campo_l:.0f} x {campo_a:.0f} mm  ({origem})")
    print(f"  {px_mm:.2f} px/mm = {1000 / px_mm:.0f} µm por píxel")
    diferenca = abs(px_mm - cfg.geral.px_por_mm) / cfg.geral.px_por_mm
    print(f"  {aviso(diferenca <= 0.03)} GERAL_PX_POR_MM = {cfg.geral.px_por_mm:g} "
          + ("(bate com a lente)" if diferenca <= 0.03 else f"(difere {diferenca:.0%}: confirmar com uma régua e atualizar)"))

    # --- Profundidade de campo e altura do saco ----------------------------------------------
    print("\nNitidez e altura do saco")
    confusao_mm = 2 * cam.pixel_um / 1000  # desfoque aceite: 2 píxeis
    profundidade = 2 * abertura * confusao_mm * (distancia / focal) ** 2
    print(f"      profundidade de campo a F{abertura:g}: ~{profundidade:.0f} mm (zona nítida à volta do plano focado)")
    h = args.espessura
    ampliacao = distancia / (distancia - h) - 1
    nominal_c = cfg.dimensoes.comprimento_nominal_mm
    print(f"  {aviso(ampliacao * nominal_c <= cfg.dimensoes.tolerancia_comprimento_mm / 3)} um saco com {h:g} mm de altura "
          f"parece {ampliacao:.1%} maior: {ampliacao * nominal_c:.1f} mm a mais em {nominal_c:g} mm "
          f"(tolerância do M3: ±{cfg.dimensoes.tolerancia_comprimento_mm:g} mm)")
    print("      para o compensar, calibrar o px/mm com a régua à altura do contorno do saco (não no tapete)")

    # --- O saco cabe? --------------------------------------------------------------------
    comprimento = cfg.localizacao.comprimento_max_mm
    largura = cfg.localizacao.largura_max_mm
    nominal = cfg.dimensoes.comprimento_nominal_mm, cfg.dimensoes.largura_nominal_mm
    diagonal = math.hypot(comprimento, largura)
    print("\nO saco cabe na imagem?")
    print(f"  {aviso(campo_a >= diagonal)} saco maior ({comprimento:g} x {largura:g} mm) em qualquer rotação precisa de "
          f"{diagonal:.0f} mm na direção do campo menor; há {campo_a:.0f} mm")

    # --- Imagens por saco ------------------------------------------------------------------
    print(f"\nImagens por saco (tapete a {velocidade:g} mm/s, {fps:g} FPS; tapete ao longo dos {cam.largura_px} px)")
    print(f"  {aviso(fps <= cam.fps_max)} {fps:g} FPS (máximo da câmara: {cam.fps_max:g})")
    for nome, extensao in (("nominal", nominal[0]), ("maior, direito", comprimento), ("maior, a 45°", diagonal)):
        inteiras = fps * (campo_l - extensao) / velocidade
        print(f"  {aviso(inteiras >= 3)} saco {nome:<15} ({extensao:5.0f} mm): ~{max(inteiras, 0):.1f} imagens com o saco "
              "inteiro (mínimo 1, ideal 3 ou mais)")
    passagem_ms = (nominal[0] + cfg.disparo.virtual_faixa_mm) / velocidade * 1000
    print(f"      passagem pela faixa da barreira virtual: ~{passagem_ms:.0f} ms = ~{passagem_ms * fps / 1000:.0f} imagens")
    print(f"      tempo entre sacos: {cfg.geral.orcamento_ms:g} ms (orçamento de análise por saco)")

    # --- Exposição -------------------------------------------------------------------------
    arrasto = velocidade * exposicao_us / 1e6 * px_mm
    max_meio_px = 0.5 / (velocidade * px_mm) * 1e6
    print(f"\nExposição de {exposicao_us:g} µs")
    print(f"  {aviso(arrasto <= 0.5)} arrasto do movimento: {arrasto:.2f} px (até 0,5 px: exposição <= {max_meio_px:.0f} µs)")
    print(f"  {aviso(cam.exposicao_min_us <= exposicao_us <= 1e6 / fps)} tem de estar entre {cam.exposicao_min_us:g} µs "
          f"e 1/FPS = {1e6 / fps:.0f} µs")

    # --- Rede e memória ------------------------------------------------------------------------
    bpp = BYTES_POR_PIXEL[cam.formato_pixel]
    mb_imagem = cam.largura_px * cam.altura_px * bpp / 1e6
    debito = mb_imagem * fps
    print(f"\nRede GigE ({cam.formato_pixel}: {mb_imagem:.1f} MB por imagem)")
    print(f"  {aviso(debito <= DEBITO_GIGE_MB_S * 1.02)} {debito:.0f} MB/s de {DEBITO_GIGE_MB_S:g} MB/s possíveis "
          f"(máximo neste formato: {DEBITO_GIGE_MB_S / mb_imagem:.0f} FPS)")
    if debito > 0.9 * DEBITO_GIGE_MB_S:
        print("      no limite da rede: placa só para a câmara e jumbo frames obrigatórios; se chegarem imagens "
              "incompletas, baixar os FPS")
    mb_bgr = cam.largura_px * cam.altura_px * 3 / 1e6
    print(f"  memória no PC: {mb_bgr:.0f} MB por imagem a cores; até "
          f"{(cfg.disparo.candidatas * cfg.disparo.fila_max + cfg.disparo.imagens_max) * mb_bgr:.0f} MB em imagens à espera")

    # --- Resolução dos defeitos ------------------------------------------------------------------
    print("\nDefeitos")
    sigma_px = min(cfg.relevo.crista_sigmas_mm) * px_mm
    print(f"  {aviso(sigma_px >= 1)} escala mais fina do M5 ({min(cfg.relevo.crista_sigmas_mm):g} mm) = {sigma_px:.1f} px "
          "(abaixo de 1 px não se distingue do ruído)")
    print(f"      um risco de {cfg.relevo.risco_largura_max_mm:g} mm de largura ocupa {cfg.relevo.risco_largura_max_mm * px_mm:.1f} px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
