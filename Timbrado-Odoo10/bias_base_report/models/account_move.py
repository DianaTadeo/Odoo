# -*- coding: utf-8 -*-

from openerp import api, fields, models, _

#----------------------------------------------------------
# Entries
#----------------------------------------------------------


class AccountMove(models.Model):
    _inherit = "account.move"
    _description = "Account Entry"


    @api.multi
    @api.depends('line_ids.debit', 'line_ids.credit')
    def _amount_debit_credit_compute(self):
        for move in self:
            total_debit = 0.0
            total_credit = 0.0
            for line in move.line_ids:
                total_debit += line.debit
                total_credit += line.credit
            move.amount_debit = total_debit
            move.amount_credit = total_credit


    amount_debit = fields.Monetary(compute='_amount_debit_credit_compute',)
    amount_credit = fields.Monetary(compute='_amount_debit_credit_compute',)


    def _get_reconciled_payments(self):
        """Helper used to retrieve the reconciled payments on this journal entry"""
        pay_term_line_ids = self.line_ids.filtered(lambda line: line.account_id.user_type_id.type in ('receivable', 'payable'))
        reconciled_amls = pay_term_line_ids.mapped('matched_debit_ids.debit_move_id') + \
                          pay_term_line_ids.mapped('matched_credit_ids.credit_move_id')
        return reconciled_amls.mapped('payment_id')
