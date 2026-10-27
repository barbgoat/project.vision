"""Os escravos do sistema, uma pasta por módulo.

    M1 Aquisição    m1_aquisicao/     dá-me as imagens de cada saco (fontes + disparo)
    M2 Localização  m2_localizacao/   onde está o saco? está completo?
    M3 Dimensões    m3_dimensoes/     tem o tamanho e a forma certos?
    M4 Cor          m4_cor/           tem a cor certa?
    M5 Relevo       m5_relevo/        há riscos, dobras, rugas ou amassos?
    M6 Manchas      m6_manchas/       há manchas?
    M7 Saída        m7_saida/         regista, desenha e depura o resultado

Nos escravos de inspeção, o ponto de entrada que o Mestre chama está em
escravo.py e cada etapa no seu ficheiro. Documentação: docs/arquitetura.md.

Regra: um módulo nunca importa outro módulo; só importa do núcleo (nucleo/).
"""
