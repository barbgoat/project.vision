# Inspeção visual de sacos de plástico (protótipo v1)

Protótipo de inspeção visual de sacos de plástico numa linha industrial, com
visão por computador clássica (OpenCV, sem deep learning), organizado numa
arquitetura **Mestre–Escravos**. Na v1: defeitos de superfície grandes (risco,
dobra, rugas, amasso), dimensões, cor e manchas, com uma decisão OK/NOK por
saco e cada defeito localizado individualmente.

Condições da linha: sacos de 10–20 × 3–8 cm, com orientação variável, em
movimento contínuo; 2 sacos/s, ou seja, **500 ms por saco** no máximo; câmara
FLIR Blackfly S BFS-PGE-50S5C-C (5 MP, cor, global shutter) com lente Basler
C23-0828-16M de 8 mm.

```
MESTRE (main.py + mestre.py)
 ├─► M1 Aquisição ...... imagens de cada saco (fontes + disparo)   → Passagem
 ├─► M2 Localização .... onde está o saco? está completo?          → Saco
 ├─► M3 Dimensões, M4 Cor, M5 Relevo, M6 Manchas                   → Relatorio cada
 ├─  decide: OK só se nenhum módulo der NOK ou ERRO
 └─► M7 Saída .......... registos CSV, imagem anotada, depuração
```

## Estrutura

```
main.py, mestre.py     o Mestre: ciclo principal, isolamento de falhas, decisão
nucleo/                partilhado: contrato, defines (TODOS os valores), configuração, imagens
modulos/               os escravos, uma pasta cada (m1_aquisicao/ ... m7_saida/)
ferramentas/           testar_modulo, testar_disparo, testar_camara, calcular_montagem, afinar, avaliar
configuracoes/         perfis por montagem: telemovel, bancada_botao, bancada_virtual, linha
testes/                testes automáticos (pytest) com imagens sintéticas
docs/                  documentação detalhada
dataset/               imagens por montagem e por classe (as imagens não vão para o git)
resultados/            saídas (não vai para o git)
instaladores/          wheel do PySpin (não vai para o git)
```

Estrutura completa: [docs/arquitetura.md](docs/arquitetura.md).

## Estado

| Parte | Estado |
|---|---|
| Núcleo, Mestre (isolamento de falhas, decisão), `main.py` | funcional |
| M1: `FontePasta`, `FonteVideo`, disparo (foto, sensor, virtual), aquisição em paralelo | funcional |
| M1: `FonteWebcam`, `FonteSpinnaker` (configuração do trigger documentada) | esqueleto |
| M2 Localização, M3 Dimensões | **funcional**; falta validar com fotos reais |
| M4 Cor, M5 Relevo, M6 Manchas | esqueleto (docstring com o plano + `NotImplementedError`) |
| M7: registos CSV, imagem anotada, depuração | funcional |
| `testar_modulo`, `testar_disparo`, `calcular_montagem` | funcionais |
| `afinar.py`, `avaliar.py` | esqueleto |

Enquanto M4–M6 estiverem por implementar, a decisão é só do M3: "OK" quer dizer
"OK nas dimensões". Os módulos `POR_IMPLEMENTAR` não contam, mas aparecem em
cada linha; se nenhum módulo de inspeção concluir, o saco fica em ERRO.

## Instalação

No PowerShell, na raiz do projeto (Python 3.12):

```powershell
python -m venv .venv                 # só da primeira vez
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt      # ou requirements-dev.txt, para correr os testes
```

Se o PowerShell não deixar ativar o venv, correr uma vez
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

O PySpin (câmara) não está no PyPI; instala-se a partir da wheel do Spinnaker
SDK 4.4, guardada em `instaladores/` (que não vai para o git: descarregar do
site da Teledyne FLIR):

```powershell
pip install ".\instaladores\spinnaker_python-4.4.0.246-cp312-cp312-win_amd64\spinnaker_python-4.4.0.246-cp312-cp312-win_amd64.whl"
```

## Como correr

Sempre a partir da raiz do projeto. Todos os comandos têm `--help`.

