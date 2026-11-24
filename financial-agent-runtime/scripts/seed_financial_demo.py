from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from agent_runtime.db import FinancialAccount, FinancialCustomer, FinancialResourceGrant, FinancialTransaction, SessionLocal, init_db


async def main() -> None:
    await init_db()
    customers = [
        {"customer_id":"CUST-DEMO-001","tenant_id":"demo-bank","full_name":"Alex Morgan（虚构）","customer_type":"individual","country_code":"US","risk_rating":"medium","kyc_status":"verified"},
        {"customer_id":"CUST-DEMO-002","tenant_id":"demo-bank","full_name":"Jamie Chen（虚构）","customer_type":"individual","country_code":"JP","risk_rating":"low","kyc_status":"pending_documents"},
        {"customer_id":"CUST-OTHER-001","tenant_id":"other-bank","full_name":"Taylor Example（虚构）","customer_type":"individual","country_code":"GB","risk_rating":"low","kyc_status":"verified"},
    ]
    accounts = [
        {"account_id":"ACC-DEMO-001","tenant_id":"demo-bank","customer_id":"CUST-DEMO-001","account_number_masked":"****-****-4821","account_type":"checking","currency":"USD","balance_minor":1250000,"status":"active","opened_at":"2024-04-01T00:00:00+00:00"},
        {"account_id":"ACC-DEMO-002","tenant_id":"demo-bank","customer_id":"CUST-DEMO-002","account_number_masked":"****-****-1930","account_type":"savings","currency":"JPY","balance_minor":84000000,"status":"active","opened_at":"2023-11-10T00:00:00+00:00"},
        {"account_id":"ACC-OTHER-001","tenant_id":"other-bank","customer_id":"CUST-OTHER-001","account_number_masked":"****-****-7712","account_type":"checking","currency":"GBP","balance_minor":500000,"status":"active","opened_at":"2024-02-01T00:00:00+00:00"},
    ]
    transactions = [
        ("TX-DEMO-001","ACC-DEMO-001","2026-11-18T10:00:00+00:00","debit",12000,"USD","Online Merchant A","US","card","posted","REF-001"),
        ("TX-DEMO-002","ACC-DEMO-001","2026-11-18T10:04:00+00:00","debit",14000,"USD","Online Merchant B","US","card","posted","REF-002"),
        ("TX-DEMO-003","ACC-DEMO-001","2026-11-18T10:09:00+00:00","debit",9000,"USD","Online Merchant C","US","card","posted","REF-003"),
        ("TX-DEMO-004","ACC-DEMO-001","2026-11-18T10:20:00+00:00","debit",17000,"USD","Online Merchant D","US","card","posted","REF-004"),
        ("TX-DEMO-005","ACC-DEMO-001","2026-11-17T09:00:00+00:00","credit",250000,"USD","Employer Payroll","US","ach","posted","PAY-001"),
        ("TX-DEMO-006","ACC-DEMO-002","2026-11-18T08:00:00+00:00","debit",250000,"JPY","Utility Company","JP","direct_debit","posted","J-001"),
        ("TX-DEMO-007","ACC-DEMO-002","2026-11-17T12:00:00+00:00","credit",800000,"JPY","Salary Payment","JP","bank_transfer","posted","J-002"),
        ("TX-OTHER-001","ACC-OTHER-001","2026-11-18T08:00:00+00:00","debit",4500,"GBP","Example Retailer","GB","card","posted","G-001"),
    ]
    grants = []
    for user_id in ("analyst-01", "compliance-01"):
        for resource_type in ("customer", "account", "transaction"):
            grants.append({"tenant_id": "demo-bank", "user_id": user_id, "resource_type": resource_type,
                           "resource_id": "*", "permission": "read", "granted_by": "demo-seed"})
    for resource_type in ("customer", "account", "transaction"):
        grants.append({"tenant_id": "demo-bank", "user_id": "analyst-01", "resource_type": resource_type,
                       "resource_id": "*", "permission": "case:create", "granted_by": "demo-seed"})
    for resource_type in ("customer", "account", "transaction"):
        grants.append({"tenant_id": "demo-bank", "user_id": "compliance-01", "resource_type": resource_type,
                       "resource_id": "*", "permission": "case:approve", "granted_by": "demo-seed"})
    for item in grants:
        item["tenant_id"] = "demo-bank"
    grants.append({"tenant_id": "other-bank", "user_id": "analyst-02", "resource_type": "account",
                   "resource_id": "*", "permission": "read", "granted_by": "demo-seed"})

    async with SessionLocal() as session:
        for item in customers:
            existing = await session.get(FinancialCustomer, item["customer_id"])
            if existing is None:
                session.add(FinancialCustomer(**item))
        for item in accounts:
            existing = await session.get(FinancialAccount, item["account_id"])
            if existing is None:
                session.add(FinancialAccount(**{**item, "opened_at": datetime.fromisoformat(item["opened_at"])}))
        for grant_data in grants:
            existing = (await session.execute(select(FinancialResourceGrant).where(
                FinancialResourceGrant.tenant_id == grant_data["tenant_id"],
                FinancialResourceGrant.user_id == grant_data["user_id"],
                FinancialResourceGrant.resource_type == grant_data["resource_type"],
                FinancialResourceGrant.resource_id == grant_data["resource_id"],
                FinancialResourceGrant.permission == grant_data["permission"],
            ))).scalar_one_or_none()
            if existing is None:
                session.add(FinancialResourceGrant(**grant_data))
        for tx in transactions:
            existing = await session.get(FinancialTransaction, tx[0])
            if existing is None:
                session.add(FinancialTransaction(
                    transaction_id=tx[0], tenant_id="other-bank" if tx[0].startswith("TX-OTHER") else "demo-bank",
                    account_id=tx[1], occurred_at=datetime.fromisoformat(tx[2]), direction=tx[3], amount_minor=tx[4],
                    currency=tx[5], counterparty_name=tx[6], counterparty_country=tx[7], channel=tx[8], status=tx[9], reference=tx[10]))
        await session.commit()
    print("已写入虚构金融演示数据。demo-bank 与 other-bank 用于验证租户隔离。")


if __name__ == "__main__":
    asyncio.run(main())
