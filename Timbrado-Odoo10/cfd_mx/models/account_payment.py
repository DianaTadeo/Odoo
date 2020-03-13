# -*- coding: utf-8 -*-

import base64
from datetime import datetime

from lxml import etree
from lxml.objectify import fromstring

from odoo import _, api, fields, models

from odoo.exceptions import UserError

from . import invoice_cfdi

class AccountPayment(models.Model):
    _name = 'account.payment'
    _inherit = ['account.payment']

   
    cfd_mx_partner_bank_id = fields.Many2one(
        'res.partner.bank', 'Partner Bank', help='If the payment was made '
        'with a financial institution define the bank account used in this '
        'payment.')
