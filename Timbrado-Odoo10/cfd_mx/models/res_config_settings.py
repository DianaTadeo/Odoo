# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import odoo
from odoo import api, fields, models, _

ADDRESS_FIELDS2 = ('street', 'street2', 'colonia_id', 'zip', 'city', 'ciudad_id', 'municipio_id', 'state_id', 'country_id', 'noExterior', 'noInterior')

class ConfigSettings(models.Model):
    _name = 'res.config.settings'
    _inherit = ['res.partner']

    cdf_mx_certificate_ids = fields.Many2many('cfd_mx.certificate',string='Lista de los certificados')
    cdf_mx_fiscal_regime = fields.Selection
    l10n_mx_edi_certificate_ids = fields.Many2many(
        related='company_id.l10n_mx_edi_certificate_ids', readonly=False,
        string='MX Certificates*')
    l10n_mx_edi_num_exporter = fields.Char(
        related='company_id.l10n_mx_edi_num_exporter', readonly=False,
        string='Number of Reliable Exporter')
    l10n_mx_edi_fiscal_regime = fields.Selection(
        related='company_id.l10n_mx_edi_fiscal_regime', readonly=False,
        string="Fiscal Regime",
        help="It is used to fill Mexican XML CFDI required field "
        "Comprobante.Emisor.RegimenFiscal.")