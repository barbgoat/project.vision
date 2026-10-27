"""M7 Saída, desenho: a imagem anotada de um saco (ecrã, imagens guardadas, painel de depuração).

    ┌──────────────────────────────────────────────────────────┐
    │ NOK 74 ms | localizacao OK | dimensoes NOK(2) | relevo ...│  ← faixa: estado de cada módulo
    │                                                          │
    │        ┌────────────────────────────◣ 1                  │  ← contorno do saco (cor do estado)
    │        │            saco             │                   │     e de cada defeito, numerado
    │        └──────────▀▀ 2 ──────────────┘                   │
    │ 1 dimensao: falta material num canto (canto dobrado?)... │  ← legenda, uma linha por defeito
    │ 2 dimensao: material a mais num lado (aba...)            │
    └──────────────────────────────────────────────────────────┘

O cv2.putText não desenha acentos: os textos passam por texto_ascii.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from nucleo.contrato import Estado, Resultado, TipoDefeito
from nucleo.imagens import texto_ascii

# Cores (BGR) do contorno do saco, conforme o estado final.
CORES_ESTADO = {
    Estado.OK: (0, 200, 0),  # verde
    Estado.NOK: (0, 0, 255),  # vermelho
    Estado.ERRO: (0, 140, 255),  # laranja
    Estado.SEM_SACO: (160, 160, 160),  # cinzento
}
# Cores (BGR) de cada tipo de defeito.
CORES_TIPO = {
    TipoDefeito.DIMENSAO: (255, 0, 255),  # magenta
    TipoDefeito.COR: (0, 215, 255),  # dourado
    TipoDefeito.RISCO: (0, 0, 255),  # vermelho
    TipoDefeito.DOBRA: (0, 128, 255),  # laranja
    TipoDefeito.RUGAS: (255, 255, 0),  # ciano
    TipoDefeito.AMASSO: (255, 0, 0),  # azul
    TipoDefeito.MANCHA: (0, 255, 255),  # amarelo
}
_PRETO = (0, 0, 0)


def desenhar(resultado: Resultado, px_por_mm: float) -> np.ndarray:
    """Produz a imagem anotada de um saco.

    Recebe:
        resultado: a decisão do Mestre (com a imagem analisada).
        px_por_mm: para desenhar o eixo maior do saco com o comprimento medido.
    Devolve:
        Cópia BGR uint8 da imagem analisada, com as anotações (a original é só de leitura).
    """
    if resultado.imagem is None:
        raise ValueError("o resultado não tem imagem (passagem descartada)")
    imagem = resultado.imagem.copy()
    altura, largura = imagem.shape[:2]
    escala = max(0.5, largura / 1600)  # tamanho do texto proporcional à imagem
    espessura = max(1, round(escala * 2))
    saco = resultado.saco

    if saco is not None:
        cor_saco = CORES_ESTADO.get(resultado.estado, (255, 255, 255))
        cv2.drawContours(imagem, [saco.contorno], -1, cor_saco, espessura + 1)
        # Eixo maior e centro
        cx, cy = saco.centro_px
        meio = saco.comprimento_mm * px_por_mm / 2
        dx, dy = meio * math.cos(math.radians(saco.angulo_graus)), meio * math.sin(math.radians(saco.angulo_graus))
        cv2.line(imagem, (round(cx - dx), round(cy - dy)), (round(cx + dx), round(cy + dy)), cor_saco, 1)
        cv2.circle(imagem, (round(cx), round(cy)), 2 * espessura, cor_saco, -1)

        # Defeitos com contorno: do recorte normalizado para a imagem original
        inversa = cv2.invertAffineTransform(saco.matriz)
        for numero, defeito in enumerate(resultado.defeitos, start=1):
            if defeito.contorno is None:
                continue
            cor = CORES_TIPO.get(defeito.tipo, (255, 255, 255))
            pontos = np.round(cv2.transform(defeito.contorno.astype(np.float32), inversa)).astype(np.int32)
            camada = imagem.copy()
            cv2.fillPoly(camada, [pontos], cor)
            imagem = cv2.addWeighted(camada, 0.35, imagem, 0.65, 0)  # semitransparente
            cv2.polylines(imagem, [pontos], True, cor, espessura)
            x, y, _, _ = cv2.boundingRect(pontos)
            _texto(imagem, str(numero), (x, max(y - 4, int(20 * escala))), escala, cor)
    else:
        aviso = "SEM SACO" if resultado.estado is Estado.SEM_SACO else str(resultado.estado)
        _texto(imagem, aviso, (largura // 3, altura // 2), escala * 2, CORES_ESTADO.get(resultado.estado, (255, 255, 255)))

    # Faixa no topo: estado final e de cada módulo
    partes = [f"{resultado.estado} {resultado.tempo_ms:.0f} ms"] + [f"{r.modulo} {r.resumo}" for r in resultado.relatorios]
    _faixa(imagem, [" | ".join(partes)], topo=True, escala=escala)

    # Legenda: um defeito por linha; e as mensagens dos módulos com problemas
    linhas = [f"{n} {d.tipo}: {d.descricao}" for n, d in enumerate(resultado.defeitos, start=1)]
    linhas += [f"{r.modulo}: {r.mensagem}" for r in resultado.relatorios
               if r.estado in (Estado.ERRO, Estado.SEM_SACO) and r.mensagem]
    if linhas:
        _faixa(imagem, linhas, topo=False, escala=escala)
    return imagem


def _texto(imagem: np.ndarray, texto: str, origem: tuple[int, int], escala: float, cor: tuple[int, int, int],
           orla: bool = True) -> None:
    """Texto, com uma orla escura para se ler sobre qualquer fundo.

    A orla só serve para textos curtos (números): o OpenCV avança cada letra
    também pela espessura do traço, por isso numa linha comprida a orla, mais
    grossa, ficaria visivelmente mais comprida do que o texto.
    """
    texto = texto_ascii(texto)
    espessura = max(1, round(escala * 2))
    if orla:
        cv2.putText(imagem, texto, origem, cv2.FONT_HERSHEY_SIMPLEX, escala, _PRETO, espessura + 2, cv2.LINE_AA)
    cv2.putText(imagem, texto, origem, cv2.FONT_HERSHEY_SIMPLEX, escala, cor, espessura, cv2.LINE_AA)


def _faixa(imagem: np.ndarray, linhas: list[str], topo: bool, escala: float) -> None:
    """Faixa escura semitransparente com texto, no topo ou no fundo da imagem."""
    altura, largura = imagem.shape[:2]
    passo = int(30 * escala)
    max_caracteres = max(20, int(largura / (11 * escala)))
    linhas = [texto_ascii(l)[:max_caracteres] for l in linhas]
    alto = passo * len(linhas) + passo // 2
    y0 = 0 if topo else altura - alto
    regiao = imagem[y0:y0 + alto]
    regiao[:] = (regiao * 0.35).astype(np.uint8)
    for i, linha in enumerate(linhas):
        _texto(imagem, linha, (int(8 * escala), y0 + passo * (i + 1) - passo // 4), escala * 0.8, (255, 255, 255),
               orla=False)  # a faixa já é escura
