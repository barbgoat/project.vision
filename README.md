# Inspeção visual de sacos de plástico (protótipo v1)

Protótipo de inspeção visual de sacos de plástico numa linha industrial, com
visão por computador clássica (OpenCV, sem deep learning), organizado numa
arquitetura **Mestre–Escravos**.

Condições da linha: sacos de 10–20 × 3–8 cm, com orientação variável, em
movimento contínuo; 2 sacos/s, ou seja, **500 ms por saco** no máximo; câmara
FLIR Blackfly S BFS-PGE-50S5C-C (5 MP, cor, global shutter) com lente Basler de
8 mm (ver "Câmara e lente").

## Arquitetura Mestre–Escravos

O programa está dividido em módulos independentes. Cada um responde a **uma
só pergunta**, tem os **seus próprios parâmetros** e fala com o Mestre através
de um **contrato comum**. Se um módulo falhar, o problema fica isolado nele e
o registo diz qual foi e onde.

```
MESTRE (main.py + mestre.py)
 │
 ├─1─► M1 Aquisição ...... "dá-me as imagens de cada saco"      → Passagem (uma por saco)
 ├─2─► M2 Localização .... "onde está o saco? está completo?"   → Saco
 │                          (se não houver saco: SEM_SACO, para aqui)
 ├─3─► Escravos de inspeção, todos com o MESMO contrato:
 │      ├─ M3 Dimensões ... "tem o tamanho e a forma certos?"   → Relatorio
 │      ├─ M4 Cor ......... "tem a cor certa?"                  → Relatorio
 │      ├─ M5 Relevo ...... "riscos, dobras, rugas, amassos?"   → Relatorio
 │      └─ M6 Manchas ..... "tem manchas?"                      → Relatorio
 ├─4─  Decide: OK só se nenhum módulo der NOK ou ERRO
 └─5─► M7 Saída .......... imagem anotada, registos CSV (mais tarde, PLC)
```

| Módulo | Ficheiro | Deteta | Técnica |
|---|---|---|---|
| M1 Aquisição | `modulos/m1_aquisicao/` | — | fontes (pasta, vídeo, webcam, Blackfly S); disparo (foto, sensor, barreira virtual); aquisição em paralelo |
| M2 Localização | `modulos/m2_localizacao.py` | sem saco, saco incompleto | diferença de cor ao fundo (CIELAB) + arestas; momentos; medição pelos lados; recorte normalizado |
| M3 Dimensões | `modulos/m3_dimensoes.py` | `dimensao` | medidas do M2 face ao nominal ± tolerância; silhueta vs retângulo ideal (falta / sobra de material) |
| M4 Cor | `modulos/m4_cor.py` | `cor` | cor mediana em CIELAB, diferença ΔE |
| M5 Relevo | `modulos/m5_relevo/` | `risco`, `dobra`, `rugas`, `amasso` | Hessiana multiescala, linhas de Steger, coerência de orientação |
| M6 Manchas | `modulos/m6_manchas.py` | `mancha` | contraste relativo + componentes ligadas |
| M7 Saída | `modulos/m7_saida/` | — | `registo.csv`, `defeitos.csv`, imagem anotada, depuração (painel com cada etapa) |

### O contrato

- O **M2** devolve um `Saco`: o recorte normalizado do saco, as máscaras e as medidas.
- Cada **escravo de inspeção** (M3–M6) recebe esse `Saco` e devolve um
  `Relatorio`: estado (OK / NOK / ERRO / POR_IMPLEMENTAR), lista de
  `Defeito`, medidas, tempo e mensagem.
- O **Mestre** junta os relatórios num `Resultado` e decide o estado do saco:
  OK, NOK, ERRO ou SEM_SACO.

Está tudo definido em `nucleo/contrato.py`.

### Como o Mestre isola as falhas

- Cada chamada a um escravo é **protegida**. Se rebentar, fica ERRO no
  relatório desse módulo, com o tipo de erro e o **ficheiro e a linha** onde
  aconteceu (p.ex. `ValueError: ... (em modulos/m5_relevo/cristas.py:57 (mapa_cristas))`),
  e os outros módulos continuam.
- O `Saco` é **só de leitura**. Um escravo que tente alterar a imagem ou as
  medidas recebe um erro no próprio módulo e não estraga a análise dos outros.
- Um escravo que não devolva o seu `Relatorio` fica com ERRO de "contrato não cumprido".
- Um módulo ainda **por implementar** aparece como `POR_IMPLEMENTAR`, com o
  ficheiro e a função em falta, e não conta para a decisão. Assim o sistema
  pode ser desenvolvido um módulo de cada vez.
- Quando um módulo dá ERRO, o saco é **rejeitado** (`GERAL_EM_CASO_DE_ERRO =
  "rejeitar"`): na dúvida, não passa. Com `"ignorar"`, decide-se só com os
  restantes módulos.
- Nunca se aprova um saco que nenhum módulo inspecionou: se todos estiverem
  desligados ou por implementar, o estado é ERRO.

### Regras da arquitetura

1. Os módulos só importam do núcleo (`nucleo/`), **nunca uns dos outros**.
2. Só o Mestre (`main.py`, `mestre.py`) e as ferramentas importam módulos.
3. Cada módulo só lê a **sua secção** da configuração (e a `geral`).
4. Os escravos **não alteram o Saco** que recebem; se precisarem, fazem uma cópia.
5. Cada defeito tem **um só dono**: as linhas são do M5 e as áreas do M6
   (o M6 ignora regiões alongadas).
