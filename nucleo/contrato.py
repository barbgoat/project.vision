"""Contrato entre o Mestre e os Escravos: o que circula entre eles.

    Captura    M1 (fonte)     → M1 (disparo)    as imagens de um instante (uma por iluminação) e a origem
    Passagem   M1 Aquisição   → Mestre          as melhores imagens de um saco, da entrada à saída
    Saco       M2 Localização → Mestre → M3–M6  o saco encontrado e normalizado (só de leitura)
    Relatorio  cada módulo    → Mestre          o que o módulo concluiu (imutável)
    Resultado  Mestre         → M7 Saída        a decisão final sobre o saco

Os escravos de inspeção (M3 a M6) cumprem o Protocol Escravo: um só método,
inspecionar(saco, captura, cfg) -> Relatorio, sem estado partilhado entre chamadas.
O Mestre não sabe como cada um trabalha; só conhece este contrato.
"""

from __future__ import annotations

import math
import traceback
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Protocol, runtime_checkable

import cv2
import numpy as np

from nucleo.config import RAIZ, Config


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


# Pastas do dataset: "ok", uma por tipo de defeito e "varios" (mais do que um defeito).
CLASSES_DATASET = ("ok", *(tipo.value for tipo in TipoDefeito), "varios")


# O nome do M2 (modulos/m2_localizacao): o primeiro relatório de cada Resultado; os outros são de inspeção.
LOCALIZACAO = "localizacao"


# Iluminações possíveis de uma Captura. Hoje só há "difusa"; um escravo que prefira
# outra (p.ex. o M5 a "rasante") recebe a difusa enquanto a sua não existir.
ILUMINACOES = ("difusa", "retro", "rasante")


@dataclass(frozen=True, eq=False)
class Captura:
    """As imagens de um instante, adquiridas pelo M1, e a identificação da sua origem.

    Imutável. As imagens são sempre BGR uint8, já sem distorção da lente (o M1 faz o
    debayer e a correção: modulos/m1_aquisicao/preprocessamento.py). Por compatibilidade,
    Captura(imagem, origem, instante) com uma só imagem guarda-a como "difusa".
    """

    imagens: dict[str, np.ndarray]  # por iluminação ("difusa", "retro", "rasante"); BGR uint8
    origem: str  # "basler:<nº de série>#<nº da imagem>" (ou o ficheiro, numa imagem lida do disco)
    instante: float  # momento da aquisição (time.time(), em s)
    # True se a imagem foi disparada pelo sinal do botão ou do sensor (modo "trigger");
    # None com a câmara a captar em contínuo (modo "ao_vivo").
    sensor: bool | None = None
    px_por_mm: float | None = None  # escala já conhecida (câmara calibrada); None = a de geral.px_por_mm
    calibracao_id: str | None = None  # a calibração aplicada (Calibracao.id); None = nenhuma
    fonte: str = ""  # "basler", "spinnaker"...
    id_saco: int | None = None  # o nº da Passagem a que pertence (posto pela Passagem)

    def __post_init__(self) -> None:
        if isinstance(self.imagens, np.ndarray):  # Captura(imagem, ...): uma só imagem, a difusa
            object.__setattr__(self, "imagens", {"difusa": self.imagens})
        desconhecidas = set(self.imagens) - set(ILUMINACOES)
        if "difusa" not in self.imagens or desconhecidas:
            raise ValueError(f"Captura: tem de ter a imagem 'difusa' e só iluminações de {ILUMINACOES} "
                             f"(chegou {sorted(self.imagens)})")

    @property
    def imagem(self) -> np.ndarray:
        """A imagem com luz difusa (a que o M2 usa)."""
        return self.imagens["difusa"]

    def imagem_para(self, iluminacao: str) -> np.ndarray:
        """A imagem da iluminação pedida, ou a difusa se essa não existir."""
        return self.imagens.get(iluminacao, self.imagens["difusa"])


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
    modo: str  # o modo de disparo que a criou: "ao_vivo" ou "trigger"
    aviso: str = ""  # algo de anormal, p.ex. "passagem demasiado longa (sacos encostados?)"
    descartada: bool = False  # True se não pôde ser analisada (a análise está atrasada face à linha)

    def __post_init__(self) -> None:
        """Cada candidata fica com o id do saco (o nº desta passagem)."""
        self.candidatas = [c if c.id_saco == self.numero else replace(c, id_saco=self.numero)
                           for c in self.candidatas]

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

    def px_para_mm(self, pontos: np.ndarray, px_por_mm: float) -> np.ndarray:
        """Pontos do recorte normalizado (px, N×2 ou N×1×2) → mm no referencial do saco (N×2)."""
        altura, largura = self.mascara.shape
        return (np.asarray(pontos, float).reshape(-1, 2) - ((largura - 1) / 2, (altura - 1) / 2)) / px_por_mm

    def mm_para_imagem(self, pontos_mm: np.ndarray, px_por_mm: float) -> np.ndarray:
        """Pontos em mm no referencial do saco (N×2) → px da imagem analisada (N×2), pela matriz do Saco."""
        altura, largura = self.mascara.shape
        recorte = np.asarray(pontos_mm, float).reshape(-1, 2) * px_por_mm + ((largura - 1) / 2, (altura - 1) / 2)
        return cv2.transform(recorte.reshape(-1, 1, 2), cv2.invertAffineTransform(self.matriz)).reshape(-1, 2)

    def recortar(self, imagem: np.ndarray) -> np.ndarray:
        """Outra imagem do mesmo instante (p.ex. outra iluminação) no referencial do recorte normalizado."""
        altura, largura = self.mascara.shape
        return cv2.warpAffine(imagem, self.matriz, (largura, altura), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0)


