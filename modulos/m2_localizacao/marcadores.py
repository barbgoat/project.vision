"""M2 Localização, etapa 0 (opcional): endireitar a foto por 4 marcadores ArUco à volta do saco.

Faz o mesmo que a folha (folha.py), mas em qualquer fundo: o saco fica pousado no fundo e
os marcadores à volta. Cada marcador é um quadrado de lado conhecido, por isso sozinho já dá
a homografia do plano (perspetiva e escala); os 4 juntos dão-na com mais precisão.

    1. Os marcadores 0 a 3 (dicionário 4x4_50), com cantos subpíxel. Podem estar em
       qualquer posição e rodados; têm de estar no plano do saco, um em cada canto.
    2. A homografia: a do maior marcador para começar; depois, alternadamente, a posição
       de cada marcador no plano (rodar e deslocar o quadrado de lado conhecido) e a
       homografia que leva os 16 cantos à foto. Erro final alto (marcador dobrado ou
       levantado) → SemSaco.
    3. Endireitar com o retângulo dos marcadores alinhado com os eixos e cortar só o que
       fica entre eles, menos uma margem: os marcadores não entram na imagem.

    python -m modulos.m2_localizacao.marcadores [ficheiro.png]
        gera a folha A4 para imprimir (300 ppp) com os 4 marcadores, para recortar.
"""

from __future__ import annotations

import sys

import cv2
import numpy as np

from nucleo import defines as D
from nucleo.config import Config
from nucleo.contrato import SemSaco

_IDS = (0, 1, 2, 3)


class SemMarcadores(SemSaco):
    """Nenhum dos marcadores na foto (com referencia = "auto", passa-se à folha)."""

_QUADRADO = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)  # cantos como o ArUco os dá


