"""5 archetype scenario definitions: static, pre-authored fixtures with
planted ground truth, plus prompt assembly for the claude-CLI run.

Fixtures are deterministic (no LLM involved in writing them) so the work
judge can be anchored on known content via `expected_content_notes` — what a
correct deliverable must reflect, given exactly what was planted below.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    use_case: str                 # fed to team_service.recommend / wire
    role_keywords: tuple[str, ...]  # priority order for select_target_agent
    task_instruction: str          # what the agent is asked to do
    fixtures: dict[str, str]       # filename -> content, inlined + written to tmpdir
    expected_content_notes: str    # ground truth, fed ONLY to the work judge


# ── 1. credit_ops ────────────────────────────────────────────────────────────
_CREDIT_OPS_CSV = """invoice_id,customer,amount_sgd,days_overdue,status,note
INV-1001,Acme Trading Pte Ltd,4200.00,15,overdue,
INV-1002,Bright Star Logistics,8900.50,45,overdue,
INV-1003,Coral Bay Foods,15600.00,92,overdue,
INV-1004,Delta Print Co,2150.00,17,overdue,
INV-1005,Everwell Clinic Group,6750.00,48,overdue,
INV-1006,Falcon Freight Pte Ltd,21300.00,105,overdue,
INV-1007,Golden Leaf Bakery,1890.00,15,disputed,customer claims goods never received; do not chase for payment yet
INV-1008,Harbor View Hotel,12400.00,45,payment_plan,on agreed installment plan of SGD 2000/month; next installment due in 5 days
INV-1009,Ivory Tower Consulting,3300.00,16,overdue,
INV-1010,Jetstream Air Cargo,9800.00,91,overdue,
"""

credit_ops = Scenario(
    id="credit_ops",
    label="Credit operations — chase overdue invoices",
    use_case="chase up customers who owe us money",
    role_keywords=("credit", "lending", "collection", "account"),
    task_instruction=(
        "You are given our overdue-invoices ledger below. Produce a chase-up "
        "action plan: for each invoice needing outreach, name the customer, "
        "amount, days overdue, and the urgency/escalation tone to use. "
        "Explicitly call out any invoice that should NOT receive a standard "
        "chase message and say why."
    ),
    fixtures={"overdue_invoices.csv": _CREDIT_OPS_CSV},
    expected_content_notes=(
        "Must prioritize the 90+ day invoices as most urgent: Coral Bay Foods "
        "(INV-1003, 92 days), Falcon Freight Pte Ltd (INV-1006, 105 days), "
        "Jetstream Air Cargo (INV-1010, 91 days). Must flag INV-1007 (Golden "
        "Leaf Bakery) as disputed and NOT recommend a standard payment-chase "
        "message for it. Must flag INV-1008 (Harbor View Hotel) as already on "
        "an agreed payment plan and NOT treat it as a standard delinquency."
    ),
)


# ── 2. sales_quote ───────────────────────────────────────────────────────────
_RFQ_EMAIL = """From: procurement@clientco.example
Subject: RFQ - Corporate laptop refresh

Hi team,

We are refreshing our fleet and need a formal quotation for:
  - 40 units of your standard business laptop
  - Bundled with 3-year on-site support/warranty for every unit

Please quote TIERED pricing at these volume bands: 10+, 25+, and 40+ units,
so we can see the per-unit price at our actual order size (40 units).

We need a firm, written quote within 5 business days of this email.

Thanks,
Procurement, ClientCo
"""

_PRICE_LIST = """# Price list (SGD, per unit, ex-GST)

| SKU | Description | 1-9 | 10-24 | 25-39 | 40+ |
|---|---|---|---|---|---|
| LAP-100 | Business 14" i5/16GB/512GB SSD | 1250 | 1200 | 1150 | 1080 |
| LAP-200 | Business 14" i7/32GB/1TB SSD | 1650 | 1580 | 1500 | 1420 |
| LAP-300 | Ultralight 13" i5/16GB/512GB SSD | 1450 | 1390 | 1320 | 1250 |
| DOCK-01 | USB-C docking station | 180 | 170 | 160 | 150 |
| MON-24 | 24" monitor | 210 | 200 | 190 | 180 |
| BAG-01 | Laptop sleeve/bag | 45 | 42 | 39 | 36 |
| SUP-3Y | 3-year on-site support/warranty add-on (per laptop) | 220 | 210 | 200 | 190 |
| SUP-1Y | 1-year on-site support/warranty add-on (per laptop) | 90 | 85 | 80 | 75 |
"""

sales_quote = Scenario(
    id="sales_quote",
    label="Sales — corporate client quotation",
    use_case="prepare a quotation for a corporate client",
    role_keywords=("sales", "business development", "account"),
    task_instruction=(
        "Below is an inbound RFQ email and our current price list. Prepare a "
        "formal written quotation responding to exactly what was asked."
    ),
    fixtures={"rfq_email.md": _RFQ_EMAIL, "price_list.md": _PRICE_LIST},
    expected_content_notes=(
        "Must quote the 40+ unit band (not a lower band) since the order is "
        "exactly 40 units: LAP-100 at 1080/unit and SUP-3Y at 190/unit (the "
        "RFQ never named a specific laptop SKU, so LAP-100, the standard "
        "business model, or an explicit assumption stating which SKU is "
        "assumed, is acceptable — but the 40+ band pricing must be used, not "
        "the 25-39 or lower band). Must include the 3-year support line item "
        "since the RFQ explicitly required it. Should reference the 5 "
        "business day deadline."
    ),
)


# ── 3. legal_review ──────────────────────────────────────────────────────────
_VENDOR_CONTRACT = """# Master Services Agreement — Vendor: Nimbus Cloud Services Pte Ltd

