# Copyright 2024 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import AutomationTestCase


class TestAutomationRecord(AutomationTestCase):
    def test_generation_orphan_record(self):
        self.configuration.editable_domain = "[('id', '=', %s)]" % self.partner_01.id
        self.configuration.start_automation()
        self.env["automation.configuration"].cron_automation()
        self.partner_01.unlink()
        self.env["automation.record"].search(
            [("configuration_id", "=", self.configuration.id), ("is_test", "=", False)]
        )
        orphan_records = self.env["automation.record"].search(
            [("configuration_id", "=", self.configuration.id), ("is_test", "=", False)]
        )
        self.assertTrue(orphan_records, "No orphan record was generated")
        self.assertEqual(len(orphan_records), 1, "More than one orphan record found")
        self.assertEqual(
            orphan_records.name, "Orphan Record", "Incorrect orphan record name"
        )
