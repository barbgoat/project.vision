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
biapp_dashboard.py   o dashboard numa janela própria, em tempo real (SSE)
nucleo/              contrato entre módulos e defines.py (TODOS os valores ajustáveis)
modulos/             um escravo por pasta: m1_aquisicao/ … m7_saida/
ferramentas/         testar um módulo, o disparo ou a câmara; montagem; afinar; avaliar; anotar; validar
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
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json --of 2026-0412 --produto PreVital
python gui.py --pasta dataset\telemovel --config configuracoes\telemovel.json                 # interface gráfica
python biapp_dashboard.py                                                                     # dashboard à parte (SSE)
python -m ferramentas.testar_modulo dimensoes <imagem ou pasta> --config configuracoes\telemovel.json
python -m nucleo.config --config configuracoes\telemovel.json                                 # valores em uso
python -m pytest
python -m ruff check .                                                                        # verificação do código
```

Resultados em `resultados/<data_hora>/`: `registo.csv` (um saco por linha, com
o estado de cada módulo), `defeitos.csv` (um defeito por linha) e imagens; as duas
com a OF e o produto.

## Produtos e ordens de fabrico (OF)

Cada produto tem só o comprimento e a largura nominais com as tolerâncias, a cor predominante
(escrita) e se tem verniz. Os produtos, as OFs e as inspeções ficam numa base de dados
(`nucleo/especificacoes.py`; por agora SQLite em `resultados/especificacoes.sqlite`), com a data e hora
e o dia pelo relógio do PC. Os dados do dashboard e a criação de produtos e OFs estão em
`nucleo/dashboard.py`; na interface só as janelas. A configuração de cada saco é **defines → perfil da
montagem → produto → OF**: o produto e a OF só mudam as medidas e tolerâncias do M3 (os cantos, a
esquadria e o resto são do perfil) e tudo passa pela `Config`. A cor e o verniz ficam guardados mas
ainda não são verificados. Cada gravação é uma versão nova, com o autor.

**Dashboard fora da BIapp**: o atalho **BIapp Dashboard** no Ambiente de Trabalho (criado com
`python biapp_dashboard.py --atalho`) abre uma janela própria, só para ver, que se atualiza sozinha.
Um servidor (`nucleo/servidor_dashboard.py`, só a biblioteca do Python) lê a base de dados e envia o
estado por **SSE** (`http://127.0.0.1:8765/eventos`; uma vez em JSON: `/estado`) ao ligar e sempre
que muda. Funciona com ou sem a BIapp aberta; `--so-servidor` corre só o servidor. Por omissão só
neste PC: o endereço e a porta são `SAIDA_DASHBOARD_*`.

## Segmentação do saco (ΔE ou SAM 3) e validação

O M2 separa o saco do fundo com um **segmentador**, escolhido no perfil em
`localizacao.segmentador`:

| | |
|---|---|
| `delta_e` (omissão) | diferença de cor ao fundo; CPU, ~50 a 80 ms; precisa de um fundo que contraste |
| `aprendido` | as cores e texturas aprendidas com as fotos anotadas (`ferramentas/anotar.py`); CPU, ~100 ms; fundos variados, se tiverem sido anotados |
| `sam3` | SAM 3 da Meta, por texto (`localizacao.sam3_prompt`, "plastic bag"); qualquer fundo; GPU |
| `duplo` | os dois; se as máscaras não coincidirem (IoU < `duplo_iou_min`), ERRO e a foto vai para `dataset/revisao/` |

O contorno subpíxel, as medidas e o M3 são os mesmos com qualquer um. Confiança abaixo de
`sam3_score_min` ou `aprendido_score_min`, ou nenhuma máscara, dá sempre ERRO ("segmentação
incerta"), nunca OK. O `aprendido` usa `dataset/segmentacao/modelo.npz`, que o anotador atualiza a
cada foto guardada: só reconhece bem os fundos que já foram anotados.

**Instalar o SAM 3** (opcional; Python 3.12, PyTorch ≥ 2.7, GPU NVIDIA com CUDA ≥ 12.6):

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
git clone https://github.com/facebookresearch/sam3.git
pip install -e .\sam3 pillow
```

Os **pesos estão no Hugging Face com acesso controlado**: pedir acesso em
<https://huggingface.co/facebook/sam3> (com a conta do Hugging Face), esperar a aprovação, criar
um token e fazer `hf auth login`. Sem GPU funciona, mas é muito lento (só para testes). Na
ferramenta de anotação, a proposta por cliques usa o **SAM 2** (`pip install git+https://github.com/facebookresearch/sam2.git`; pesos
`facebook/sam2.1-hiera-large`, de acesso livre).

```powershell
python -m ferramentas.anotar <pasta> --tipo PreVital --fundo preto    # anotar (GrabCut, pontos, SAM 3, SAM 2)
python -m ferramentas.dividir_dataset --copiar                         # treino/validação/teste por saco físico
python -m ferramentas.validar --config configuracoes\telemovel.json --backends delta_e sam3
```

`validar` usa `dataset/teste/` (`imagens/`, `ground_truth.csv` com as medidas do paquímetro e,
opcional, `mascaras/`) e dá o erro em mm, o IoU, a taxa de ERRO e o tempo de cada segmentador,
por tipo de saco e por fundo, e a repetibilidade do mesmo saco ([docs/dataset.md](docs/dataset.md)).

## Interface gráfica

- Abre com **login** (contas em `interface/login.py`): `admin`/`admin` faz tudo; `op`/`op`
  só vê a vista Operador. F5 inicia, Shift+F5 para.
- **OF**: o operador indica a OF e/ou o produto a cada início em Produção. OF ou produto conhecidos
  usam as especificações guardadas (uma OF nova herda as da última OF do produto); senão abre o
  formulário. O operador cria e edita a OF com o nome e o nº; o admin muda tudo e tem o **Dashboard**
  (OFs e produtos, OK/NOK, defeitos por tipo, taxa de NOK, evolução). Permissões em `ACESSOS`.
  Mudar de OF a correr só conta a partir do saco seguinte.
- Barra: o menu **⚙** à esquerda (definições, câmara, histórico, dashboard, vista, mudar de utilizador),
  a **OF** ao centro, e o estado, Ao vivo/Produção, Iniciar e Parar à direita. O perfil, a fonte e
  a pasta ficam lembrados para a sessão seguinte.
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
  o **Histórico** abre numa janela à parte (menu ⚙). As imagens ampliam com a roda do rato.
- **Guardar no perfil** escreve os parâmetros no perfil escolhido (`configuracoes/*.json` ou `config.json`).

## Estado

| | |
|---|---|
| **Funcional** | Mestre, M1 (pasta, vídeo, disparo), M2, M3, M7, interface gráfica, produtos e OFs, testes |
| **Por fazer** | câmara (`FonteSpinnaker`), M4, M5, M6, `afinar`, `avaliar`, servidor partilhado, assistente do nominal |

Próximos passos: dataset e `px_por_mm` medido → validar M2/M3 com fotos reais →
M5 Relevo → M6 → `avaliar` → câmara e M4 → PLC.

## Documentação

- [docs/arquitetura.md](docs/arquitetura.md): Mestre–Escravos, regras, configuração
- [docs/camara.md](docs/camara.md): câmara, lente, montagem, disparo (trigger)
- [docs/modulos.md](docs/modulos.md): como funcionam M2, M3 e M5; depuração
- [docs/dataset.md](docs/dataset.md): organização e como fotografar
