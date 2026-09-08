"""Template de correções de domínio para text_utils.py.

Como usar:
  1. Copie este arquivo para text_corrections.py (na mesma pasta)
  2. Personalize SUBSTITUTIONS com os erros específicos do seu modelo e domínio
  3. text_corrections.py é gitignored — nunca commitar com dados reais

Estrutura de SUBSTITUTIONS:
  - Chave: texto errado que o ASR produz
  - Valor: texto correto esperado
  - Use word boundaries (já aplicados em correct_common_names via regex \b)
  - Cuidado com termos muito curtos — podem causar substituições indesejadas
"""

NUMBER_WORDS: dict[str, str] = {
    "zero": "0", "um": "1", "dois": "2", "três": "3", "quatro": "4",
    "cinco": "5", "seis": "6", "sete": "7", "oito": "8", "nove": "9",
    "dez": "10", "onze": "11", "doze": "12", "treze": "13", "quatorze": "14",
    "quinze": "15", "dezesseis": "16", "dezessete": "17", "dezoito": "18", "dezenove": "19",
    "vinte": "20", "trinta": "30", "quarenta": "40", "cinquenta": "50",
    "sessenta": "60", "setenta": "70", "oitenta": "80", "noventa": "90",
    "cem": "100", "cento": "100", "duzentos": "200", "trezentos": "300",
    "quatrocentos": "400", "quinhentos": "500", "seiscentos": "600",
    "setecentos": "700", "oitocentos": "800", "novecentos": "900",
    "mil": "1000",
}

SUBSTITUTIONS: dict[str, str] = {
    # -------------------------------------------------------------------------
    # Fala informal — PT-BR genérico
    # -------------------------------------------------------------------------
    "tá": "está",
    "ta": "está",
    "esta": "está",
    "tô": "estou",
    "tava": "estava",
    "pra": "para",
    "ai": "aí",
    "ah": "a",
    "as": "às",
    "à": "a",
    "ô": "o",
    "  ": " ",
    "   ": " ",

    # -------------------------------------------------------------------------
    # Correções de verbos (erros comuns de ASR em PT-BR)
    # -------------------------------------------------------------------------
    "estar": "está",
    "levar": "elevar",
    "levando": "elevando",
    "meu cara": "meu caro",

    # -------------------------------------------------------------------------
    # Normalização geográfica (exemplo: setor de energia elétrica)
    # -------------------------------------------------------------------------
    "centroeste": "centro oeste",
    "centrooeste": "centro oeste",
    "centro é": "centro oeste",
    "centroest": "centro oeste",
    "norte centro é": "norte centro oeste",
    "north": "norte",

    # -------------------------------------------------------------------------
    # Terminologia técnica do domínio (personalize para o seu setor)
    # -------------------------------------------------------------------------
    "megawatts": "mw",
    "megawatt": "mw",
    "substação": "subestação",
    "tape": "tap",
    "atenção": "tensão",

    # -------------------------------------------------------------------------
    # Nomes de instalações e localidades do domínio (exemplos)
    # -------------------------------------------------------------------------
    # Formato: "como o ASR erra" -> "nome correto da instalação/localidade"
    "esemplo um": "exemplo um",
    "sub esemplo": "sub exemplo",
    "usina esemplo dois": "usina exemplo dois",

    # -------------------------------------------------------------------------
    # Siglas e acrônimos do domínio (personalize para o seu setor)
    # -------------------------------------------------------------------------
    # "sigla_errada": "SIGLA_CORRETA",

    # -------------------------------------------------------------------------
    # Correções de siglas da organização (adicione os erros do seu modelo)
    # O ASR frequentemente fragmenta ou distorce siglas curtas.
    # Exemplo: se sua organização é "ABC", adicione variações como:
    # "a b c": "abc",
    # "ab c": "abc",
    # -------------------------------------------------------------------------
}
