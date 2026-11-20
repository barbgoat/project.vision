"""M5 Relevo: o ponto de entrada que o Mestre chama (Relevo.inspecionar).

    1. a zona a inspecionar: a mascara_util do M2 menos relevo.margem_mm (bordas e soldaduras)
    2. linhas finas e dobras largas, escuras e claras, que não são impressão (linhas.py)
    3. a impressão do produto, aprendida dos sacos OK (referencia.py, relevo.referencia_pasta): cada saco
       OK ensinado alinha-se com este (em qualquer posição, mesmo só com uma parte em comum); e as linhas
       que o administrador marcou como "não é ruga" noutras imagens, levadas da mesma maneira
    4. rugas: retas compridas feitas dessas linhas, atravessando o que está queimado, que se destacam
       das paralelas no resto do mesmo saco
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from modulos.m5_relevo import linhas, referencia
from nucleo.config import Config, ConfigRelevo
from nucleo.contrato import Captura, Defeito, Escravo, Relatorio, Saco, SemConfianca, TipoDefeito

TEXTURA_AVISO_PCT = 10.0  # a mensagem diz quanto do saco ficou por ver por ser textura, a partir disto
COBERTURA_AVISO_PCT = 90.0  # abaixo disto, a mensagem diz que os sacos OK ensinados não cobrem o saco todo
LARGURA_DESENHO_MM = 2.0  # largura da faixa que marca uma ruga na imagem anotada (a linha em si tem ~0,3 mm)
# Na etapa das rugas: uma ruga sozinha a vermelho; cada família de rugas paralelas com uma cor (BGR)
COR_SOZINHA = (0, 0, 255)
CORES_FAMILIA = ((255, 140, 0), (0, 140, 255), (255, 0, 255), (255, 255, 0), (180, 105, 255), (0, 200, 120))


def parametros(r: ConfigRelevo) -> dict:
    """Os parâmetros do detetor (linhas.PARAMETROS) com os da configuração por cima."""
    return {**linhas.PARAMETROS, "limiar": r.limiar, "ruga_min_mm": r.ruga_min_mm, "prova_min_mm": r.prova_min_mm,
            "prova_min_mm_familia": r.prova_min_mm_familia, "ruga_min_mm_familia": r.ruga_min_mm_familia,
            "desvio_min_familia": r.desvio_min_familia}


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
                cada família de rugas paralelas com uma cor, os candidatos a amarelo, a impressão
                descontada a azul e as faixas "não é ruga" a verde),
                "resposta", "linhas", "impressao" e "candidatos".

        Devolve:
            Relatorio NOK com um Defeito RUGAS por ruga (contorno: uma faixa ao longo dela), OK sem
            nenhuma. Medidas: rugas, ruga_prova_max_mm, queimado_pct, referencia_sacos (os sacos OK que
            alinharam), referencia_alinhamento (a correlação deles, ou a melhor se nenhum alinhou),
            referencia_cobertura_pct (a parte do saco que eles veem), familias (as famílias de 2 ou mais
            rugas paralelas), familia_rugas_max, rugas_familia (as achadas pela procura da família), referencia_nao_rugas (as imagens com
            linhas "não é ruga" que alinharam) e rugas_nao_ruga (as retas que não contaram por estarem sobre
            uma dessas linhas), as duas só se a referência as tiver, e, com a textura ligada, textura_pct.
            A mensagem diz se a impressão não foi descontada, ou só em parte do saco, e cada família
            ("família de 3 rugas a +12° (espaçamento 8–15 mm)").

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

        ref = referencia.carregar(r.referencia_pasta, r.margem_mm, ppm, p)
        det = linhas.detetar(prep, ref, p, imagens=guardar_intermedios)

        defeitos = [_defeito(achado, saco, ppm, r) for achado in det.achados]
        medidas = {"rugas": float(len(det.achados)), "ruga_prova_max_mm": det.prova_mm,
                   "queimado_pct": queimado_pct, "referencia_sacos": float(det.vistas),
                   "familias": float(len(det.familias)),
                   "familia_rugas_max": float(max((len(f.rugas) for f in det.familias), default=0)),
                   "rugas_familia": float(sum(a.da_familia for a in det.achados))}
        if p["textura_coerencia_max"] > 0:
            medidas["textura_pct"] = det.textura_pct
        if not math.isnan(det.alinhamento) and det.alinhamento > -1:
            medidas["referencia_alinhamento"] = det.alinhamento
        if det.vistas:
            medidas["referencia_cobertura_pct"] = det.cobertura_pct
        if ref is not None and ref.nao_rugas:
            medidas["referencia_nao_rugas"] = float(det.nao_rugas)
            medidas["rugas_nao_ruga"] = float(det.nao_sao)
        intermedios = _intermedios(saco, det, prep) if guardar_intermedios else {}
        aviso = _aviso_referencia(r, ref, det)
        if det.textura_pct >= TEXTURA_AVISO_PCT:
            aviso = " | ".join(filter(None, [aviso, f"{det.textura_pct:.0f} % do saco é textura (fotografia, letras): "
                                                    "aí não se procuram rugas"]))
        aviso = " | ".join(filter(None, [aviso] + [f.texto() for f in det.familias]))
        return Relatorio.concluir(self.nome, defeitos, medidas, intermedios, aviso)


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
    destaque = f"; destaca-se {a.desvio:.1f}x do resto do saco" if math.isfinite(a.desvio) else ""
    if math.isfinite(a.desvio_local):
        destaque += f" e {a.desvio_local:.1f}x da zona à volta"
    if a.familia:
        destaque += f"; família {a.familia}" + (", achada pela família" if a.da_familia else "")
    return Defeito(
        TipoDefeito.RUGAS, x_mm, y_mm,
        comprimento_mm=a.comprimento_mm,
        angulo_graus=angulo,
        intensidade=a.prova_mm,
        severidade=a.prova_mm / r.prova_min_mm,
        contorno=saco.px_para_mm(faixa, ppm),
        descricao=f"ruga de {a.comprimento_mm:.0f} mm a {angulo:+.0f} graus (traço visível {a.prova_mm:.0f} mm{destaque})",
    )


def _aviso_referencia(r: ConfigRelevo, ref: linhas.Referencia | None, det: linhas.Deteccao) -> str:
    """Porque é que a impressão não foi descontada, ou só em parte do saco (aí pode passar por ruga)."""
    if det.impressao_usada:
        if det.cobertura_pct >= COBERTURA_AVISO_PCT:
            return ""
        return (f"os sacos OK da referência só cobrem {det.cobertura_pct:.0f} % deste (viam outra parte dele): no "
                "resto a impressão pode passar por ruga; ensinar um saco OK nesta posição")
    if not r.referencia_pasta:
        return ("sem referência de sacos OK (relevo.referencia_pasta): a tinta é descontada, mas outras marcas "
                "impressas (traços claros, contornos finos) podem passar por ruga")
    if ref is None or not ref.n:
        return f"a referência ({r.referencia_pasta}) não tem sacos OK: a impressão pode passar por ruga"
    correlacao = f" (correlação {det.alinhamento:.2f})" if det.alinhamento > -1 else ""
    return (f"nenhum saco OK da referência se parece com este{correlacao}: outro produto ou a outra face? "
            "a impressão pode passar por ruga")


def _intermedios(saco: Saco, det: linhas.Deteccao, prep: linhas.Preparado) -> dict[str, np.ndarray]:
    ver = saco.imagem.copy()
    candidatos = det.imagens["candidatos"]
    amarelos = np.all(candidatos == (0, 255, 255), axis=2)
    nao_ruga = det.imagens["nao_ruga"] & prep.util
    impressao = det.imagens["impressao"] & prep.util & ~nao_ruga
    ver[impressao] = (ver[impressao] * 0.5 + np.array((160, 60, 0)) * 0.5).astype(np.uint8)
    ver[nao_ruga] = (ver[nao_ruga] * 0.5 + np.array((40, 160, 40)) * 0.5).astype(np.uint8)  # "não é ruga": verde
    ver[amarelos] = (0, 255, 255)
    espessura = max(2, round(prep.ppm * 0.5))
    for a in det.achados:
        p1, p2 = (tuple(int(round(v)) for v in np.asarray(p)[:2]) for p in (a.p1, a.p2))
        cor = CORES_FAMILIA[(a.familia - 1) % len(CORES_FAMILIA)] if a.familia else COR_SOZINHA
        cv2.line(ver, p1, p2, cor, espessura, cv2.LINE_AA)
    return {"rugas": ver, "resposta": det.imagens["resposta"], "linhas": det.imagens["linhas"],
            "impressao": det.imagens["impressao"], "candidatos": candidatos}
