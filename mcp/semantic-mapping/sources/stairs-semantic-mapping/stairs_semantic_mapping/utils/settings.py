from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBConfig(BaseModel):
    host: str
    port: int
    dbname: str
    user: str
    password: str


class MapperSettings(BaseSettings):
    # PostgreSQL DB settings
    db_config: DBConfig = Field(..., alias='DB_CONFIG')

    category_id_field: str = 'cat'
    category_name_field: str = 'name'

    # Embedders' settings
    embedding_name: str = 'intfloat/multilingual-e5-large'
    embedding_host: str = 'embedder'
    embedding_host_categories: str = 'embedder'
    embedding_host_tasks: str = 'embedder'
    embedding_host_hierarchies: str = 'embedder'
    
    embedding_access_token: str | None = Field(default=None) 

    batch_size_categories: int = 32
    batch_size_tasks: int = 32
    top_k_categories: int = 5
    top_k_names: int = 1

    model_config = SettingsConfigDict(
        env_file=Path(Path(__file__).parent.parent, 'configs',
                      'mapper.env'),
        env_file_encoding='utf-8',
        extra='ignore',
    )


settings = MapperSettings()