6. **Nenhum número "mágico" no código**: tudo o que pode variar está em
   `nucleo/defines.py`.

### Acrescentar um módulo novo (selagem, furos, impressão...)

1. Criar `modulos/m8_<nome>.py` com uma classe que deriva de `Escravo`
   (`nome`, `pergunta`, `analisar`).
2. Pôr os seus valores em `nucleo/defines.py` (com `<MÓDULO>_ATIVO`), criar a
   sua secção em `nucleo/config.py` a ler esses defines e juntá-la à `Config`.
3. Juntar a classe a `ESCRAVOS` em `mestre.py` e, se tiver tipos novos, a `TipoDefeito`.

Nenhum dos módulos existentes precisa de ser alterado.

## Estrutura

```
Projeto_Framework/
├─ main.py                  MESTRE: linha de comandos e ciclo principal
├─ mestre.py                MESTRE: chama os escravos, isola as falhas, decide
├─ nucleo/                  partilhado por todos (não é um escravo)
│   ├─ contrato.py          Estado, TipoDefeito, Captura, Saco, Defeito, Relatorio, Resultado, Escravo
│   ├─ defines.py           TODOS os valores que podem variar (é aqui que se mudam)
│   ├─ config.py            agrupa os defines por módulo, valida-os, lê/escreve JSON
│   └─ imagens.py           ler/guardar imagens (acentos), extrair_canal, contraste_relativo
├─ modulos/
│   ├─ m1_aquisicao/
│   │   ├─ fontes.py            de onde vêm as imagens (pasta, vídeo, webcam, Blackfly S)
│   │   ├─ disparo.py           quando começa e acaba cada saco (trigger)
│   │   └─ em_paralelo.py       capta o saco seguinte enquanto o anterior é analisado
│   ├─ m2_localizacao.py
│   ├─ m3_dimensoes.py
│   ├─ m4_cor.py
│   ├─ m5_relevo/           um ficheiro por etapa
│   │   ├─ escravo.py           ponto de entrada (liga as etapas e mede o tempo de cada uma)
│   │   ├─ preprocessamento.py
│   │   ├─ cristas.py
│   │   ├─ zonas.py             rugas, amasso
│   │   └─ linhas.py            risco, dobra
│   ├─ m6_manchas.py
│   └─ m7_saida/
│       ├─ registo.py           registo.csv, defeitos.csv, imagens anotadas
│       ├─ desenho.py           a imagem anotada: contorno, defeitos numerados, estado dos módulos
│       └─ depuracao.py         pasta por saco com cada etapa, painel.jpg e resultado.json
├─ ferramentas/
│   ├─ testar_modulo.py     correr UM módulo e ver o relatório completo
│   ├─ testar_disparo.py    ver que passagens (sacos) o disparo encontra num vídeo
│   ├─ calcular_montagem.py lente, distância, px/mm, FPS e exposição para esta câmara
│   ├─ afinar.py            barras deslizantes para os parâmetros de um módulo
│   └─ avaliar.py           métricas por saco, por módulo e por defeito
├─ config.json              (opcional) só os valores que mudam em relação aos defines
├─ requirements.txt
├─ dataset/                 ok/, um tipo de defeito por pasta, varios/
└─ resultados/              saídas do main.py e do avaliar.py
```

### Estado atual da implementação

| Parte | Estado |
|---|---|
| Núcleo: contrato, configuração, ler/guardar imagens | funcional |
| Mestre: isolamento de falhas, decisão, tempos | funcional |
| `main.py` (ciclo completo, uma decisão por saco), `testar_modulo.py`, `testar_disparo.py` | funcionais |
| M1: `FontePasta`, `FonteVideo` | funcional |
| M1: disparo (foto, sensor, virtual) e aquisição em paralelo | **funcional** (ver secção "Disparo") |
| M1: `FonteWebcam`, `FonteSpinnaker` (com a configuração do trigger documentada) | esqueleto |
| M2 Localização | **funcional** (ver secção "M2 Localização") |
| M3 Dimensões | **funcional** (ver secção "M3 Dimensões") |
| M4 a M6: algoritmos de visão | esqueleto (docstring com o plano + `NotImplementedError`) |
| M7: registos CSV, imagem anotada (`desenhar`) e depuração (`--depurar`) | **funcional** (ver secção "Depuração") |
| `afinar.py`, `avaliar.py` | esqueleto (argparse + fluxo em comentários) |

## Instalação

No PowerShell, na raiz do projeto:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Se o PowerShell não deixar ativar o venv, correr uma vez
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

O PySpin não está no PyPI; instala-se a partir da wheel da FLIR (Spinnaker 4.4, Python 3.12):

```powershell
pip install ".\spinnaker_python-4.4.0.246-cp312-cp312-win_amd64\spinnaker_python-4.4.0.246-cp312-cp312-win_amd64.whl"
```

## Como correr

Sempre a partir da raiz do projeto. Todos os comandos têm `--help`.