def retificar(img: np.ndarray, cfg: Config) -> np.ndarray:
    """O fundo entre os marcadores visto de cima, a cfg.geral.px_por_mm.

    Recebe:
        img: a foto original, BGR uint8.
        cfg: usa geral.px_por_mm, localizacao.marcadores_lado_mm e localizacao.referencia_margem_mm.
    Devolve:
        A imagem endireitada, orientada como na foto. Levanta SemSaco se faltar um
        marcador, se não estiverem no mesmo plano ou se não houver espaço entre eles.
    """
    p = cfg.localizacao
    cantos = detetar(img)
    homografia, mundo = _homografia(cantos, p.marcadores_lado_mm)

    # Eixos: os do retângulo formado pelos centros, com o x o mais perto possível do x da foto
    (_, _), (_, _), angulo = cv2.minAreaRect(mundo.mean(axis=1).astype(np.float32))
    centro = mundo.reshape(-1, 2).mean(axis=0)

    def rotacao(graus: float) -> np.ndarray:
        c, s = np.cos(np.radians(graus)), np.sin(np.radians(graus))
        return np.array([[c, s], [-s, c]])  # leva a direção (c, s) para o eixo x

    def direcao_na_foto(r: np.ndarray) -> float:
        a, b = _projetar(homografia, np.array([centro, centro + 10 * r[0]]))
        return (b - a)[0] / np.linalg.norm(b - a)

    r = max((rotacao(angulo + 90 * k) for k in range(4)), key=direcao_na_foto)
    rodados = mundo @ r.T

    # Só o que fica entre os marcadores: à direita dos 2 da esquerda, abaixo dos 2 de cima...
    por_x, por_y = np.argsort(rodados.mean(axis=1)[:, 0]), np.argsort(rodados.mean(axis=1)[:, 1])
    margem = p.referencia_margem_mm
    esquerda = rodados[por_x[:2], :, 0].max() + margem
    direita = rodados[por_x[2:], :, 0].min() - margem
    cima = rodados[por_y[:2], :, 1].max() + margem
    baixo = rodados[por_y[2:], :, 1].min() - margem
    if direita - esquerda < D.M2_MARCADORES_ESPACO_MIN_MM or baixo - cima < D.M2_MARCADORES_ESPACO_MIN_MM:
        raise SemSaco("os marcadores têm de estar um em cada canto, à volta do saco (não há espaço entre eles)")

    # Píxel q da imagem endireitada → mm no plano (rodado) → mm no plano dos marcadores → foto
    ppm = cfg.geral.px_por_mm
    para_plano = np.eye(3)
    para_plano[:2, :2] = r.T / ppm
    para_plano[:2, 2] = r.T @ (np.array([esquerda, cima]) + 0.5 / ppm)
    tamanho = (round((direita - esquerda) * ppm), round((baixo - cima) * ppm))
    return cv2.warpPerspective(img, homografia @ para_plano, tamanho,
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def detetar(img: np.ndarray) -> np.ndarray:
    """Os cantos (px, subpíxel) dos marcadores 0 a 3: array (4 marcadores, 4 cantos, 2)."""
    dicionario = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    parametros = cv2.aruco.DetectorParameters()
    parametros.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    cinza = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cantos, ids, _ = cv2.aruco.ArucoDetector(dicionario, parametros).detectMarkers(cinza)
    achados = {} if ids is None else {int(i): c.reshape(4, 2).astype(np.float64) for c, i in zip(cantos, ids.ravel())}
    faltam = [str(i) for i in _IDS if i not in achados]
    if len(faltam) == len(_IDS):
        raise SemMarcadores("nenhum marcador de referência na foto")
    if faltam:
        raise SemSaco(f"faltam os marcadores {', '.join(faltam)} (têm de aparecer os 4 inteiros, com a orla branca)")
    return np.array([achados[i] for i in _IDS])


def _homografia(cantos: np.ndarray, lado_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """(homografia mm → px, cantos de cada marcador no plano em mm) que melhor explicam os cantos na foto.

    Incógnitas: a homografia (8) e, para cada marcador menos o de referência, a rotação e a
    posição no plano (3). Ajuste de Levenberg-Marquardt ao erro dos 16 cantos na foto, em
    coordenadas normalizadas (foto centrada e ~1; plano em décimos de metro).
    """
    escala_mm = 100.0
    centro_px = cantos.reshape(-1, 2).mean(axis=0)
    escala_px = float(np.abs(cantos.reshape(-1, 2) - centro_px).mean())
    foto = (cantos - centro_px) / escala_px
    quadrado = _QUADRADO * lado_mm / escala_mm
    ref = int(np.argmax([cv2.contourArea(c.astype(np.float32)) for c in cantos]))  # o maior: o mais preciso
    outros = [k for k in range(len(cantos)) if k != ref]

    def plano(x: np.ndarray) -> np.ndarray:
        """Cantos de cada marcador no plano, para os parâmetros x."""
        mundo = np.repeat(quadrado[None], len(cantos), axis=0)
        for j, k in enumerate(outros):
            angulo, tx, ty = x[8 + 3 * j:11 + 3 * j]
            c, s = np.cos(angulo), np.sin(angulo)
            mundo[k] = quadrado @ np.array([[c, s], [-s, c]]) + (tx, ty)
        return mundo

    def residuos(x: np.ndarray) -> np.ndarray:
        return (_projetar(np.append(x[:8], 1.0).reshape(3, 3), plano(x)) - foto).ravel()

    # Ponto de partida: a homografia do marcador de referência e a posição dos outros vista por ela
    h = cv2.getPerspectiveTransform(quadrado.astype(np.float32), foto[ref].astype(np.float32))
    x = [*(h / h[2, 2]).ravel()[:8]]
    for k in outros:
        no_plano = _projetar(np.linalg.inv(h), foto[k])
        lado = no_plano[1] - no_plano[0]
        x += [np.arctan2(lado[1], lado[0]), *no_plano[0]]
    x = np.array(x)

    erro, amortecimento = residuos(x), 1e-3
    for _ in range(D.M2_MARCADORES_ITERACOES):
        jacobiana = np.empty((erro.size, x.size))
        for i in range(x.size):
            passo = np.zeros_like(x)
            passo[i] = 1e-7
            jacobiana[:, i] = (residuos(x + passo) - erro) / 1e-7
        normal, gradiente = jacobiana.T @ jacobiana, jacobiana.T @ erro
        while amortecimento < 1e8:
            novo = x - np.linalg.solve(normal + amortecimento * np.diag(np.diag(normal)), gradiente)
            erro_novo = residuos(novo)
            if erro_novo @ erro_novo < erro @ erro:
                x, erro, amortecimento = novo, erro_novo, amortecimento / 10
                break
            amortecimento *= 10
        else:
            break  # não melhora mais

    rms_px = float(np.sqrt(np.mean(erro.reshape(-1, 2) ** 2 * 2))) * escala_px
    if rms_px > D.M2_MARCADORES_ERRO_MAX_PX:
        raise SemSaco(f"os marcadores não estão no mesmo plano (erro de {rms_px:.1f} px): estão dobrados ou levantados?")
    # De volta a mm e px: px = centro + escala_px · H · (mm / escala_mm)
    para_px = np.array([[escala_px, 0, centro_px[0]], [0, escala_px, centro_px[1]], [0, 0, 1]])
    de_mm = np.diag([1 / escala_mm, 1 / escala_mm, 1])
    return para_px @ np.append(x[:8], 1.0).reshape(3, 3) @ de_mm, plano(x) * escala_mm


def _projetar(homografia: np.ndarray, pontos: np.ndarray) -> np.ndarray:
    return cv2.perspectiveTransform(pontos.reshape(-1, 1, 2).astype(np.float64), homografia).reshape(pontos.shape)


def folha_para_imprimir(lado_mm: float = D.LOCALIZACAO_MARCADORES_LADO_MM, ppp: int = 300) -> np.ndarray:
    """A4 ao alto com os 4 marcadores (um por quadrante), a linha de corte e uma régua de 100 mm."""
    px = lambda mm: round(mm * ppp / 25.4)  # noqa: E731
    folha = np.full((px(297), px(210)), 255, np.uint8)
    dicionario = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    lado, orla = px(lado_mm), px(10)  # a orla branca à volta é precisa para o detetar em fundos escuros
    for i in _IDS:
        cx, cy = px(105 / 2 + 105 * (i % 2)), px(80 + 110 * (i // 2))
        x, y = cx - lado // 2, cy - lado // 2
        folha[y:y + lado, x:x + lado] = cv2.aruco.generateImageMarker(dicionario, i, lado)
        cv2.rectangle(folha, (x - orla, y - orla), (x + lado + orla, y + lado + orla), 160, 2, cv2.LINE_8)
        cv2.putText(folha, str(i), (x + lado + orla + px(3), y + lado // 2), cv2.FONT_HERSHEY_SIMPLEX, 2, 0, 4)
    texto = [f"Marcadores ArUco 4x4, lado {lado_mm:g} mm. Imprimir a 100% (sem ajustar a pagina).",
             "Medir o lado com a regua: se nao der certo, por o valor medido em localizacao.marcadores_lado_mm.",
             "Recortar pela linha cinzenta (com a orla branca) e por um em cada canto, a volta do saco."]
    for k, linha in enumerate(texto):
        cv2.putText(folha, linha, (px(10), px(12 + 6 * k)), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    y = px(285)
    cv2.line(folha, (px(55), y), (px(155), y), 0, 3)
    for mm in range(0, 101, 10):
        cv2.line(folha, (px(55 + mm), y - px(3 if mm % 50 else 5)), (px(55 + mm), y), 0, 3)
    cv2.putText(folha, "100 mm", (px(160), y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, 0, 2)
    return folha


if __name__ == "__main__":
    from nucleo.imagens import guardar_imagem

    destino = sys.argv[1] if len(sys.argv) > 1 else "marcadores_aruco.png"
    guardar_imagem(destino, folha_para_imprimir())
    print(f"Folha para imprimir em {destino}")
