from pydantic import BaseModel

class AudioRequest(BaseModel):
    audio_source_link: str
    destination_link: str
    language: str = "pt"