```powershell
python -m nucleo.config                                   # mostra todos os valores em uso (defines + config.json)
python main.py --fonte pasta --pasta dataset --mostrar    # fotos: cada foto é um saco
python main.py --fonte video --video linha.mp4 --disparo virtual
python main.py --fonte spinnaker --disparo sensor --guardar nok
python -m ferramentas.testar_disparo --video linha.mp4 --guardar resultados\disparo
python -m ferramentas.testar_modulo localizacao dataset\ok\saco_001.jpg
python -m ferramentas.testar_modulo relevo dataset\rugas
python -m ferramentas.afinar relevo dataset\rugas\saco_007.jpg
python -m ferramentas.avaliar --guardar-erros
```

| Comando | Para quê |
|---|---|
| `main.py` | Lê da fonte, inspeciona cada saco e escreve uma linha por saco na consola, com o estado de cada módulo. Cria `resultados/<data_hora>/` com o `registo.csv` (uma linha por saco, com o estado e o tempo de **cada módulo** e as mensagens de erro), o `defeitos.csv` (uma linha por defeito) e as imagens anotadas. `--depurar nok` guarda a depuração (painel com cada etapa) dos sacos com problemas. `--detalhe` mostra o traceback completo dos erros. |
| `ferramentas.calcular_montagem` | A partir das características da câmara: campo de visão, px/mm, imagens por saco, arrasto, débito na rede, lente necessária. |
| `ferramentas.testar_disparo` | Corre só o M1 (fonte + disparo) num vídeo ou sequência de imagens e mostra cada passagem: duração, imagens, candidatas e avisos. |
| `ferramentas.testar_modulo` | Corre só o M2 e o módulo pedido (mesmo que desligado) numa imagem ou pasta e mostra o relatório completo: estado, tempo, medidas, defeitos e, se falhar, onde. Com `--guardar`, grava a depuração de cada imagem (painel). |
| `ferramentas.afinar` | Barras deslizantes para os parâmetros de um módulo; só a secção desse módulo é alterada. |
| `ferramentas.avaliar` | Métricas por saco (matriz de confusão, escapados, falsas rejeições), por módulo (qual causa cada falsa rejeição, tempos) e por defeito (precisão, sensibilidade, fundidos, partidos). |

Exemplo do que aparece na consola nesta fase (M2 e M3 feitos, M4–M6 em esqueleto):

```
WARNING mestre: Módulo relevo ainda por implementar (modulos/m5_relevo/preprocessamento.py:30 (preprocessar)); ...
certo.jpg: OK 71 ms | localizacao=OK dimensoes=OK cor=POR_IMPLEMENTAR relevo=POR_IMPLEMENTAR manchas=POR_IMPLEMENTAR
canto_e_aba.jpg: NOK 74 ms | localizacao=OK dimensoes=NOK(2) cor=POR_IMPLEMENTAR relevo=POR_IMPLEMENTAR manchas=POR_IMPLEMENTAR | 2 dimensao
saco_002.jpg: SEM_SACO 80 ms | localizacao=SEM_SACO
```

Enquanto M4–M6 estiverem por implementar, a decisão é só do M3: "OK" quer dizer
"OK nas dimensões". Os módulos `POR_IMPLEMENTAR` não contam, mas aparecem em
cada linha. Se nenhum módulo de inspeção concluir, o saco fica em ERRO: o
sistema nunca aprova um saco que ninguém inspecionou.

## Depuração: ver cada etapa para perceber onde está o erro

```powershell
python main.py --fonte pasta --pasta dataset --depurar nok      # só os sacos NOK, ERRO e SEM_SACO
python main.py --fonte pasta --pasta dataset --depurar todas    # todos
python -m ferramentas.testar_modulo dimensoes dataset --guardar resultados\verificacao   # a mesma, para um módulo
```

Para cada saco depurado fica uma pasta em `resultados/<data_hora>/depuracao/`
(e o `registo.csv` diz qual, na coluna `depuracao`):

```
saco_0012_canto_dobrado_NOK/
   painel.jpg                   ← TUDO numa só imagem: abrir este primeiro
   00_original.png              ← a imagem analisada, sem alterações: para repetir o caso
   01_anotada.jpg               ← contorno, defeitos numerados, estado de cada módulo
   02_disparo_candidata_1.jpg   ← (modos sensor/virtual) as candidatas da passagem
   02_localizacao_diferenca.png ← M2: quanto cada píxel difere do fundo (claro = muito)
   03_localizacao_segmentacao.png  ← M2: a máscara do saco
   04_localizacao_recorte.png   ← M2: o saco endireitado, com a zona útil a amarelo
   05_dimensoes_forma.png       ← M3: retângulo ideal (amarelo), falta (vermelho), sobra (magenta)
   ...                          ← o M5 e o M6 juntam-se sozinhos, quando existirem
   resultado.json               ← estados, medidas, limiares usados, defeitos, tempos,
                                  traceback completo dos erros, valores que o config.json muda
```

O **painel** mostra as etapas lado a lado, por ordem, e o resumo por baixo:
o estado e o tempo de cada módulo, as medidas, os defeitos e, a vermelho, os
erros. Como ler: percorrer as etapas da esquerda para a direita e procurar a
primeira que já está mal. Exemplo: o M3 diz "comprimento fora", mas o saco está
bom; se a **segmentação** apanhou a sombra, o problema é do M2 (contraste,
`LOCALIZACAO_PESO_LUMINOSIDADE`); se a segmentação está certa, é do M3
(nominal, tolerância) ou do `GERAL_PX_POR_MM`.

