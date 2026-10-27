"""M7 Saída, depuração: ver o que aconteceu em cada etapa, para perceber onde está o erro.

Para cada saco depurado cria uma pasta, p.ex. depuracao/saco_0012_canto_dobrado_NOK/:

    painel.jpg              TUDO numa só imagem: as etapas lado a lado e o resumo por baixo
    00_original.png         a imagem analisada, sem alterações (PNG, sem perdas): para repetir o caso
    01_anotada.jpg          a imagem com o contorno, os defeitos numerados e o estado de cada módulo
    02_disparo_candidata_1.jpg ...   as imagens candidatas da passagem (modos sensor/virtual)
    0N_<módulo>_<etapa>.png as imagens de cada etapa de cada módulo, pela ordem do Mestre
                            (localizacao_diferenca, localizacao_segmentacao, ...,
                            dimensoes_forma; o M5 e o M6 juntam-se sozinhos)
    resultado.json          estados, medidas, limiares usados, defeitos, tempos, mensagens,
                            traceback completo dos erros e os valores que o config.json muda

Os módulos não sabem nada disto: basta porem as imagens das suas etapas em
Relatorio.intermedios (o contrato). A depuração nunca interrompe a inspeção:
se falhar, regista o erro no log e o programa continua.

Gasta tempo e disco: usar em ensaios ("nok" ou "todas"); na linha, "nenhuma".
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from modulos.m7_saida.desenho import desenhar
from nucleo.config import Config, GuardarImagens
from nucleo.contrato import Estado, Passagem, Resultado
from nucleo.imagens import guardar_imagem, para_ver, texto_ascii

log = logging.getLogger(__name__)

_FUNDO = (40, 40, 40)  # cinzento escuro do painel
_TITULO_PX = 26  # altura da barra de título de cada etapa no painel
_LINHA_PX = 22  # altura de cada linha de texto do resumo


def nome_curto(origem: str) -> str:
    """Parte da origem que serve para nome de ficheiro, p.ex. "saco_007" ou "webcam0_17"."""
    return re.sub(r"[^\w\-]+", "_", Path(origem).stem)[:60] or "imagem"


class Depuracao:
    """Grava a pasta de depuração dos sacos pedidos ("nok": NOK, ERRO e SEM_SACO; "todas")."""

    def __init__(self, cfg: Config, pasta: str | Path, modo: GuardarImagens = "nok") -> None:
        self.cfg = cfg
        self.pasta = Path(pasta)
        self.modo = modo
        self._contador = 0

    def deve_depurar(self, estado: Estado) -> bool:
        return self.modo == "todas" or (self.modo == "nok" and estado is not Estado.OK)

    def processar(self, resultado: Resultado, numero: int | None = None) -> Path | None:
        """Grava a pasta de depuração do saco, se for caso disso. Devolve a pasta, ou None.

        numero: o nº do saco nesta execução (omissão: conta os sacos que recebe).
        """
        self._contador = numero if numero is not None else self._contador + 1
        if not self.deve_depurar(resultado.estado):
            return None
        try:
            return self._gravar(resultado, self._contador)
        except Exception:
            log.exception("Depuração do saco %d falhou (a inspeção continua).", self._contador)
            return None

    # --- Gravação ------------------------------------------------------------------
    def _gravar(self, resultado: Resultado, numero: int) -> Path:
        origem = resultado.captura.origem if resultado.captura else (
            f"passagem_{resultado.passagem.numero}" if resultado.passagem else "imagem")
        pasta = self.pasta / f"saco_{numero:04d}_{nome_curto(origem)}_{resultado.estado}"
        etapas = self.etapas(resultado)
        for i, (nome, imagem) in enumerate(etapas):
            if nome == "original":  # sem perdas, para repetir o caso tal e qual
                guardar_imagem(pasta / f"{i:02d}_{nome}.png", imagem, [cv2.IMWRITE_PNG_COMPRESSION, 1])
            elif nome == "anotada" or nome.startswith("disparo_"):
                guardar_imagem(pasta / f"{i:02d}_{nome}.jpg", imagem, [cv2.IMWRITE_JPEG_QUALITY, 90])
            else:
                guardar_imagem(pasta / f"{i:02d}_{nome}.png", imagem)
        guardar_imagem(pasta / "painel.jpg", self.painel(etapas, resultado, numero), [cv2.IMWRITE_JPEG_QUALITY, 90])
        texto = json.dumps(self.resumo(resultado, numero), indent=2, ensure_ascii=False)
        (pasta / "resultado.json").write_text(texto + "\n", encoding="utf-8")
        return pasta

    def etapas(self, resultado: Resultado) -> list[tuple[str, np.ndarray]]:
        """As imagens de cada etapa, pela ordem do pipeline, já prontas a ver (BGR uint8)."""
        etapas: list[tuple[str, np.ndarray]] = []
        if resultado.imagem is not None:
            etapas.append(("original", resultado.imagem))
            try:
                etapas.append(("anotada", desenhar(resultado, self.cfg.geral.px_por_mm)))
            except Exception:
                log.exception("Depuração: o desenho falhou.")
        passagem = resultado.passagem
        if passagem is not None and passagem.modo != "foto":
            for i, captura in enumerate(passagem.candidatas, start=1):
                etapas.append((f"disparo_candidata_{i}", captura.imagem))
        for relatorio in resultado.relatorios:
            for nome, imagem in relatorio.intermedios.items():
                etapas.append((f"{relatorio.modulo}_{nome}", para_ver(imagem)))
        return etapas

    # --- Painel ------------------------------------------------------------------------
    def painel(self, etapas: list[tuple[str, np.ndarray]], resultado: Resultado, numero: int) -> np.ndarray:
        """Uma só imagem: as etapas em grelha (com título) e o resumo do saco por baixo."""
        colunas = self.cfg.saida.painel_colunas
        largura = self.cfg.saida.painel_largura_etapa_px
        altura = round(largura * 0.75)
        quadros = [self._quadro(self._titulo(nome, resultado), imagem, largura, altura)
                   for nome, imagem in etapas if nome != "original"]
        if not quadros:
            quadros = [self._quadro("sem imagens", np.zeros((10, 10, 3), np.uint8), largura, altura)]
        while len(quadros) % colunas:
            quadros.append(np.full_like(quadros[0], _FUNDO))
        grelha = np.vstack([np.hstack(quadros[i:i + colunas]) for i in range(0, len(quadros), colunas)])
        return np.vstack([grelha, self._texto_resumo(resultado, numero, grelha.shape[1])])

    def _titulo(self, nome: str, resultado: Resultado) -> str:
        if nome.startswith("disparo_candidata_"):
            i = int(nome.rsplit("_", 1)[1])
            p = resultado.passagem
            texto = f"disparo: candidata {i} ({Passagem.texto_pontuacao(p.pontuacoes[i - 1])})"
            return texto + (" <- analisada" if i == resultado.imagens_analisadas else "")
        return nome.replace("_", " ", 1) if nome != "anotada" else "anotada (resultado final)"

    @staticmethod
    def _quadro(titulo: str, imagem: np.ndarray, largura: int, altura: int) -> np.ndarray:
        """A imagem reduzida para caber no quadro, com uma barra de título por cima."""
        quadro = np.full((altura + _TITULO_PX, largura, 3), _FUNDO, np.uint8)
        a, l = imagem.shape[:2]
        f = min(largura / l, altura / a)
        reduzida = cv2.resize(imagem, (max(1, round(l * f)), max(1, round(a * f))), interpolation=cv2.INTER_AREA)
        y0 = _TITULO_PX + (altura - reduzida.shape[0]) // 2
        x0 = (largura - reduzida.shape[1]) // 2
        quadro[y0:y0 + reduzida.shape[0], x0:x0 + reduzida.shape[1]] = reduzida
        cv2.putText(quadro, texto_ascii(titulo)[: largura // 9], (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.rectangle(quadro, (0, 0), (largura - 1, altura + _TITULO_PX - 1), (90, 90, 90), 1)
        return quadro

    def _texto_resumo(self, resultado: Resultado, numero: int, largura: int) -> np.ndarray:
        linhas = self.linhas_resumo(resultado, numero)
        bloco = np.full((_LINHA_PX * len(linhas) + 10, largura, 3), _FUNDO, np.uint8)
        max_caracteres = largura // 9
        for i, linha in enumerate(linhas):
            cor = (0, 0, 255) if "ERRO" in linha else (255, 255, 255)
            cv2.putText(bloco, texto_ascii(linha)[:max_caracteres], (8, _LINHA_PX * (i + 1)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, cor, 1, cv2.LINE_AA)
        return bloco

    def linhas_resumo(self, resultado: Resultado, numero: int) -> list[str]:
        """O resumo do saco em texto: estado, passagem, cada módulo, cada defeito."""
        origem = resultado.captura.origem if resultado.captura else ""
        linhas = [f"saco {numero} | {resultado.estado} | {resultado.tempo_ms:.0f} ms | {Path(origem).name}"]
        if resultado.mensagem:
            linhas.append(f"   {resultado.mensagem}")
        p = resultado.passagem
        if p is not None and p.modo != "foto":
            linhas.append(f"disparo ({p.modo}): passagem {p.numero}, {p.num_imagens} imagens em {p.duracao_ms:.0f} ms; "
                          f"analisada a candidata {resultado.imagens_analisadas}" + (f"; AVISO: {p.aviso}" if p.aviso else ""))
        for r in resultado.relatorios:
            medidas = ", ".join(f"{k}={v:.2f}" for k, v in list(r.medidas.items())[:6])
            linha = f"{r.modulo}: {r.resumo} em {r.tempo_ms:.1f} ms"
            if r.mensagem:
                linha += f" - {r.mensagem}"
            elif medidas:
                linha += f" | {medidas}"
            linhas.append(linha)
        linhas += [f"   {n}. {d.tipo}: {d.descricao}" for n, d in enumerate(resultado.defeitos, start=1)]
        return linhas

    # --- JSON --------------------------------------------------------------------------
    def resumo(self, resultado: Resultado, numero: int) -> dict:
        """Tudo o que se sabe sobre o saco, para o resultado.json."""
        captura, passagem, saco = resultado.captura, resultado.passagem, resultado.saco
        return {
            "saco": numero,
            "estado": str(resultado.estado),
            "mensagem": resultado.mensagem,
            "tempo_ms": round(resultado.tempo_ms, 2),
            "origem": captura.origem if captura else None,
            "data_hora": datetime.fromtimestamp(captura.instante).isoformat(timespec="milliseconds") if captura else None,
            "passagem": None if passagem is None else {
                "numero": passagem.numero,
                "modo": passagem.modo,
                "num_imagens": passagem.num_imagens,
                "duracao_ms": round(passagem.duracao_ms, 1),
                "aviso": passagem.aviso,
                "candidatas": [{"origem": c.origem, "posicao": Passagem.texto_pontuacao(p)}
                               for c, p in zip(passagem.candidatas, passagem.pontuacoes)],
                "imagens_analisadas": resultado.imagens_analisadas,
            },
            "saco_medido": None if saco is None else {
                "comprimento_mm": round(saco.comprimento_mm, 2),
                "largura_mm": round(saco.largura_mm, 2),
                "angulo_graus": round(saco.angulo_graus, 2),
                "centro_px": [round(c, 1) for c in saco.centro_px],
            },
            "modulos": [{
                "modulo": r.modulo,
                "estado": str(r.estado),
                "tempo_ms": round(r.tempo_ms, 2),
                "mensagem": r.mensagem,
                "medidas": {k: _numero_json(v) for k, v in r.medidas.items()},
                "defeitos": [{
                    "tipo": str(d.tipo), "descricao": d.descricao,
                    "x_mm": round(d.x_mm, 2), "y_mm": round(d.y_mm, 2),
                    "comprimento_mm": round(d.comprimento_mm, 2), "largura_mm": round(d.largura_mm, 2),
                    "area_mm2": round(d.area_mm2, 2), "angulo_graus": round(d.angulo_graus, 2),
                    "intensidade": _numero_json(d.intensidade),
                } for d in r.defeitos],
                "traceback": r.detalhe or None,
            } for r in resultado.relatorios],
            "configuracao_alterada": self.cfg.descrever_alteracoes(),
        }


def _numero_json(valor: float) -> float | str:
    """Números para JSON: arredondados, e inf/nan como texto (o JSON não os aceita)."""
    valor = float(valor)
    return round(valor, 4) if math.isfinite(valor) else str(valor)
