# Copyright 2024 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from collections import defaultdict

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class AutomationRecord(models.Model):

    _name = "automation.record"
    _description = "Automation Record"

    name = fields.Char(compute="_compute_name")
    state = fields.Selection(
        [("run", "Running"), ("done", "Done")], compute="_compute_state", store=True
    )
    configuration_id = fields.Many2one(
        "automation.configuration", required=True, readonly=True
    )
    model = fields.Char(index=True, required=False, readonly=True)
    resource_ref = fields.Reference(
        selection="_selection_target_model",
        compute="_compute_resource_ref",
        readonly=True,
    )
    res_id = fields.Many2oneReference(
        string="Record",
        index=True,
        required=False,
        readonly=True,
        model_field="model",
        copy=False,
    )
    automation_step_ids = fields.One2many(
        "automation.record.step", inverse_name="record_id", readonly=True
    )
    is_test = fields.Boolean()

    is_placeholder = fields.Boolean(
        default=False,
        help="Indicates if this record is a placeholder for a missing resource.",
        readonly=True,
    )
    missing_res_id = fields.Integer(
        help="ID of the missing referenced resource.",
        readonly=True,
    )

    @api.model
    def _selection_target_model(self):
        return [
            (model.model, model.name)
            for model in self.env["ir.model"]
            .sudo()
            .search([("is_mail_thread", "=", True)])
        ]

    @api.depends("automation_step_ids.state")
    def _compute_state(self):
        for record in self:
            record.state = (
                "run"
                if record.automation_step_ids.filtered(lambda r: r.state == "scheduled")
                else "done"
            )

    @api.depends("model", "res_id")
    def _compute_resource_ref(self):
        for record in self:
            if record.model and record.model in self.env:
                record.resource_ref = "%s,%s" % (record.model, record.res_id or 0)
            else:
                record.resource_ref = None

    @api.depends("res_id", "model")
    def _compute_name(self):
        for record in self:
            if not record.is_placeholder:
                record.name = self.env[record.model].browse(record.res_id).display_name
            else:
                record.name = _("Orphan Record")

    @api.model
    def _search(
        self,
        args,
        offset=0,
        limit=None,
        order=None,
        count=False,
        access_rights_uid=None,
    ):
        ids = super()._search(
            args,
            offset=offset,
            limit=limit,
            order=order,
            count=False,
            access_rights_uid=access_rights_uid,
        )
        if not ids:
            return 0 if count else []
        orig_ids = ids
        ids = set(ids)
        result = []
        model_data = defaultdict(lambda: defaultdict(set))

        for sub_ids in self._cr.split_for_in_conditions(ids):
            self._cr.execute(
                """
                            SELECT id, res_id, model, configuration_id
                            FROM "%s"
                            WHERE id = ANY (%%(ids)s)"""
                % self._table,
                dict(ids=list(sub_ids)),
            )
            for eid, res_id, model, config_id in self._cr.fetchall():
                model_data[model][(res_id, config_id)].add(eid)

        for model, targets in model_data.items():
            if not self.env[model].check_access_rights("read", False):
                continue
            res_ids = [res_id for res_id, _ in targets]
            recs = self.env[model].browse(res_ids)
            missing = recs - recs.exists()
            if missing:
                for (res_id, config_id) in [
                    (res_id, config_id)
                    for (res_id, config_id) in targets.keys()
                    if res_id in missing.ids
                ]:
                    _logger.warning(
                        "Deleted record %s,%s is referenced by automation.record",
                        model,
                        res_id,
                    )

                    placeholder_id = None
                    for pid in orig_ids:
                        placeholder = self.env["automation.record"].browse(pid)
                        if (
                            placeholder.missing_res_id == res_id
                            and placeholder.configuration_id.id == config_id
                        ):
                            placeholder_id = placeholder.id
                            break
                    if placeholder_id:
                        result.append(placeholder_id)
                    else:
                        automation_record = self.env["automation.record"].browse(
                            targets[(res_id, config_id)]
                        )
                        placeholder = self.sudo().create(
                            {
                                "name": "Orphan Record",
                                "is_placeholder": True,
                                "missing_res_id": res_id,
                                "configuration_id": config_id,
                                "res_id": None,
                                "state": automation_record.state or "done",
                                "model": automation_record.model or model,
                                "resource_ref": automation_record.resource_ref,
                                "is_test": automation_record.is_test or False,
                            }
                        )
                        result.append(placeholder.id)
            recs = recs - missing
            allowed = (
                self.env[model]
                .with_context(active_test=False)
                ._search([("id", "in", recs.ids)])
            )
            for target_id in allowed:
                result += list(targets.get((target_id, config_id), []))
        if len(orig_ids) == limit and len(result) < len(orig_ids):
            result.extend(
                self._search(
                    args,
                    offset=offset + len(orig_ids),
                    limit=limit,
                    order=order,
                    count=count,
                    access_rights_uid=access_rights_uid,
                )[: limit - len(result)]
            )
        result = [x for x in orig_ids if x in result]
        return len(result) if count else list(result)

    def read(self, fields=None, load="_classic_read"):
        """Override to explicitely call check_access_rule, that is not called
        by the ORM. It instead directly fetches ir.rules and apply them."""
        self.check_access_rule("read")
        return super().read(fields=fields, load=load)

    def check_access_rule(self, operation):
        """In order to check if we can access a record, we are checking if we can access
        the related document"""
        super().check_access_rule(operation)
        if self.env.is_superuser():
            return
        default_checker = self.env["mail.thread"].get_automation_access
        by_model_rec_ids = defaultdict(set)
        by_model_checker = {}
        for exc_rec in self.sudo():
            by_model_rec_ids[exc_rec.model].add(exc_rec.res_id)
            if exc_rec.model not in by_model_checker:
                by_model_checker[exc_rec.model] = getattr(
                    self.env[exc_rec.model], "get_automation_access", default_checker
                )

        for model, rec_ids in by_model_rec_ids.items():
            records = self.env[model].browse(rec_ids).with_user(self._uid)
            checker = by_model_checker[model]
            for record in records:
                check_operation = checker(
                    [record.id], operation, model_name=record._name
                )
                record.check_access_rights(check_operation)
                record.check_access_rule(check_operation)

    def write(self, vals):
        self.check_access_rule("write")
        return super().write(vals)