**Repetir um caso:** copiar o `00_original.png` para uma pasta, mudar o define
suspeito e correr `python main.py --fonte pasta --pasta <essa pasta> --depurar todas`.

**Custo:** os módulos só produzem as imagens das etapas quando a depuração está
ligada, e gravá-las gasta tempo e disco (nos testes, ~0,25 s por saco). Por
omissão está desligada (`SAIDA_DEPURAR = "nenhuma"`): usar em ensaios, nunca na
linha. Se a própria depuração falhar, a inspeção continua (o erro fica no log).

**Para quem faz um módulo novo:** basta pôr as imagens de cada etapa em
`Relatorio.intermedios` (quando `guardar_intermedios` for `True`); aparecem
sozinhas na pasta e no painel.

## Câmara e lente

**Câmara: FLIR Blackfly S BFS-PGE-50S5C-C** (PoE GigE, cor, Sony IMX264)

| Característica | Valor | O que implica |
|---|---|---|
| Resolução | 2448 × 2048 px (5 MP), píxel de 3,45 µm, sensor 2/3" (8,45 × 7,07 mm) | com a lente abaixo: **~7,9 px/mm** (0,13 mm por píxel) |
| FPS | até 24 a resolução total | `DISPARO_FPS = 24`; ~7–8 imagens com o saco nominal inteiro a 500 mm/s |
| Obturador | global | sem deformação dos sacos em movimento |
| Exposição | 11 µs a 30 s | tapete a andar, a 500 mm/s: ≤ 125 µs para o arrasto ficar ≤ 0,5 px, o que **pede muita luz** |
| Interface | GigE Vision 1.2 (~120 MB/s), PoE | 24 FPS só em `BayerRG8` (8 bits, cor reconstruída no PC); com `BGR8` ~8 FPS, com 12 bits ~16 |
| Profundidade | 12 bits | usamos 8 bits (`BayerRG8`): chega para a inspeção e permite os 24 FPS |
| Buffer interno | 240 MB | ~48 imagens guardadas na câmara se a rede atrasar |

