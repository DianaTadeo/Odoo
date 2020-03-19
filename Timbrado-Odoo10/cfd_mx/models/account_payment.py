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

    cfdi_timbre_id = fields.Many2one('cfdi.timbres.sat', string='Timbre SAT',  readonly=True)
    

    def _post_sign_process(self, xml_signed, code=None, msg=None):
        """Post process the results of the sign service.

        :param xml_signed: the xml signed datas codified in base64
        :param code: an eventual error code
        :param msg: an eventual error msg
        """
        # TODO - Duplicated
        self.ensure_one()
        Currency = self.env['res.currency']
        Timbre = self.env['cfdi.timbres.sat']
        tree = fromstring(base64.decodestring(xml_signed))
        tfdtree= self.get_tfd_etree(tree)
        currency_id = Currency.search([('name', '=', tree.get('Moneda'))])
        if not currency_id:
            currency_id = Currency.search([('name', '=', 'MXN')])

        timbre_id = Timbre.create({
            'name': tfdtree.get('UUID'),
            'cfdi_supplier_rfc': tree.Emisor.get('Rfc',tree.Emisor.get('rfc')),
            'cfdi_customer_rfc': tree.Receptor.get('Rfc',tree.Receptor.get('rfc')),
            'cfdi_amount': float(tree.get('Total', '0.0')),
            'cfdi_certificate': tree.get('NoCertificado'),
            'cfdi_certificate_sat':tfdtree.get('NoCertificadoSAT'),
            'time_invoice': tree.get('Fecha'),
            'time_invoice_sat': tfdtree.get('FechaTimbrado'),
            'currency_id': currency_id and currency_id.id or False,
            'cfdi_type': tree.get('TipoDeComprobante'),
            'cfdi_pac_rfc': tfdtree.get('RfcProvCertif'),
            'cfdi_cadena_ori': tfdtree.get('cadenaOri'),#
            'cfdi_cadena_sat': tfdtree.get('cadenaSat'),#
            'cfdi_state': "Vigente",
            'journal_id': self.journal_id.id,
            'partner_id': self.partner_id.id,
            'test': self.company_id.cfd_mx_test
        })

        self.write({
            'cfdi_timbre_id': timbre_id.id
        })

        if xml_signed:
            body_msg = _('The sign service has been called with success')
            # Update the pac status
            self.cfd_mx_pac_status = 'signed'
            self.cfd_mx_cfdi = xml_signed
            # Update the content of the attachment
            attachment_id = self.retrieve_last_attachment()
            filename = ('%s-%s-MX-Payment-10.xml' % (
                self.journal_id.code, self.name))
            attachment_id.write({
                'name': filename,
                'datas_fname': filename,
                'type': 'binary',
                'res_id': self.id,
                'res_model': self._name,
                'description': _('Mexican CFDI to payment'),
                'datas': xml_signed,
                'mimetype': 'application/xml'
            })
            post_msg = [_('The content of the attachment has been updated')]
        else:
            body_msg = _('The sign service requested failed')
            post_msg = []
        if code:
            post_msg.extend([_('Code: %s') % code])
        if msg:
            post_msg.extend([_('Message: %s') % msg])