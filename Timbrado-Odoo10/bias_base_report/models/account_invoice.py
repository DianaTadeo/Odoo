# -*- coding: utf-8 -*-

from openerp import api, fields, models, _

import logging
_logger = logging.getLogger(__name__)

class AccountInvoice(models.Model):
    _inherit = "account.invoice"

    """
    total_trasladados = fields.Float(string="Total de impuestos trasladados", compute="_calcula_trasladados")
    total_retenidos = fields.Float(string="Total de impuestos retenidos", compute="_calcula_retenidos")

    @api.depends('invoice_line_ids')
    def _calcula_trasladados(self):   
	for record in self:
            total=0
            taxes_line = record.invoice_line_ids.filtered('price_subtotal').invoice_line_tax_ids
            taxes_line = taxes_line.filtered(lambda tax: tax.amount_type != 'group') + taxes_line.filtered(lambda tax: tax.amount_type == 'group').mapped('children_tax_ids')
            if taxes_line:
                trasladados = taxes_line.filtered(lambda r: r.amount >= 0)
                for traslado in trasladados:
                    total += traslado.amount
            _logger.info('#########El total es tras: '+str(total))
            record.total_trasladados = total

    @api.depends('invoice_line_ids')
    def _calcula_retenidos(self):
       for record in self:
            total=0
            taxes_line = record.invoice_line_ids.filtered('price_subtotal').invoice_line_tax_ids
            taxes_line = taxes_line.filtered(lambda tax: tax.amount_type != 'group') + taxes_line.filtered(lambda tax: tax.amount_type == 'group').mapped('children_tax_ids')
            if taxes_line:
                retenidos = taxes_line.filtered(lambda r: r.amount > 0)
                for retenido in retenidos:
                    total += retenidos.amount
            _logger.info('#########El total es ret: '+str(total))
            record.total_retenidos=total

        """
    def _l10n_mx_edi_finkok_info(self, company_id, service_type):
        test = company_id.cfd_mx_test
        username = company_id.cfd_finkok_user
        password = company_id.cfd_finkok_key
        if service_type == 'sign':
            url = 'http://demo-facturacion.finkok.com/servicios/soap/stamp.wsdl'\
                if test else 'http://facturacion.finkok.com/servicios/soap/stamp.wsdl'
        else:
            url = 'http://demo-facturacion.finkok.com/servicios/soap/cancel.wsdl'\
                if test else 'http://facturacion.finkok.com/servicios/soap/cancel.wsdl'
        return {
            'url': url,
            'multi': False,  # TODO: implement multi
            'username': 'cfdi@vauxoo.com' if test else username,
            'password': 'vAux00__' if test else password,
        }

    def _l10n_mx_edi_finkok_sign(self, pac_info):
        '''SIGN for Finkok.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for inv in self:
            cfdi = base64.decodestring(inv.l10n_mx_edi_cfdi)
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                response = client.service.stamp(cfdi, username, password)
            except Exception as e:
                inv.l10n_mx_edi_log_error(str(e))
                continue
            code = 0
            msg = None
            if response.Incidencias:
                code = getattr(response.Incidencias.Incidencia[0], 'CodigoError', None)
                msg = getattr(response.Incidencias.Incidencia[0], 'MensajeIncidencia', None)
            xml_signed = getattr(response, 'xml', None)
            if xml_signed:
                xml_signed = base64.b64encode(xml_signed.encode('utf-8'))
            inv._l10n_mx_edi_post_sign_process(xml_signed, code, msg)

    def _l10n_mx_edi_finkok_cancel(self, pac_info):
        '''CANCEL for Finkok.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for inv in self:
            uuid = inv.l10n_mx_edi_cfdi_uuid
            certificate_ids = inv.company_id.l10n_mx_edi_certificate_ids
            certificate_id = certificate_ids.sudo().get_valid_certificate()
            company_id = self.company_id
            cer_pem = certificate_id.get_pem_cer(
                certificate_id.content)
            key_pem = certificate_id.get_pem_key(
                certificate_id.key, certificate_id.password)
            cancelled = False
            code = False
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                uuid_type = client.get_type('ns0:stringArray')()
                uuid_type.string = [uuid]
                invoices_list = client.get_type('ns1:UUIDS')(uuid_type)
                response = client.service.cancel(
                    invoices_list, username, password, company_id.vat, cer_pem, key_pem)
            except Exception as e:
                inv.l10n_mx_edi_log_error(str(e))
                continue
            if not getattr(response, 'Folios', None):
                code = getattr(response, 'CodEstatus', None)
                msg = _("Cancelling got an error") if code else _('A delay of 2 hours has to be respected before to cancel')
            else:
                code = getattr(response.Folios.Folio[0], 'EstatusUUID', None)
                cancelled = code in ('201', '202')  # cancelled or previously cancelled
                # no show code and response message if cancel was success
                code = '' if cancelled else code
                msg = '' if cancelled else _("Cancelling got an error")
            inv._l10n_mx_edi_post_cancel_process(cancelled, code, msg)
    def _l10n_mx_edi_finkok_info(self, company_id, service_type):
        test = company_id.l10n_mx_edi_pac_test_env
        username = company_id.l10n_mx_edi_pac_username
        password = company_id.l10n_mx_edi_pac_password
        if service_type == 'sign':
            url = 'http://demo-facturacion.finkok.com/servicios/soap/stamp.wsdl'\
                if test else 'http://facturacion.finkok.com/servicios/soap/stamp.wsdl'
        else:
            url = 'http://demo-facturacion.finkok.com/servicios/soap/cancel.wsdl'\
                if test else 'http://facturacion.finkok.com/servicios/soap/cancel.wsdl'
        return {
            'url': url,
            'multi': False,  # TODO: implement multi
            'username': 'cfdi@vauxoo.com' if test else username,
            'password': 'vAux00__' if test else password,
        }

    def _l10n_mx_edi_finkok_sign(self, pac_info):
        '''SIGN for Finkok.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for inv in self:
            cfdi = base64.decodestring(inv.l10n_mx_edi_cfdi)
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                response = client.service.stamp(cfdi, username, password)
            except Exception as e:
                inv.l10n_mx_edi_log_error(str(e))
                continue
            code = 0
            msg = None
            if response.Incidencias:
                code = getattr(response.Incidencias.Incidencia[0], 'CodigoError', None)
                msg = getattr(response.Incidencias.Incidencia[0], 'MensajeIncidencia', None)
            xml_signed = getattr(response, 'xml', None)
            if xml_signed:
                xml_signed = base64.b64encode(xml_signed.encode('utf-8'))
            inv._l10n_mx_edi_post_sign_process(xml_signed, code, msg)

    def _l10n_mx_edi_finkok_cancel(self, pac_info):
        '''CANCEL for Finkok.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for inv in self:
            uuid = inv.l10n_mx_edi_cfdi_uuid
            certificate_ids = inv.company_id.l10n_mx_edi_certificate_ids
            certificate_id = certificate_ids.sudo().get_valid_certificate()
            company_id = self.company_id
            cer_pem = certificate_id.get_pem_cer(
                certificate_id.content)
            key_pem = certificate_id.get_pem_key(
                certificate_id.key, certificate_id.password)
            cancelled = False
            code = False
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                uuid_type = client.get_type('ns0:stringArray')()
                uuid_type.string = [uuid]
                invoices_list = client.get_type('ns1:UUIDS')(uuid_type)
                response = client.service.cancel(
                    invoices_list, username, password, company_id.vat, cer_pem, key_pem)
            except Exception as e:
                inv.l10n_mx_edi_log_error(str(e))
                continue
            if not getattr(response, 'Folios', None):
                code = getattr(response, 'CodEstatus', None)
                msg = _("Cancelling got an error") if code else _('A delay of 2 hours has to be respected before to cancel')
            else:
                code = getattr(response.Folios.Folio[0], 'EstatusUUID', None)
                cancelled = code in ('201', '202')  # cancelled or previously cancelled
                # no show code and response message if cancel was success
                code = '' if cancelled else code
                msg = '' if cancelled else _("Cancelling got an error")
            inv._l10n_mx_edi_post_cancel_process(cancelled, code, msg)

