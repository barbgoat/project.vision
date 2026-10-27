# Inspeção visual de sacos de plástico (protótipo v1)

Visão por computador clássica (OpenCV) para inspecionar sacos numa linha
industrial: uma decisão **OK/NOK por saco** e cada defeito localizado.

- Sacos de 10–20 × 3–8 cm, em qualquer posição e rotação; 2 sacos/s (**500 ms por saco**).
- Câmara FLIR Blackfly S BFS-PGE-50S5C-C com lente Basler C23-0828-16M (8 mm).

```
MESTRE (main.py, mestre.py)
 ├─► M1 Aquisição ...... as imagens de cada saco
 ├─► M2 Localização .... onde está o saco? (mede-o e endireita-o)
 ├─► M3 Dimensões · M4 Cor · M5 Relevo · M6 Manchas     ← um relatório cada
 ├─  decisão: OK só se nenhum módulo der NOK ou ERRO
 └─► M7 Saída .......... CSV, imagens anotadas, depuração
```

## Pastas

```
main.py, mestre.py   o Mestre: ciclo principal e decisão
nucleo/              contrato entre módulos e defines.py (TODOS os valores ajustáveis)
modulos/             um escravo por pasta: m1_aquisicao/ … m7_saida/
ferramentas/         testar um módulo, o disparo ou a câmara; calcular a montagem; afinar; avaliar
configuracoes/       perfis por montagem: telemovel, bancada_botao, bancada_virtual, linha
dataset/             imagens por montagem (telemovel/, blackfly/) e por classe; fora do git
testes/              testes automáticos (python -m pytest)
docs/                documentação
```

## Instalar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Câmara (PySpin, Spinnaker 4.4): `pip install` da wheel em `instaladores\` (ver `requirements.txt`).

## Correr

```powershell
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json                # cada foto é um saco
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json --depurar nok   # + cada etapa dos NOK
python main.py --fonte video --video linha.mp4 --disparo virtual
python -m ferramentas.testar_modulo dimensoes <imagem ou pasta> --config configuracoes\telemovel.json
python -m nucleo.config --config configuracoes\telemovel.json                                 # valores em uso
python -m pytest
```

Resultados em `resultados/<data_hora>/`: `registo.csv` (um saco por linha, com
o estado de cada módulo), `defeitos.csv` (um defeito por linha) e imagens.

## Estado

| | |
|---|---|
| **Funcional** | Mestre, M1 (pasta, vídeo, disparo), M2, M3, M7, testes |
| **Por fazer** | câmara (`FonteSpinnaker`), M4, M5, M6, `afinar`, `avaliar` |

Próximos passos: dataset e `px_por_mm` medido → validar M2/M3 com fotos reais →
M5 Relevo → M6 → `avaliar` → câmara e M4 → PLC.

## Documentação

- [docs/arquitetura.md](docs/arquitetura.md): Mestre–Escravos, regras, configuração
- [docs/camara.md](docs/camara.md): câmara, lente, montagem, disparo (trigger)
- [docs/modulos.md](docs/modulos.md): como funcionam M2, M3 e M5; depuração
- [docs/dataset.md](docs/dataset.md): organização e como fotografar