## 1. Parties
This Agreement is between Client and Nimbus Cloud Services Pte Ltd ("Vendor").

## 2. Term
This Agreement commences on the Effective Date for an initial term of 12
months, and shall AUTOMATICALLY RENEW for successive 12-month terms unless
either party gives written notice of non-renewal at least 90 days before the
end of the then-current term.

## 3. Services
Vendor shall provide cloud hosting and managed-services support as described
in Schedule A.

## 4. Fees
Client shall pay the fees set out in Schedule B. Vendor may increase the
fees at its sole discretion at any time upon 30 days' written notice to
Client, with no cap on the amount or frequency of such increases.

## 5. Payment Terms
Invoices are payable net 30 days from the invoice date.

## 6. Intellectual Property
All work product, deliverables, code, and materials created by Vendor in the
course of performing the Services shall be owned exclusively by Vendor.
Client is granted a limited, non-exclusive license to use such deliverables
for its internal business purposes only.

## 7. Confidentiality
Each party shall keep the other's confidential information confidential and
use it only for purposes of this Agreement.

## 8. Limitation of Liability
Vendor's total liability under this Agreement, for any claim arising out of
or relating to this Agreement, shall not exceed the fees paid by Client in
the one (1) month preceding the claim. Client's liability to Vendor under
this Agreement is uncapped.

## 9. Indemnification
Client shall indemnify Vendor against third-party claims arising from
Client's use of the Services.

## 10. Termination for Cause
Either party may terminate this Agreement for the other's uncured material
breach, on 30 days' written notice.

## 11. Governing Law
This Agreement is governed by the laws of Singapore.

## 12. Signatures
Signed for and on behalf of Client: ______________
Signed for and on behalf of Vendor: ______________
"""

legal_review = Scenario(
    id="legal_review",
    label="Legal — vendor contract review before signing",
    use_case="review a vendor contract before signing",
    role_keywords=("legal", "counsel", "contract"),
    task_instruction=(
        "Review the vendor contract below before we sign it. Produce a "
        "clause-by-clause risk review that flags every clause that is "
        "unfavorable, one-sided, or risky for the Client, and recommend a "
        "specific redline or negotiation ask for each."
    ),
    fixtures={"vendor_contract.md": _VENDOR_CONTRACT},
    expected_content_notes=(
        "Must flag all 4 planted problems: (1) clause 2's auto-renewal with "
        "only a narrow 90-day non-renewal notice window (the auto-renew "
        "trap); (2) clause 4's unilateral, uncapped fee-increase right held "
        "solely by Vendor; (3) clause 6's vendor ownership of all work "
        "product/deliverables with only a limited internal-use license back "
        "to Client; (4) clause 8's asymmetric liability cap — Vendor capped "
        "at 1 month's fees while Client's liability is uncapped."
    ),
)


# ── 4. warehouse_ops ─────────────────────────────────────────────────────────
_STOCK_LEVELS_CSV = """sku,description,on_hand,reorder_point,weekly_demand,lead_time_weeks,max_stock
WH-100,Cardboard Box 12x12,850,300,60,2,1200
WH-101,Packing Tape 48mm,60,150,25,3,500
WH-102,Pallet Wrap Roll,220,180,20,2,600
WH-103,Bubble Wrap 1m,45,120,18,4,400
WH-104,Shipping Labels A6,3000,500,150,1,4000
WH-105,Steel Strapping Coil,15,80,12,3,250
WH-106,Foam Corner Guards,1800,200,25,2,900
WH-107,Wooden Pallets 1.2m,110,100,30,5,500
WH-108,Zip Ties 300mm,900,250,40,2,1500
WH-109,Warning Labels - Fragile,600,150,35,2,1200
WH-110,Stretch Film Machine Roll,320,200,28,3,700
WH-111,Packing Peanuts 100L,150,90,10,2,300
"""

warehouse_ops = Scenario(
    id="warehouse_ops",
    label="Warehouse ops — inventory reorder review",
    use_case="manage warehouse inventory and reorder stock",
    role_keywords=("warehouse", "inventory", "logistics", "supply"),
    task_instruction=(
        "Review the stock-levels report below. Produce a reorder action "
        "plan: which SKUs need reordering now, which are overstocked, and "
        "which are at real risk of stocking out before a reorder could "
        "arrive (compare weeks of cover — on_hand / weekly_demand — against "
        "lead_time_weeks, not just the reorder_point flag)."
    ),
    fixtures={"stock_levels.csv": _STOCK_LEVELS_CSV},
    expected_content_notes=(
        "Must flag exactly these 3 SKUs as below reorder point and needing a "
        "reorder: WH-101 (60 on hand vs reorder point 150), WH-103 (45 vs "
        "120), WH-105 (15 vs 80). Must flag WH-106 as overstocked (1800 on "
        "hand vs max_stock 900). Must flag WH-107 as stockout-imminent even "
        "though on_hand (110) is technically above its reorder_point (100): "
        "weeks of cover = 110/30 = ~3.7 weeks, which is LESS than its "
        "5-week lead time, so it will run out before a reorder arrives."
    ),
)


# ── 5. marketing_campaign ────────────────────────────────────────────────────
_PRODUCT_BRIEF = """# Product launch brief — AquaPure Personal Filter Bottle

