"""
AMBAR — Office → Operator mapping
Each office has its own operator. Operators ONLY receive orders from their office.
"""
import os

# Fallback if an office_id arrives that isn't listed below
DEFAULT_OPERATORS = [int(x.strip()) for x in os.getenv("OPERATOR_IDS","").split(",") if x.strip().isdigit()]

# Map office_id → list of operator Telegram IDs
OFFICE_OPERATORS = {
    "office_central": [686932322],   # Ambar — Центр
    "office_north":   [686932322],   # Ambar — Север
    "office_south":   [686932322],   # Ambar — Юг (same as central, change if needed)
}