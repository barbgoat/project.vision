# Dataset

```
dataset/
├─ telemovel/    fotos do telemóvel   → --config configuracoes\telemovel.json
└─ blackfly/     imagens da câmara    → --config configuracoes\bancada_*.json ou linha.json

Em cada uma:  ok/   dimensao/ cor/ risco/ dobra/ rugas/ amasso/ mancha/ (um defeito)   varios/
```

- A pasta é a classe real: `ok/` é OK, as outras são NOK.
- Nunca misturar montagens: cada uma tem o seu `px_por_mm`.
- As imagens não vão para o git (só as pastas e as anotações); para as guardar, Git LFS.
- **Anotações** (obrigatórias em `varios/`): um `.json` do [LabelMe](https://github.com/wkentaro/labelme)
  com o nome da imagem; rótulo = tipo do defeito.

## Segmentação (para treinar uma rede)

`python -m ferramentas.anotar dataset\telemovel` propõe o contorno do saco em cada foto (qualquer
fundo); Enter guarda, um retângulo à volta do saco corrige (GrabCut), o pincel afina; também pontos com
zoom (3, e para editar), SAM 3 por texto (s) e SAM 2 por cliques (4). Fica em
`dataset/segmentacao/`: `imagens/`, `mascaras/` (PNG), `etiquetas/` (YOLO-seg), `anotacoes_coco.json` e
`metadados.csv` (`--tipo`, `--fundo`, `--saco`). Variar fundos, luz,
distância e tipos de saco; incluir algumas fotos sem saco (tecla v). Cada foto guardada ensina
o modelo (`modelo.npz`: cores e textura do saco e do fundo), que dá as propostas seguintes;
`--treinar` refaz-o depois de corrigir máscaras à mão. Com 8 fotos (folha A4 e fundo preto),
cada foto proposta pelo modelo das outras 7 coincide ≥ 99 % com a anotação. O M2 usa o mesmo
modelo com `localizacao.segmentador = "aprendido"`. Um fundo nunca anotado engana-o (com só 3
fotos da folha sobre inox, achou que o couro preto era saco): anotar algumas fotos de cada fundo.

Divisão: `python -m ferramentas.dividir_dataset` põe cada saco físico (`--saco`, ou a pasta da
foto) num só conjunto (treino, validação ou teste); `--fora-fundo X` / `--fora-tipo Y` testam
num fundo ou num saco nunca vistos; `--copiar` cria `rfdetr/` (COCO) e `yolo/` prontos a treinar.

## Conjunto de teste (validar os segmentadores)

`dataset/teste/imagens/` com as fotos (com a referência de escala do perfil), `ground_truth.csv`
(`ficheiro;saco;tipo_saco;fundo;comprimento_mm;largura_mm`, medidas com o paquímetro; `saco` = o
saco físico) e, opcional, `mascaras/` (PNG com o mesmo nome) para o IoU. Várias fotos do mesmo
saco dão a repetibilidade. `python -m ferramentas.validar --config <perfil> --backends delta_e sam3`.

## Como fotografar (telemóvel)

- JPG (não HEIC), sem flash nem HDR, um saco por foto.
- Uma referência de tamanho em cada foto (`localizacao.referencia`, perfil `telemovel`): a foto
  é endireitada por ela, a distância e a inclinação do telemóvel deixam de contar e as medidas
  saem em mm reais (`px_por_mm` = 5).
  - `"folha"`: o saco **em cima de uma folha A4**, inteira na foto, lisa, com os 4 cantos
    visíveis e fundo à volta.
  - `"marcadores"` (qualquer fundo): imprimir [marcadores_aruco.png](marcadores_aruco.png) a 100 %,
    medir o lado (40 mm; senão, pôr o medido em `localizacao.marcadores_lado_mm`), recortar com a
    orla branca e pôr um em cada canto **à volta** do saco, planos e inteiros na foto. Só fica
    na imagem o que está entre os marcadores: o saco tem de caber aí, com fundo à volta.
- Variar posição, rotação e intensidade dos defeitos; muitos sacos OK; guardar
  algumas imagens à parte, nunca usadas para afinar.
