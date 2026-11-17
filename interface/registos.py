"""Interface, registos: o que fica escrito das decisões de quem usa a BIapp.

    revisoes.csv            a decisão sobre cada saco não OK (NOK, falso NOK ou descartado)
    correcoes.csv, dataset/ a classe real de um saco, para o avaliar e a afinação
    <imagem>.json           as zonas do defeito nessa imagem do dataset (retângulos, formato LabelMe)
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from modulos.m7_saida import nome_curto
from nucleo.config import RAIZ
from nucleo.contrato import Resultado
from nucleo.imagens import guardar_imagem


COLUNAS_CORRECOES = ["data_hora", "origem", "estado_sistema", "tipos_sistema", "classe_real", "imagem_dataset"]


# --- Revisão dos sacos não OK -------------------------------------------------------------

COLUNAS_REVISOES = ["data_hora", "saco", "origem", "estado_sistema", "decisao", "mensagem"]


def registar_revisao(resultado: Resultado, numero: int, decisao: str, pasta: Path) -> None:
    """Acrescenta a decisão sobre um saco ("NOK", "falso NOK" ou "descartado") a <pasta>/revisoes.csv."""
    caminho = pasta / "revisoes.csv"
    novo = not caminho.exists()
    pasta.mkdir(parents=True, exist_ok=True)
    with open(caminho, "a", newline="", encoding="utf-8-sig") as ficheiro:
        escritor = csv.writer(ficheiro, delimiter=";")
        if novo:
            escritor.writerow(COLUNAS_REVISOES)
        escritor.writerow([datetime.now().isoformat(sep=" ", timespec="seconds"), numero,
                           resultado.captura.origem if resultado.captura else "", resultado.estado, decisao,
                           resultado.mensagem])


# --- Dataset: a classe real de um saco ----------------------------------------------------

def acrescentar_correcao(registo: Path, linha: list) -> None:
    """Acrescenta uma linha (COLUNAS_CORRECOES) ao correcoes.csv; cria-o, com o cabeçalho, se não existir.
    Também o usa a ferramenta rever_sessao: o mesmo registo para a BIapp e para a revisão das sessões."""
    novo = not registo.exists()
    registo.parent.mkdir(parents=True, exist_ok=True)
    with open(registo, "a", newline="", encoding="utf-8-sig") as ficheiro:
        escritor = csv.writer(ficheiro, delimiter=";")
        if novo:
            escritor.writerow(COLUNAS_CORRECOES)
        escritor.writerow(linha)


def pasta_dataset(perfil: Path) -> Path:
    """A pasta do dataset para as correções: uma por montagem, com o nome do perfil (dataset/linha...)."""
    return RAIZ / "dataset" / perfil.stem


def guardar_correcao(resultado: Resultado, classe: str, dataset: Path, registo: Path,
                     anterior: Path | None = None) -> tuple[str, Path | None]:
    """Corrige o veredito de um saco: a classe real que o operador ou o engenheiro viu.

    A imagem original (sem anotações, PNG) vai para <dataset>/<classe>/, como
    o avaliar e a afinação esperam, e a correção fica numa linha do registo
    (correcoes.csv, ";" e utf-8-sig como os do M7). Uma imagem que já está
    no dataset não é copiada outra vez: a pasta onde está é que diz a classe.

    Recebe:
        resultado: o saco a corrigir (com a captura analisada).
        classe: "ok", um TipoDefeito ou "varios" (as pastas do dataset).
        dataset: a pasta da montagem, p.ex. dataset/linha.
        registo: o correcoes.csv (acrescenta uma linha; cria-o se não existir).
        anterior: a cópia que este saco já tem no dataset (de uma correção anterior, p.ex. na
            revisão), ou None. Com outra classe, é movida para a pasta nova: nunca fica em duas.
    Devolve:
        (o que foi feito, em texto, para a barra de estado; a imagem no dataset, ou None se não há cópia).
    """
    captura = resultado.captura
    if captura is None:
        raise ValueError("este saco não tem imagem (passagem descartada)")
    origem = Path(captura.origem)
    if anterior is not None and anterior.is_file():
        if anterior.parent.name == classe:
            destino = None
            feito = f"{anterior.name} já está em {dataset.name}/{classe}/: ficou só registada"
        else:
            destino = dataset / classe / anterior.name
            destino.parent.mkdir(parents=True, exist_ok=True)
            anterior.replace(destino)
            _mover_zonas(anterior, destino, classe)
            feito = f"imagem movida de {anterior.parent.name}/ para {dataset.name}/{classe}/{destino.name}"
        copia: Path | None = destino or anterior
    elif origem.is_file() and origem.resolve().is_relative_to(dataset.parent.resolve()):
        destino = copia = None
        feito = f"{origem.name} já está no dataset ({origem.parent.name}/): ficou só registada"
    else:
        nome = f"{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3] + f"_{nome_curto(captura.origem)}.png"
        destino = copia = dataset / classe / nome
        guardar_imagem(destino, captura.imagem)
        feito = f"imagem em {dataset.name}/{classe}/{nome}"

    tipos = ",".join(f"{tipo}:{n}" for tipo, n in sorted(resultado.contagem_tipos.items()))
    acrescentar_correcao(registo, [
        datetime.fromtimestamp(captura.instante).isoformat(sep=" ", timespec="milliseconds"),
        captura.origem, resultado.estado, tipos, classe, destino or "",
    ])
    return feito, copia


# --- Zonas do defeito numa imagem do dataset (LabelMe) ------------------------------------

def _json_das_zonas(imagem: Path) -> Path:
    return imagem.with_suffix(".json")


def zonas(imagem: Path) -> list[tuple[int, int, int, int]]:
    """Os retângulos (x, y, largura, altura, px da imagem) marcados nesta imagem do dataset."""
    caminho = _json_das_zonas(imagem)
    if not caminho.is_file():
        return []
    caixas = []
    for forma in json.loads(caminho.read_text(encoding="utf-8")).get("shapes", []):
        if forma.get("shape_type") == "rectangle":
            (x0, y0), (x1, y1) = forma["points"]
            caixas.append((round(min(x0, x1)), round(min(y0, y1)), round(abs(x1 - x0)), round(abs(y1 - y0))))
    return caixas


def acrescentar_zona(imagem: Path, classe: str, caixa: tuple[int, int, int, int],
                     largura: int, altura: int) -> int:
    """Acrescenta um retângulo à volta do defeito ao .json da imagem (formato LabelMe: abre-se e corrige-se
    no labelme, e é o que o docs/dataset.md define). Devolve o nº de zonas da imagem."""
    caminho = _json_das_zonas(imagem)
    dados = (json.loads(caminho.read_text(encoding="utf-8")) if caminho.is_file() else
             {"version": "5.0.1", "flags": {}, "shapes": [], "imagePath": imagem.name, "imageData": None,
              "imageHeight": altura, "imageWidth": largura})
    x, y, w, h = caixa
    dados["shapes"].append({"label": classe, "points": [[x, y], [x + w, y + h]], "group_id": None,
                            "shape_type": "rectangle", "flags": {}})
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(dados["shapes"])


def _mover_zonas(de: Path, para: Path, classe: str) -> None:
    """A imagem mudou de classe: as zonas vão com ela, com o rótulo novo."""
    origem = _json_das_zonas(de)
    if not origem.is_file():
        return
    dados = json.loads(origem.read_text(encoding="utf-8"))
    for forma in dados.get("shapes", []):
        forma["label"] = classe
    _json_das_zonas(para).write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    origem.unlink()