class SemSaco(Exception):
    """Levantada pelo M2 quando a imagem não tem um saco completo; a mensagem diz porquê.

    O Mestre trata-a como estado SEM_SACO (não é um erro do módulo), p.ex.
    raise SemSaco("o saco toca na borda da imagem (incompleto)").
    """


class SemConfianca(Exception):
    """Levantada por um módulo que correu bem mas não pode decidir com confiança: a segmentação é
    incerta, ou a imagem não deixou medir (aresta escondida por um reflexo, luz que mudou...).

    O Mestre dá ERRO com esta mensagem, sem o traceback (não é uma falha do código): o saco vai à
    revisão em vez de levar um OK ou NOK sem fundamento.
    """


@dataclass(frozen=True, eq=False)
class Defeito:
    """Um defeito encontrado num saco (imutável). Medidas em mm, no referencial do saco:
    origem no centro do saco, x ao longo do eixo maior, y ao longo do menor."""

    tipo: TipoDefeito
    x_mm: float = 0.0  # centro do defeito, ao longo do eixo maior, a partir do centro do saco
    y_mm: float = 0.0  # centro do defeito, ao longo do eixo menor
    comprimento_mm: float = 0.0  # linha: ao longo da curva; zona/mancha: lado maior do retângulo envolvente
    largura_mm: float = 0.0  # linha: largura da crista; zona/mancha: lado menor do retângulo envolvente
    area_mm2: float = 0.0  # área afetada
    angulo_graus: float = 0.0  # orientação em relação ao eixo maior do saco
    intensidade: float = 0.0  # força do sinal (resposta, contraste, ΔE...); ajuda a afinar limiares
    severidade: float = 0.0  # quanto ultrapassa o limiar: valor / limiar (≥ 1 num defeito); 0 = não indicada
    contorno: np.ndarray | None = None  # em mm no referencial do saco (N×2, float); None = o saco todo
    descricao: str = ""  # texto legível, p.ex. "comprimento 158.2 mm (150 +/- 3)"
    modulo: str = ""  # preenchido pelo Mestre: o módulo que o encontrou


@dataclass(frozen=True, eq=False)
class Relatorio:
    """O que um módulo devolve ao Mestre. Imutável: o Mestre acrescenta o tempo com replace().

    defeitos fica um tuplo; medidas e intermedios, dicionários só de leitura.
    """

    modulo: str  # nome do módulo, p.ex. "dimensoes"
    estado: Estado
    defeitos: tuple[Defeito, ...] = ()
    medidas: MappingProxyType = field(default_factory=dict)  # valores medidos, p.ex. {"comprimento_mm": 151.2}
    tempo_ms: float = 0.0  # preenchido pelo Mestre
    mensagem: str = ""  # explicação, ou o erro se estado == ERRO
    intermedios: MappingProxyType = field(default_factory=dict)  # imagens de cada etapa (depuração)
    detalhe: str = ""  # preenchido pelo Mestre quando há ERRO: o traceback completo

    def __post_init__(self) -> None:
        object.__setattr__(self, "defeitos", tuple(self.defeitos))
        object.__setattr__(self, "medidas", MappingProxyType(dict(self.medidas)))
        object.__setattr__(self, "intermedios", MappingProxyType(dict(self.intermedios)))

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

    @classmethod
    def por_implementar(cls, modulo: str, falta: BaseException | None = None) -> Relatorio:
        """Relatório de um módulo que ainda é um esqueleto: não conta para a decisão.

        falta: o NotImplementedError apanhado, para dizer onde (ficheiro:linha (função)).
        """
        onde = ""
        if falta is not None and falta.__traceback__ is not None:
            quadro = traceback.extract_tb(falta.__traceback__)[-1]
            try:
                ficheiro = Path(quadro.filename).resolve().relative_to(RAIZ).as_posix()
            except ValueError:
                ficheiro = quadro.filename
            onde = f": {ficheiro}:{quadro.lineno} ({quadro.name})"
        return cls(modulo, Estado.POR_IMPLEMENTAR, mensagem=f"por implementar{onde}")

    @property
    def resumo(self) -> str:
        """Texto curto para o registo, p.ex. "OK", "NOK(2)", "ERRO"."""
        return f"NOK({len(self.defeitos)})" if self.estado is Estado.NOK else str(self.estado)


