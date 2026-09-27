"""Production-volume semantics, periods and stable identities."""

import unittest
from datetime import datetime

from production import daily_record, monthly_record, number


class ProductionTests(unittest.TestCase):
    def test_missing_is_not_zero(self):
        for value in (None, "NULL", "", " "):
            self.assertIsNone(number(value))
        self.assertEqual(number(0), 0.0)
        self.assertEqual(number(-1.5), -1.5)
        for value in ("garbage", float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                number(value)

    def test_daily_identification_uses_npd_code(self):
        raw = {"DATEPRD": datetime(2014, 4, 1), "NPD_WELL_BORE_CODE": "7405",
               "NPD_WELL_BORE_NAME": "15/9-F-1 C", "BORE_OIL_VOL": 24,
               "BORE_WAT_VOL": None, "ON_STREAM_HRS": 12,
               "FLOW_KIND": "production", "WELL_TYPE": "OP"}
        result = daily_record(raw, 2)
        self.assertEqual(result["entityId"], "osdu:master-data--Wellbore:NPD-7405")
        self.assertEqual(result["periodStart"], "2014-04-01")
        self.assertEqual(result["periodEnd"], "2014-04-02")
        self.assertEqual(result["oilVolumeSm3"], 24)
        self.assertNotIn("waterVolumeSm3", result)
        self.assertNotIn("oilRate", result)
        self.assertEqual(result["uid"], daily_record(raw, 99)["uid"])

    def test_monthly_handles_leap_year_and_null(self):
        result = monthly_record({"NPDCode": 5599, "Year": 2016, "Month": 2,
                                 "Oil": 0, "Water": "NULL", "WI": 12.5}, 3)
        self.assertEqual(result["periodEnd"], "2016-03-01")
        self.assertEqual(result["oilVolumeSm3"], 0)
        self.assertNotIn("waterVolumeSm3", result)
        self.assertEqual(result["waterInjectionVolumeSm3"], 12.5)

    def test_invalid_id_and_date_are_rejected(self):
        with self.assertRaises(ValueError):
            monthly_record({"NPDCode": 5599.5, "Year": 2016, "Month": 2}, 3)
        with self.assertRaises(ValueError):
            monthly_record({"NPDCode": 5599, "Year": 2016, "Month": 13}, 3)


if __name__ == "__main__":
    unittest.main()
