"""
Converte o modelo pyannote/segmentation-3.0 do formato PyAnnote nativo
(config.yaml + pytorch_model.bin) para o formato HuggingFace
(config.json + model.safetensors), que é o esperado pelo pipeline.

Uso:
    cd ONSTranscribe
    python finetuning/scripts/convert_segmentation_to_hf_format.py \
        --model-dir finetuning/workspace/models/segmentation

Dependências (instaladas automaticamente pelo uv se executar via workspace):
    pip install torch safetensors
"""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Converte segmentation model para formato HF")
    parser.add_argument(
        "--model-dir",
        default="finetuning/workspace/models/segmentation",
        help="Diretório com config.yaml e pytorch_model.bin",
    )
    args = parser.parse_args()

    model_dir = args.model_dir

    bin_path = os.path.join(model_dir, "pytorch_model.bin")
    config_yaml_path = os.path.join(model_dir, "config.yaml")
    safetensors_path = os.path.join(model_dir, "model.safetensors")
    config_json_path = os.path.join(model_dir, "config.json")

    if not os.path.exists(bin_path):
        print(f"Erro: {bin_path} não encontrado.")
        sys.exit(1)

    if not os.path.exists(config_yaml_path):
        print(f"Erro: {config_yaml_path} não encontrado.")
        sys.exit(1)

    # --- Passo 1: criar config.json ---
    # Os valores abaixo correspondem à arquitetura de pyannote/segmentation-3.0.
    # Se o config.yaml tiver valores diferentes, ajuste aqui.
    try:
        import yaml

        with open(config_yaml_path) as f:
            cfg = yaml.safe_load(f)

        task = cfg.get("task", {})
        model = cfg.get("model", {})

        config_json = {
            "architectures": ["SegmentationModel"],
            "chunk_duration": task.get("duration", 10),
            "max_speakers_per_chunk": task.get("max_speakers_per_chunk", 3),
            "max_speakers_per_frame": task.get("max_speakers_per_frame", 2),
            "min_duration": None,
            "model_type": "pyannet",
            "sample_rate": model.get("sample_rate", 16000),
            "torch_dtype": "float32",
            "transformers_version": "4.46.0",
            "warm_up": [0.0, 0.0],
            "weigh_by_cardinality": False,
        }
    except ImportError:
        print("Aviso: PyYAML não encontrado — usando valores padrão do segmentation-3.0")
        config_json = {
            "architectures": ["SegmentationModel"],
            "chunk_duration": 10,
            "max_speakers_per_chunk": 3,
            "max_speakers_per_frame": 2,
            "min_duration": None,
            "model_type": "pyannet",
            "sample_rate": 16000,
            "torch_dtype": "float32",
            "transformers_version": "4.46.0",
            "warm_up": [0.0, 0.0],
            "weigh_by_cardinality": False,
        }

    with open(config_json_path, "w") as f:
        json.dump(config_json, f, indent=2)
    print(f"[OK] {config_json_path}")

    # --- Passo 2: converter pytorch_model.bin → model.safetensors ---
    try:
        import torch
        from safetensors.torch import save_file
    except ImportError as e:
        print(f"Erro de dependência: {e}")
        print("Instale as dependências: pip install torch safetensors")
        sys.exit(1)

    print(f"Carregando {bin_path}...")

    # O checkpoint contém objetos de classes externas (pyannote, pytorch_lightning, etc.)
    # Usamos pickle_module customizado — API oficial do torch.load — para substituir
    # classes desconhecidas por stubs reais. Os tensores são reconstruídos normalmente;
    # os objetos pyannote viram stubs e são descartados na filtragem abaixo.
    import pickle as _pickle

    _stub_cache: dict = {}

    def _make_stub(module: str, name: str) -> type:
        key = (module, name)
        if key not in _stub_cache:
            _stub_cache[key] = type(name, (), {
                "__new__": lambda cls, *a, **kw: object.__new__(cls),
                "__init__": lambda self, *a, **kw: None,
                "__setstate__": lambda self, d: self.__dict__.update(
                    d if isinstance(d, dict) else {}
                ),
            })
        return _stub_cache[key]

    class _StubPickle:
        """pickle_module substituto para torch.load que stubba classes desconhecidas."""
        class Unpickler(_pickle.Unpickler):
            _SAFE = ("torch", "collections", "_collections", "builtins", "copy_reg")

            def find_class(self, module: str, name: str) -> type:
                if any(module.startswith(m) for m in self._SAFE):
                    try:
                        return super().find_class(module, name)
                    except (ImportError, AttributeError):
                        pass
                return _make_stub(module, name)

        load = _pickle.load
        loads = _pickle.loads
        dump = _pickle.dump
        dumps = _pickle.dumps
        PicklingError = _pickle.PicklingError
        UnpicklingError = _pickle.UnpicklingError

    try:
        state_dict = torch.load(
            bin_path, map_location="cpu", pickle_module=_StubPickle, weights_only=False
        )
    except Exception as e:
        print(f"Erro ao carregar {bin_path}: {e}")
        sys.exit(1)

    # Se checkpoint completo (ex: Lightning), extrair só os pesos
    if isinstance(state_dict, dict) and "state_dict" in state_dict:
        state_dict = state_dict["state_dict"]

    state_dict = {k: v for k, v in state_dict.items() if isinstance(v, torch.Tensor)}
    if not state_dict:
        print("Erro: nenhum tensor encontrado no checkpoint.")
        sys.exit(1)

    print(f"  {len(state_dict)} tensores encontrados.")
    print(f"Salvando {safetensors_path}...")
    save_file(state_dict, safetensors_path, metadata={"format": "pt"})
    print(f"[OK] {safetensors_path}")

    print("\nConversão concluída. Estrutura resultante:")
    for f in sorted(os.listdir(model_dir)):
        if not f.startswith("."):
            size = os.path.getsize(os.path.join(model_dir, f))
            print(f"  {f}  ({size / 1e6:.1f} MB)" if size > 1000 else f"  {f}")


if __name__ == "__main__":
    main()
