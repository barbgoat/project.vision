"""Lista as sessões de resultados/ que se podem apagar (não apaga nada).

Uma sessão é apagável quando é mais antiga do que --dias e o nome dela não aparece em nenhum
ficheiro de texto de dataset/ ou configuracoes/ (os manifestos das rugas vão buscar lá as imagens).

    python -m ferramentas.sessoes_apagaveis
    python -m ferramentas.sessoes_apagaveis --dias 7
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SESSAO = re.compile(r"\d{8}_\d{6}")
TEXTO = {".json", ".csv", ".txt", ".yaml", ".yml", ".toml", ".md"}


def referenciadas(pastas: list[Path]) -> set[str]:
    nomes: set[str] = set()
    for pasta in pastas:
        for f in pasta.rglob("*"):
            if f.is_file() and f.suffix.lower() in TEXTO:
                nomes.update(SESSAO.findall(f.read_text(encoding="utf-8", errors="ignore")))
    return nomes


def tamanho(pasta: Path) -> int:
    return sum(f.stat().st_size for f in pasta.rglob("*") if f.is_file())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--resultados", type=Path, default=RAIZ / "resultados")
    p.add_argument("--dias", type=float, default=14, help="só sessões mais antigas do que isto (omissão: 14)")
    args = p.parse_args(argv)

    usadas = referenciadas([RAIZ / "dataset", RAIZ / "configuracoes"])
    limite = time.time() - args.dias * 86400
    apagaveis, mantidas = [], {"referenciadas": 0, "recentes": 0}
    for s in sorted(args.resultados.iterdir()):
        if not (s.is_dir() and SESSAO.fullmatch(s.name)):
            continue  # base de dados, logs, copias_bd, verificar_rugas... nunca entram
        if s.name in usadas:
            mantidas["referenciadas"] += 1
        elif s.stat().st_mtime > limite:
            mantidas["recentes"] += 1
        else:
            apagaveis.append((s, tamanho(s)))

    for s, t in apagaveis:
        print(f"{s.name}  {t / 1e6:8.1f} MB  {time.strftime('%Y-%m-%d', time.localtime(s.stat().st_mtime))}")
    total = sum(t for _, t in apagaveis)
    print(f"\n{len(apagaveis)} sessões apagáveis, {total / 1e9:.2f} GB "
          f"(mantidas: {mantidas['referenciadas']} usadas pelo dataset, {mantidas['recentes']} com menos de {args.dias:g} dias)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
