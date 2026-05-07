"""Generate synthetic amazon_export.zip fixture for testing the Amazon adapter."""

import io
import os
import zipfile

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "amazon")
ZIP_PATH = os.path.join(FIXTURE_DIR, "amazon_export.zip")

DIGITAL_ITEMS_CSV = """\
Title,ASIN,DateAddedTimestamp,OrderId
"Test Kindle Book","B00TEST001","2023-01-15T10:30:00Z","D01-TEST-001"
"Another eBook","B00TEST002","2023-02-20T14:00:00Z","D01-TEST-002"
"""

RETAIL_ORDER_CSV = """\
Order ID,Order Date,Purchase Price Per Unit,Quantity,Payment Instrument Type,Shipping Address,Title,Category,ASIN/ISBN,Condition
"TEST-ORD-001","2023-03-15","$29.99","1","Visa","123 Test St","Test Gadget","Electronics","B00GADG001","new"
"""


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Digital Items.csv", DIGITAL_ITEMS_CSV)
        zf.writestr("Retail.OrderHistory.1.csv", RETAIL_ORDER_CSV)

    with open(ZIP_PATH, "wb") as f:
        f.write(buf.getvalue())

    print(f"Created {ZIP_PATH}")


if __name__ == "__main__":
    main()
