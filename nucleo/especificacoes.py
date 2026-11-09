"""Especificações: o que cada produto tem de ser, as ordens de fabrico (OF) e as inspeções de cada uma.

    Produto (Especificacao)  comprimento e largura nominais e as tolerâncias, a cor
                             predominante (escrita) e se tem verniz
    Ordem de fabrico (OF)    um número, o produto e o que esta OF muda nele

A configuração de cada saco faz-se por camadas e é sempre validada pela Config:

    nucleo/defines.py → perfil da montagem → produto → ajustes da OF

O produto só mexe nas medidas e tolerâncias do M3 (MEDIDAS); o resto (cantos,
esquadria, câmara, px/mm...) é do perfil da montagem. A cor e o verniz ficam
guardados, mas por agora nenhum módulo os verifica.

Correspondência BD ↔ OpenCV (CORRESPONDENCIA): o comprimento e a largura que o M2
mede (arestas subpíxel, mediana de cada lado, em mm) são comparados pelo M3 com
nominal ± tolerância do produto. Cada inspeção guarda o medido e o que a BD pedia
nesse momento (pedido), por isso o dashboard conta os sacos fora por medida.

A aplicação fala com a base de dados só através do Repositorio (procurar_of,
procurar_produto, guardar, historico...). Por agora é SQLite local; um servidor
partilhado (API REST) ou o ERP/PLC entram com outra classe com os mesmos
métodos, que use este SQLite como cópia local para quando não houver rede.
Cada gravação é uma versão nova: nada se apaga e fica quem mudou o quê e quando.
As datas são as do relógio do PC (hora local), guardadas com o instante e o dia.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nucleo.config import RAIZ, Config
from nucleo.contrato import Estado, Resultado

# O que define um produto (e o que uma OF pode mudar). As MEDIDAS entram na secção dimensoes.
MEDIDAS = ("comprimento_nominal_mm", "largura_nominal_mm", "tolerancia_comprimento_mm", "tolerancia_largura_mm")
CAMPOS = (*MEDIDAS, "cor", "verniz")
# Medida do OpenCV (Saco do M2, comparada pelo M3) → (nominal, tolerância) do produto.
CORRESPONDENCIA = {
    "comprimento_mm": ("comprimento_nominal_mm", "tolerancia_comprimento_mm"),
    "largura_mm": ("largura_nominal_mm", "tolerancia_largura_mm"),
}
FICHEIRO = "especificacoes.sqlite"  # na pasta dos resultados, se SAIDA_BASE_DADOS = ""

Ajustes = dict[str, Any]  # o que a OF muda no produto, p.ex. {"tolerancia_comprimento_mm": 4.0}


class ErroRepositorio(Exception):
    """A base de dados falhou (ficheiro, rede...). Quem a usa decide se continua sem ela."""


@dataclass(frozen=True)
class Especificacao:
    """Um produto (saco)."""

    produto: str
    comprimento_nominal_mm: float = 0.0
    largura_nominal_mm: float = 0.0
    tolerancia_comprimento_mm: float = 0.0  # aceita nominal ± tolerância
    tolerancia_largura_mm: float = 0.0
    cor: str = ""  # cor predominante, escrita (p.ex. "branco"); guardada, ainda não verificada
    verniz: bool = False  # guardado; ainda não há módulo que o verifique
    autor: str = ""  # quem gravou esta versão
    data_hora: str = ""  # quando, pelo relógio do PC (preenchido ao guardar)


@dataclass(frozen=True)
class OrdemFabrico:
    """Uma OF: o produto que se está a fazer e o que esta OF muda nele."""

    numero: str
    produto: str
    ajustes: Ajustes = field(default_factory=dict)  # só o que difere do produto
    autor: str = ""
    data_hora: str = ""  # relógio do PC


@dataclass(frozen=True)
class Escolha:
    """Com que produto e OF se vai produzir. produto None = é preciso criar a especificação."""

    produto: Especificacao | None
    of: OrdemFabrico | None
    nova: bool = False  # a OF (ou o produto) ainda não está guardada


@dataclass(frozen=True)
class Inspecao:
    """Um saco inspecionado, como fica na base de dados."""

    data_hora: str  # relógio do PC
    of: str
    produto: str
    estado: str  # o do sistema: OK, NOK, ERRO, SEM_SACO
    decisao: str  # a da revisão: "", "NOK" ou "descartado"
    tipos: dict[str, int]  # defeitos por tipo
    comprimento_mm: float | None  # o que o OpenCV mediu (None sem saco)
    largura_mm: float | None
    pedido: dict[str, float] = field(default_factory=dict)  # as MEDIDAS da BD quando o saco passou
    nao_verificado: tuple[str, ...] = ()  # os módulos que não verificaram o saco (Resultado.nao_verificado)
    atipicas: tuple[str, ...] = ()  # as medidas longe das dos sacos OK do produto (Resultado.atipicas)

    def fora(self, medida: str) -> bool | None:
        """Se a medida (de CORRESPONDENCIA) saiu de nominal ± tolerância; None sem medida ou sem pedido."""
        medido = getattr(self, medida)
        nominal, tolerancia = (self.pedido.get(c) for c in CORRESPONDENCIA[medida])
        if medido is None or nominal is None or tolerancia is None:
            return None
        return abs(medido - nominal) > tolerancia

    @property
    def final(self) -> str:
        """Como conta na produção: "OK", "NOK" (rejeitado) ou "descartado" (não conta)."""
        if self.decisao == "descartado":
            return "descartado"
        return "OK" if self.estado == "OK" else "NOK"


@dataclass(frozen=True)
class Estatistica:
    """Uma medida numa OF: o que a BD pede e o que o OpenCV mediu (nos sacos que contam)."""

    nominal: float  # o pedido mais recente
    tolerancia: float
    n: int
    media: float
    desvio_padrao: float
    minimo: float
    maximo: float
    fora: int  # sacos fora de nominal ± tolerância (cada um com o pedido do seu momento)


@dataclass
class Resumo:
    """As contas de uma OF para o dashboard."""

    ok: int = 0
    nok: int = 0
    descartados: int = 0
    tipos: Counter[str] = field(default_factory=Counter)  # defeitos por tipo (sem os descartados)
    evolucao: list[tuple[str, int, int]] = field(default_factory=list)  # (início do período, OK, NOK)
    medidas: dict[str, Estatistica] = field(default_factory=dict)  # por medida de CORRESPONDENCIA
    ok_incompletos: int = 0  # OK com algum módulo que não o verificou
    nao_verificado: Counter[str] = field(default_factory=Counter)  # nos OK: módulo → sacos que não verificou
    ok_atipicos: int = 0  # OK com alguma medida longe das dos sacos OK (a produção a desviar-se?)
    atipicas: Counter[str] = field(default_factory=Counter)  # nos OK: medida → sacos atípicos nela

    @property
    def total(self) -> int:
        return self.ok + self.nok + self.descartados

    @property
    def taxa_nok(self) -> float:
        """Fração de NOK nos sacos que contam (sem os descartados); 0 sem sacos."""
        return self.nok / (self.ok + self.nok) if self.ok + self.nok else 0.0


# --- Camadas da configuração -----------------------------------------------------------

def efetiva(produto: Especificacao, of: OrdemFabrico | None = None) -> Especificacao:
    """O produto com os ajustes da OF por cima. ValueError se a OF muda o que não é do produto."""
    if of is None:
        return produto
    fora = sorted(set(of.ajustes) - set(CAMPOS))
    if fora:
        raise ValueError(f"A OF {of.numero} só pode mudar {', '.join(CAMPOS)}; não: {', '.join(fora)}")
    return replace(produto, **of.ajustes)


def ajustes_entre(produto: Especificacao, mudado: Especificacao) -> Ajustes:
    """O que `mudado` tem de diferente do produto (os ajustes de uma OF)."""
    return {c: getattr(mudado, c) for c in CAMPOS if getattr(mudado, c) != getattr(produto, c)}


def compor(montagem: Config, produto: Especificacao | None = None, of: OrdemFabrico | None = None) -> Config:
    """A configuração de uma produção: a da montagem com as medidas do produto (e da OF) por cima.

    Passa pela Config: valores incoerentes (p.ex. uma tolerância negativa, ou um
    nominal fora dos limites do M2) dão ValueError, a dizer de onde vieram.
    """
    if produto is None:
        return montagem
    p = efetiva(produto, of)
    try:
        medidas = {c: float(getattr(p, c)) for c in MEDIDAS}
        return replace(montagem, dimensoes=replace(montagem.dimensoes, **medidas))
    except (ValueError, TypeError) as erro:
        raise ValueError(f"O produto '{p.produto}'{f' (OF {of.numero})' if of else ''}: {erro}") from None


def repor_medidas(cfg: Config, perfil: Config) -> Config:
    """`cfg` com as medidas do produto como estão no perfil da montagem (sem produto nem OF)."""
    return replace(cfg, dimensoes=replace(cfg.dimensoes, **{c: getattr(perfil.dimensoes, c) for c in MEDIDAS}))


def resolver(repo: Repositorio, numero: str, produto: str) -> Escolha:
    """O que usar quando o operador indica a OF e/ou o produto.

    OF que já existe → a OF e o seu produto. OF nova de um produto que já
    existe → uma OF nova com os ajustes da última OF desse produto (as últimas
    especificações usadas). Produto desconhecido → Escolha(produto=None): é
    preciso criar a especificação. ValueError se não se indicou nada ou se a OF
    é de outro produto.
    """
    numero, produto = numero.strip(), produto.strip()
    if not numero and not produto:
        raise ValueError("Indica o número da OF e/ou o produto.")
    if numero and (of := repo.procurar_of(numero)) is not None:
        if produto and produto.casefold() != of.produto.casefold():
            raise ValueError(f"A OF {of.numero} é do produto '{of.produto}', não de '{produto}'.")
        return Escolha(repo.procurar_produto(of.produto), of)
    if produto and (espec := repo.procurar_produto(produto)) is not None:
        if not numero:
            return Escolha(espec, None)
        anterior = repo.ultima_of(espec.produto)
        return Escolha(espec, OrdemFabrico(numero, espec.produto, anterior.ajustes if anterior else {}), nova=True)
    return Escolha(None, OrdemFabrico(numero, produto) if numero else None, nova=True)


def resumir(inspecoes: list[Inspecao], periodo_min: int = 60) -> Resumo:
    """Conta OK, NOK e descartados, os defeitos por tipo e a evolução por períodos de `periodo_min`."""
    resumo = Resumo()
    periodos: dict[str, Counter[str]] = {}
    for i in inspecoes:
        final = i.final
        if final == "OK":
            resumo.ok += 1
            resumo.ok_incompletos += bool(i.nao_verificado)
            resumo.nao_verificado.update(i.nao_verificado)
            resumo.ok_atipicos += bool(i.atipicas)
            resumo.atipicas.update(i.atipicas)
        elif final == "NOK":
            resumo.nok += 1
            resumo.tipos.update(i.tipos)
        else:
            resumo.descartados += 1
            continue
        instante = datetime.fromisoformat(i.data_hora)
        inicio = instante - timedelta(minutes=(instante.hour * 60 + instante.minute) % periodo_min,
                                      seconds=instante.second, microseconds=instante.microsecond)
        periodos.setdefault(inicio.strftime("%Y-%m-%d %H:%M"), Counter())[final] += 1
    resumo.evolucao = [(p, c["OK"], c["NOK"]) for p, c in sorted(periodos.items())]
    contam = [i for i in inspecoes if i.final != "descartado"]
    for medida, (nominal, tolerancia) in CORRESPONDENCIA.items():
        conferidas = [i for i in contam if i.fora(medida) is not None]
        if not conferidas:
            continue
        valores = [getattr(i, medida) for i in conferidas]
        media = sum(valores) / len(valores)
        resumo.medidas[medida] = Estatistica(
            conferidas[-1].pedido[nominal], conferidas[-1].pedido[tolerancia], len(valores), media,
            (sum((v - media) ** 2 for v in valores) / len(valores)) ** 0.5, min(valores), max(valores),
            sum(bool(i.fora(medida)) for i in conferidas))
    return resumo


def caminho_base_dados(cfg: Config, pasta_resultados: str | Path) -> Path:
    """O ficheiro da base de dados: saida.base_dados, ou especificacoes.sqlite na pasta dos resultados."""
    return RAIZ / cfg.saida.base_dados if cfg.saida.base_dados else Path(pasta_resultados) / FICHEIRO


# --- Repositório (SQLite local) -------------------------------------------------------

# data_hora: o instante pelo relógio do PC ("2026-10-29 14:05:31.123"); dia: só o dia ("2026-10-29").
_TABELAS = """
CREATE TABLE IF NOT EXISTS produtos (
    id INTEGER PRIMARY KEY, produto TEXT NOT NULL COLLATE NOCASE, dados TEXT NOT NULL,
    autor TEXT, data_hora TEXT, dia TEXT);
