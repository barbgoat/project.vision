"""MESTRE: coordena os escravos e decide OK/NOK. Não analisa imagens.

Para cada saco (uma passagem do M1, com as suas melhores imagens):
    0. escolhe a imagem a analisar: a primeira candidata em que o M2 encontra
       o saco completo (inspecionar_passagem);
    1. pede ao M2 Localização o Saco;
    2. entrega o mesmo Saco, só de leitura, a cada escravo de inspeção ativo
       (M3 Dimensões, M4 Cor, M5 Relevo, M6 Manchas);
    3. protege cada chamada: um escravo que rebente fica com ERRO no seu
       relatório (com o ficheiro e a linha onde falhou) e os outros continuam;
    4. junta os relatórios e decide o estado do saco.

Regra da decisão:
    - algum módulo NOK                         → NOK
    - algum módulo ERRO (em_caso_de_erro =
      "rejeitar")                              → ERRO (o saco é rejeitado)
    - nenhum escravo de inspeção concluiu      → ERRO (não se aprova um saco
      (todos desligados ou por implementar)       que ninguém inspecionou)
    - caso contrário                           → OK
Um módulo POR_IMPLEMENTAR não conta para a decisão: permite desenvolver o
sistema um módulo de cada vez.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Iterable
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np

from modulos.m2_localizacao import Localizador
from modulos.m3_dimensoes import Dimensoes
from modulos.m4_cor import Cor
from modulos.m5_relevo import Relevo
from modulos.m6_manchas import Manchas
from nucleo.config import RAIZ, Config
from nucleo.contrato import Escravo, Estado, Passagem, Relatorio, Resultado, Saco, SemSaco

log = logging.getLogger("mestre")

# Escravos de inspeção, pela ordem em que são chamados. Para acrescentar um
# módulo novo (selagem, furos...): criar o ficheiro em modulos/, a secção em
# nucleo/config.py (com os valores em nucleo/defines.py) e juntá-lo aqui.
ESCRAVOS: tuple[type[Escravo], ...] = (Dimensoes, Cor, Relevo, Manchas)


class Mestre:
    """Coordena o M2 e os escravos de inspeção M3–M6."""

    def __init__(self, cfg: Config, apenas: Iterable[str] | None = None) -> None:
        """Prepara os escravos.

        Recebe:
            cfg: configuração.
            apenas: se dado, corre só estes escravos de inspeção (pelo nome),
                mesmo que estejam desligados na configuração; útil para testar
                um módulo sozinho. Por omissão corre os que têm "ativo": true.
        """
        self.cfg = cfg
        self.localizador = Localizador(cfg)
        if apenas is None:
            classes = [e for e in ESCRAVOS if getattr(cfg, e.nome).ativo]
        else:
            pedidos = set(apenas)
            desconhecidos = pedidos - {e.nome for e in ESCRAVOS}
            if desconhecidos:
                raise ValueError(f"Módulos desconhecidos: {sorted(desconhecidos)}; existem: {self.disponiveis()}")
            classes = [e for e in ESCRAVOS if e.nome in pedidos]
        self.escravos: list[Escravo] = [classe(cfg) for classe in classes]
        self._avisados: set[str] = set()

    @staticmethod
    def disponiveis() -> list[str]:
        """Nomes de todos os escravos de inspeção que existem."""
        return [e.nome for e in ESCRAVOS]

    @property
    def nomes(self) -> list[str]:
        """Nomes dos módulos que este Mestre executa, pela ordem: localizacao, ..."""
        return [Localizador.nome, *(e.nome for e in self.escravos)]

    # --- Inspeção ---------------------------------------------------------------------
    def inspecionar(self, img: np.ndarray, guardar_intermedios: bool = False) -> Resultado:
        """Inspeciona uma imagem: localiza o saco, distribui-o pelos escravos e decide.

        Recebe:
            img: imagem BGR uint8 (Captura.imagem). Passa a ser só de leitura.
            guardar_intermedios: pedir a cada escravo as imagens das suas etapas.
        Devolve:
            Resultado com o estado do saco e o relatório de cada módulo.
        """
        inicio = perf_counter()
        img.flags.writeable = False

        # 1. M2 Localização
        saco, rel_localizacao = self._executar(
            Localizador.nome, lambda: self.localizador.localizar(img, guardar_intermedios)
        )
        rel_localizacao.medidas = dict(self.localizador.medidas)
        rel_localizacao.intermedios = dict(self.localizador.intermedios)
        relatorios = [rel_localizacao]
        if rel_localizacao.estado is Estado.OK:
            if saco is None:
                rel_localizacao.estado = Estado.SEM_SACO
                rel_localizacao.mensagem = "nenhum saco completo na imagem"
            elif not isinstance(saco, Saco):
                self._violacao(rel_localizacao, f"devolveu {type(saco).__name__} em vez de Saco ou None")
        if rel_localizacao.estado is not Estado.OK:
            estado = Estado.SEM_SACO if rel_localizacao.estado is Estado.SEM_SACO else Estado.ERRO
            return Resultado(img, estado, None, relatorios, _ms_desde(inicio),
                             f"{Localizador.nome}: {rel_localizacao.mensagem}")
        _so_leitura(saco)
        rel_localizacao.medidas.update(
            comprimento_mm=saco.comprimento_mm,
            largura_mm=saco.largura_mm,
            angulo_graus=saco.angulo_graus,
        )

        # 2. Escravos de inspeção, cada um protegido
        for escravo in self.escravos:
            devolvido, execucao = self._executar(
                escravo.nome, lambda e=escravo: e.analisar(saco, guardar_intermedios)
            )
            if execucao.estado is Estado.OK:
                if isinstance(devolvido, Relatorio) and devolvido.modulo == escravo.nome:
                    devolvido.tempo_ms = execucao.tempo_ms
                    for defeito in devolvido.defeitos:
                        defeito.modulo = escravo.nome
                    execucao = devolvido
                else:
                    self._violacao(execucao, f"devolveu {devolvido!r:.80} em vez do seu Relatorio")
            relatorios.append(execucao)

        # 3. Decisão
        estado, mensagem = self._decidir(relatorios[1:])
        return Resultado(img, estado, saco, relatorios, _ms_desde(inicio), mensagem)

    # --- Passagem de um saco (várias imagens) ---------------------------------------------
    def inspecionar_passagem(self, passagem: Passagem, guardar_intermedios: bool = False) -> Resultado:
        """Inspeciona um saco a partir da sua passagem: UMA decisão por saco.

        Experimenta as imagens candidatas pela ordem (a mais promissora
        primeiro) até o M2 encontrar o saco completo numa delas, e analisa
        essa. Como o M2 desiste depressa quando o saco está cortado, tentar
        a seguinte custa pouco.

        Recebe:
            passagem: do M1 (disparo).
        Devolve:
            O Resultado da imagem analisada, com a passagem e a captura usadas.
            Uma passagem descartada (a análise não acompanhou a linha), ou em
            que nenhuma candidata tinha o saco completo, dá ERRO: um saco que
            passou e não foi inspecionado nunca é aprovado.
        """
        inicio = perf_counter()
        if passagem.descartada or not passagem.candidatas:
            mensagem = passagem.aviso or "passagem sem imagens"
            return Resultado(None, Estado.ERRO, None, [], _ms_desde(inicio), mensagem, passagem=passagem)

        resultado: Resultado | None = None
        for tentativa, captura in enumerate(passagem.candidatas, start=1):
            resultado = self.inspecionar(captura.imagem, guardar_intermedios)
            resultado.captura, resultado.imagens_analisadas = captura, tentativa
            if resultado.estado is not Estado.SEM_SACO:
                break
        assert resultado is not None
        resultado.passagem = passagem
        resultado.tempo_ms = _ms_desde(inicio)
        # Numa passagem (sensor/virtual) passou mesmo um saco: não o ter visto inteiro é um erro.
        if resultado.estado is Estado.SEM_SACO and passagem.modo != "foto":
            resultado.estado = Estado.ERRO
            resultado.mensagem = (f"nenhuma das {len(passagem.candidatas)} imagens candidatas tinha o saco "
                                  f"completo ({resultado.mensagem})")
        if passagem.aviso:
            resultado.mensagem = " | ".join(filter(None, [resultado.mensagem, f"disparo: {passagem.aviso}"]))
        return resultado

    # --- Proteção de cada chamada ------------------------------------------------------
    def _executar(self, nome: str, chamada: Callable[[], Any]) -> tuple[Any, Relatorio]:
        """Corre um escravo e devolve (o que ele devolveu, relatório da execução).

        O relatório da execução tem estado OK (correu), SEM_SACO (o M2 diz
        porquê), POR_IMPLEMENTAR ou ERRO, o tempo gasto e, nos três últimos,
        a explicação.
        """
        inicio = perf_counter()
        try:
            devolvido = chamada()
        except SemSaco as motivo:
            return None, Relatorio(nome, Estado.SEM_SACO, tempo_ms=_ms_desde(inicio), mensagem=str(motivo))
        except NotImplementedError as erro:
            onde = _onde(erro)
            if nome not in self._avisados:
                self._avisados.add(nome)
                log.warning("Módulo %s ainda por implementar (%s); não conta para a decisão.", nome, onde)
            return None, Relatorio(nome, Estado.POR_IMPLEMENTAR, tempo_ms=_ms_desde(inicio),
                                   mensagem=f"por implementar: {onde}")
        except Exception as erro:
            onde = _onde(erro)
            sitio = onde if onde == _FORA_DO_PROJETO else f"em {onde}"
            log.error("Módulo %s falhou (%s): %s: %s", nome, sitio, type(erro).__name__, erro)
            log.debug("Traceback completo:", exc_info=erro)
            return None, Relatorio(nome, Estado.ERRO, tempo_ms=_ms_desde(inicio),
                                   mensagem=f"{type(erro).__name__}: {erro} ({sitio})",
                                   detalhe="".join(traceback.format_exception(erro)))
        return devolvido, Relatorio(nome, Estado.OK, tempo_ms=_ms_desde(inicio))

    @staticmethod
    def _violacao(relatorio: Relatorio, problema: str) -> None:
        """Marca um módulo que não cumpriu o contrato."""
        relatorio.estado = Estado.ERRO
        relatorio.mensagem = f"contrato não cumprido: {problema}"
        log.error("Módulo %s: %s", relatorio.modulo, relatorio.mensagem)

    # --- Decisão ------------------------------------------------------------------------
    def _decidir(self, relatorios: list[Relatorio]) -> tuple[Estado, str]:
        """Junta os relatórios dos escravos de inspeção no estado final do saco."""
        com_erro = [r for r in relatorios if r.estado is Estado.ERRO]
        mensagem = " | ".join(f"{r.modulo}: {r.mensagem}" for r in com_erro)
        if any(r.estado is Estado.NOK for r in relatorios):
            return Estado.NOK, mensagem
        if com_erro and self.cfg.geral.em_caso_de_erro == "rejeitar":
            return Estado.ERRO, mensagem
        if not any(r.estado in (Estado.OK, Estado.NOK) for r in relatorios):
            return Estado.ERRO, mensagem or "nenhum escravo de inspeção concluiu a análise"
        return Estado.OK, mensagem


# --- Auxiliares ------------------------------------------------------------------------

_FORA_DO_PROJETO = "fora do código do projeto"


def _ms_desde(inicio: float) -> float:
    return (perf_counter() - inicio) * 1000


def _so_leitura(saco: Saco) -> None:
    """Bloqueia a escrita nos arrays do Saco antes de o entregar aos escravos."""
    for array in (saco.contorno, saco.matriz, saco.imagem, saco.mascara, saco.mascara_util):
        array.flags.writeable = False


def _onde(erro: BaseException) -> str:
    """Último ponto do código do projeto por onde a exceção passou: "ficheiro:linha (função)"."""
    quadros = [q for q in traceback.extract_tb(erro.__traceback__) if _no_projeto(q.filename)]
    if not quadros:
        return _FORA_DO_PROJETO
    q = quadros[-1]
    return f"{_relativo(q.filename)}:{q.lineno} ({q.name})"


def _no_projeto(caminho: str) -> bool:
    if caminho.startswith("<"):  # código gerado pelo Python (p.ex. "<string>" das dataclasses)
        return False
    try:
        relativo = _relativo(caminho)
    except ValueError:
        return False
    return not relativo.startswith((".venv", "mestre.py"))


def _relativo(caminho: str) -> str:
    return Path(caminho).resolve().relative_to(RAIZ).as_posix()
