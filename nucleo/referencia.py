r"""Referência dos sacos OK: como são os sacos bons de um produto, medidos por este sistema.

    python -m ferramentas.referencia --config configuracoes\bancada_botao.json --produto Garnier dataset\bancada_botao\ok

A ferramenta passa os sacos OK (30 a 50, de vários sacos físicos, em posições e rotações
diferentes) pelo Mestre e guarda, para cada medida de MEDIDAS, a média, o desvio-padrão, o
mínimo e o máximo em <referencia.pasta>/<produto>.json.

Depois, o Mestre (Referencia.para) compara cada saco medido com essa distribuição. Uma medida
a mais de referencia.sigmas desvios-padrão (mais a resolução da medida) da média dos bons
fica "atípica" (contrato.Atipica): é um aviso, o estado do saco não muda. Um saco pode estar
dentro de nominal ± tolerância e longe dos bons. É o primeiro sinal de uma lâmina a gastar,
de um saco a escorregar ou de uma câmara que mexeu, antes de os sacos darem NOK.

A referência só vale para a montagem, o produto e a escala com que foi feita: com outro
nominal, ou com outro px_por_mm (recalibração), o Mestre não a usa e avisa que é preciso refazê-la.
"""

from __future__ import annotations

import json
import logging
import math
import re
import statistics
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from nucleo.config import RAIZ, Config
from nucleo.contrato import Atipica, Estado, Relatorio

log = logging.getLogger("referencia")


@dataclass(frozen=True)
class Medida:
    """Uma medida que se compara com os sacos OK."""

    resolucao: float  # o que a medida não distingue (ruído da medição): somado ao limite, para um desvio-padrão ~0
    so_acima: bool  # True: só conta ser maior do que nos bons (falta de material: ter menos não é defeito)
    descricao: str


# As medidas dos sacos (as do M3, que já incluem as do M2). As que dependem do sentido do saco
# (a ponta que fica à esquerda não está resolvida) entram em valor absoluto e pela pior ponta.
# Quando o M4 Cor estiver feito, as suas medidas (L*, a*, b*, ΔE) entram aqui.
MEDIDAS: dict[str, Medida] = {
    "comprimento_mm": Medida(0.1, False, "comprimento (mm)"),
    "largura_mm": Medida(0.1, False, "largura (mm)"),
    "area_mm2": Medida(10.0, False, "área (mm²)"),
    "retangularidade": Medida(0.002, False, "área / (comprimento × largura)"),
    "falta_mm2": Medida(2.0, True, "falta de material face ao retângulo ideal (mm²)"),
    "sobra_mm2": Medida(2.0, True, "material a mais face ao retângulo ideal (mm²)"),
    "forma_profundidade_max_mm": Medida(0.2, True, "a falta ou sobra mais funda (mm)"),
    "esquadria_max_graus": Medida(0.1, True, "a ponta mais fora de esquadria (°, valor absoluto)"),
    "paralelismo_graus": Medida(0.1, True, "lados compridos não paralelos (°, valor absoluto)"),
    "canto_desvio_max_graus": Medida(0.1, True, "o canto mais longe de 90° (°)"),
}


def medidas_do_saco(relatorios: Iterable[Relatorio]) -> dict[str, float]:
    """As MEDIDAS de um saco, a partir dos relatórios dos módulos que concluíram (OK ou NOK)."""
    todas: dict[str, float] = {}
    for r in relatorios:
        if r.estado in (Estado.OK, Estado.NOK):
            todas.update({k: float(v) for k, v in r.medidas.items()
                          if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)})
    if "esquadria_esquerda_graus" in todas and "esquadria_direita_graus" in todas:
        todas["esquadria_max_graus"] = max(abs(todas["esquadria_esquerda_graus"]),
                                           abs(todas["esquadria_direita_graus"]))
    if "paralelismo_graus" in todas:
        todas["paralelismo_graus"] = abs(todas["paralelismo_graus"])
    return {nome: todas[nome] for nome in MEDIDAS if nome in todas}


@dataclass(frozen=True)
class Distribuicao:
    """Uma medida nos sacos OK."""

    n: int
    media: float
    desvio_padrao: float  # amostral (n − 1)
    minimo: float
    maximo: float

    @classmethod
    def de_valores(cls, valores: list[float]) -> Distribuicao:
        return cls(len(valores), statistics.fmean(valores), statistics.stdev(valores) if len(valores) > 1 else 0.0,
                   min(valores), max(valores))

    def limite(self, nome: str, sigmas: float) -> float:
        """O afastamento da média aceite: sigmas × desvio-padrão + a resolução da medida."""
        return sigmas * self.desvio_padrao + MEDIDAS[nome].resolucao


