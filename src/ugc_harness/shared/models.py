from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Shared base for strict artifact contracts across domain capabilities."""

    model_config = ConfigDict(extra="forbid")
