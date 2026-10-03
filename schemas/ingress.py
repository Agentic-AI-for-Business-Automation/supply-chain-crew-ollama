"""Ingress guards: typed models for data that crosses a trust boundary (ERP rows, Scout text, Analyst text).
Every validator returns readable, fixable error strings; CrewAI guardrails feed them back to the agent for a retry."""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator

from schemas.policy_constants import BUFFER_DAYS, CONFIRMING_SOURCES

MAX_SCOUT_CHARS = 6_000
MAX_ANALYST_CHARS = 12_000


# ------------------------------------------------------------------ ERP rows
class SupplierPartRow(BaseModel):
    supplier_id: str = Field(pattern=r"^S\d{3}$")
    part_id: str = Field(pattern=r"^P-\d{4}$")
    sourcing_role: str = Field(pattern=r"^(PRIMARY|APPROVED_BACKUP)$")
    unit_price_inr: float = Field(gt=0)
    moq: int = Field(gt=0)
    monthly_capacity: int = Field(gt=0)
    standard_lead_days: int = Field(gt=0)
    avl_status: str = Field(pattern=r"^(APPROVED|CONDITIONAL)$")
    contact_email: str

    @field_validator("contact_email")
    @classmethod
    def _email(cls, v):
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v or ""):
            raise ValueError("contact_email missing or malformed")
        return v


class InventoryRow(BaseModel):
    part_id: str = Field(pattern=r"^P-\d{4}$")
    on_hand_qty: int = Field(ge=0)
    daily_consumption: int = Field(gt=0)


def validate_erp_rows(supplier_parts: list[dict], inventory: list[dict]) -> dict:
    """Validate raw rows and build the snapshot dict used by policy_check. Raises ValueError on any bad row."""
    try:
        sp = [SupplierPartRow(**r) for r in supplier_parts]
        inv = [InventoryRow(**r) for r in inventory]
    except ValidationError as e:
        raise ValueError(f"ERP rows failed validation: {e.errors()[0]['loc']} {e.errors()[0]['msg']}") from None
    if not sp or not inv:
        raise ValueError("ERP returned no supplier_parts or inventory rows")
    erp: dict = {(r.supplier_id, r.part_id): {"role": r.sourcing_role, "price": r.unit_price_inr, "moq": r.moq,
                                              "capacity": r.monthly_capacity, "avl": r.avl_status, "email": r.contact_email,
                                              "lead_days": r.standard_lead_days}
                 for r in sp}
    erp["parts"] = {r.part_id: {"cover": r.on_hand_qty / r.daily_consumption, "daily": r.daily_consumption} for r in inv}
    return erp


# ------------------------------------------------------------------ Scout report
_URL = re.compile(r"https?://[^\s,)\]>]+")


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


class ScoutEvent(BaseModel):
    location: str
    country: str
    type: str
    event_date: date
    delay_low: int = Field(ge=0, le=365)
    delay_high: int = Field(ge=0, le=365)
    worst_case: int = Field(ge=0, le=365)
    confidence: str = Field(pattern=r"^(high|medium|low)$")
    sources: list[str] = Field(min_length=1)


class ScoutReport(BaseModel):
    events: list[ScoutEvent]
    verdict: str = Field(pattern=r"^(MATERIAL DISRUPTION FOUND|NO MATERIAL DISRUPTION FOUND)$")

    @property
    def material(self) -> bool:
        return self.verdict == "MATERIAL DISRUPTION FOUND"

    @property
    def worst_case(self) -> int:
        return max((e.worst_case for e in self.events), default=0)


def _plain(text: str) -> str:
    return re.sub(r"[*`]", "", text or "")


