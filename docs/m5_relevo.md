# M5 Relevo: riscos, dobras, rugas e amassos

**Estado:** esqueleto. O plano de cada etapa está na docstring da função
respetiva, em `modulos/m5_relevo/`:

| Etapa | Ficheiro | O que faz |
|---|---|---|
| 1. Pré-processamento | `preprocessamento.py` | remove as variações lentas de iluminação |
| 2. Cristas | `cristas.py` | realça as cristas (Hessiana multiescala) e extrai as linhas centrais (Steger) |
| 3. Zonas | `zonas.py` | zonas com muitas cristas: `rugas` (paralelas) ou `amasso` (caóticas) |
| 4. Linhas | `linhas.py` | cristas isoladas e compridas: `risco` (fina) ou `dobra` (larga) |
| liga as etapas | `escravo.py` | mede o tempo de cada etapa (`tempo_<etapa>_ms` no relatório) |

Os quatro defeitos são alterações de relevo que, com iluminação adequada,
aparecem na imagem como cristas (linhas mais claras ou mais escuras do que a
vizinhança).

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

As técnicas e as referências estão em [recursos.md](recursos.md); a iluminação
(luz rasante) e a impressão nos sacos, em [notas_tecnicas.md](notas_tecnicas.md).
