from pydantic import BaseModel

from app.models.enums import SenderReviewStatus


class SenderReviewUpdateRequest(BaseModel):
    status: SenderReviewStatus | None = None
    owner: str | None = None
    notes: str | None = None
