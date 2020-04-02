# -*- coding: utf-8 -*-

import base64
from datetime import datetime

from lxml import etree
from lxml.objectify import fromstring

from odoo import _, api, fields, models

from odoo.exceptions import UserError

from . import invoice_cfdi
import logging
_logger = logging.getLogger(__name__)

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
            _logger.warning('The content of the attachment has been updated')
        else:
            body_msg = _('The sign service requested failed')
            post_msg = []
        if code:
            post_msg.extend([_('Code: %s') % code])
        if msg:
            post_msg.extend([_('Message: %s') % msg])
            _logger.warning('Message: %s')


    def _post_cancel_process(self, result, cancelled, code=None, msg=None):
        '''Post process the results of the cancel service.

        :param cancelled: is the cancel has been done with success
        :param code: an eventual error code
        :param msg: an eventual error msg
        '''
        self.ensure_one()
        cfdi_params = {
            "uuid": self.cfdi_timbre_id.name,
            'noCertificado': self.cfdi_timbre_id.cfdi_certificate
        }
        #result = self.company_id.action_ws_finkok_sat('cancel', cfdi_params)
        self.cfdi_timbre_id.write({
            "cfdi_cancel_date_sat": getattr(result,'Fecha', None),
            "cfdi_state": "Cancelado" if hasattr(result, 'Folios') else 'Vigente',
            "cfdi_cancel_status_sat": msg,
            "cfdi_cancel_code_sat": getattr(result.Folios.Folio[0], 'EstatusUUID', None),
            "cfdi_cancel_state": getattr(result.Folios.Folio[0], 'EstatusCancelacion', None)
        })
        if getattr(result,'Acuse'):
            Attachment = self.env['ir.attachment']
            fname = "cancelacion_cfd_%s.xml"%(self.cfdi_timbre_id.name or "")
            attachment_values = {
                'name': fname,
                'datas': base64.b64encode( result["Acuse"] ),
                'datas_fname': fname,
                'description': 'Cancelar Comprobante Fiscal Digital',
                'res_model': "cfdi.timbres.sat",
                'res_id': self.cfdi_timbre_id.id,
                'type': 'binary'
            }
            Attachment.create(attachment_values)
            attachment_values['res_model'] = self._name
            attachment_values['res_id'] = self.id
            Attachment.create(attachment_values)

        if cancelled:
            body_msg = _('The cancel service has been called with success')
            _logger.warning(body_msg)
            self.cfd_mx_pac_status = 'cancelled'
            legal = _(
                '''<h3 style="color:red">Legal warning</h3>
                <p> Regarding the issue of the CFDI with' Complement for
                receipt of payments', where there are errors in the receipt, this
                may be canceled provided it is replaced by another with the correct data.
                If the error consists in which the payment receipt
                complement should not have been issued because the consideration
                had already been paid in full; replaced by another with an
                amount of one peso.</p>
                <p><a href="http://www.sat.gob.mx/informacion_fiscal/factura_electronica/Documents/Complementoscfdi/Guia_comple_pagos.pdf">
                For more information here (Pag. 5)</a></p>''')
            self.message_post(body=legal)
            _logger.warning(legal)
        else:
            body_msg = _('The cancel service requested failed')
        post_msg = []
        if code:
            post_msg.extend([_('Code: %s') % code])
        if msg:
            post_msg.extend([_('Message: %s') % msg])
            _logger.warning('Message: %s')





