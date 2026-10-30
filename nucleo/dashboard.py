"""Dashboard, os dados: o que o dashboard mostra e como se criam e mudam os produtos e as OFs.

Não tem nada de gráfico: as janelas (interface/vista_dashboard.py, na BIapp e no
biapp_dashboard.py) mostram o que vem daqui e chamam estas funções para guardar. Tudo
é lido e escrito pelo Repositorio (nucleo/especificacoes.py).

    estado                       tudo o que o dashboard mostra, em JSON: é o que o servidor
                                 (nucleo/servidor_dashboard.py) envia por SSE
    ordens, resumo_of            as OFs com as contagens, e o detalhe de uma OF
    linhas_dias                  os totais de cada dia
    linhas_medidas               o que a BD pede ao lado do que o OpenCV mediu, por medida
    colunas_produto,             cada produto e os ajustes de uma OF em texto
    descrever_ajustes
    criar_produto, guardar_produto, criar_of, guardar_of
                                 criar e mudar (cada gravação é uma versão nova, com o autor);
                                 tudo é validado com a configuração da montagem antes de se guardar
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from nucleo.config import Config
from nucleo.especificacoes import (
    Escolha, Especificacao, OrdemFabrico, Repositorio, Resumo, ajustes_entre, carimbo, compor, resumir,
)

PERIODO_MIN = 60  # a evolução de cada OF conta os sacos por períodos de N minutos
DIAS_MAX = 31  # dias mostrados no separador "Por dia"

# A data (da última alteração) vem sempre primeiro.
COLUNAS_OF = ["Data", "OF", "Produto", "Sacos", "OK", "NOK", "Taxa NOK", "Por"]
COLUNAS_MEDIDAS = ["Medida", "Pedido (BD)", "Medido (média ± dp)", "Mín a máx", "Fora"]
COLUNAS_PRODUTOS = ["Data", "Produto", "Comprimento × largura (mm)", "Tolerâncias (mm)", "Cor", "Verniz", "Por"]
COLUNAS_DIAS = ["Dia", "Sacos", "OK", "NOK", "Descartados", "Taxa NOK"]
COLUNAS_TIPOS = ["Defeito", "Sacos NOK com ele"]


@dataclass(frozen=True)
class LinhaOF:
    """Uma OF na lista do dashboard, com as suas contagens."""

    of: OrdemFabrico
    resumo: Resumo

    def colunas(self) -> list:
        """Os valores pela ordem de COLUNAS_OF."""
        r = self.resumo
        return [hora(self.of.data_hora), self.of.numero, self.of.produto, r.total, r.ok, r.nok,
                f"{100 * r.taxa_nok:.1f} %", self.of.autor]


# --- Ver ---------------------------------------------------------------------------------

def estado(repo: Repositorio) -> dict:
    """Tudo o que o dashboard mostra, só com texto e números (passa a JSON sem mais nada).

    {"gerado": "2026-10-29 14:05:31", "hoje": {...}, "ordens": [...], "produtos": [...], "dias": [...]}
    Cada OF traz as colunas da lista e o detalhe (resumo, medidas, defeitos, evolução); cada
    produto e cada dia, as suas colunas. A data é a do relógio do PC.
    """
    agora, hoje = carimbo()
    dias = linhas_dias(repo)
    return {
        "gerado": agora[:19],
        "hoje": next((d for d in dias if d["dia"] == hoje), {"dia": hoje, "ok": 0, "nok": 0, "descartados": 0,
                                                             "taxa_nok": 0.0}),
        "ordens": [_ordem(linha) for linha in ordens(repo)],
        "produtos": [{"produto": p.produto, "colunas": colunas_produto(p)} for p in repo.produtos()],
        "dias": dias,
    }


def _ordem(linha: LinhaOF) -> dict:
    of, r = linha.of, linha.resumo
    return {
        "numero": of.numero, "produto": of.produto, "colunas": linha.colunas(),
        "resumo": f"{r.total} sacos: OK {r.ok}, NOK {r.nok}, descartados {r.descartados}",
        "taxa_nok": r.taxa_nok, "ajustes": descrever_ajustes(of),
        "medidas": linhas_medidas(r), "tipos": [[tipo, n] for tipo, n in r.tipos.most_common()],
        "evolucao": [list(periodo) for periodo in r.evolucao],
    }


def linhas_dias(repo: Repositorio) -> list[dict]:
    """Os totais dos últimos DIAS_MAX dias com sacos, o mais recente primeiro, com as colunas de COLUNAS_DIAS."""
    linhas = []
    for dia in repo.dias(DIAS_MAX):
        r = resumir(repo.historico(dia=dia), PERIODO_MIN)
        linhas.append({"dia": dia, "ok": r.ok, "nok": r.nok, "descartados": r.descartados, "taxa_nok": r.taxa_nok,
                       "colunas": [dia, r.total, r.ok, r.nok, r.descartados, f"{100 * r.taxa_nok:.1f} %"]})
    return linhas


def ordens(repo: Repositorio, procura: str = "") -> list[LinhaOF]:
    """As OFs cujo número ou produto contém `procura` (as mais recentes primeiro), com as contagens."""
    return [LinhaOF(of, resumo_of(repo, of.numero)) for of in repo.ordens(procura)]


def resumo_of(repo: Repositorio, numero: str) -> Resumo:
    """OK, NOK, descartados, defeitos por tipo e evolução de uma OF."""
    return resumir(repo.historico(numero), PERIODO_MIN)


def linhas_medidas(r: Resumo) -> list[list]:
    """Uma linha por medida, pela ordem de COLUNAS_MEDIDAS: o pedido da BD e o que o OpenCV mediu."""
    return [[medida.removesuffix("_mm"), f"{e.nominal:g} ± {e.tolerancia:g} mm",
             f"{e.media:.1f} ± {e.desvio_padrao:.1f} mm", f"{e.minimo:.1f} a {e.maximo:.1f} mm", f"{e.fora} de {e.n}"]
            for medida, e in r.medidas.items()]


def colunas_produto(p: Especificacao) -> list:
    """Um produto em texto, pela ordem de COLUNAS_PRODUTOS."""
    return [hora(p.data_hora), p.produto, f"{p.comprimento_nominal_mm:g} × {p.largura_nominal_mm:g}",
            f"± {p.tolerancia_comprimento_mm:g} / ± {p.tolerancia_largura_mm:g}", p.cor,
            "sim" if p.verniz else "não", p.autor]


def descrever_ajustes(of: OrdemFabrico) -> str:
    """O que a OF muda em relação ao produto, p.ex. "tolerancia_comprimento_mm = 4.0"."""
    return ", ".join(f"{n} = {v!r}" for n, v in of.ajustes.items()) or "nenhum"


def hora(data_hora: str) -> str:
    """Uma data e hora guardada, sem segundos: "2026-10-29 14:05"."""
    return data_hora[:16]


# --- Criar e mudar -------------------------------------------------------------------

def criar_produto(repo: Repositorio, montagem: Config, produto: Especificacao,
                  numero_of: str = "") -> tuple[Especificacao, OrdemFabrico | None]:
    """Um produto novo e, se `numero_of` for dado, a sua OF.

    ValueError se as medidas não servem para esta montagem, ou se já existe um
    produto com esse nome ou uma OF com esse número.
    """
    numero_of = numero_of.strip()
    compor(montagem, produto)
    if repo.procurar_produto(produto.produto) is not None:
        raise ValueError(f"Já existe um produto '{produto.produto.strip()}'.")
    if numero_of and repo.procurar_of(numero_of) is not None:
        raise ValueError(f"A OF {numero_of} já existe.")
    produto = repo.guardar(produto)
    of = repo.guardar(OrdemFabrico(numero_of, produto.produto, {}, produto.autor)) if numero_of else None
    return produto, of


def guardar_produto(repo: Repositorio, montagem: Config, produto: Especificacao) -> Especificacao:
    """Uma versão nova de um produto que já existe. ValueError se as medidas não servem para esta montagem."""
    compor(montagem, produto)
    return repo.guardar(produto)


def guardar_of(repo: Repositorio, montagem: Config, numero: str, produto: Especificacao, mudado: Especificacao,
               autor: str) -> OrdemFabrico:
    """Uma versão nova de uma OF: os ajustes são o que `mudado` tem de diferente do produto."""
    of = OrdemFabrico(numero, produto.produto, ajustes_entre(produto, mudado), autor)
    compor(montagem, produto, of)
    return repo.guardar(of)


def criar_of(repo: Repositorio, escolha: Escolha, autor: str) -> Escolha:
    """Guarda a OF nova de uma escolha (a de um produto que já existe); devolve a escolha com a OF guardada."""
    if not escolha.nova or escolha.of is None or escolha.produto is None:
        return escolha
    return Escolha(escolha.produto, repo.guardar(replace(escolha.of, autor=autor)))
