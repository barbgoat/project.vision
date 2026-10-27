# Recursos e referências

## Técnicas usadas

- **Realce de estruturas lineares com a Hessiana**: A. Frangi et al.,
  *Multiscale vessel enhancement filtering*, MICCAI 1998.
- **Extração de linhas com largura e linha central**: C. Steger, *An unbiased
  detector of curvilinear structures*, IEEE PAMI 1998 (base do `lines_gauss` do HALCON).
- **Coerência de orientação / tensor de estrutura**: J. Bigün e G. Granlund,
  *Optimal orientation detection of linear symmetry*, 1987; B. Jähne, *Digital Image Processing*.
- **Diferença de cor**: CIE ΔE76 / ΔE2000 em CIELAB.
- **Avaliação de deteção**: precisão, sensibilidade e F1 com emparelhamento
  entre deteções e anotações.

## A considerar mais tarde

- **scikit-image** (`frangi`, `hessian_matrix`, `structure_tensor`,
  `skeletonize`) como referência para validar a implementação em OpenCV.
- **Estéreo fotométrico** (várias luzes de direções diferentes): dá o relevo
  diretamente; é o método industrial mais forte para riscos e vincos em
  superfícies brilhantes.
- **Câmara polarizada** (há Blackfly S polarizadas): reduz os reflexos do plástico.
- **Deteção de anomalias com aprendizagem profunda** (Anomalib: PatchCore,
  EfficientAD): treina só com sacos OK; pode entrar como mais um escravo para
  comparar com a abordagem clássica. Dataset de referência: MVTec AD.
