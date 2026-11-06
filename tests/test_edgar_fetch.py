import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location("edgar_fetch", pathlib.Path(__file__).parents[1] / "scripts" / "edgar_fetch.py")
edgar_fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edgar_fetch)


def test_extract_keeps_every_filing_and_prefers_the_first_reported_tag():
    facts = {"facts": {
        "us-gaap": {
            # revenue reported under two tags: the preferred one wins even though the other has more rows
            "Revenues": {"units": {"USD": [
                {"start": "2023-01-01", "end": "2023-03-31", "val": 100, "fy": 2023, "fp": "Q1", "form": "10-Q", "filed": "2023-05-01"},
                {"start": "2023-01-01", "end": "2023-03-31", "val": 101, "fy": 2024, "fp": "Q1", "form": "10-Q", "filed": "2024-05-01"}]}},
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [{"end": "2023-03-31", "val": 1, "filed": "2023-05-01"}] * 3}},
            "EarningsPerShareDiluted": {"units": {"USD/shares": [
                {"end": "2023-03-31", "val": 1.5, "fy": 2023, "fp": "Q1", "form": "10-Q", "filed": "2023-05-01", "frame": "CY2023Q1"}]}},
        },
        "ifrs-full": {"Revenue": {"units": {"GBP": [{"end": "2023-12-31", "val": 5, "filed": "2024-03-01"}]}}},
    }}
    out = edgar_fetch.extract(facts)
    assert out["revenue"]["tag"] == "us-gaap:Revenues" and out["revenue"]["unit"] == "USD"
    assert [f["val"] for f in out["revenue"]["facts"]] == [100, 101]            # the restated figure is kept, with its own filing date
    assert out["revenue"]["facts"][1]["filed"] == "2024-05-01"
    assert out["eps_diluted"]["facts"][0]["frame"] == "CY2023Q1"
    assert set(out) == {"revenue", "eps_diluted"}


def test_user_agent_requires_a_contact(monkeypatch):
    import pytest
    monkeypatch.delenv("EDGAR_CONTACT", raising=False)
    with pytest.raises(SystemExit):
        edgar_fetch.user_agent()
    monkeypatch.setenv("EDGAR_CONTACT", "Jane Doe jane@example.com")
    assert edgar_fetch.user_agent() == "global-investment-analysis Jane Doe jane@example.com"