def parse_scout_report(raw: str) -> ScoutReport:
    """Parse the Scout handover format. Raises ValueError with a message the agent can act on."""
    text = _plain(raw)
    verdict_m = re.search(r"##\s*Verdict\s*\n\s*(MATERIAL DISRUPTION FOUND|NO MATERIAL DISRUPTION FOUND)", text)
    if not verdict_m:
        raise ValueError("missing '## Verdict' section with exactly 'MATERIAL DISRUPTION FOUND' or 'NO MATERIAL DISRUPTION FOUND'")
    head = text[:verdict_m.start()]
    blocks = re.split(r"(?m)^\s*\d+\.\s+(?=Location/port:)", head.split("## Disruption events", 1)[-1])[1:]
    events = []
    for i, blk in enumerate(blocks, 1):
        def grab(pattern, label):
            m = re.search(pattern, blk, re.I)
            if not m:
                raise ValueError(f"event {i}: missing or malformed '{label}'")
            return m
        loc = grab(r"Location/port:\s*([^|\n]+)", "Location/port").group(1).strip()
        country = grab(r"Country:\s*([^\n|]+)", "Country").group(1).strip()
        typ = grab(r"Type:\s*([^\n]+)", "Type").group(1).strip()
        d = grab(r"Date:\s*(\d{4}-\d{2}-\d{2})", "Date: YYYY-MM-DD").group(1)
        rng = grab(r"Delay range:\s*(\d+)\s*[-–to ]+\s*(\d+)\s*days", "Delay range: <a>-<b> days")
        worst = grab(r"Worst-case delay:\s*(\d+)", "Worst-case delay: <b> days").group(1)
        conf = grab(r"Confidence:\s*(high|medium|low)", "Confidence").group(1).lower()
        src_line = grab(r"Sources:\s*([^\n]+(?:\n\s+https?://[^\n]+)*)", "Sources").group(1)
        try:
            events.append(ScoutEvent(location=loc, country=country, type=typ, event_date=date.fromisoformat(d),
                                     delay_low=int(rng.group(1)), delay_high=int(rng.group(2)), worst_case=int(worst),
                                     confidence=conf, sources=_URL.findall(src_line)))
        except (ValidationError, ValueError) as e:
            raise ValueError(f"event {i}: {str(e).splitlines()[1] if isinstance(e, ValidationError) else e}") from None
    return ScoutReport(events=events, verdict=verdict_m.group(1))


def check_scout_report(raw: str, seen_urls: set[str], today: date | None = None) -> tuple[ScoutReport | None, list[str]]:
    """Semantic checks on top of parsing. Returns (report, errors); errors is empty when the report may travel on."""
    today = today or date.today()
    if len(raw or "") > MAX_SCOUT_CHARS:
        return None, [f"report is {len(raw)} characters; keep it under {MAX_SCOUT_CHARS} (events and verdict only)"]
    try:
        rep = parse_scout_report(raw)
    except (ValueError, ValidationError) as e:
        return None, [str(e)]
    errs: list[str] = []
    if rep.material and not rep.events:
        errs.append("verdict is MATERIAL but no event is listed")
    if not rep.material and rep.events:
        errs.append("events are listed but the verdict is NO MATERIAL DISRUPTION FOUND; remove them or change the verdict")
    seen = {u.rstrip("/").lower() for u in seen_urls}
    for i, ev in enumerate(rep.events, 1):
        if ev.delay_low > ev.delay_high or ev.worst_case != ev.delay_high:
            errs.append(f"event {i}: worst-case delay must equal the upper end of the delay range")
        if ev.event_date > today or ev.event_date < today - timedelta(days=30):
            errs.append(f"event {i}: date {ev.event_date} is outside the last 30 days")
        for u in ev.sources:
            if u.rstrip("/").lower() not in seen:
                errs.append(f"event {i}: URL {u} was never returned by Web Search; copy URLs only from search results")
    groups: dict[tuple, set[str]] = {}
    for ev in rep.events:                                # one incident = same type family and date
        groups.setdefault((ev.type.split()[0].lower(), ev.event_date), set()).update(_domain(u) for u in ev.sources)
    for (kind, when), domains in groups.items():
        if len(domains) < CONFIRMING_SOURCES:
            errs.append(f"incident '{kind}' on {when} is reported by {len(domains)} independent domain(s); SOP 2.3 needs "
                        f"{CONFIRMING_SOURCES}. Move it to 'Unconfirmed (L1, no RFQ)' after the verdict or find a second source")
    return rep, errs


# ------------------------------------------------------------------ Analyst report
class AnalystRow(BaseModel):
    part_id: str = Field(pattern=r"^P-\d{4}$")
    part_name: str
    critical: str
    primary_supplier_id: str = Field(pattern=r"^S\d{3}$")
    days_of_cover: float
    daily_consumption: int
    worst_case_delay: int
    gap_days: float
    shortfall_units: int


