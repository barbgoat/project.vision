# Dataset

As imagens vêm da câmara: na BIapp (vista Engenharia), "Guardar no dataset" copia a imagem
do saco no ecrã para a pasta da classe certa, e a depuração de cada saco guarda a
`00_original.png`. A revisão de cada saco não OK também as põe cá: "Era OK" em `ok/`, "Mover
para NOK" na pasta do defeito escolhido (corrigir depois na Engenharia move a imagem, não a
duplica). Os `originais/ok|nok|erro/` da sessão são o veredito do sistema, não a verdade: os OK
não confirmados não entram aqui sozinhos. A pasta `dataset/` não está no git (as imagens são
grandes; para as guardar, Git LFS) e cria-se sozinha.

Para os OK (onde se escondem os falsos OK), rever uma amostra de cada sessão:
`python -m ferramentas.rever_sessao resultados\<sessão> --config configuracoes\<perfil>.json`
(50 ao acaso; `--amostra 0` todos; `--pasta nok` ou `erro` para os outros). Enter = OK, 1–7 o
defeito, v vários, espaço salta. Os já revistos são saltados; no fim diz quantos o sistema errou.
Com `saida.originais_max_sacos` só ficam os últimos N sacos na sessão: rever antes que saiam.

```
dataset/
└─ <perfil>/     uma pasta por montagem (linha, bancada_botao...): cada uma tem o seu px_por_mm

Em cada uma:  ok/   dimensao/ cor/ risco/ dobra/ rugas/ amasso/ mancha/ (um defeito)   varios/
```

- A pasta é a classe real: `ok/` é OK, as outras são NOK.
- Nunca misturar montagens: cada uma tem o seu `px_por_mm`.
- **Anotações** (obrigatórias em `varios/`): um `.json` do [LabelMe](https://github.com/wkentaro/labelme)
  com o nome da imagem; rótulo = tipo do defeito. Na BIapp, "Guardar no dataset ▾ <defeito>" entra no
  modo zonas: cada retângulo arrastado à volta do defeito vai para esse `.json` ("Concluir zonas" no
  fim). Mudar a classe depois leva a imagem e as zonas para a pasta nova.

## Conjunto de teste (validar as medidas)

`dataset/teste/imagens/` com imagens da câmara (tiradas com a montagem do perfil), `ground_truth.csv`
(`ficheiro;saco;tipo_saco;fundo;comprimento_mm;largura_mm`, medidas com o paquímetro; `saco` = o
saco físico). Várias imagens do mesmo saco dão a repetibilidade.
`python -m ferramentas.validar --config <perfil>`.

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
