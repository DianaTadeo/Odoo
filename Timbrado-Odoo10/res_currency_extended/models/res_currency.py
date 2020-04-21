# -*- coding: utf-8 -*-
from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.bias_base_report.bias_utis.amount_to_text_es_MX import amount_to_text

import logging
logging.basicConfig(level=logging.INFO)

_logger = logging.getLogger(__name__)


class ResCurrency(models.Model):
    _inherit = 'res.currency'

    cfd_mx_decimal_places = fields.Integer(
        'Number of decimals', readonly=True,
        help='Number of decimals to be supported for this currency, according '
        'to the SAT. It will be used in the CFDI to format amounts.')


    def _convert(self, from_amount, to_currency, company, date, round=True):
        """Returns the converted amount of ``from_amount``` from the currency
           ``self`` to the currency ``to_currency`` for the given ``date`` and
           company.

           :param company: The company from which we retrieve the convertion rate
           :param date: The nearest date from which we retriev the conversion rate.
           :param round: Round the result or not
        """
        self, to_currency = self or to_currency, to_currency or self
        assert self, "convert amount from unknown currency"
        assert to_currency, "convert amount to unknown currency"
        assert company, "convert amount from unknown company"
        assert date, "convert amount from unknown date"
        # apply conversion rate
        if self == to_currency:
            to_amount = from_amount
        else:
            to_amount = from_amount * self._get_conversion_rate(self, to_currency)
        # apply rounding
        return to_currency.round(to_amount) if round else to_amount

