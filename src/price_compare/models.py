"""The shape a product listing gets normalized into, regardless of platform.

`brand` and `simple_name` exist purely to make cross-platform matching
precise (see matcher.py). Comparing whole product titles -- "Dove Cream
Beauty Bathing Bar" against "Dove Cream Beauty Bar Soap" -- is noisy even
before accounting for genuine vocabulary differences ("besan" vs "gram
flour", "curd" vs "dahi") that plain string similarity can't bridge at
all, since there's no shared substring for it to find.

`simple_name` asks the extraction LLM to canonicalize rather than copy:
use standard, everyday grocery terminology instead of the page's exact
wording. Blinkit's and Zepto's extractions are two independent LLM calls
that never see each other's output, so this can't guarantee identical
strings -- but a model's own world knowledge tends to converge on similar
plain wording for the same real product even without coordination, which
is what makes the fuzzy comparison in matcher.py actually work well. This
costs nothing extra: it's the same one call per platform that already
extracts `name`/`price`/etc., just better instructions on that same call.
"""

from typing import Optional

from pydantic import BaseModel, Field


class Product(BaseModel):
    brand: str = Field(
        ..., description="The brand only, e.g. 'Dove', 'India Gate', 'Amul' -- not the rest of the title"
    )
    simple_name: str = Field(
        ...,
        description=(
            "A short, standardized description of what the product is and its variant/flavor/type -- "
            "brand and pack size left out. Use common, everyday grocery terminology rather than copying "
            "the page's exact wording, so the same real product described differently by different "
            "retailers ends up worded the same way here: always 'gram flour', not 'besan' or 'chickpea "
            "flour'; always 'soap bar', not 'bathing bar' or 'bar soap'; always 'yogurt', not 'curd' or "
            "'dahi'; always 'wheat flour', not 'atta'. Keep it short, e.g. 'gram flour', 'basmati rice', "
            "'soap bar, cream beauty'."
        ),
    )
    name: str = Field(..., description="The full product title exactly as displayed, for showing to a person")
    price: float = Field(..., description="Current selling price in rupees, numeric only, no currency symbol")
    mrp: Optional[float] = Field(None, description="Original struck-through MRP in rupees if a discount is shown, else null")
    quantity: Optional[str] = Field(None, description="Pack size as displayed, e.g. '100 g', '3 x 75 g', '1 L'")


def build_extraction_instruction(max_items: int) -> str:
    """The prompt itself caps how many products come back and asks for
    canonicalized naming -- the scraper doesn't need page-inspection logic
    for "enough items", and the matcher doesn't need a synonym dictionary
    for "same product, different words", because both are handled here,
    in the one LLM call that already runs.
    """
    return (
        "This is a grocery delivery app's search results page. Extract only the first "
        f"{max_items} distinct products in the main results grid, in the order they appear. "
        "For each one, identify: the brand only (e.g. 'Dove', 'India Gate', 'Amul' -- just the "
        "brand name, not the rest of the title); a simple_name using standard, everyday grocery "
        "terminology for what the product is, NOT the page's exact wording -- the goal is that the "
        "same real product would be described the same way here regardless of which retailer's page "
        "this is (leaving out the brand name and the pack size); the full product title exactly as "
        "displayed; the current selling price in rupees; the original MRP if a struck-through or "
        "discounted price is shown; and the pack size/quantity if shown. Skip banners, ads, and 'you "
        f"may also like' sections. Never return more than {max_items} products, even if more are visible."
    )


def clean_products(raw_products: list, platform_key: str) -> list[dict]:
    """Drops what the LLM returned that isn't a usable product.

    Extraction can include error markers, entries without a readable price,
    and the same product twice. Everything downstream (matching, the
    dashboard) assumes a name and a numeric price are always present.
    """
    cleaned, seen = [], set()
    for product in raw_products:
        if not isinstance(product, dict) or product.get("error"):
            continue
        name = str(product.get("name") or "").strip()
        try:
            price = float(product.get("price"))
        except (TypeError, ValueError):
            continue
        if not name or price <= 0:
            continue

        duplicate_key = (name.lower(), str(product.get("quantity") or "").lower())
        if duplicate_key in seen:
            continue
        seen.add(duplicate_key)

        product = {k: v for k, v in product.items() if k != "error"}
        product.update(name=name, price=price, platform=platform_key)
        cleaned.append(product)
    return cleaned