CREATE TABLE IF NOT EXISTS ordens (
    id INTEGER PRIMARY KEY, numero TEXT NOT NULL COLLATE NOCASE, produto TEXT NOT NULL COLLATE NOCASE,
    dados TEXT NOT NULL, autor TEXT, data_hora TEXT, dia TEXT);
CREATE TABLE IF NOT EXISTS inspecoes (
    id INTEGER PRIMARY KEY, data_hora TEXT, dia TEXT, sessao TEXT, saco INTEGER, of TEXT COLLATE NOCASE,
    produto TEXT COLLATE NOCASE, estado TEXT, decisao TEXT DEFAULT '', tipos TEXT,
    comprimento_mm REAL, largura_mm REAL, pedido TEXT, observacoes TEXT);
CREATE INDEX IF NOT EXISTS inspecoes_of ON inspecoes (of);
"""


def carimbo(segundos: float | None = None) -> tuple[str, str]:
    """(data_hora, dia) pelo relógio do PC (hora local): agora, ou um instante em segundos desde 1970."""
    instante = datetime.now() if segundos is None else datetime.fromtimestamp(segundos)
    return instante.isoformat(sep=" ", timespec="milliseconds"), instante.date().isoformat()


class Repositorio:
    """Os produtos, as OFs e as inspeções num ficheiro SQLite.

    Cada operação abre e fecha a sua ligação: pode ser usado ao mesmo tempo
    pela inspeção (uma thread) e pela janela. As falhas dão ErroRepositorio.
    """

    def __init__(self, caminho: str | Path) -> None:
        self.caminho = Path(caminho)
        with self._ligar() as bd:
            bd.executescript(_TABELAS)
            # Bases de dados de antes das colunas dia, pedido e observacoes
            for tabela, coluna in (("produtos", "dia"), ("ordens", "dia"), ("inspecoes", "dia"),
                                   ("inspecoes", "pedido"), ("inspecoes", "observacoes")):
                if coluna not in {c[1] for c in bd.execute(f"PRAGMA table_info({tabela})")}:
                    bd.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} TEXT")
            bd.execute("PRAGMA journal_mode=WAL")  # escritas rápidas; a janela lê enquanto a inspeção escreve

    @contextmanager
    def _ligar(self) -> Iterator[sqlite3.Connection]:
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            bd = sqlite3.connect(self.caminho, timeout=5)
        except (sqlite3.Error, OSError) as erro:
            raise ErroRepositorio(f"base de dados {self.caminho}: {erro}") from erro
        try:
            with bd:  # commit no fim, ou rollback se falhar
                yield bd
        except sqlite3.Error as erro:
            raise ErroRepositorio(f"base de dados {self.caminho}: {erro}") from erro
        finally:
            bd.close()

    # --- Produtos e OFs ---------------------------------------------------------------
    def procurar_produto(self, nome: str) -> Especificacao | None:
        """A última versão da especificação de um produto (sem distinguir maiúsculas), ou None."""
        with self._ligar() as bd:
            linha = bd.execute("SELECT dados FROM produtos WHERE produto = ? ORDER BY id DESC LIMIT 1",
                               (nome.strip(),)).fetchone()
        return _de_json(Especificacao, linha[0]) if linha else None

    def procurar_of(self, numero: str) -> OrdemFabrico | None:
        """A última versão de uma OF, ou None."""
        with self._ligar() as bd:
            linha = bd.execute("SELECT dados FROM ordens WHERE numero = ? ORDER BY id DESC LIMIT 1",
                               (numero.strip(),)).fetchone()
        return _de_json(OrdemFabrico, linha[0]) if linha else None

    def ultima_of(self, produto: str) -> OrdemFabrico | None:
        """A OF mais recente de um produto (a última que foi criada ou mudada), ou None."""
        with self._ligar() as bd:
            linha = bd.execute("SELECT dados FROM ordens WHERE produto = ? ORDER BY id DESC LIMIT 1",
                               (produto.strip(),)).fetchone()
        return _de_json(OrdemFabrico, linha[0]) if linha else None

    def produtos(self, procura: str = "") -> list[Especificacao]:
        """A última versão de cada produto cujo nome contém `procura`, por ordem alfabética."""
        with self._ligar() as bd:
            linhas = bd.execute(
                "SELECT dados FROM produtos WHERE id IN (SELECT MAX(id) FROM produtos GROUP BY produto) "
                "AND produto LIKE ? ORDER BY produto", (f"%{procura}%",)).fetchall()
        return [_de_json(Especificacao, d) for (d,) in linhas]

    def ordens(self, procura: str = "") -> list[OrdemFabrico]:
        """A última versão de cada OF cujo número ou produto contém `procura`, as mais recentes primeiro."""
        with self._ligar() as bd:
            linhas = bd.execute(
                "SELECT dados FROM ordens WHERE id IN (SELECT MAX(id) FROM ordens GROUP BY numero) "
                "AND (numero LIKE ? OR produto LIKE ?) ORDER BY id DESC", (f"%{procura}%",) * 2).fetchall()
        return [_de_json(OrdemFabrico, d) for (d,) in linhas]

    def guardar(self, item: Especificacao | OrdemFabrico) -> Especificacao | OrdemFabrico:
        """Guarda uma versão nova de um produto ou de uma OF; devolve-a com a data e hora.

        ValueError se falta o nome ou o número, se a OF muda o que não é do
        produto, ou se a OF é de um produto que não existe.
        """
        data_hora, dia = carimbo()
        item = replace(item, data_hora=data_hora)
        if isinstance(item, Especificacao):
            item = replace(item, produto=item.produto.strip(), cor=item.cor.strip())
            if not item.produto:
                raise ValueError("Falta o nome do produto.")
            sql = "INSERT INTO produtos (produto, dados, autor, data_hora, dia) VALUES (?, ?, ?, ?, ?)"
            valores = (item.produto, _json(item), item.autor, item.data_hora, dia)
        else:
            item = replace(item, numero=item.numero.strip(), produto=item.produto.strip())
            if not item.numero:
                raise ValueError("Falta o número da OF.")
            produto = self.procurar_produto(item.produto)
            if produto is None:
                raise ValueError(f"O produto '{item.produto}' da OF {item.numero} não existe.")
            item = replace(item, produto=produto.produto)  # o nome como está no produto
            efetiva(produto, item)  # só pode mudar o que é do produto
            sql = "INSERT INTO ordens (numero, produto, dados, autor, data_hora, dia) VALUES (?, ?, ?, ?, ?, ?)"
            valores = (item.numero, item.produto, _json(item), item.autor, item.data_hora, dia)
        with self._ligar() as bd:
            bd.execute(sql, valores)
        return item

    # --- Inspeções -------------------------------------------------------------------
    def registar(self, resultado: Resultado, sessao: str, saco: int, cfg: Config | None = None) -> None:
        """Acrescenta um saco inspecionado (com a OF e o produto do Resultado).

        cfg: a configuração com que foi inspecionado; as suas MEDIDAS ficam como o
        pedido do saco (o que a BD pedia), ao lado do que o OpenCV mediu.
        """
        captura, passagem = resultado.captura, resultado.passagem
        data_hora, dia = carimbo(captura.instante if captura else passagem.inicio if passagem else None)
        saco_m2 = resultado.saco if resultado.estado in (Estado.OK, Estado.NOK) else None  # só com mm
        pedido = {c: getattr(cfg.dimensoes, c) for c in MEDIDAS} if cfg else {}
        observacoes = {"nao_verificado": resultado.nao_verificado,
                       "atipicas": [a.medida for a in resultado.atipicas]}
        with self._ligar() as bd:
            bd.execute(
                "INSERT INTO inspecoes (data_hora, dia, sessao, saco, of, produto, estado, tipos, comprimento_mm, "
                "largura_mm, pedido, observacoes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (data_hora, dia, sessao, saco, resultado.of, resultado.produto, str(resultado.estado),
                 json.dumps(dict(resultado.contagem_tipos)), saco_m2.comprimento_mm if saco_m2 else None,
                 saco_m2.largura_mm if saco_m2 else None, json.dumps(pedido), json.dumps(observacoes)))

    def marcar_decisao(self, sessao: str, saco: int, decisao: str) -> None:
        """A decisão da revisão de um saco não OK: "NOK" ou "descartado"."""
        with self._ligar() as bd:
            bd.execute("UPDATE inspecoes SET decisao = ? WHERE sessao = ? AND saco = ?", (decisao, sessao, saco))

    def historico(self, of: str = "", dia: str = "") -> list[Inspecao]:
        """Os sacos inspecionados de uma OF e/ou de um dia ("2026-10-29"), pela ordem em que passaram."""
        filtros = [(campo, valor.strip()) for campo, valor in (("of", of), ("dia", dia)) if valor.strip()]
        onde = " AND ".join(f"{campo} = ?" for campo, _ in filtros) or "1"
        with self._ligar() as bd:
            linhas = bd.execute(
                "SELECT data_hora, of, produto, estado, decisao, tipos, comprimento_mm, largura_mm, pedido, "
                f"observacoes FROM inspecoes WHERE {onde} ORDER BY id", [valor for _, valor in filtros]).fetchall()
        inspecoes = []
        for d, o, p, e, dec, t, c, la, pe, obs in linhas:
            observacoes = json.loads(obs or "{}")
            inspecoes.append(Inspecao(d, o, p, e, dec or "", json.loads(t or "{}"), c, la, json.loads(pe or "{}"),
                                      tuple(observacoes.get("nao_verificado", ())),
                                      tuple(observacoes.get("atipicas", ()))))
        return inspecoes

    def dias(self, limite: int) -> list[str]:
        """Os últimos `limite` dias com sacos inspecionados, o mais recente primeiro."""
        with self._ligar() as bd:
            return [d for (d,) in bd.execute("SELECT DISTINCT dia FROM inspecoes WHERE dia IS NOT NULL "
                                             "ORDER BY dia DESC LIMIT ?", (limite,))]

    def limpar(self, tudo: bool = False) -> int:
        """Apaga as inspeções e, com tudo=True, também as OFs e os produtos (para recomeçar os testes).

        Não se pode desfazer. Devolve quantas linhas apagou.
        """
        tabelas = ("inspecoes", "ordens", "produtos") if tudo else ("inspecoes",)
        with self._ligar() as bd:
            return sum(bd.execute(f"DELETE FROM {tabela}").rowcount for tabela in tabelas)

    def versao(self) -> tuple:
        """Muda sempre que muda alguma coisa (produto, OF, saco ou decisão): o dashboard só se refaz então."""
        with self._ligar() as bd:
            return bd.execute(
                "SELECT (SELECT MAX(id) FROM produtos), (SELECT MAX(id) FROM ordens), (SELECT MAX(id) FROM inspecoes), "
                "(SELECT COUNT(*) FROM inspecoes WHERE decisao <> '')").fetchone()


def _json(item: Any) -> str:
    return json.dumps(asdict(item), ensure_ascii=False)


def _de_json(classe: type, texto: str) -> Any:
    """Uma Especificacao ou OrdemFabrico a partir do JSON guardado (campos desconhecidos são ignorados)."""
    dados = json.loads(texto)
    return classe(**{f.name: dados[f.name] for f in fields(classe) if f.name in dados})
