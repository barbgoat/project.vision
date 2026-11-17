"""M5 Relevo: o ponto de entrada que o Mestre chama (Relevo.inspecionar).

    1. a zona a inspecionar: a mascara_util do M2 menos relevo.margem_mm (bordas e soldaduras)
    2. linhas finas, escuras e claras, que não são impressão (linhas.py)
    3. a impressão do produto, aprendida dos sacos OK (referencia.py, relevo.referencia_pasta)
    4. rugas: retas compridas feitas dessas linhas, atravessando o que está queimado
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from modulos.m5_relevo import linhas, referencia
from nucleo.config import Config, ConfigRelevo
from nucleo.contrato import Captura, Defeito, Escravo, Relatorio, Saco, SemConfianca, TipoDefeito

LARGURA_DESENHO_MM = 2.0  # largura da faixa que marca uma ruga na imagem anotada (a linha em si tem ~0,3 mm)


def parametros(r: ConfigRelevo) -> dict:
    """Os parâmetros do detetor (linhas.PARAMETROS) com os da configuração por cima."""
    return {**linhas.PARAMETROS, "limiar": r.limiar, "ruga_min_mm": r.ruga_min_mm, "prova_min_mm": r.prova_min_mm}


class Relevo(Escravo):
    """O escravo M5. Usa a secção relevo da configuração; a referência lê-se dos ficheiros (em memória
    enquanto não mudarem)."""

    nome = "relevo"
    pergunta = "O saco tem rugas (vincos de lado a lado)?"

    def inspecionar(self, saco: Saco, captura: Captura, cfg: Config, guardar_intermedios: bool = False) -> Relatorio:
        """Procura rugas no recorte normalizado do saco.

        Recebe:
            saco: o Saco do M2 (só de leitura): o recorte (imagem) e a zona a inspecionar (mascara_util).
            captura: não usada (o recorte já é da imagem analisada).
            cfg: usa relevo e geral.px_por_mm.
            guardar_intermedios: se True, devolve as imagens "rugas" (o recorte com as rugas a vermelho,
                os candidatos a amarelo e a impressão descontada a azul), "resposta", "linhas" e "impressao".

        Devolve:
            Relatorio NOK com um Defeito RUGAS por ruga (contorno: uma faixa ao longo dela), OK sem
            nenhuma. Medidas: rugas, ruga_prova_max_mm, queimado_pct, referencia_sacos e, se a
            referência alinhou, referencia_alinhamento. A mensagem diz se a impressão não foi descontada.

        Levanta:
            SemConfianca se a zona a inspecionar estiver vazia ou mais queimada (saturada) do que
            relevo.queimado_max_pct: aí as rugas não se veem.
        """
        r, ppm = cfg.relevo, cfg.geral.px_por_mm
        p = parametros(r)
        k = 2 * int(round(r.margem_mm * ppm)) + 1
        util = cv2.erode(saco.mascara_util, np.ones((k, k), np.uint8)) > 0
        area = int(util.sum())
        if area == 0:
            raise SemConfianca("relevo: a zona a inspecionar está vazia (saco pequeno para relevo.margem_mm?)")
        cinza = cv2.cvtColor(saco.imagem, cv2.COLOR_BGR2GRAY).astype(np.float32)
        prep = linhas.preparar(cinza, util, ppm, p)
        queimado_pct = 100 * float(prep.queimado.sum()) / area
        if queimado_pct > r.queimado_max_pct:
            raise SemConfianca(f"relevo: {queimado_pct:.0f} % do saco está queimado (saturado), máximo "
                               f"{r.queimado_max_pct:g} %: as rugas não se veem (baixar a exposição ou a luz)")

        grupos = referencia.carregar(r.referencia_pasta, r.margem_mm, ppm, p)
        grupo = referencia.escolher(grupos, prep)
        det = linhas.detetar(prep, grupo.referencia if grupo else None, p, imagens=guardar_intermedios)

        defeitos = [_defeito(achado, saco, ppm, r) for achado in det.achados]
        medidas = {"rugas": float(len(det.achados)), "ruga_prova_max_mm": det.prova_mm,
                   "queimado_pct": queimado_pct,
                   "referencia_sacos": float(grupo.referencia.n if grupo and det.impressao_usada else 0)}
        if grupo is not None and not math.isnan(det.alinhamento):
            medidas["referencia_alinhamento"] = det.alinhamento
        intermedios = _intermedios(saco, det, prep) if guardar_intermedios else {}
        return Relatorio.concluir(self.nome, defeitos, medidas, intermedios, _aviso_referencia(r, grupos, grupo, det))


def _defeito(a: linhas.Achado, saco: Saco, ppm: float, r: ConfigRelevo) -> Defeito:
    """Uma ruga: posição, comprimento e ângulo no referencial do saco; o contorno é uma faixa ao longo dela."""
    p1, p2 = np.asarray(a.p1, float)[:2], np.asarray(a.p2, float)[:2]
    direcao = p2 - p1
    direcao /= max(float(np.hypot(*direcao)), 1e-9)
    normal = np.array([-direcao[1], direcao[0]]) * LARGURA_DESENHO_MM / 2 * ppm
    faixa = np.array([p1 + normal, p2 + normal, p2 - normal, p1 - normal])
    angulo = math.degrees(math.atan2(direcao[1], direcao[0]))
    angulo = (angulo + 90) % 180 - 90  # uma reta não tem sentido: entre -90 e 90 graus
    x_mm, y_mm = saco.posicao_mm(*((p1 + p2) / 2), ppm)
    return Defeito(
        TipoDefeito.RUGAS, x_mm, y_mm,
        comprimento_mm=a.comprimento_mm,
        angulo_graus=angulo,
        intensidade=a.prova_mm,
        severidade=a.prova_mm / r.prova_min_mm,
        contorno=saco.px_para_mm(faixa, ppm),
        descricao=f"ruga de {a.comprimento_mm:.0f} mm a {angulo:+.0f} graus (traço visível {a.prova_mm:.0f} mm)",
    )


def _aviso_referencia(r: ConfigRelevo, grupos: tuple, grupo, det: linhas.Deteccao) -> str:
    """Porque é que a impressão não foi descontada (a impressão pode então passar por ruga)."""
    if det.impressao_usada:
        return ""
    if not r.referencia_pasta:
        return ("sem referência de sacos OK (relevo.referencia_pasta): a tinta é descontada, mas outras marcas "
                "impressas (traços claros, contornos finos) podem passar por ruga")
    if not grupos:
        return f"a referência ({r.referencia_pasta}) não tem sacos OK: a impressão pode passar por ruga"
    if grupo is None:
        return "nenhum saco OK da referência tem o tamanho deste: a impressão pode passar por ruga"
    return (f"este saco não alinha com os sacos OK da referência (correlação {det.alinhamento:.2f}): "
            "outro produto ou a outra face? a impressão pode passar por ruga")


def _intermedios(saco: Saco, det: linhas.Deteccao, prep: linhas.Preparado) -> dict[str, np.ndarray]:
    ver = saco.imagem.copy()
    candidatos = det.imagens["candidatos"]
    amarelos = np.all(candidatos == (0, 255, 255), axis=2)
    impressao = det.imagens["impressao"] & prep.util
    ver[impressao] = (ver[impressao] * 0.5 + np.array((160, 60, 0)) * 0.5).astype(np.uint8)
    ver[amarelos] = (0, 255, 255)
    espessura = max(2, round(prep.ppm * 0.5))
    for a in det.achados:
        p1, p2 = (tuple(int(round(v)) for v in np.asarray(p)[:2]) for p in (a.p1, a.p2))
        cv2.line(ver, p1, p2, (0, 0, 255), espessura, cv2.LINE_AA)
    return {"rugas": ver, "resposta": det.imagens["resposta"], "linhas": det.imagens["linhas"],
            "impressao": det.imagens["impressao"], "candidatos": candidatos}
