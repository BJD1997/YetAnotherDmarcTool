from pydantic import BaseModel


class UpdateSettingsPatch(BaseModel):
    include_prereleases: bool