def nome_ficheiro(produto: str) -> str:
    """O nome do ficheiro de um produto, sem caracteres que o Windows recusa ("" → "sem_produto")."""
    return re.sub(r"[^\w\-]+", "_", produto.strip()).strip("_") or "sem_produto"


def nominal_de(cfg: Config) -> dict[str, float]:
    """O nominal do produto na configuração: a referência só vale para ele."""
    return {"comprimento_nominal_mm": cfg.dimensoes.comprimento_nominal_mm,
            "largura_nominal_mm": cfg.dimensoes.largura_nominal_mm}


@dataclass(frozen=True)
class Referencia:
    """Os sacos OK de um produto: a distribuição de cada medida e com que configuração foi feita."""

    produto: str
    sacos: int  # quantos sacos OK entraram
    medidas: dict[str, Distribuicao]
    nominal: dict[str, float]  # nominal_de(cfg) quando foi feita
    px_por_mm: float  # a escala com que foi feita
    origem: str = ""  # de onde vieram as imagens
    data_hora: str = ""
    autor: str = ""

    @classmethod
    def de_medicoes(cls, produto: str, medicoes: list[dict[str, float]], cfg: Config, origem: str = "",
                    data_hora: str = "", autor: str = "") -> Referencia:
        """A referência a partir das medidas de cada saco OK (medidas_do_saco)."""
        valores: dict[str, list[float]] = {nome: [] for nome in MEDIDAS}
        for m in medicoes:
            for nome, v in m.items():
                valores[nome].append(v)
        return cls(produto, len(medicoes), {n: Distribuicao.de_valores(v) for n, v in valores.items() if v},
                   nominal_de(cfg), cfg.geral.px_por_mm, origem, data_hora, autor)

    def comparar(self, medidas: dict[str, float], sigmas: float, sacos_min: int) -> tuple[Atipica, ...]:
        """As medidas do saco longe das dos bons (só as que têm pelo menos `sacos_min` sacos)."""
        atipicas = []
        for nome, d in self.medidas.items():
            if nome not in medidas or nome not in MEDIDAS or d.n < sacos_min:
                continue
            limite, diferenca = d.limite(nome, sigmas), medidas[nome] - d.media
            if diferenca > limite or (not MEDIDAS[nome].so_acima and diferenca < -limite):
                atipicas.append(Atipica(nome, medidas[nome], d.media, d.desvio_padrao, limite))
        return tuple(atipicas)

    def problema(self, cfg: Config) -> str:
        """Porque é que esta referência não serve para `cfg` ("" se serve)."""
        nominal = nominal_de(cfg)
        if any(abs(self.nominal.get(c, math.nan) - v) > 1e-6 for c, v in nominal.items()) \
                or set(self.nominal) != set(nominal):
            return (f"foi feita com o nominal {self.nominal}, e o produto agora tem {nominal}")
        if abs(self.px_por_mm - cfg.geral.px_por_mm) > 0.005 * cfg.geral.px_por_mm:
            return (f"foi feita com px_por_mm = {self.px_por_mm:g} e a montagem agora tem "
                    f"{cfg.geral.px_por_mm:g} (recalibrada?)")
        return ""

    # --- Ficheiro --------------------------------------------------------------------
    @staticmethod
    def caminho(cfg: Config, produto: str) -> Path:
        """<referencia.pasta>/<produto>.json (pasta relativa à raiz do projeto)."""
        return RAIZ / cfg.referencia.pasta / f"{nome_ficheiro(produto)}.json"

    def guardar(self, caminho: str | Path) -> None:
        caminho = Path(caminho)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    @classmethod
    def carregar(cls, caminho: str | Path) -> Referencia:
        """ValueError (com o ficheiro) se não for uma referência."""
        try:
            dados = json.loads(Path(caminho).read_text(encoding="utf-8-sig"))
            dados["medidas"] = {n: Distribuicao(**d) for n, d in dados["medidas"].items()}
            return cls(**dados)
        except (json.JSONDecodeError, KeyError, TypeError) as erro:
            raise ValueError(f"{caminho} não é uma referência de sacos OK válida: {erro}") from None

    @classmethod
    def para(cls, cfg: Config, produto: str) -> Referencia | None:
        """A referência do produto, se estiver ativa, existir e servir para esta configuração; senão None
        (com um aviso no log, se existir mas não servir)."""
        if not cfg.referencia.ativo:
            return None
        caminho = cls.caminho(cfg, produto)
        if not caminho.exists():
            return None
        referencia = cls.carregar(caminho)
        if problema := referencia.problema(cfg):
            log.warning("Referência dos sacos OK %s não usada: %s. Refazer com python -m ferramentas.referencia.",
                        caminho.name, problema)
            return None
        return referencia
