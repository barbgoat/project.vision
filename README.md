# Inspeção visual de sacos de plástico (protótipo v1)

Visão por computador clássica (OpenCV) para inspecionar sacos numa linha
industrial: uma decisão **OK/NOK por saco** e cada defeito localizado.

- Sacos de 10–20 × 3–8 cm, em qualquer posição e rotação; 2 sacos/s (**500 ms por saco**).
- Câmara fixa: Basler (pypylon) agora, FLIR Blackfly S BFS-PGE-50S5C-C mais tarde; lente Basler C23-0828-16M (8 mm).
- Dois modos: **Ao vivo** (a câmara capta em contínuo e uma barreira virtual separa os sacos) e
  **Trigger** (um botão ou sensor na entrada da câmara: um saco por sinal).

```
MESTRE (main.py, mestre.py)
 ├─► M1 Aquisição ...... as imagens de cada saco
 ├─► M2 Localização .... onde está o saco? (mede-o e endireita-o)
 ├─► M3 Dimensões · M5 Relevo (por fazer)     ← um relatório cada
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
ferramentas/         testar um módulo, o disparo ou a câmara; avaliar; referencia; qualidade; validar; rever_sessao; plc_teste
configuracoes/       perfis por montagem: bancada_botao, bancada_virtual, linha; referencias/ dos sacos OK
dataset/             imagens da câmara por montagem e por classe (criadas pela BIapp); fora do git
testes/              testes automáticos (python -m pytest)
docs/                documentação
```

## Instalar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Câmara Basler: o `pypylon` (já no `requirements.txt`). Mais tarde a Blackfly: PySpin, da wheel do
Spinnaker 4.4 em `instaladores\`, e uma `FonteSpinnaker` em `modulos/m1_aquisicao/fontes.py` (há um rascunho no
histórico do git, antes da limpeza de 2026-11-17).
Ligação, modos e ensaios: [docs/camara.md](docs/camara.md).

## Correr

```powershell
python main.py --config configuracoes\linha.json                          # ao vivo (o modo do perfil)
python main.py --config configuracoes\linha.json --disparo trigger --depurar nok   # + cada etapa dos NOK
python main.py --config configuracoes\linha.json --of 2026-0412 --produto PreVital
python gui.py                                                                 # interface gráfica
python biapp_dashboard.py                                                     # dashboard à parte (SSE)
python -m ferramentas.testar_disparo --config configuracoes\linha.json        # só a separação dos sacos
python -m ferramentas.testar_modulo dimensoes <imagem ou pasta> --config configuracoes\linha.json
python -m nucleo.config --config configuracoes\linha.json                     # valores em uso
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

## Validação das medidas

O M2 separa o saco do fundo pela diferença de cor ao fundo (ΔE; CPU, ~50 a 80 ms; precisa de um
fundo que contraste). O `validar` compara as medidas com as do paquímetro:

```powershell
python -m ferramentas.validar --config configuracoes\linha.json
```

Usa `dataset/teste/` (`imagens/` e `ground_truth.csv` com as medidas do paquímetro) e dá o erro em
mm, a taxa de ERRO e o tempo, por tipo de saco e por fundo, e a repetibilidade do mesmo saco
([docs/dataset.md](docs/dataset.md)).

## O que um OK garante

- **Módulos que verificaram**: um saco OK diz sempre o que ficou por ver (módulos por implementar,
  com ERRO ignorado ou desligados): na faixa ("OK saco 12 · só dimensoes"), no painel, na imagem
  anotada, no `registo.csv` (`nao_verificado`), na base de dados e no dashboard ("OK sem verificar").
  Em produção, `geral.exigir_todos = true`: um módulo ativo que não concluiu dá ERRO, em vez de OK.
- **Referência dos sacos OK** (`nucleo/referencia.py`): a média e o desvio-padrão de cada medida
  nos sacos bons de um produto. Cada saco é comparado com eles; longe dos bons
  (`referencia.sigmas`) fica **atípico**. É um aviso, o estado não muda, e mostra uma produção a
  desviar-se antes dos NOK. Refazer quando a montagem, a luz, a escala ou o nominal mudarem: uma
  referência que não bate certo deixa de ser usada (com um aviso no log).
- **Rejeições falsas e defeitos escapados**: o `avaliar` passa o dataset da montagem pelo sistema e
  dá a taxa de cada um com o intervalo de confiança (0 em 30 sacos OK ainda quer dizer "até ~11 %";
  para "abaixo de 1 %" são precisos 380 sacos OK sem nenhuma), e o módulo responsável.