**Lente: Basler C23-0828-16M** ([documentação](https://docs.baslerweb.com/c23-0828-16m))

| Característica | Valor | O que implica |
|---|---|---|
| Distância focal | 8 mm (ângulo horizontal 56,4°) | a **300 mm** do tapete: campo de **308 × 258 mm** |
| Distância ótima de trabalho | 0,3 m (foco de 0,1 m ao infinito) | `LENTE_DISTANCIA_MM = 300` |
| Abertura | F2.8–F16, mais nítida a **F4** | a F4, profundidade de campo de ~78 mm: chega para sacos pousados |
| Distorção | 0,07 % | desprezável: não é preciso corrigir a distorção para medir |
| Círculo de imagem | 11,8 mm | cobre a diagonal do sensor (11,0 mm): cantos sem escurecer |

**Atenção, grande-angular:** o que está mais alto parece maior. Um saco com
5 mm de altura parece ~2,5 mm mais comprido (em 150 mm), perto da tolerância de
±3 mm do M3. Calibrar o `GERAL_PX_POR_MM` com a régua **à altura do contorno do
saco** (não pousada no tapete) e medir com o saco centrado (o disparo já escolhe
essa imagem). Para medidas mais exatas no futuro: uma lente de 16 mm
(C23-1628-16M) a ~600 mm reduz o efeito para metade.

As características estão nos defines `CAMARA_*` e `LENTE_*`. A configuração
recusa pedidos impossíveis: FPS acima de 24, exposição fora de 11 µs–30 s ou
maior do que o intervalo entre imagens, lente que não cobre o sensor.

**Montagem:**
- lente a ~300 mm do tapete, focada à altura dos sacos, abertura F4;
- o lado de 2448 px **ao longo do movimento do tapete** (mais imagens com o saco inteiro);
- uma **placa de rede só para a câmara**, com "Jumbo Packet" 9014 nas
  propriedades da placa no Windows (`CAMARA_PACOTE_BYTES = 9000`); a 24 FPS a
  ligação GigE fica no limite;
- câmara e placa na mesma sub-rede (no SpinView, "Auto Force IP" se for preciso);
- alimentação por PoE (switch ou injetor PoE entre a câmara e o PC).

**Fases de ensaio** (descritas em `modulos/m1_aquisicao/__init__.py`):
1. sem tapete, saco parado; 2. em cima do tapete, parado; 3. tapete a andar.
Nas fases 1 e 2 a velocidade do tapete não importa e a exposição escolhe-se só
pelo brilho. **Trigger com um botão** ligado à entrada opto-isolada Line0
(disparo `foto`, o valor por omissão): pousar o saco, carregar no botão → uma
imagem → um resultado. Os saltos do botão são ignorados
(`DISPARO_ANTI_RESSALTO_MS` e `TriggerOverlap = Off`). Os mesmos fios servem
depois para o sensor de presença. Ligação: botão normalmente aberto em série
entre uma fonte de tensão externa e a Line0, retorno no OPTO_GND (ligar o
OPTO_GND primeiro); **pinos e tensão máxima a confirmar no guia de instalação
da BFS-PGE**. Sem botão: disparo `virtual` com
`"disparo": {"modo": "virtual", "duracao_max_ms": 60000}` num config.json de ensaio.

**Calcular a montagem:**

```powershell
python -m ferramentas.calcular_montagem                       # câmara + lente dos defines
python -m ferramentas.calcular_montagem --distancia 350       # a mesma lente, mais longe
python -m ferramentas.calcular_montagem --espessura 8         # saco mais alto: que erro nas medidas?
python -m ferramentas.calcular_montagem --focal 16 --distancia 600
```

Mostra o campo de visão e o px/mm (e se bate com `GERAL_PX_POR_MM`), a
profundidade de campo, o erro causado pela altura do saco, se o saco cabe em
qualquer rotação, quantas imagens terá o saco inteiro à vista, o arrasto da
exposição, o débito na rede e a memória. Depois de montar, medir o px/mm com
uma régua e pô-lo em `GERAL_PX_POR_MM`.

## Disparo (trigger): uma passagem = um saco

Com a câmara a captar a vários FPS, o mesmo saco aparece em várias imagens,
desde que entra até que sai do campo de visão. O M1 junta essas imagens numa
**passagem** e o Mestre dá **uma só decisão por saco**:

```
imagens a 24 FPS:  . . . [ s s S S S s s ] . . . [ s s S S S s s ] . . .
                         └── passagem 1 ──┘       └── passagem 2 ──┘
                         S = saco inteiro e perto do centro (candidatas)
```

1. **Disparo** (`modulos/m1_aquisicao/disparo.py`): decide quando começa e
   acaba cada passagem e guarda as melhores imagens (as `DISPARO_CANDIDATAS`
   com o saco mais centrado e inteiro).
2. **Mestre** (`inspecionar_passagem`): experimenta as candidatas pela ordem até
   o M2 encontrar o saco completo e analisa essa imagem. Se nenhuma tiver o
   saco inteiro, o saco fica em ERRO: um saco que passou sem ser inspecionado
   nunca é aprovado.
3. **Aquisição em paralelo** (`em_paralelo.py`): uma thread capta o saco
   seguinte enquanto o anterior é analisado. Se a análise se atrasar mais do
   que `DISPARO_FILA_MAX` sacos, a passagem é descartada e registada como ERRO
   ("não analisada"): nunca se perde um saco em silêncio.

### Modos (`DISPARO_MODO`, ou `--disparo` no `main.py`)

| Modo | Quando usar | Como separa os sacos |
|---|---|---|
| `foto` | fotos do telemóvel, uma por saco; ou a câmara com um **botão** (ou o sensor) na Line0 | cada imagem é uma passagem; os saltos do botão são ignorados |
| `sensor` | **na linha**: sensor de presença ligado à entrada Line0 da Blackfly S | as imagens com o sensor ativo |
| `virtual` | vídeos do telemóvel, webcam, ou câmara sem sensor | o saco a atravessar uma faixa estreita no centro da imagem (barreira ótica virtual) |

No modo `sensor` há duas estratégias (`DISPARO_SENSOR_ESTRATEGIA`):
- `janela`: a câmara capta em contínuo a `DISPARO_FPS` e grava em cada imagem o
  estado do sensor (chunk data `ExposureEndLineStatusAll`); a passagem são as
  imagens com o sensor ativo. Alinhar o sensor com o centro do campo de visão.
- `rajada`: cada ativação do sensor dispara `DISPARO_RAJADA_IMAGENS` imagens
  (`FrameBurstStart`), depois de `DISPARO_RAJADA_ATRASO_US`. Para o saco ficar
  centrado a meio da rajada:
  atraso ≈ (distância do sensor ao centro do campo + comprimento do saco / 2) / velocidade − (N − 1) / (2 · FPS).

### Quantos FPS?

Imagens com o saco **inteiro** à vista ≈ FPS × (campo de visão − comprimento do
saco) / velocidade do tapete. Tem de dar pelo menos 1 (melhor 3 ou mais).
Exemplo com esta câmara: 24 FPS × (306 − 150 mm) / 500 mm/s ≈ 7 imagens. Se o campo de visão
for pouco maior do que o saco, é preciso mais FPS (ou um campo de visão maior).
A exposição tem de caber em 1/FPS.

### Testar já, sem câmara

Filmar sacos a passar com o telemóvel (em H.264, "Mais compatível" no iPhone),
**começando com o tapete vazio** (as primeiras imagens ensinam o fundo), e:

```powershell
python -m ferramentas.testar_disparo --video "D:\Vídeos\linha.mp4" --guardar resultados\disparo
python main.py --fonte video --video "D:\Vídeos\linha.mp4" --disparo virtual
```

O `testar_disparo` mostra cada passagem (duração, nº de imagens, candidatas e a
distância do saco ao centro em cada uma) e grava as candidatas. Confirmar: uma
passagem por saco e a 1.ª candidata com o saco inteiro e centrado. Acertar
`DISPARO_VIRTUAL_DIRECAO` (sentido do tapete na imagem) e `GERAL_PX_POR_MM`.

Testes com uma linha sintética (6 sacos de cores diferentes, 100 mm entre
sacos, a ponta do seguinte já à vista, 24 FPS): 6 passagens de 9 imagens; 1.ª
candidata com o saco a 0–5 % do centro; o M2 mede-o com 150,0–150,2 × 55,0–55,1 mm
(real 150 × 55). Sacos encostados não se separam (como com um sensor físico)
e o Mestre não os aprova.

## M2 Localização: como funciona e como verificar

O saco pode chegar em qualquer posição e com qualquer rotação; o M2 encontra-o,
mede-o e entrega aos outros módulos um recorte com o saco sempre na horizontal.

1. **Modelo do fundo**: uma foto do fundo vazio (`LOCALIZACAO_FUNDO_REFERENCIA`)
   ou, sem ela, uma superfície suave ajustada à faixa junto à borda da imagem
   (acompanha a luz rasante, que deixa um lado mais claro).
2. **Diferença de cor ao fundo** (ΔE, em CIELAB): serve para sacos de qualquer
   cor, mais claros, mais escuros ou de outra cor do que o fundo. Limiar
   automático (Otsu), nunca abaixo de `LOCALIZACAO_DELTA_E_MIN`.
3. **Arestas**, só se a cor não chegar (sacos transparentes): o contorno e os
   reflexos fecham o saco e o interior é preenchido.
4. **Orientação** pelos momentos da forma; **medidas** como um paquímetro:
   posição mediana de cada lado, sem os cantos (a "escada" de píxeis de um
   saco rodado não engana a medida).
5. **Recorte normalizado**, rodado e centrado, e **zona útil** (sem
   `margem_borda_mm` junto ao contorno e sem `margem_extremidades_mm` nas pontas).

Quando não encontra saco, o registo diz porquê: "nada difere do fundo", "o
saco toca na borda da imagem (incompleto)" ou "objeto com 41 x 21 mm, fora dos
limites de um saco".

Resultados em imagens sintéticas (5 a 15 px/mm, luz rasante, ruído):

| Caso | Erro nas medidas | Erro no ângulo |
|---|---|---|
| Opacos de 8 cores (branco, preto, vermelho, azul, verde, amarelo, cinzento, rosa) | ≤ 0,13 mm | ≤ 0,03° |
| 12 rotações, de −89° a +90°, e várias posições na imagem | ≤ 0,2 mm | ≤ 0,03° |
| Translúcidos (25–40 %) | ≤ 0,14 mm | ≤ 0,02° |
| Transparente (só contorno e reflexos) | ~0,9 mm | 0,02° |
| Fundo com textura, com foto de referência | 0,1 mm | — |

Tempo: ~80 ms numa imagem de 5 MP (Blackfly) e ~110 ms numa de 12 MP (telemóvel).

**Para verificar com as tuas fotos:**

```powershell
python -m ferramentas.testar_modulo localizacao dataset\ok --guardar resultados\verificacao_m2
```

Para cada foto, aparecem na consola as medidas, o ângulo e o limiar usado, e
fica na pasta indicada uma pasta de depuração (ver "Depuração") com o
`painel.jpg` e as etapas do M2: a imagem anotada (contorno, centro e eixo
maior), a **diferença ao fundo** (claro = muito; é aqui que se vê se o fundo e o
saco se distinguem bem), a máscara do saco e o recorte com a zona útil.

**Dicas:**
- Tira uma foto do **fundo vazio** em cada sessão (mesma posição e luz) e
  indica-a em `LOCALIZACAO_FUNDO_REFERENCIA`. É o que mais ajuda com sacos
  transparentes e fundos com textura.
- Se o contorno incluir a **sombra** do saco, baixa `LOCALIZACAO_PESO_LUMINOSIDADE`
  (p.ex. 0.5); se apanhar **pedaços do fundo**, sobe `LOCALIZACAO_DELTA_E_MIN`.
- Calibra o `GERAL_PX_POR_MM` com uma régua: se estiver errado, os sacos
  são rejeitados como "fora dos limites".

## M3 Dimensões: como funciona e como verificar

O M3 usa as medidas que o M2 já fez (não volta a medir) e responde a "o saco
tem o tamanho e a forma certos?":

1. **Comprimento** e 2. **largura**: NOK se |medido − nominal| > tolerância
   (`DIMENSOES_*_NOMINAL_MM`, `DIMENSOES_TOLERANCIA_*_MM`), p.ex.
   "comprimento 158.1 mm, fora de 150 +/- 3 mm (+8.1 mm)".
3. **Forma**: a silhueta do saco é comparada com o **retângulo ideal** do
   tamanho medido. Cada zona onde **falta** material (canto dobrado, corte
   torto, rasgão na borda) ou **sobra** (aba, dobra para fora) com mais de
   `DIMENSOES_FORMA_AREA_MIN_MM2` é um defeito próprio, com a posição em mm e a
   causa provável. Desvios até `DIMENSOES_FORMA_FOLGA_MM` junto às arestas são
   ignorados (cantos arredondados, soldaduras). Falta num canto e sobra no
   canto oposto da mesma ponta são reconhecidas como **corte torto**.

```
   retângulo ideal (amarelo)      canto dobrado (vermelho)      aba (magenta)
   ┌────────────────┐              ┌──────────────◣┐            ┌────────────────┐
   │                │              │               │            │                │
   └────────────────┘              └───────────────┘            └──────▀▀▀───────┘
```

Medidas no relatório: comprimento, largura, desvio de cada um, área,
retangularidade, área em falta e área a mais.

Resultados em sacos sintéticos medidos pelo M2 (5 px/mm, luz rasante, ruído):

| Caso | Resultado |
|---|---|
| 8 sacos certos (8 cores, rotações de −60° a +89°) | todos **OK**, sem falsos alarmes (150,0–150,1 × 55,0–55,2 mm) |
| comprido (158 mm) / estreito (50 mm) | NOK só pelo comprimento / só pela largura |
| canto dobrado | NOK: "falta material num canto (canto dobrado?): 71 mm² em x=70, y=−22 mm" (real: ±70, ±22,5) |
| corte torto (12 mm) | NOK: comprimento curto + falta e sobra na mesma ponta, "(corte torto?)" |
| aba / rasgão num lado | NOK: "material a mais num lado" em x=0, y=30 mm / "falta material num lado" |
| canto dobrado + aba no mesmo saco | NOK com **2 defeitos separados** |
| `main.py` numa pasta com os 8 casos | saco certo **OK**, os outros 7 **NOK**; M3 em ~5 ms |

Para ver o que o M3 fez a cada foto (etapa `dimensoes_forma` no painel de depuração):

```powershell
python -m ferramentas.testar_modulo dimensoes dataset\dimensao --guardar resultados\verificacao_m3
```

Ao desenvolver o M3 corrigiu-se também o M2: o ângulo do saco passou a ser
afinado pelos lados compridos (antes, um canto dobrado ou uma aba inclinavam o
saco ~0,8° no recorte; agora o erro é de 0,01°).

**Atenção à lente** (grande-angular): um saco com 5 mm de altura parece ~2,5 mm
mais comprido. Calibrar o `GERAL_PX_POR_MM` com a régua à altura do contorno do
saco (ver "Câmara e lente").

## Configuração: os defines

**Todos os valores que podem variar estão em `nucleo/defines.py`**: limites,
tolerâncias, distâncias, limiares, margens... Para mudar um valor, muda-o aí.

```python
# nucleo/defines.py
GERAL_PX_POR_MM: Final = 5.0                     # [MEDIR] píxeis por mm na imagem
DIMENSOES_COMPRIMENTO_NOMINAL_MM: Final = 150.0  # [MEDIR] comprimento do produto
DIMENSOES_TOLERANCIA_COMPRIMENTO_MM: Final = 3.0 # [MEDIR] aceita nominal ± tolerância
LOCALIZACAO_MARGEM_BORDA_MM: Final = 3.0         # faixa junto ao contorno que não é inspecionada
```

- `[MEDIR]`: valor provisório que **tem** de ser medido para a montagem ou o
  produto real. Os restantes são pontos de partida, a afinar com imagens reais.
- Os defines estão agrupados por módulo (GERAL, AQUISICAO, LOCALIZACAO,
  DIMENSOES, COR, RELEVO, MANCHAS, SAIDA). Os que começam por `M2_`, `M3_`...
  são afinações internas de um módulo; normalmente não é preciso mexer neles.
- Comprimentos sempre em **mm** (áreas em mm²); a conversão para píxeis é feita
  com `GERAL_PX_POR_MM`. Nenhum módulo tem números "mágicos" no código: um
  valor que possa variar vai para os defines.
- Valores incoerentes dão erro logo no arranque, com todos os problemas de uma
  vez (p.ex. limiar baixo maior do que o alto, ou o nominal do M3 fora dos
  limites do M2).

Para ver todos os valores em uso, com o define de onde vem cada um:

```powershell
python -m nucleo.config
```

### config.json (opcional): o que muda numa montagem ou produto

Um ficheiro JSON pode sobrepor **alguns** valores aos defines sem mexer no
código, p.ex. o `px_por_mm` do telemóvel, ou as medidas de outro produto. Tem
só o que muda; o resto vem dos defines:

```json
{"geral": {"px_por_mm": 12.4}, "cor": {"ativo": false}}
```

- `config.json` na raiz é usado automaticamente, se existir; outro ficheiro
  escolhe-se com `--config config_telemovel.json`.
- No arranque, o programa diz que valores o JSON está a mudar, p.ex.
  `geral.px_por_mm = 12.4 (define GERAL_PX_POR_MM = 5.0)`. Um valor mudado no
  JSON nunca fica escondido.
- O nome de cada parâmetro é o do define em minúsculas, sem a secção:
  `GERAL_PX_POR_MM` → `"geral": {"px_por_mm": ...}`.
- Gralhas (`relevo.crista_limar_alto`), secções desconhecidas e tipos errados
  dão erro ao carregar.
- O `afinar.py` guarda os valores afinados num JSON destes (só os que mudaram).
  Quando estiveres satisfeito, passa-os para os defines e apaga-os do JSON.

## Vários defeitos no mesmo saco

Cada defeito é reportado individualmente, com tipo, posição, dimensões e área,
e com o módulo que o encontrou: um saco com dois riscos e umas rugas dá três
defeitos, numerados na imagem anotada e com uma linha cada no `defeitos.csv`.
O M5 foi desenhado para não fundir defeitos que se tocam:

| Situação | Como se separa |
|---|---|
| Dois riscos que se cruzam | As linhas centrais são partidas nos cruzamentos e religadas pela continuidade da direção. |
| Um risco partido em troços | Os troços alinhados e próximos são religados (`linha_ligar_*`). |
| Uma dobra vista como duas cristas (clara + escura) | Pares paralelos muito próximos são juntados numa só dobra. |
| Rugas encostadas a um amasso | A coerência de orientação é medida píxel a píxel, o que separa as duas zonas. |
| Um risco que atravessa uma zona de rugas | Continua a ser reportado se a maior parte do comprimento estiver fora da zona. |

## Dataset

```
dataset/
├─ ok/        sacos sem defeitos
├─ risco/  dobra/  rugas/  amasso/  mancha/  cor/  dimensao/
│             sacos com um só tipo de defeito, uma pasta por tipo
└─ varios/    sacos com mais do que um defeito
```

- O nome da pasta é a classe real do saco: `ok/` é OK, todas as outras são NOK.
- **Anotações (recomendado, obrigatório em `varios/`):** para avaliar se cada
  defeito foi encontrado no sítio certo, cada imagem pode ter ao lado um `.json`
  com o mesmo nome (`saco_007.jpg` → `saco_007.json`), feito no
  [LabelMe](https://github.com/wkentaro/labelme). Rótulo = tipo do defeito;
  linhas com *linestrip*, zonas e manchas com *polygon*, defeitos do saco todo
  (dimensão, cor) com *rectangle*. O LabelMe instala-se fora do `.venv` do
  projeto (p.ex. `pipx install labelme`).
- **Fotos de telemóvel:** em JPG (o OpenCV não lê HEIC), telemóvel num suporte
  a altura fixa, régua numa das fotos para calibrar o `px_por_mm`, fundo
  uniforme, saco inteiro na foto, sem flash nem HDR, exposição e foco
  bloqueados. O M4 Cor não é fiável com telemóvel (balanço de brancos automático).
- Variar orientações, posições e intensidade dos defeitos; incluir muitos
  sacos OK, porque são eles que medem as falsas rejeições.
- Guardar à parte algumas imagens que nunca se usam para afinar.

## Fases do projeto

0. **Estrutura Mestre–Escravos** (concluída): contrato, configuração por
   módulo, Mestre com isolamento de falhas, `main.py`, registos CSV.
1. **Dataset**: fotos (telemóvel, depois a câmara), anotações, calibrar `px_por_mm`.
2. **M2 Localização** (concluída): o primeiro, porque todos dependem dele. Falta validar com fotos reais.
3. **M3 Dimensões** (concluída): o sistema já dá OK/NOK de ponta a ponta. Falta validar com fotos reais.
4. **M7 `desenhar` e depuração** (concluída): imagem anotada e painel com cada etapa. Falta o **`afinar.py`**.
5. **M5 Relevo**: o núcleo da v1 (riscos, dobras, rugas, amassos).
6. **M6 Manchas**.
7. **`avaliar.py`**: métricas; iterar até os resultados e o tempo (< 500 ms) serem aceitáveis.
8. **M1 com a câmara + M4 Cor**: `FonteSpinnaker` com o sensor na Line0 (disparo `sensor`), FPS e atraso medidos, balanço de brancos fixo, calibrar a cor de referência.
9. **Integração na linha**: PLC (no M7): sinal OK/NOK por saco.
10. **Depois da v1**: novos escravos para selagem, furos e impressão.

## Notas técnicas

- **Iluminação**: o relevo realça-se com luz rasante (de ângulo baixo). Vale a
  pena testar várias posições da luz antes de afinar o software.
- **Movimento**: arrasto (px) ≈ velocidade × exposição × `px_por_mm`. Por
  exemplo, 500 mm/s × 120 µs × 8 px/mm ≈ 0,48 px.
- **Tempo**: o `registo.csv` tem o tempo de cada módulo e o M5 mede o de cada
  etapa (`tempo_<etapa>_ms`). Se não couber nos 500 ms: desfoques grandes numa
  imagem reduzida, menos escalas na Hessiana e, como os escravos de inspeção
  são independentes, corrê-los em paralelo.
- **Impressão**: os contornos da impressão também são cristas e podem dar
  falsos defeitos no M5 e no M6. Na v1, escolher um canal onde a tinta tenha
  pouco contraste, ou excluir essa zona.
- **Caminhos com acentos**: usar `nucleo.imagens.ler_imagem` e `guardar_imagem`
  em vez de `cv2.imread`/`cv2.imwrite`. O `cv2.putText` não desenha acentos.

## Recursos e referências

Técnicas usadas:
- **Realce de estruturas lineares com a Hessiana**: A. Frangi et al.,
  *Multiscale vessel enhancement filtering*, MICCAI 1998.
- **Extração de linhas com largura e linha central**: C. Steger, *An unbiased
  detector of curvilinear structures*, IEEE PAMI 1998 (base do `lines_gauss` do HALCON).
- **Coerência de orientação / tensor de estrutura**: J. Bigün e G. Granlund,
  *Optimal orientation detection of linear symmetry*, 1987; B. Jähne, *Digital Image Processing*.
- **Diferença de cor**: CIE ΔE76 / ΔE2000 em CIELAB.
- **Avaliação de deteção**: precisão, sensibilidade e F1 com emparelhamento
  entre deteções e anotações.

A considerar mais tarde:
- **scikit-image** (`frangi`, `hessian_matrix`, `structure_tensor`,
  `skeletonize`) como referência para validar a implementação em OpenCV.
- **Estéreo fotométrico** (várias luzes de direções diferentes): dá o relevo
  diretamente; é o método industrial mais forte para riscos e vincos em
  superfícies brilhantes.
- **Câmara polarizada** (há Blackfly S polarizadas): reduz os reflexos do plástico.
- **Deteção de anomalias com aprendizagem profunda** (Anomalib: PatchCore,
  EfficientAD): treina só com sacos OK; pode entrar como mais um escravo para
  comparar com a abordagem clássica. Dataset de referência: MVTec AD.