def _table(text: str, header: str) -> list[list[str]]:
    m = re.search(rf"##\s*{header}\s*\n(.*?)(?=\n##\s|\Z)", text, re.S | re.I)
    if not m:
        return []
    rows = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|") or re.match(r"^\|[\s:|-]+\|?$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and not cells[0].lower().startswith(("part_id", "po_id", "<")):
            rows.append(cells)
    return rows


def parse_analyst_exposed(raw: str) -> list[AnalystRow]:
    rows = []
    for i, c in enumerate(_table(_plain(raw), "Exposed parts"), 1):
        if len(c) != 9:
            raise ValueError(f"'Exposed parts' row {i} has {len(c)} columns; expected 9 "
                             "(part_id | part_name | critical | primary_supplier_id | days_of_cover | daily_consumption | worst_case_delay | gap_days | shortfall_units)")
        try:
            rows.append(AnalystRow(part_id=c[0], part_name=c[1], critical=c[2], primary_supplier_id=c[3],
                                   days_of_cover=float(c[4].replace(",", "")), daily_consumption=int(float(c[5].replace(",", ""))),
                                   worst_case_delay=int(float(c[6])), gap_days=float(c[7]), shortfall_units=int(float(c[8].replace(",", "")))))
        except (ValueError, ValidationError) as e:
            raise ValueError(f"'Exposed parts' row {i} ({c[0]}): a number is malformed ({str(e).splitlines()[0]})") from None
    return rows


def check_analyst_report(raw: str, scout: ScoutReport | None, erp: dict) -> list[str]:
    """Recompute every number from the ERP. `erp` = {'parts': {pid: {cover, daily, primary, country, port}}, 'pos': {...}}."""
    if len(raw or "") > MAX_ANALYST_CHARS:
        return [f"report is {len(raw)} characters; keep it under {MAX_ANALYST_CHARS}: select only the needed columns"]
    try:
        rows = parse_analyst_exposed(raw)
    except ValueError as e:
        return [str(e)]
    errs: list[str] = []
    if scout is None or not scout.material:
        return ["the Scout found no material disruption: 'Exposed parts' must be empty"] if rows else []
    worst = scout.worst_case
    places = " ".join(f"{e.location} {e.country}" for e in scout.events).lower()
    for r in rows:
        part = erp["parts"].get(r.part_id)
        if not part:
            errs.append(f"{r.part_id} does not exist in the ERP")
            continue
        if r.primary_supplier_id != part["primary"]:
            errs.append(f"{r.part_id}: primary supplier is {part['primary']}, not {r.primary_supplier_id}")
        if not (part["country"].lower() in places or (part["port"] or "").lower() in places):
            errs.append(f"{r.part_id}: primary supplier {part['primary']} ({part['country']}, {part['port']}) is not on an affected "
                        "lane; apply the lane filter before the SOP 3.2 trigger test")
        if abs(r.days_of_cover - part["cover"]) > 0.1:
            errs.append(f"{r.part_id}: days_of_cover is {part['cover']:.1f} in the ERP, not {r.days_of_cover}")
        if r.daily_consumption != part["daily"]:
            errs.append(f"{r.part_id}: daily_consumption is {part['daily']} in the ERP, not {r.daily_consumption}")
        if r.worst_case_delay != worst:
            errs.append(f"{r.part_id}: worst_case_delay must be the Scout's {worst}, not {r.worst_case_delay}")
        gap = worst + BUFFER_DAYS - part["cover"]
        if gap <= 0:
            errs.append(f"{r.part_id}: gap_days is {gap:.1f} (<= 0), so it belongs under 'Parts not at risk'")
            continue
        if abs(r.gap_days - gap) > 0.1:
            errs.append(f"{r.part_id}: gap_days must be {worst}+{BUFFER_DAYS}-{part['cover']:.1f} = {gap:.1f}, not {r.gap_days}")
        want = round(gap * part["daily"])
        if abs(r.shortfall_units - want) > max(1, 0.002 * want):
            errs.append(f"{r.part_id}: shortfall_units must be gap_days x daily_consumption = {want}, not {r.shortfall_units}")
    return errs
