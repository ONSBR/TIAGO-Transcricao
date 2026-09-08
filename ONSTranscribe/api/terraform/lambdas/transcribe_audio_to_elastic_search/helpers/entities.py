from enum import Enum
from dataclasses import dataclass
from typing import Optional, List
import os

class DestinationEnum(Enum):
    DATABASE = "Database"
    TRANSCRIBE = "Transcribe"
    VOCABULARY = "Vocabulary"
    TRANSCODER = "Transcoder"
    NOWHERE = "Nowhere"

@dataclass
class KeyValueEntity:
    key: str
    value: str

    def to_dict(self):
        return {"key": self.key, "value": self.value}


@dataclass
class Audio:
    id: str
    shorticut: str
    data: int
    data_full: int
    hora: int
    ramal: int
    locutorUm: KeyValueEntity
    duracao: int
    direcao: KeyValueEntity
    locutorDois: KeyValueEntity
    gravador: str
    comentario: Optional[str] = None
    transcricao_audio: Optional[dict] = None

    @property
    def locutores(self) -> List[str]:
        media_format = os.getenv("MEDIA_FORMAT", "")
        return [
            self.locutorUm.key.replace(media_format, ""),
            self.locutorDois.key.replace(media_format, "")
        ]

    @property
    def locutores_autocomplete(self) -> List[str]:
        return [self.locutorUm.key, self.locutorDois.key]

    @property
    def gravadores_autocomplete(self) -> str:
        return self.gravador

    @property
    def tem_comentario(self) -> bool:
        return bool(self.comentario)

    def to_dict(self):
        return {
            "id": self.id,
            "shorticut": self.shorticut,
            "data_full": self.data_full,
            "data": self.data,
            "hora": self.hora,
            "ramal": self.ramal,
            "locutorUm": self.locutorUm.to_dict(),
            "duracao": self.duracao,
            "direcao": self.direcao.to_dict(),
            "locutorDois": self.locutorDois.to_dict(),
            "gravador": self.gravador,
            "comentario": self.comentario,
            "temComentario": self.tem_comentario,
            "locutores": self.locutores,
            "locutoresAutocomplete": self.locutores_autocomplete,
            "gravadoresAutocomplete": self.gravadores_autocomplete,
            "transcricaoAudio": self.transcricao_audio
        }