@dataclass(frozen=True)
class Atipica:
    """Uma medida do saco longe das dos sacos OK do produto (nucleo/referencia.py). É um aviso:
    o saco pode estar dentro da tolerância, mas a produção está a desviar-se."""

    medida: str  # p.ex. "largura_mm"
    valor: float  # o deste saco
    media: float  # a dos sacos OK
    desvio_padrao: float  # o dos sacos OK
    limite: float  # o afastamento aceite: referencia.sigmas × desvio-padrão + a resolução da medida

    @property
    def texto(self) -> str:
        """P.ex. "largura_mm 55.90 (sacos OK: 55.20 ± 0.45)"."""
        return f"{self.medida} {self.valor:.2f} (sacos OK: {self.media:.2f} ± {self.limite:.2f})"


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
    of: str = ""  # a ordem de fabrico e o produto com que foi analisado ("" = sem OF)
    produto: str = ""
    desligados: tuple[str, ...] = ()  # escravos de inspeção que não foram executados (ativo = False)
    atipicas: tuple[Atipica, ...] = ()  # medidas longe das dos sacos OK do produto (só com referência)

    @property
    def ok(self) -> bool:
        """True só se o saco foi aprovado."""
        return self.estado is Estado.OK

    @property
    def _inspecao(self) -> list[Relatorio]:
        """Os relatórios dos escravos de inspeção (sem o do M2)."""
        return [r for r in self.relatorios if r.modulo != LOCALIZACAO]

    @property
    def verificado_por(self) -> list[str]:
        """Os escravos de inspeção que concluíram (OK ou NOK): só estes contam para um OK."""
        return [r.modulo for r in self._inspecao if r.estado in (Estado.OK, Estado.NOK)]

    @property
    def por_verificar(self) -> list[str]:
        """Os escravos de inspeção executados que não concluíram: por implementar, ou ERRO."""
        return [r.modulo for r in self._inspecao if r.estado in (Estado.POR_IMPLEMENTAR, Estado.ERRO)]

    @property
    def nao_verificado(self) -> list[str]:
        """Tudo o que ficou por ver neste saco: os por verificar e os desligados."""
        return [*self.por_verificar, *self.desligados] if self.saco is not None else []

    @property
    def aviso(self) -> str:
        """O que um OK não garante, numa linha (vazio se nada): os módulos que não verificaram o saco
        e as medidas atípicas, p.ex. "não verificado: cor, relevo | atípico: largura_mm 55.90 (...)"."""
        partes = []
        if self.nao_verificado:
            partes.append(f"não verificado: {', '.join(self.nao_verificado)}")
        if self.atipicas:
            partes.append(f"atípico: {'; '.join(a.texto for a in self.atipicas)}")
        return " | ".join(partes)

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


@runtime_checkable
class Escravo(Protocol):
    """O contrato dos escravos de inspeção (M3 a M6).

    Cada escravo:
        - responde a uma só pergunta (`pergunta`);
        - lê só a sua secção da configuração (com o mesmo nome do módulo) e a geral;
        - não importa outros módulos;
        - não guarda estado entre chamadas (o Mestre pode chamá-los em paralelo);
        - não altera o Saco, a Captura nem a configuração que recebe (são imutáveis);
        - devolve sempre um Relatorio (os erros são apanhados pelo Mestre); um módulo
          ainda por fazer devolve Relatorio.por_implementar(nome).
    Os escravos do projeto declaram-se subclasses de Escravo (herdam a iluminação por omissão).
    """

    nome: ClassVar[str]  # nome da sua secção da configuração e prefixo dos seus defines, p.ex. "dimensoes"
    pergunta: ClassVar[str]  # a pergunta a que o módulo responde
    # A iluminação que prefere (uma de ILUMINACOES); sem ela na Captura, usa a "difusa".
    iluminacao: ClassVar[str] = "difusa"

    def inspecionar(self, saco: Saco, captura: Captura, cfg: Config,
                    guardar_intermedios: bool = False) -> Relatorio:
        """Inspeciona o saco e devolve as conclusões do módulo.

        Recebe:
            saco: o Saco do M2 (só de leitura), no espaço métrico (cfg.geral.px_por_mm).
            captura: as imagens do mesmo instante, no espaço métrico; a da sua iluminação
                é captura.imagem_para(self.iluminacao), e saco.recortar(...) leva-a ao recorte.
            cfg: a configuração (só de leitura).
            guardar_intermedios: se True, pôr em Relatorio.intermedios as
                imagens de cada etapa, para afinar e depurar.
        Devolve:
            Relatorio.concluir(self.nome, defeitos, medidas, ...): os contornos dos
            defeitos em mm, no referencial do saco (saco.px_para_mm).
        """
        ...
