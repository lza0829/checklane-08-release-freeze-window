from pydantic import BaseModel


class InspectRequest(BaseModel):
    dataset_id: int


class RuleEnablePatch(BaseModel):
    enabled: bool


class FreezeWindowConfig(BaseModel):
    # ISO-8601 时间串（建议带 Z 或 +00:00 的 UTC 时间）；边界：start_at <= now < end_at
    start_at: str
    end_at: str
    note: str | None = None
