from pydantic import BaseModel, Field


class BlindReviewInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    dataset_id: int
    holdout_ratio: float = Field(default=0.3, ge=0.1, le=0.5)
    seed: int | None = Field(default=1011)


class BlindCandidateInput(BaseModel):
    alias: str | None = Field(default=None, max_length=80)
    params: dict = Field(default_factory=dict)
