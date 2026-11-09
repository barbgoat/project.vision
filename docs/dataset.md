# Dataset

As imagens vêm da câmara: na BIapp (vista Engenharia), "Guardar no dataset" copia a imagem
do saco no ecrã para a pasta da classe certa, e a depuração de cada saco guarda a
`00_original.png`. A pasta `dataset/` não está no git (as imagens são grandes; para as
guardar, Git LFS) e cria-se sozinha.

```
dataset/
└─ <perfil>/     uma pasta por montagem (linha, bancada_botao...): cada uma tem o seu px_por_mm

Em cada uma:  ok/   dimensao/ cor/ risco/ dobra/ rugas/ amasso/ mancha/ (um defeito)   varios/
```

- A pasta é a classe real: `ok/` é OK, as outras são NOK.
- Nunca misturar montagens: cada uma tem o seu `px_por_mm`.
- **Anotações** (obrigatórias em `varios/`): um `.json` do [LabelMe](https://github.com/wkentaro/labelme)
  com o nome da imagem; rótulo = tipo do defeito.

## Segmentação (para treinar uma rede)

`python -m ferramentas.anotar dataset\linha` propõe o contorno do saco em cada imagem (qualquer
fundo); Enter guarda, um retângulo à volta do saco corrige (GrabCut), o pincel afina; também pontos com
zoom (3, e para editar), SAM 3 por texto (s) e SAM 2 por cliques (4). Fica em
`dataset/segmentacao/`: `imagens/`, `mascaras/` (PNG), `etiquetas/` (YOLO-seg), `anotacoes_coco.json` e
`metadados.csv` (`--tipo`, `--fundo`, `--saco`). Variar fundos, luz e tipos de saco; incluir
algumas imagens sem saco (tecla v). Cada imagem guardada ensina o modelo (`modelo.npz`: cores e
textura do saco e do fundo), que dá as propostas seguintes; `--treinar` refaz-o depois de
corrigir máscaras à mão. O M2 usa o mesmo modelo com `localizacao.segmentador = "aprendido"`.
Um fundo nunca anotado engana-o: anotar algumas imagens de cada fundo.

Divisão: `python -m ferramentas.dividir_dataset` põe cada saco físico (`--saco`, ou a pasta da
imagem) num só conjunto (treino, validação ou teste); `--fora-fundo X` / `--fora-tipo Y` testam
num fundo ou num saco nunca vistos; `--copiar` cria `rfdetr/` (COCO) e `yolo/` prontos a treinar.

## Conjunto de teste (validar as medidas)

`dataset/teste/imagens/` com imagens da câmara (tiradas com a montagem do perfil), `ground_truth.csv`
(`ficheiro;saco;tipo_saco;fundo;comprimento_mm;largura_mm`, medidas com o paquímetro; `saco` = o
saco físico) e, opcional, `mascaras/` (PNG com o mesmo nome) para o IoU. Várias imagens do mesmo
saco dão a repetibilidade. `python -m ferramentas.validar --config <perfil> --backends delta_e sam3`.

## Avaliar o sistema e a referência dos sacos OK

- `python -m ferramentas.avaliar --dataset dataset\<perfil> --config configuracoes\<perfil>.json`
  passa cada imagem das pastas das classes pelo sistema. Dá as falsas rejeições (OK dado como NOK, ERRO
  ou SEM_SACO) e os defeitos escapados, com o intervalo de confiança de 95 %. Diz também o módulo
  responsável por cada falsa rejeição e, por pasta de defeito, se o módulo responsável o apanhou.
  `--guardar-erros` guarda a imagem anotada de cada erro.
- `python -m ferramentas.referencia --config ... --produto <nome> dataset\<perfil>\ok` faz a
  referência dos sacos OK do produto (`configuracoes/referencias/<produto>.json`). Só entram os
  sacos que o sistema também dá como bons; os outros são listados (são falsas rejeições, ou não são
  OK). No fim mostra os sacos do conjunto que ficam atípicos face aos outros: confirmar que são OK.
- As imagens têm de ser da **montagem atual**: mesma ROI e escala, e o mesmo tamanho do fundo
  vazio. Depois de mudar a ROI, a escala ou a luz, voltar a recolher.

## Como recolher as imagens

- Câmara fixa, sempre à mesma distância e de frente para o tapete; `geral.px_por_mm` medido
  com uma régua na imagem. Se a câmara mexer, medir outra vez.
- Um saco de cada vez, inteiro, com fundo à volta; uma imagem do fundo vazio em cada sessão
  (`localizacao.fundo_referencia`).
- Variar posição, rotação e intensidade dos defeitos; muitos sacos OK; guardar
  algumas imagens à parte, nunca usadas para afinar.
