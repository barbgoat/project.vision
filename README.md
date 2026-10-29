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
gui.py, interface/   interface gráfica: ver, corrigir vereditos e afinar (PySide6)
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
python gui.py --pasta dataset\telemovel --config configuracoes\telemovel.json                 # interface gráfica
python -m ferramentas.testar_modulo dimensoes <imagem ou pasta> --config configuracoes\telemovel.json
python -m nucleo.config --config configuracoes\telemovel.json                                 # valores em uso
python -m pytest
```

Resultados em `resultados/<data_hora>/`: `registo.csv` (um saco por linha, com
o estado de cada módulo), `defeitos.csv` (um defeito por linha) e imagens.

## Interface gráfica

- Abre com **login** (contas em `interface/login.py`): `admin`/`admin` faz tudo; `operador`/`operador`
  só vê a vista Operador. F5 inicia, Shift+F5 para.
- Ícones na barra: **câmara** (exposição, ganho, trigger, lente), **roda dentada** (perfil, fonte,
  parâmetros de inspeção e saída) e **pessoa** (mudar de utilizador, contas e permissões). O perfil,
  a fonte e a pasta ficam lembrados para a sessão seguinte.
- **Atalho no ambiente de trabalho**: aponta para `.venv\Scripts\pythonw.exe gui.py`, com a pasta do
  projeto como pasta de trabalho e o ícone `interface\recursos\biapp.ico`. Sem consola: o log vai para
  `resultados\biapp.log`.
- **Ao vivo**: câmara/vídeo em contínuo, sem trigger, com uma cruz no centro: centrar o
  saco, luz e foco, afinar (os parâmetros entram logo). Não regista nada.
- **Produção**: com o trigger, uma decisão por saco, registada como no `main.py`. Um
  parâmetro mudado é pré-visualizado no saco do ecrã e só entra com "Aplicar à produção".
- **Revisão**: em produção, cada saco não OK para a inspeção e abre uma janela com a imagem;
  "Descartar" (não conta) ou "Mover para NOK". As decisões ficam em `revisoes.csv`.
- Vistas **Operador** (estado em grande) e **Engenharia** (etapas, log, "Guardar no dataset");
  o **Histórico** abre numa janela à parte. As imagens ampliam com a roda do rato.
- **Guardar no perfil** escreve os parâmetros no perfil escolhido (`configuracoes/*.json` ou `config.json`).

## Estado

| | |
|---|---|
| **Funcional** | Mestre, M1 (pasta, vídeo, disparo), M2, M3, M7, interface gráfica, testes |
| **Por fazer** | câmara (`FonteSpinnaker`), M4, M5, M6, `afinar`, `avaliar` |

Próximos passos: dataset e `px_por_mm` medido → validar M2/M3 com fotos reais →
M5 Relevo → M6 → `avaliar` → câmara e M4 → PLC.

## Documentação

- [docs/arquitetura.md](docs/arquitetura.md): Mestre–Escravos, regras, configuração
- [docs/camara.md](docs/camara.md): câmara, lente, montagem, disparo (trigger)
- [docs/modulos.md](docs/modulos.md): como funcionam M2, M3 e M5; depuração
- [docs/dataset.md](docs/dataset.md): organização e como fotografar