**Audience:** Urban professionals aged 25-40, environmentally conscious,
frequent commuters and gym-goers.

**Unique selling points:**
- Filters 99.9% of waterborne bacteria and microplastics on the go
- Eliminates single-use plastic bottle waste
- Filter cartridge lasts 500 refills before replacement

**Budget:** SGD 15,000 for the launch campaign.

**Launch date:** 2026-09-01.

**Constraints (must be respected in ALL campaign messaging):**
1. No discounting, coupon codes, or promotional price cuts of any kind — the
   brand is positioned as premium, not promotional.
2. Do not name or compare against any competitor products or brands.
"""

marketing_campaign = Scenario(
    id="marketing_campaign",
    label="Marketing — product launch social campaign",
    use_case="run a social media marketing campaign for a product launch",
    role_keywords=("marketing", "brand", "social", "campaign"),
    task_instruction=(
        "Using the product brief below, produce a social media launch "
        "campaign plan: key messages, content pillars/post ideas, and a "
        "rough allocation of the budget across the campaign window leading "
        "up to launch. Respect every constraint in the brief."
    ),
    fixtures={"product_brief.md": _PRODUCT_BRIEF},
    expected_content_notes=(
        "Must reference the SGD 15,000 budget and the 2026-09-01 launch "
        "date. Must build messaging around the stated USPs (bacteria/"
        "microplastic filtering, plastic-waste elimination, 500-refill "
        "cartridge life) for the stated audience (urban professionals "
        "25-40). Must NOT include any discount/coupon/price-cut messaging "
        "and must NOT name or compare against any competitor — both are "
        "explicit constraints in the brief."
    ),
)


ARCHETYPES: tuple[Scenario, ...] = (
    credit_ops, sales_quote, legal_review, warehouse_ops, marketing_campaign,
)
BY_ID: dict[str, Scenario] = {s.id: s for s in ARCHETYPES}


def build_scenario_prompt(scenario: Scenario, produces: list[dict] | None = None) -> str:
    """Assemble the full `-p` prompt: task instruction, fixtures inlined under
    `## FILE: <name>` fenced blocks, then the closing drop-in directive.

    `produces` is the selected agent's wired deliverable list (from
    `skill_bundle_service.build_agent_context(...)["produces"]`) — each item
    is `{artifact, description}`. Falls back to a generic phrase when the
    agent has nothing wired (itself a coherence finding elsewhere).
    """
    parts = [scenario.task_instruction, ""]
    for fname, content in scenario.fixtures.items():
        parts += [f"## FILE: {fname}", "```", content.rstrip("\n"), "```", ""]

    produces_desc = ", ".join(
        p.get("artifact", "") for p in (produces or []) if p.get("artifact")
    ) or "the deliverable your skill defines"
    parts.append(
        "Acting strictly within your skill definition, produce the "
        f"deliverable(s) your skill names: {produces_desc}. Output the "
        "deliverable itself as markdown, no preamble. If a required real "
        "input is missing, state exactly what's missing instead of "
        "inventing it."
    )
    return "\n".join(parts)