```powershell
python -m ferramentas.referencia --config configuracoes\bancada_botao.json --produto Garnier dataset\bancada_botao\ok
python -m ferramentas.referencia --config configuracoes\bancada_botao.json --produto Garnier --ver
python -m ferramentas.avaliar --dataset dataset\bancada_botao --config configuracoes\bancada_botao.json --produto Garnier --guardar-erros
```

As imagens: "Guardar no dataset" na BIapp (vista Engenharia), com a montagem e a luz da produção;
muitos sacos OK (≥ `referencia.sacos_min`, melhor 30 a 50 para a referência e centenas para o
`avaliar`), de vários sacos físicos, em posições e rotações diferentes.

## Interface gráfica

- Abre com **login** (contas em `interface/login.py`): `admin`/`admin` faz tudo; `op`/`op`
  só vê a vista Operador. F5 inicia, Shift+F5 para.
- **OF**: o operador indica a OF e/ou o produto a cada início. OF ou produto conhecidos
  usam as especificações guardadas (uma OF nova herda as da última OF do produto); senão abre o
  formulário. O operador cria e edita a OF com o nome e o nº; o admin muda tudo e tem o **Dashboard**
  (OFs e produtos, OK/NOK, defeitos por tipo, taxa de NOK, evolução). Permissões em `ACESSOS`.
  Mudar de OF a correr só conta a partir do saco seguinte.
- Barra: o menu **⚙** à esquerda (definições, câmara, histórico, dashboard, vista, mudar de utilizador),
  a **OF** ao centro, e o estado, Ao vivo/Trigger, Iniciar e Parar à direita. O perfil fica
  lembrado para a sessão seguinte; o modo inicial é o do perfil (`disparo.modo`).
- **Atalho no ambiente de trabalho**: aponta para `.venv\Scripts\pythonw.exe gui.py`, com a pasta do
  projeto como pasta de trabalho e o ícone `interface\recursos\biapp.ico`. Sem consola: o log vai para
  `resultados\biapp.log`.
- **Ao vivo**: a câmara capta em contínuo e a barreira virtual separa os sacos.
- **Trigger**: a câmara só capta com o sinal do botão ou do sensor: um saco por sinal.
- Nos dois, uma decisão por saco, registada como no `main.py`. Um parâmetro mudado é
  pré-visualizado no saco do ecrã e só entra com "Aplicar à produção".
- **Revisão**: cada saco não OK para a inspeção e abre uma janela com a imagem;
  "Descartar" (passagem má: não conta), "Era OK" (falso NOK: não conta, a imagem vai para
  `dataset/<perfil>/ok/`) ou "Mover para NOK" (a imagem vai para a pasta do defeito escolhido,
  proposto pelo sistema). As decisões ficam em `revisoes.csv`, as classes em `correcoes.csv`;
  na base de dados o falso NOK conta como descartado. Os originais da sessão ficam em
  `originais/ok/`, `nok/` e `erro/`: o que o sistema disse, ainda por confirmar.
- Vistas **Operador** (estado em grande) e **Engenharia** (etapas, log, "Guardar no dataset");
  o **Histórico** abre numa janela à parte (menu ⚙). As imagens ampliam com a roda do rato.
- **Guardar no perfil** escreve os parâmetros no perfil escolhido (`configuracoes/*.json` ou `config.json`).

## Estado

| | |
|---|---|
| **Funcional** | Mestre, M1 (câmara Basler, Ao vivo e Trigger), M2, M3, M7, interface gráfica, produtos e OFs, referência dos sacos OK, `avaliar` (por saco, módulo e classe), testes |
| **Por fazer** | ensaios com a Basler real, Blackfly (`FonteSpinnaker`), M5 Relevo, verificar a cor e as manchas, `avaliar` por defeito (anotações), servidor partilhado, assistente do nominal |

Próximos passos: dataset e `px_por_mm` medido → validar M2/M3 com fotos reais → referência dos
sacos OK e `avaliar` → M5 Relevo → câmara → PLC.

## Documentação

- [docs/arquitetura.md](docs/arquitetura.md): Mestre–Escravos, regras, configuração
- [docs/camara.md](docs/camara.md): câmara, lente, montagem, disparo (trigger)
- [docs/modulos.md](docs/modulos.md): como funcionam M2, M3 e M5; depuração
- [docs/dataset.md](docs/dataset.md): organização e como fotografar