```powershell
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json            # fotos: cada foto é um saco
python main.py --pasta dataset\telemovel --config configuracoes\telemovel.json --depurar nok
python main.py --fonte video --video linha.mp4 --disparo virtual
python main.py --fonte spinnaker --config configuracoes\bancada_botao.json                # câmara + botão (quando a FonteSpinnaker existir)
python -m nucleo.config --config configuracoes\telemovel.json                             # todos os valores em uso
python -m ferramentas.testar_modulo dimensoes dataset\telemovel\dimensao --config configuracoes\telemovel.json --guardar resultados\verificacao
python -m ferramentas.testar_disparo --video linha.mp4 --guardar resultados\disparo
python -m ferramentas.calcular_montagem
python -m pytest                                                                          # testes automáticos
```

O `main.py` escreve uma linha por saco na consola, com o estado de cada módulo:

```
certo.jpg: OK 71 ms | localizacao=OK dimensoes=OK cor=POR_IMPLEMENTAR relevo=POR_IMPLEMENTAR manchas=POR_IMPLEMENTAR
canto_e_aba.jpg: NOK 74 ms | localizacao=OK dimensoes=NOK(2) cor=POR_IMPLEMENTAR ... | 2 dimensao
saco_002.jpg: SEM_SACO 80 ms | localizacao=SEM_SACO
```

e cria `resultados/<data_hora>/` com o `registo.csv` (uma linha por saco, com o
estado, o tempo e as mensagens de **cada módulo**), o `defeitos.csv` (uma linha
por defeito) e as imagens anotadas.

## Fases do projeto

0. **Estrutura Mestre–Escravos** (concluída).
1. **Dataset**: fotos (telemóvel, depois a câmara), anotações, calibrar `px_por_mm`.
2. **M2 Localização** (concluída): falta validar com fotos reais.
3. **M3 Dimensões** (concluída): o sistema já dá OK/NOK de ponta a ponta. Falta validar com fotos reais.
4. **M7 `desenhar` e depuração** (concluída). Falta o **`afinar.py`**.
5. **M5 Relevo**: o núcleo da v1 (riscos, dobras, rugas, amassos).
6. **M6 Manchas**.
7. **`avaliar.py`**: métricas; iterar até os resultados e o tempo (< 500 ms) serem aceitáveis.
8. **M1 com a câmara + M4 Cor**: `FonteSpinnaker` com o botão e depois o sensor
   na Line0; ensaios sem tapete → tapete parado → tapete a andar; balanço de
   brancos fixo, calibrar a cor de referência.
9. **Integração na linha**: PLC (no M7): sinal OK/NOK por saco.
10. **Depois da v1**: novos escravos para selagem, furos e impressão.

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/arquitetura.md](docs/arquitetura.md) | Mestre–Escravos, contrato, isolamento de falhas, regras, estrutura completa, acrescentar um módulo |
| [docs/configuracao.md](docs/configuracao.md) | os defines, os perfis JSON, `[MEDIR]` |
| [docs/depuracao.md](docs/depuracao.md) | `--depurar`: painel com cada etapa, como encontrar onde está o erro |
| [docs/camara_e_lente.md](docs/camara_e_lente.md) | Blackfly S + Basler 8 mm, montagem, fases de ensaio, botão, `calcular_montagem` |
| [docs/disparo.md](docs/disparo.md) | trigger: passagens, modos foto / sensor / virtual, FPS, testar sem câmara |
| [docs/m2_localizacao.md](docs/m2_localizacao.md) | como o M2 encontra e mede o saco; resultados; dicas |
| [docs/m3_dimensoes.md](docs/m3_dimensoes.md) | comprimento, largura, forma (falta / sobra de material) |
| [docs/m5_relevo.md](docs/m5_relevo.md) | etapas do M5; vários defeitos no mesmo saco |
| [docs/dataset.md](docs/dataset.md) | organização, anotações, como fotografar |
| [docs/notas_tecnicas.md](docs/notas_tecnicas.md) | iluminação, movimento, tempo, impressão, acentos |
| [docs/recursos.md](docs/recursos.md) | referências das técnicas e o que considerar mais tarde |
| [configuracoes/README.md](configuracoes/README.md) | os perfis de cada montagem |
| [dataset/README.md](dataset/README.md) | resumo do dataset |
