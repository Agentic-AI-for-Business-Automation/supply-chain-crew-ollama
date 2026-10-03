"""Action record sent to n8n. Field names are a contract with the n8n workflow (Person 1)."""
from typing import Literal
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class AffectedPart(BaseModel):
    part_id: str = Field(pattern=r"^P-\d{4}$")
    part_name: str = Field(min_length=3)
    days_of_cover: float = Field(ge=0)
    projected_delay_days: int = Field(ge=0, le=365)
    shortfall_units: int = Field(ge=0)


class RFQLine(BaseModel):
    supplier_id: str = Field(pattern=r"^S\d{3}$")
    supplier_name: str = Field(min_length=3)
    supplier_email: str = ""
    part_id: str = Field(pattern=r"^P-\d{4}$")
    quantity: int = Field(gt=0)
    unit_price_inr: float = Field(gt=0)
    est_value_inr: float = Field(ge=0)
    required_within_days: int = Field(gt=0, le=365)
    justification: str = Field(min_length=10)  # must cite a clause, e.g. "SOP-SC-014 3.2"

    @model_validator(mode="after")
    def value_matches_qty_x_price(self):
        # Catch LLM arithmetic slips: allow 1% rounding tolerance, else reject with a fixable message
        expected = self.quantity * self.unit_price_inr
        if expected and abs(self.est_value_inr - expected) / expected > 0.01:
            raise ValueError(f"est_value_inr {self.est_value_inr} != quantity x unit_price ({expected:.0f}). Recompute.")
        return self


class Citation(BaseModel):
    document: str = Field(pattern=r".*\.pdf$")
    page: int = Field(ge=1, le=10)
    clause: str = Field(min_length=1)  # e.g. "3.2", "5", "VC-4.1", "Schedule A"


class ActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal["RFQ", "CONTINGENCY_PLAN", "MONITOR_ONLY"]
    severity: Literal["L1", "L2", "L3"]
    disruption_summary: str = Field(min_length=10)
    disruption_location: str = Field(min_length=3)
    sources: list[AnyHttpUrl] = []
    affected_parts: list[AffectedPart]
    rfqs: list[RFQLine] = []
    contingency_actions: list[str] = []
    approval_authority: Literal["Operations Manager - Procurement",
                                "Head of Supply Chain Management",
                                "Chief Financial Officer"]
    policy_citations: list[Citation] = Field(min_length=1)

    @model_validator(mode="after")
    def rfq_needs_lines(self):
        if self.action_type == "RFQ" and not self.rfqs:
            raise ValueError("action_type RFQ requires at least one entry in rfqs")
        if self.action_type == "MONITOR_ONLY" and self.rfqs:
            raise ValueError("MONITOR_ONLY must have empty rfqs")
        if self.action_type == "RFQ" and not self.sources:
            raise ValueError("RFQ requires at least one source URL from the Scout")
        return self
