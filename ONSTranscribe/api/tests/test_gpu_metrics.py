"""Testes do parsing do nvidia-smi (sem GPU nem AWS)."""
from app.services.gpu_metrics import parse_nvidia_smi


def test_parse_nvidia_smi_le_util_e_memoria():
    # saída de --format=csv,noheader,nounits
    m = parse_nvidia_smi("37, 4096, 16384\n")
    assert m == {
        "utilization_gpu": 37.0,
        "memory_used_mb": 4096.0,
        "memory_total_mb": 16384.0,
    }


def test_parse_nvidia_smi_pega_so_a_primeira_gpu():
    # com múltiplas linhas (várias GPUs), usa a primeira
    m = parse_nvidia_smi("80, 8000, 16384\n10, 100, 16384\n")
    assert m["utilization_gpu"] == 80.0


def test_parse_nvidia_smi_retorna_none_em_saida_invalida():
    # linha vazia, sem colunas suficientes, ou não-numérica → None (pula o ciclo)
    assert parse_nvidia_smi("") is None
    assert parse_nvidia_smi("   ") is None
    assert parse_nvidia_smi("sem virgula") is None
    assert parse_nvidia_smi("a, b, c") is None
