"""Contrato entre o Mestre e os Escravos: o que circula entre eles.

    Captura    M1 (fonte)     → M1 (disparo)    uma imagem e a sua origem
    Passagem   M1 Aquisição   → Mestre          as melhores imagens de um saco, da entrada à saída
    Saco       M2 Localização → Mestre → M3–M6  o saco encontrado e normalizado (só de leitura)
    Relatorio  cada módulo    → Mestre          o que o módulo concluiu
    Resultado  Mestre         → M7 Saída        a decisão final sobre o saco

Os escravos de inspeção (M3 a M6) derivam de Escravo e implementam um só
método: analisar(saco) -> Relatorio. O Mestre não sabe como cada um trabalha;
só conhece este contrato.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import math
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

import numpy as np

from nucleo.config import Config


class Estado(StrEnum):
    """Estado de um módulo (Relatorio) ou do saco (Resultado)."""

    OK = "OK"  # módulo: nada a apontar; saco: aprovado
    NOK = "NOK"  # módulo: encontrou defeitos; saco: rejeitado por defeito
    ERRO = "ERRO"  # o módulo falhou (ver Relatorio.mensagem); saco: rejeitado por precaução
    POR_IMPLEMENTAR = "POR_IMPLEMENTAR"  # o módulo ainda é um esqueleto; não conta para a decisão
    SEM_SACO = "SEM_SACO"  # não havia um saco completo na imagem


class TipoDefeito(StrEnum):
    """Tipos de defeito e o módulo responsável. Os valores são também pastas do dataset."""

    DIMENSAO = "dimensao"  # M3: tamanho fora da tolerância ou forma irregular
    COR = "cor"  # M4: cor diferente da referência
    RISCO = "risco"  # M5: linha fina e comprida
    DOBRA = "dobra"  # M5: vinco, linha larga e comprida
    RUGAS = "rugas"  # M5: zona com várias linhas paralelas
    AMASSO = "amasso"  # M5: zona com várias linhas em direções caóticas
    MANCHA = "mancha"  # M6: zona mais clara ou mais escura do que a vizinhança


@dataclass
class Captura:
    """Uma imagem adquirida pelo M1 e a identificação da sua origem."""

    imagem: np.ndarray  # BGR uint8 (altura × largura × 3)
    origem: str  # caminho do ficheiro, "linha.mp4#17", "spinnaker:<nº de série>#<FrameID>"
    instante: float  # momento da aquisição (time.time(), em s)
    # Estado do sensor de presença no momento da exposição (gravado pela câmara
    # em cada imagem); None se a fonte não tem sensor (pasta, vídeo, webcam).
    sensor: bool | None = None


@dataclass
class Passagem:
    """A passagem de UM saco pela câmara: as suas melhores imagens, da entrada à saída.

    O M1 (disparo) junta as imagens em passagens; o Mestre analisa as
    candidatas, pela ordem, até uma ter o saco completo, e dá uma só decisão.
    """

    numero: int  # 1, 2, 3... desde o arranque
    candidatas: list[Captura]  # as melhores imagens, da mais promissora para a menos
    pontuacoes: list[float | None]  # para cada candidata: distância do saco ao centro (0 = centrado; inf = cortado)
    num_imagens: int  # quantas imagens a passagem teve ao todo
    inicio: float  # instante da primeira imagem (s)
    fim: float  # instante da última imagem (s)
    modo: str  # o modo de disparo que a criou: "foto", "sensor" ou "virtual"
    aviso: str = ""  # algo de anormal, p.ex. "passagem demasiado longa (sacos encostados?)"
    descartada: bool = False  # True se não pôde ser analisada (a análise está atrasada face à linha)

    @property
    def duracao_ms(self) -> float:
        return (self.fim - self.inicio) * 1000

    @staticmethod
    def texto_pontuacao(pontuacao: float | None) -> str:
        """Pontuação legível: "centrado a 4 %", "cortado pela borda" ou "sem posição"."""
        if pontuacao is None:
            return "sem posição"
        return "cortado pela borda" if math.isinf(pontuacao) else f"a {pontuacao:.0%} do centro"


@dataclass(frozen=True)
class Saco:
    """O saco encontrado pelo M2, já normalizado. É o que todos os escravos de inspeção recebem.

    É imutável: o Mestre bloqueia também a escrita nos arrays antes de o
    distribuir. Um escravo que precise de alterar uma imagem faz uma cópia;
    se tentar escrever na original, o erro aparece nesse escravo e não
    estraga a análise dos outros.

    Referenciais:
        - imagem original: píxeis da Captura;
        - recorte normalizado (imagem, mascara, mascara_util): eixo maior do
          saco na horizontal e centro do saco no centro do recorte, à mesma
          escala (px_por_mm) da imagem original;
        - saco, em mm: origem no centro do saco, x ao longo do eixo maior e y
          ao longo do menor. As posições dos defeitos são dadas neste referencial.
    O sentido do saco (qual das pontas fica à esquerda) não é resolvido na v1.
    """

    contorno: np.ndarray  # contorno na imagem original (N×1×2, int32, px)
    centro_px: tuple[float, float]  # centro (x, y) na imagem original
    angulo_graus: float  # ângulo do eixo maior na imagem original (o recorte desfaz esta rotação)
    comprimento_mm: float  # dimensão ao longo do eixo maior
    largura_mm: float  # dimensão ao longo do eixo menor
    matriz: np.ndarray  # transformação afim 2×3 (float64): imagem original -> recorte
    imagem: np.ndarray  # recorte normalizado (BGR uint8)
    mascara: np.ndarray  # saco no recorte (uint8, 0/255)
    mascara_util: np.ndarray  # zona a inspecionar: mascara sem as margens (uint8, 0/255)

    def posicao_mm(self, u: float, v: float, px_por_mm: float) -> tuple[float, float]:
        """Posição em mm, no referencial do saco, do ponto (u, v) do recorte normalizado.

        O centro do saco está no centro do recorte, ((largura − 1)/2, (altura − 1)/2);
        x cresce ao longo do eixo maior e y ao longo do menor.
        """
        altura, largura = self.mascara.shape
        return (u - (largura - 1) / 2) / px_por_mm, (v - (altura - 1) / 2) / px_por_mm


class SemSaco(Exception):
    """Levantada pelo M2 quando a imagem não tem um saco completo; a mensagem diz porquê.

    O Mestre trata-a como estado SEM_SACO (não é um erro do módulo), p.ex.
    raise SemSaco("o saco toca na borda da imagem (incompleto)").
    """


@dataclass
class Defeito:
    """Um defeito encontrado num saco. Medidas em mm, no referencial do saco."""

    tipo: TipoDefeito
    x_mm: float = 0.0  # centro do defeito, ao longo do eixo maior, a partir do centro do saco
    y_mm: float = 0.0  # centro do defeito, ao longo do eixo menor
    comprimento_mm: float = 0.0  # linha: ao longo da curva; zona/mancha: lado maior do retângulo envolvente
    largura_mm: float = 0.0  # linha: largura da crista; zona/mancha: lado menor do retângulo envolvente
    area_mm2: float = 0.0  # área afetada
    angulo_graus: float = 0.0  # orientação em relação ao eixo maior do saco
    intensidade: float = 0.0  # força do sinal (resposta, contraste, ΔE...); ajuda a afinar limiares
    contorno: np.ndarray | None = None  # no recorte normalizado (N×1×2, int32, px); None = o saco todo
    descricao: str = ""  # texto legível, p.ex. "comprimento 158.2 mm (150 +/- 3)"
    modulo: str = ""  # preenchido pelo Mestre: o módulo que o encontrou


@dataclass
class Relatorio:
    """O que um módulo devolve ao Mestre."""

    modulo: str  # nome do módulo, p.ex. "dimensoes"
    estado: Estado
    defeitos: list[Defeito] = field(default_factory=list)
    medidas: dict[str, float] = field(default_factory=dict)  # valores medidos, p.ex. {"comprimento_mm": 151.2}
    tempo_ms: float = 0.0  # preenchido pelo Mestre
    mensagem: str = ""  # explicação, ou o erro se estado == ERRO
    intermedios: dict[str, np.ndarray] = field(default_factory=dict)  # imagens de cada etapa (depuração)
    detalhe: str = ""  # preenchido pelo Mestre quando há ERRO: o traceback completo

    @classmethod
    def concluir(
        cls,
        modulo: str,
        defeitos: list[Defeito],
        medidas: dict[str, float] | None = None,
        intermedios: dict[str, np.ndarray] | None = None,
        mensagem: str = "",
    ) -> Relatorio:
        """Relatório normal de um escravo: NOK se houver defeitos, senão OK."""
        return cls(
            modulo=modulo,
            estado=Estado.NOK if defeitos else Estado.OK,
            defeitos=defeitos,
            medidas=medidas or {},
            mensagem=mensagem,
            intermedios=intermedios or {},
        )

    @property
    def resumo(self) -> str:
        """Texto curto para o registo, p.ex. "OK", "NOK(2)", "ERRO"."""
        return f"NOK({len(self.defeitos)})" if self.estado is Estado.NOK else str(self.estado)


@dataclass
class Resultado:
    """A decisão do Mestre sobre um saco, com os relatórios de todos os módulos."""

    imagem: np.ndarray | None  # imagem analisada (BGR uint8, só de leitura); None numa passagem descartada
    estado: Estado  # OK, NOK, ERRO ou SEM_SACO
    saco: Saco | None  # None se o M2 não encontrou um saco (ou falhou)
    relatorios: list[Relatorio]  # um por módulo executado, pela ordem: localizacao, dimensoes, ...
    tempo_ms: float  # tempo total da inspeção
    mensagem: str = ""  # porque é que o saco ficou em ERRO ou SEM_SACO
    captura: Captura | None = None  # a imagem da passagem que foi analisada
    passagem: Passagem | None = None  # a passagem a que pertence (None se veio de uma imagem solta)
    imagens_analisadas: int = 1  # quantas candidatas o Mestre teve de experimentar

    @property
    def ok(self) -> bool:
        """True só se o saco foi aprovado."""
        return self.estado is Estado.OK

    @property
    def defeitos(self) -> list[Defeito]:
        """Todos os defeitos, de todos os módulos. O número de cada um é a posição + 1."""
        return [d for r in self.relatorios for d in r.defeitos]

    @property
    def contagem_tipos(self) -> Counter[str]:
        """Nº de defeitos por tipo, p.ex. Counter({"risco": 2, "rugas": 1})."""
        return Counter(str(d.tipo) for d in self.defeitos)

    def relatorio(self, modulo: str) -> Relatorio | None:
        """O relatório de um módulo, ou None se não chegou a ser executado."""
        return next((r for r in self.relatorios if r.modulo == modulo), None)


class Escravo(ABC):
    """Base dos escravos de inspeção (M3 a M6).

    Cada escravo:
        - responde a uma só pergunta (`pergunta`);
        - lê só a sua secção da configuração (com o mesmo nome do módulo) e a geral;
        - não importa outros módulos;
        - não altera o Saco que recebe;
        - devolve sempre um Relatorio (os erros são apanhados pelo Mestre).
    """

    nome: ClassVar[str]  # nome da sua secção da configuração e prefixo dos seus defines, p.ex. "dimensoes"
    pergunta: ClassVar[str]  # a pergunta a que o módulo responde

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    @abstractmethod
    def analisar(self, saco: Saco, guardar_intermedios: bool = False) -> Relatorio:
        """Analisa o saco e devolve as conclusões do módulo.

        Recebe:
            saco: o Saco do M2 (só de leitura).
            guardar_intermedios: se True, pôr em Relatorio.intermedios as
                imagens de cada etapa, para afinar e depurar.
        Devolve:
            Relatorio.concluir(self.nome, defeitos, medidas, ...).
        """
