# -*- coding: utf-8 -*-

from odoo import fields, models
import math


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    address_issued_id = fields.Many2one('res.partner',
        domain="[('type', '=', 'invoice')]",
        string='Address Issued',
        help='Used in multiple-offices environments to fill, with the given '
        'address, the node "ExpedidoEn" in the XML for invoices of this '
        'journal. If empty, the node won\'t be added.')
    payment_method_id = fields.Many2one(
        'l10n_mx_edi.payment.method',
        string='Payment Way',
        help='Indicates the way the payment was/will be received, where the '
        'options could be: Cash, Nominal Check, Credit Card, etc.')


class AccountTax(models.Model):
    _inherit = 'account.tax'

    # To this case the options in selection field are in Spanish, because are
    # only three options and We need that value to set in the CFDI
    cfdi_tax_type = fields.Selection(
        [('Tasa', 'Tasa'),
         ('Cuota', 'Cuota'),
         ('Exento', 'Exento')], 'Factor Type',
        help='The CFDI version 3.3 have the attribute "TipoFactor" in the tax '
        'lines. In it is indicated the factor type that is applied to the '
        'base of the tax.')


    