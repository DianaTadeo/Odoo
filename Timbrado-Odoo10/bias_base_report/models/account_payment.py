# -*- coding: utf-8 -*-
import subprocess#
import tempfile#


import base64, json
from datetime import datetime
from itertools import groupby
import requests

import ssl#
from lxml import etree
from lxml.objectify import fromstring
from zeep import Client
from zeep.transports import Transport
from odoo import _, api, fields, models,  tools
from odoo.tools import DEFAULT_SERVER_TIME_FORMAT
from odoo.tools.float_utils import float_compare
from odoo.tools.misc import html_escape
from odoo.exceptions import UserError

from . import invoice_cfdi

try:
    from OpenSSL import crypto
except ImportError:
    _logger.warning('OpenSSL library not found. If you plan to use l10n_mx_edi, please install the library from https://pypi.python.org/pypi/pyOpenSSL')

KEY_TO_PEM_CMD = 'openssl pkcs8 -in %s -inform der -outform pem -out %s -passin file:%s'

#from odoo.addons.l10n_mx_edi.tools.run_after_commit import run_after_commit

CFDI_TEMPLATE = 'bias_base_report.payment10'
CFDI_XSLT_CADENA = 'bias_base_report/data/3.3/cadenaoriginal.xslt'
CFDI_XSLT_CADENA_TFD = 'bias_base_report/data/xslt/3.3/cadenaoriginal_TFD_1_1.xslt'
CFDI_SAT_QR_STATE = {
    'No Encontrado': 'not_found',
    'Cancelado': 'cancelled',
    'Vigente': 'valid',
}


def get_format(xml):
    xml = xml.replace("<Comprobante", "<cfdi:Comprobante xsi:schemaLocation=\"http://www.sat.gob.mx/cfd/3 http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv33.xsd http://www.sat.gob.mx/Pagos http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos10.xsd\" xmlns:cfdi=\"http://www.sat.gob.mx/cfd/3\" xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" xmlns:pago10= \"http://www.sat.gob.mx/Pagos\"")
    xml = xml.replace("</Comprobante", "</cfdi:Comprobante")
    xml = xml.replace("<CfdiRelacionados", "<cfdi:CfdiRelacionados")
    xml = xml.replace("</CfdiRelacionado", "</cfdi:CfdiRelacionados")

    xml = xml.replace("<Emisor", "<cfdi:Emisor")
    xml = xml.replace("</Emisor", "</cfdi:Emisor")
    xml = xml.replace("<Receptor", "<cfdi:Receptor")
    xml = xml.replace("</Receptor", "</cfdi:Receptor")

    xml = xml.replace("<Concepto", "<cfdi:Concepto")
    xml = xml.replace("</Concepto", "</cfdi:Concepto")

    xml = xml.replace("<Complemento", "<cfdi:Complemento")
    xml = xml.replace("</Complemento", "</cfdi:Complemento")
    
    xml = xml.replace("<Pago", "<pago10:Pago")
    xml = xml.replace("</Pago", "</pago10:Pago")
    xml = xml.replace("<DoctoRelacionado", "<pago10:DoctoRelacionado")
    xml = xml.replace("</DoctoRelacionado", "</pago10:DoctoRelacionado")

    xml = xml.replace("<Traslado", "<pago10:Traslado")
    xml = xml.replace("</Traslado", "</pago10:Traslado")
    xml = xml.replace("<Impuesto", "<pago10:Impuesto")
    xml = xml.replace("</Impuesto", "</pago10:Impuesto")
    return xml.replace("\n","")#.replace("  ", "")

class AccountPayment(models.Model):
    _name = 'account.payment'
    _inherit = ['account.payment', 'mail.thread']

    cfd_mx_pac_status = fields.Selection(
        selection=[
            ('none', 'CFDI not necessary'),
            ('retry', 'Retry'),
            ('to_sign', 'To sign'),
            ('signed', 'Signed'),
            ('to_cancel', 'To cancel'),
            ('cancelled', 'Cancelled')
        ],
        string='PAC status', default='none',
        help='Refers to the status of the CFDI inside the PAC.',
        readonly=True, copy=False)
    cfd_mx_sat_status = fields.Selection(
        selection=[
            ('none', 'State not defined'),
            ('undefined', 'Not Synced Yet'),
            ('not_found', 'Not Found'),
            ('cancelled', 'Cancelled'),
            ('valid', 'Valid'),
        ],
        string='SAT status',
        help='Refers to the status of the CFDI inside the SAT system.',
        readonly=True, copy=False, required=True,
        tracking=True, default='undefined')
    cfd_mx_cfdi_name = fields.Char(string='CFDI name', copy=False, readonly=True,
        help='The attachment name of the CFDI.')
    cfd_mx_payment_method_id = fields.Many2one('account.payment.method', string=u'Metodo de Pago')

    cfd_mx_cfdi = fields.Binary(
        string='Cfdi content', copy=False, readonly=True,
        help='The cfdi xml content encoded in base64.',
        compute='_compute_cfdi_values')
    cfd_mx_cfdi_uuid = fields.Char(string='Fiscal Folio', copy=False, readonly=True,
        help='Folio in electronic invoice, is returned by SAT when send to stamp.',
        compute='_compute_cfdi_values') ####No se puede registrar pago
    cfd_mx_cfdi_supplier_rfc = fields.Char('Supplier RFC', copy=False, readonly=True,
        help='The supplier tax identification number.',
        compute='_compute_cfdi_values')
    cfd_mx_cfdi_customer_rfc = fields.Char('Customer RFC', copy=False, readonly=True,
        help='The customer tax identification number.',
        compute='_compute_cfdi_values')
    cfd_mx_origin = fields.Char(
        string='CFDI Origin', copy=False,
        help='In some cases the payment must be regenerated to fix data in it. '
        'In that cases is necessary this field filled, the format is: '
        '\n04|UUID1, UUID2, ...., UUIDn.\n'
        'Example:\n"04|89966ACC-0F5C-447D-AEF3-3EED22E711EE,89966ACC-0F5C-447D-AEF3-3EED22E711EE"')
    cfd_mx_expedition_date = fields.Date(
        string='Expedition Date', copy=False,
        help='Save the expedition date of the CFDI that according to the SAT '
        'documentation must be the date when the CFDI is issued.')
    cfd_mx_time_payment = fields.Char(
        string='Time payment', readonly=True, copy=False,
        states={'draft': [('readonly', False)]},
        help="Keep empty to use the current Mexico central time")
    cfd_mx_partner_bank_id = fields.Many2one(
        'res.partner.bank', 'Partner Bank', help='If the payment was made '
        'with a financial institution define the bank account used in this '
        'payment.')
    serial_number = fields.Char(string="No. de serie del certificado", size=64, copy=False)

    def post(self):

        """Generate CFDI to payment after that invoice is paid"""
        res = super(AccountPayment, self).post()
        for record in self.filtered(lambda r: r.is_required()):
            
            partner = record.journal_id.address_issued_id or record.company_id.partner_id.commercial_partner_id
            tz = self.env['account.invoice']._get_timezone(
                partner.state_id.code)
            date_mx = datetime.now(tz)
            record.write({
                'cfd_mx_expedition_date': date_mx,
                'cfd_mx_time_payment': date_mx.strftime(
                    DEFAULT_SERVER_TIME_FORMAT),
                'cfd_mx_cfdi_name': ('%s-%s-MX-Payment-10.xml' % (
                    record.journal_id.code, record.name)),
            })

            record._retry()
        return res

    # -----------------------------------------------------------------------
    # Cancellation
    # -----------------------------------------------------------------------

    def cancel(self):
        result = super(AccountPayment, self).cancel()
        for record in self.filtered(lambda r: r.cfd_mx_pac_status in [
                'to_sign', 'signed', 'to_cancel']):
            record._cancellation()
        return result

    def _cancellation(self):
        """Call the cancel service with records that can be cancelled."""
        records = self.search([
            ('cfd_mx_pac_status', 'in', ['to_sign', 'signed', 'to_cancel', 'retry']),
            ('id', 'in', self.ids)])
        for record in records:
            if record.cfd_mx_pac_status in ['to_sign', 'retry']:
                record.cfd_mx_pac_status = False
                record.message_post(body=_('The cancel service has been called with success'))
            else:
                record.cfd_mx_pac_status = 'to_cancel'
        records = self.search([
            ('cfd_mx_pac_status', '=', 'to_cancel'),
            ('id', 'in', self.ids)])
        records._call_service('cancel')

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    @api.model
    def retrieve_attachments(self):
        """Retrieve all the cfdi attachments generated for this payment.

        :return: An ir.attachment recordset
        """
        self.ensure_one()
        if not self.cfd_mx_cfdi_name:
            return []
        domain = [
            ('res_id', '=', self.id),
            ('res_model', '=', self._name),
            ('name', '=', self.cfd_mx_cfdi_name)]
        return self.env['ir.attachment'].search(domain)

    @api.model
    def retrieve_last_attachment(self):
        attachment_ids = self.retrieve_attachments()
        return attachment_ids and attachment_ids[0] or None

    @api.model
    def get_xml_etree(self, cfdi=None):
        '''Get an objectified tree representing the cfdi.
        If the cfdi is not specified, retrieve it from the attachment.

        :param cfdi: The cfdi as string
        :return: An objectified tree
        '''
        #TODO helper which is not of too much help and should be removed
        self.ensure_one()
        if cfdi is None:
            cfdi = base64.decodestring(self.cfd_mx_cfdi)
        return fromstring(cfdi)

    @api.model
    def get_tfd_etree(self, cfdi):
        '''Get the TimbreFiscalDigital node from the cfdi.

        :param cfdi: The cfdi as etree
        :return: the TimbreFiscalDigital node
        '''
        if not hasattr(cfdi, 'Complemento'):
            return None
        attribute = 'tfd:TimbreFiscalDigital[1]'
        namespace = {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'}
        node = cfdi.Complemento.xpath(attribute, namespaces=namespace)
        return node[0] if node else None


    @api.model
    def _get_payment_etree(self, cfdi):
        '''Get the Complement node from the cfdi.

        :param cfdi: The cfdi as etree
        :return: the Payment node
        '''
        if not hasattr(cfdi, 'Complemento'):
            return None
        attribute = '//pago10:DoctoRelacionado'
        namespace = {'pago10': 'http://www.sat.gob.mx/Pagos'}
        node = cfdi.Complemento.xpath(attribute, namespaces=namespace)
        return node

    @api.model
    def _get_cadena(self):
        self.ensure_one()
        #get the xslt path
        xslt_path = CFDI_XSLT_CADENA_TFD
        #get the cfdi as eTree
        cfdi = base64.decodestring(self.cfd_mx_cfdi)
        cfdi = self.get_xml_etree(cfdi)
        cfdi = self.get_tfd_etree(cfdi)
        #return the cadena
        return self.env['account.invoice'].generate_cadena(xslt_path, cfdi)

    def is_required(self):
        self.ensure_one()

        required = (
            self.payment_type == 'inbound' and
            self.company_id.country_id == self.env.ref('base.mx') and
            not self.invoice_ids.filtered(lambda i: i.type != 'out_invoice'))
        if not required:
            return required

        if self.cfd_mx_pac_status != 'none':
            return True
        if self.invoice_ids and False in self.invoice_ids.mapped('uuid'):
            raise UserError(_(
                'Some of the invoices that will be paid with this record '
                'are not signed, and the UUID is required to indicate '
                'the invoices that are paid with this CFDI'))
        messages = []
        if not self.invoice_ids:
            raise UserError('no invoices')
            messages.append(_(
                '<b>This payment <b>has not</b> invoices related.'
                '</b><br/><br/>'
                'Which actions can you take?\n'
                '<ul>'
                '<ol>If this is an payment advance, you need to create a new '
                'invoice with a product that will represent the payment in '
                'advance and reconcile such invoice with this payment. For '
                'more information please read '
                '<a href="http://omawww.sat.gob.mx/informacion_fiscal/factura_electronica/Documents/Complementoscfdi/Caso_uso_Anticipo.pdf">'
                'this SAT reference.</a></ol>'
                '<ol>If you already have the invoices that are paid make the '
                'payment matching of them.</ol>'
                '</ul>'
                '<p>If you follow this steps once you finish them and the '
                'paid amount is bellow the sum of invoices the payment '
                'will be automatically signed'
                '</p>'))
        
        #categ_force = self._get_force_rep_category()
        force = self._context.get('force_ref') #or (
            #categ_force and categ_force in self.partner_id.category_id)
        if self.invoice_ids and not self.invoice_ids.filtered(
                lambda i: i.get_payment_method_cfdi() == 'PPD') and not force:
            messages.append(_(
                '<b>The invoices related with this payment have the payment '
                'method as <b>PUE</b>.'
                '</b><br/><br/>'
                'When an invoice has the payment method <b>PUE</b> do not '
                'requires generate a payment complement. For more information '
                'please read '
                '<a href="http://omawww.sat.gob.mx/informacion_fiscal/factura_electronica/Documents/Complementoscfdi/Guia_comple_pagos.pdf">'
                'this SAT reference.</a>, Pag. 3. Or read the '
                '<a href="https://www.odoo.com/documentation/user/11.0/es/accounting/localizations/mexico.html#payments-just-available-for-cfdi-3-3">'
                'Odoo documentation</a> to know how to indicate the payment '
                'method in the invoice CFDI.'
                ))
        if messages:
            self.message_post(body=invoice_cfdi.create_list_html(messages))
            return force or False
        return required

  

    def cfd_mx_log_error(self, message):
        self.ensure_one()
        self.message_post(body=_('Error during the process: %s') % message)

    @api.depends('cfd_mx_cfdi_name')
    def _compute_cfdi_values(self):
        """Fill the invoice fields from the cfdi values."""
        for rec in self:
            attachment_id = rec.retrieve_last_attachment()
            rec.cfd_mx_cfdi_uuid = None
            if not attachment_id:
                rec.cfd_mx_cfdi = None
                rec.cfd_mx_cfdi_supplier_rfc = None
                rec.cfd_mx_cfdi_customer_rfc = None
                continue
            attachment_id = attachment_id[0]
            # At this moment, the attachment contains the file size in its 'datas' field because
            # to save some memory, the attachment will store its data on the physical disk.
            # To avoid this problem, we read the 'datas' directly on the disk.
            datas = attachment_id._file_read(attachment_id.store_fname)
            rec.cfd_mx_cfdi = datas
            tree = rec.get_xml_etree(base64.decodestring(datas))
            tfd_node = rec.get_tfd_etree(tree)
            if tfd_node is not None:
                rec.cfd_mx_cfdi_uuid = tfd_node.get('UUID')
            rec.cfd_mx_cfdi_supplier_rfc = tree.Emisor.get(
                'Rfc', tree.Emisor.get('rfc'))
            rec.cfd_mx_cfdi_customer_rfc = tree.Receptor.get(
                'Rfc', tree.Receptor.get('rfc'))
            certificate = tree.get('noCertificado', tree.get('NoCertificado'))

    def _retry(self):
        rep_is_required = self.filtered(lambda r: r.is_required())
        for rec in rep_is_required:
            cfdi_values = rec._create_cfdi_payment()
            error = cfdi_values.pop('error', None)
            cfdi = cfdi_values.pop('cfdi', None)

            if error:
                # cfdi failed to be generated
                rec.cfd_mx_pac_status = 'retry'
                #rec.message_post(body=error)
                raise UserError(error)
                continue
            # cfdi has been successfully generated
            #raise UserError('Todo bien ')
            rec.cfd_mx_pac_status = 'to_sign'
            filename = ('%s-%s-MX-Payment-10.xml' % (
                rec.journal_id.code, rec.name))
            ctx = self.env.context.copy()
            ctx.pop('default_type', False)
            rec.cfd_mx_cfdi_name = filename
            attachment_id = self.env['ir.attachment'].with_context(ctx).create({
                'name': filename,
                'datas_fname': filename,
                'type': 'binary',
                'res_id': rec.id,
                'res_model': rec._name,
                'datas': base64.encodestring(cfdi),
                'description': _('Mexican CFDI to payment'),
                })
            #rec.message_post(
            #    body=_('CFDI document generated (may be not signed)'),
            #    attachment_ids=[attachment_id.id])
            rec._sign()
        (self - rep_is_required).write({
            'cfd_mx_pac_status': 'none',
        })

    def _create_cfdi_payment(self):
        self.ensure_one()
        qweb = self.env['ir.qweb']
        error_log = []
        company_id = self.company_id
        pac_name = company_id.cfd_mx_pac
        values = self._create_cfdi_values()
        
        #raise UserError(values)
        if 'error' in values:
            #error_log.append(values.get('error'))
            raise UserError(values.get('error'))
        # -----------------------
        # Check the configuration
        # -----------------------
        # -Check certificate
        certificate = False
        cer_pem = False
        try:
            cer_pem, certificate = self.get_data()
            serial_number = certificate.get_serial_number()
            self.serial_number= ('%x' % serial_number)[1::2]
        except Exception:
            raise ValidationError(_('The certificate content is invalid.'))
        if not certificate:
            raise ValidationError('No valid certificate found')
            #error_log.append(_('No valid certificate found'))

        # -Check PAC
        if pac_name:
            pac_test_env = company_id.cfd_mx_test
            pac_password = company_id.cfd_mx_pac_password
            if not pac_test_env and not pac_password:

                raise ValidationError('No PAC credentials specified.')
        else:
            raise ValidationError('No PAC specified.')

        # -Compute date and time of the invoice
        partner = self.journal_id.address_issued_id or self.company_id.partner_id.commercial_partner_id
        tz = self.env['account.invoice']._get_timezone(
            partner.state_id.code)
        date_mx = datetime.now(tz)
        if not self.cfd_mx_expedition_date:
            self.cfd_mx_expedition_date = date_mx.date()
        if not self.cfd_mx_time_payment:
            self.cfd_mx_time_payment = date_mx.strftime(
                DEFAULT_SERVER_TIME_FORMAT)

        time_invoice = datetime.strptime(self.cfd_mx_time_payment,
                                         DEFAULT_SERVER_TIME_FORMAT).time()

        # -----------------------
        # Create the EDI document
        # -----------------------

        # -Compute certificate data
        values['date'] = datetime.combine(
            fields.Datetime.from_string(self.cfd_mx_expedition_date),
            time_invoice).strftime('%Y-%m-%dT%H:%M:%S')

        values['certificate_number'] = self.serial_number
        values['certificate'] = cer_pem

        # -Compute cfdi
        cfdi = qweb.render(CFDI_TEMPLATE, values=values)
        cfdi = get_format(cfdi)
        tree = fromstring(cfdi)
        
        cadena = self.env['account.invoice'].generate_cadena(CFDI_XSLT_CADENA, tree)
        tree.attrib['Sello'] = self.get_encrypted_cadena(cadena, company_id)
        fil= open('/tmp/pago.xml', 'w')
        fil.write(etree.tostring(tree))
        fil.close()

        # TODO - Check with XSD
        return {'cfdi': etree.tostring(tree, pretty_print=True, xml_declaration=True, encoding='UTF-8')}

    def _create_cfdi_values(self):
        """Create the values to fill the CFDI template with complement to
        payments."""
        self.ensure_one()
        invoice_obj = self.env['account.invoice']
        precision_digits = self.env['decimal.precision'].precision_get(
            self.currency_id.name)
        values = {
            'record': self,
            'supplier': self.company_id.partner_id.commercial_partner_id,
            'issued': self.journal_id.address_issued_id,
            'customer': self.partner_id.commercial_partner_id,
            'fiscal_regime': "601",#self.company_id.cfd_mx_fiscal_regime,---------PROBLEMAS CON EL REGIMEN
            'invoice': invoice_obj,
        }

        values.update(invoice_obj._get_serie_and_folio(self.name))

        values['decimal_precision'] = precision_digits
        values.update(self.payment_data())
        return values

    def get_cfdi_related(self):
        """To node CfdiRelacionados get documents related with each invoice
        from cfd_mx_origin, hope the next structure:
            relation type|UUIDs separated by ,"""
        self.ensure_one()
        if not self.cfd_mx_origin:
            return {}
        origin = self.cfd_mx_origin.split('|')
        uuids = origin[1].split(',') if len(origin) > 1 else []
        return {
            'type': origin[0],
            'related': [u.strip() for u in uuids],
            }
    #Revisar los nombres de las variables y si funciona
    def payment_data(self):
        self.ensure_one()
        # Based on "En caso de no contar con la hora se debe registrar 12:00:00"
        mxn = self.env.ref('base.MXN')
        date = datetime.combine(
            fields.Datetime.from_string(self.payment_date),
            datetime.strptime('12:00:00', '%H:%M:%S').time()).strftime('%Y-%m-%dT%H:%M:%S')
        total_paid = total_curr = total_currency = 0
        for invoice in self.invoice_ids:
            lista = invoice._get_invoice_payment_info_JSON()
            amount = [p for p in invoice._get_invoice_payment_info_JSON() if (
                p.get('payment_id', False) == self.id or not p.get(
                    'payment_id') and (not p.get('invoice_id') or p.get(
                        'invoice_id') == invoice.id))]
            #raise UserError(amount)
            amount_payment = sum([data.get('amount', 0.0) for data in amount])
            total_paid += amount_payment if invoice.currency_id != self.currency_id else 0
            total_currency += amount_payment if invoice.currency_id == self.currency_id else 0
            total_curr += invoice.currency_id.with_context(date=self.payment_date)._convert(
                    amount_payment, self.currency_id, self.company_id,
                    self.payment_date)
        precision = self.env['decimal.precision'].precision_get('Account')
        if float_compare(
                self.amount, total_curr, precision_digits=precision) > 0.0:
            return {'error': _(
                '<b>The amount paid is bigger than the sum of the invoices.'
                '</b><br/><br/>'
                'Which actions can you take?\n'
                '<ul>'
                '<ol>If the customer has more invoices, go to those invoices '
                'and reconcile them with this payment.</ol>'
                '<ol>If the customer <b>has not</b> more invoices to be paid '
                'You need to create a new invoice with a product that will '
                'represent the payment in advance and reconcile such invoice '
                'with this payment.</ol>'
                '</ul>'
                '<p>If you follow this steps once you finish them and the '
                'paid amount is bellow the sum of invoices the payment '
                'will be automatically signed'
                '</p><blockquote>For more information please read '
                '<a href="http://omawww.sat.gob.mx/informacion_fiscal/factura_electronica/Documents/Complementoscfdi/Guia_comple_pagos.pdf">'
                ' this SAT reference </a>, Pag. 22</blockquote>')
            }
        ctx = dict(company_id=self.company_id.id, date=self.payment_date)
        rate = ('%.6f' % (self.currency_id.with_context(**ctx)._convert(
            1, mxn, self.company_id, self.payment_date, round=False))) if self.currency_id.name != 'MXN' else False
        partner_bank = self.cfd_mx_partner_bank_id.bank_id
        company_bank = self.journal_id.bank_account_id
        payment_code = self.cfd_mx_payment_method_id.code
        acc_emitter_ok = payment_code in [
            '02', '03', '04', '05', '06', '28', '29', '99']
        acc_receiver_ok = payment_code in [
            '02', '03', '04', '05', '28', '29', '99']
        bank_name_ok = payment_code in ['02', '03', '04', '28', '29', '99']
        vat = 'XEXX010101000' if partner_bank.country and partner_bank.country != self.env.ref(
            'base.mx') else partner_bank.cfd_mx_vat
        return {
            'mxn': mxn,
            'payment_date': date,
            'payment_rate': rate,
            'pay_vat_ord': False,
            'pay_account_ord': False,
            'pay_vat_receiver': False,
            'pay_account_receiver': False,
            'pay_ent_type': False,
            'pay_certificate': False,
            'pay_string': False,
            'pay_stamp': False,
            'total_paid': total_paid,
            'total_currency': total_currency,
            'pay_vat_ord': vat if acc_emitter_ok else None,
            'pay_name_ord': partner_bank.name if bank_name_ok else None,
            'pay_account_ord': (self.cfd_mx_partner_bank_id.acc_number or '').replace(
                ' ', '') if acc_emitter_ok else None,
            'pay_vat_receiver': company_bank.bank_id.cfd_mx_vat if acc_receiver_ok else None,
            'pay_account_receiver': (company_bank.acc_number or '').replace(
                ' ', '') if acc_receiver_ok else None,
        }

        
        #raise UserError(str(rate)+'#'+str(total_paid)+'#'+str(total_currency))

    def _sign(self):
        """Call the sign service with records that can be signed."""
        records = self.search([
            ('cfd_mx_pac_status', 'not in', ['signed', 'to_cancel', 'cancelled', 'retry']),
            ('id', 'in', self.ids)])
        records._call_service('sign')

    def _post_cancel_process(self, cancelled, code=None, msg=None):
        '''Post process the results of the cancel service.

        :param cancelled: is the cancel has been done with success
        :param code: an eventual error code
        :param msg: an eventual error msg
        '''

        self.ensure_one()
        if cancelled:
            body_msg = _('The cancel service has been called with success')
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
        else:
            body_msg = _('The cancel service requested failed')
        post_msg = []
        if code:
            post_msg.extend([_('Code: %s') % code])
        if msg:
            post_msg.extend([_('Message: %s') % msg])
        self.message_post(
            body=body_msg + invoice_cfdi.create_list_html(post_msg))

    
    def _call_service(self, service_type):
        """Call the right method according to the pac_name, it's info returned
        by the '_l10n_mx_edi_%s_info' % pac_name'
        method and the service_type passed as parameter.
        :param service_type: sign or cancel"""
        invoice_obj = self.env['account.invoice']
        # Regroup the invoices by company (= by pac)
        comp_x_records = groupby(self, lambda r: r.company_id)
        for company_id, records in comp_x_records:
            pac_name = company_id.cfd_mx_pac
            if not pac_name:
                continue
            # Get the informations about the pac
            #pac_info_func = '_%s_info' % pac_name
            service_func = '_%s_%s' % (pac_name, service_type)
            pac_info =  invoice_obj._finkok_info(company_id, service_type)#getattr(invoice_obj, pac_info_func)(company_id, service_type)
            # Call the service with invoices one by one or all together according to the 'multi' value.
            # TODO - Check multi
            for record in records:
                if service_type == 'sign':
                    record._finkok_sign(pac_info)
                else:
                    record._finkok_cancel(pac_info)
                #getattr(record, service_func)(pac_info)

    # -------------------------------------------------------------------------
    # SAT/PAC service methods
    # -------------------------------------------------------------------------

    
    def _get_payment_write_off(self):
        self.ensure_one()
        writeoff_move_line = self.move_line_ids.filtered(lambda l: l.account_id == self.writeoff_account_id and l.name == self.writeoff_label)
        res = {}
        if writeoff_move_line and self.invoice_ids:
            # get the writeoff value in invoice currency
            last_invoice = self.invoice_ids[-1]
            # if the invoice has the same currency as the company, use the balance
            if last_invoice.currency_id == last_invoice.company_currency_id:
                write_off_invoice_currency = writeoff_move_line.balance
            # if the invoice has the same currency as the payment, use the amount_currency
            elif last_invoice.currency_id == writeoff_move_line.currency_id:
                write_off_invoice_currency = writeoff_move_line.amount_currency
            # if the invoice don't have the same currency as the company or as the payment
            # convert the write_off from the currency of the payment, to the currency of the invoice
            else:
                write_off_invoice_currency = writeoff_move_line.currency_id._convert(
                    writeoff_move_line.amount_currency or writeoff_move_line.balance, last_invoice.currency_id,
                    last_invoice.company_id, last_invoice.date
                )
            if write_off_invoice_currency > 0:
                res[last_invoice.id] = write_off_invoice_currency
        return res
    
    @api.model
    def _solfact_info(self, company_id, service_type):
        test = company_id.l10n_mx_edi_pac_test_env
        username = company_id.l10n_mx_edi_pac_username
        password = company_id.l10n_mx_edi_pac_password
        url = 'https://testing.solucionfactible.com/ws/services/Timbrado?wsdl'\
            if test else 'https://solucionfactible.com/ws/services/Timbrado?wsdl'
        return {
            'url': url,
            'multi': False,  # TODO: implement multi
            'username': 'testing@solucionfactible.com' if test else username,
            'password': 'timbrado.SF.16672' if test else password,
        }

    def _solfact_sign(self, pac_info):
        '''SIGN for Solucion Factible.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for rec in self:
            cfdi = base64.decodestring(rec.cfd_mx_cfdi)
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                response = client.service.timbrar(username, password, cfdi, False)
            except Exception as e:
                rec.cfd_mx_log_error(str(e))
                continue
            msg = getattr(response.resultados[0], 'mensaje', None)
            code = getattr(response.resultados[0], 'status', None)
            xml_signed = getattr(response.resultados[0], 'cfdiTimbrado', None)
            if xml_signed:
                xml_signed = base64.b64encode(xml_signed)
            rec._post_sign_process(
                xml_signed if xml_signed else None, code, msg)

    def _solfact_cancel(self, pac_info):
        '''CANCEL for Solucion Factible.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for rec in self:
            uuids = [rec.cfd_mx_cfdi_uuid]
            certificate_ids = rec.company_id.l10n_mx_edi_certificate_ids
            certificate_id = certificate_ids.sudo().get_valid_certificate()
            cer_pem = certificate_id.get_pem_cer(certificate_id.content)
            key_pem = certificate_id.get_pem_key(
                certificate_id.key, certificate_id.password)
            key_password = certificate_id.password
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                response = client.service.cancelar(username, password, uuids, cer_pem, key_pem, key_password)
            except Exception as e:
                rec.cfd_mx_log_error(str(e))
                continue
            code = getattr(response.resultados[0], 'statusUUID', None)
            cancelled = code in ('201', '202')  # cancelled or previously cancelled
            # no show code and response message if cancel was success
            msg = '' if cancelled else getattr(response.resultados[0], 'mensaje', None)
            code = '' if cancelled else code
            rec._post_cancel_process(cancelled, code, msg)
   


    def _finkok_sign(self, pac_info):
        """SIGN for Finkok."""
        # TODO - Duplicated with the invoice one
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for rec in self:
            cfdi = base64.decodestring(rec.cfd_mx_cfdi)
            ####################### Imprime el cfdi para ser enviado plz
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                response = client.service.stamp(cfdi, username, password)
            except Exception as e:
                raise UserError(str(e))
                #rec.cfd_mx_log_error(str(e))
                continue
            code = 0
            msg = None
            if response.Incidencias:
                code = getattr(response.Incidencias.Incidencia[0], 'CodigoError', None)
                msg = getattr(response.Incidencias.Incidencia[0], 'MensajeIncidencia', None)

            xml_signed = getattr(response, 'xml', None)
            ################################Para obtener el resultado de la firma
            if xml_signed:
                xml_signed = base64.b64encode(xml_signed.encode('utf-8'))
            rec._post_sign_process(xml_signed, code, msg)

    

    def _finkok_cancel(self, pac_info):
        '''CANCEL for Finkok.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for inv in self:
            uuid = inv.cfd_mx_cfdi_uuid
            certificate_id = inv.company_id.cfd_mx_cer
            key_certificate = inv.company_id.cfd_mx_key
            pass_certificate = inv.company_id.cfd_mx_key_password
            company_id = self.company_id
            cer_pem = inv.get_pem_cer(certificate_id)
            key_pem = inv.get_pem_key(key_certificate, pass_certificate)
            cancelled = False
            code = False
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                uuid_type = client.get_type('ns0:stringArray')()
                uuid_type.string = [uuid]
                invoices_list = client.get_type('ns1:UUIDS')(uuid_type)
                response = client.service.cancel(invoices_list, username, password, company_id.vat, cer_pem, key_pem)
            except Exception as e:
                raise UserError(e)
            if not (hasattr(response, 'Folios') and response.Folios):
                msg = _('A delay of 2 hours has to be respected before to cancel')
            else:
                code = getattr(response.Folios.Folio[0], 'EstatusUUID', None)
                cancelled = code in ('201', '202')  # cancelled or previously cancelled
                # no show code and response message if cancel was success
                code = '' if cancelled else code
                msg = '' if cancelled else _("Cancelling got an error")
                raise UserError('Código de error: '+code+' Mensaje: '+msg)
            inv._post_cancel_process(cancelled, code, msg)

    def _post_sign_process(self, xml_signed, code=None, msg=None):
        """Post process the results of the sign service.

        :param xml_signed: the xml signed datas codified in base64
        :param code: an eventual error code
        :param msg: an eventual error msg
        """
        # TODO - Duplicated
        self.ensure_one()
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
            
    def update_pac_status(self):
        """Synchronize both systems: Odoo & PAC if the invoices need to be
        signed or cancelled."""
        # TODO - Duplicated
        for record in self:
            if record.cfd_mx_pac_status == 'to_sign':
                record._sign()
            elif record.cfd_mx_pac_status == 'to_cancel':
                record._cancellation()
            elif record.cfd_mx_pac_status == 'retry':
                record._retry()

    def update_sat_status(self):
        """Synchronize both systems: Odoo & SAT to make sure the invoice is valid.
        """
        url = 'https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc?wsdl'
        headers = {'SOAPAction': 'http://tempuri.org/IConsultaCFDIService/Consulta', 'Content-Type': 'text/xml; charset=utf-8'}
        template = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:ns0="http://tempuri.org/" xmlns:ns1="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
   <SOAP-ENV:Header/>
   <ns1:Body>
      <ns0:Consulta>
         <ns0:expresionImpresa>${data}</ns0:expresionImpresa>
      </ns0:Consulta>
   </ns1:Body>
</SOAP-ENV:Envelope>"""
        namespace = {'a': 'http://schemas.datacontract.org/2004/07/Sat.Cfdi.Negocio.ConsultaCfdi.Servicio'}
        for rec in self.filtered('cfd_mx_cfdi'):
            supplier_rfc = rec.cfd_mx_cfdi_supplier_rfc
            customer_rfc = rec.cfd_mx_cfdi_customer_rfc
            total = 0
            uuid = rec.cfd_mx_cfdi_uuid
            params = '?re=%s&amp;rr=%s&amp;tt=%s&amp;id=%s' % (
                html_escape(html_escape(supplier_rfc or '')),
                html_escape(html_escape(customer_rfc or '')),
                total or 0.0, uuid or '')
            soap_env = template.format(data=params)

            try:
                soap_xml = requests.post(url, data=soap_env,
                                         headers=headers, timeout=20)
                response = fromstring(soap_xml.text)
                status = response.xpath(
                    '//a:Estado', namespaces=namespace)
            except Exception as e:
                raise UserError(str(e))
                rec.self.message_post(str(e))
            rec.cfd_mx_sat_status = CFDI_SAT_QR_STATE.get(
                status[0] if status else '', 'none')
    
    def force_payment_complement(self):
        '''Allow force the CFDI generation when the complement is not required
        '''
        self.with_context(force_ref=True)._retry()
    """
    def _l10n_mx_edi_sat_synchronously(self, batch_size=10):
        Update the SAT status synchronously

        This method Calls :meth:`~.l10n_mx_edi_update_sat_status` by batches,
        ensuring changes are committed after processing each batch. This is
        intended to be able to process a lot of records on a safely manner,
        avoiding a possible sistematic failure withoud any payment updated.

        This is especially useful when running crons.

        :param batch_size: the number of payments to process by batch
        :type batch_size: int
        
        for idx in range(0, len(self), batch_size):
            with self.env.cr.savepoint():
                self[idx:idx+batch_size].update_sat_status()
    """

    def _set_cfdi_origin(self, uuid):
        """Try to write the origin in of the CFDI, it is important in order
        to have a centralized way to manage this elements due to the fact
        that this logic can be used in several places functionally speaking
        all around Odoo.
        :param uuid:
        :return:
        """
        self.ensure_one()
        origin = '04|%s' % uuid
        self.update({'cfd_mx_origin': origin})
        return origin

    def action_draft(self):
        for record in self.filtered('cfd_mx_cfdi_uuid'):
            record.write({
                'cfd_mx_expedition_date': False,
                'cfd_mx_pac_status': 'none',
                'cfd_mx_time_payment': False,
                'cfd_mx_origin': record._set_cfdi_origin(
                    record.cfd_mx_cfdi_uuid),
            })
        return super(AccountPayment, self).action_draft()

    def unlink(self):
        for rec in self.filtered(lambda r: r.is_required() and
                                 r.cfd_mx_pac_status == 'signed'):
            rec._cancellation()
        if self.filtered(lambda p: p.cfd_mx_pac_status == 'signed'):
            raise UserError(_('In order to delete a payment, you must first '
                              'cancel it in the SAT system.'))
        return super(AccountPayment, self).unlink()

    @api.onchange('partner_id')
    def _onchange_partner_bank_id(self):
        self.cfd_mx_partner_bank_id = False
        if len(self.partner_id.commercial_partner_id.bank_ids) == 1:
            self.cfd_mx_partner_bank_id = self.partner_id.commercial_partner_id.bank_ids  # noqa


    def get_encrypted_cadena(self, cadena, company):
        '''Encrypt the cadena using the private key.
        '''
        key_pem = self.get_pem_key(company.cfd_mx_key, company.cfd_mx_key_password)
        
        private_key = crypto.load_privatekey(crypto.FILETYPE_PEM, key_pem)
        #raise UserError(private_key)
        encrypt = 'sha256WithRSAEncryption'
        cadena_crypted = crypto.sign(private_key, cadena, b"sha256")
        #raise UserError(cadena_crypted)
        return base64.b64encode(cadena_crypted)

    def generate_cadena(self, xslt_path, cfdi_as_tree):
        '''Generate the cadena of the cfdi based on an xslt file.
        The cadena is the sequence of data formed with the information contained within the cfdi.
        This can be encoded with the certificate to create the digital seal.
        Since the cadena is generated with the invoice data, any change in it will be noticed resulting in a different
        cadena and so, ensure the invoice has not been modified.

        :param xslt_path: The path to the xslt file.
        :param cfdi_as_tree: The cfdi converted as a tree
        :return: A string computed with the invoice data called the cadena
        '''
        xslt_root = etree.parse(tools.file_open(xslt_path))
        return str(etree.XSLT(xslt_root)(cfdi_as_tree))


    def get_pem_key(self, key, password):
        '''Get the current key in PEM format
        '''
        return self.convert_key_cer_to_pem(base64.decodestring(key), password)
   
    def get_pem_cer(self, content):
        '''Get the current content in PEM format
        '''
        return ssl.DER_cert_to_PEM_cert(base64.decodestring(content)).encode('UTF-8')


    def convert_key_cer_to_pem(self, key, password):
        # TODO compute it from a python way
        with tempfile.NamedTemporaryFile('wb', suffix='.key', prefix='edi.mx.tmp.') as key_file, \
                tempfile.NamedTemporaryFile('wb', suffix='.txt', prefix='edi.mx.tmp.') as pwd_file, \
                tempfile.NamedTemporaryFile('rb', suffix='.key', prefix='edi.mx.tmp.') as keypem_file:
            
            key_file.write(key)
            key_file.flush()
            pwd_file.write(password)
            pwd_file.flush()

            subprocess.call((KEY_TO_PEM_CMD % (key_file.name, keypem_file.name, pwd_file.name)).split())
            
            key_pem = keypem_file.read()

        return key_pem
    
    def get_data(self):
        '''Return the content (b64 encoded) and the certificate decrypted
        '''
        cer_pem = self.get_pem_cer(self.company_id.cfd_mx_cer)
        certificate = crypto.load_certificate(crypto.FILETYPE_PEM, cer_pem)
        for to_del in ['\n', ssl.PEM_HEADER, ssl.PEM_FOOTER]:
            cer_pem = cer_pem.replace(to_del.encode('UTF-8'), b'')
        return cer_pem, certificate


    def _get_xml_etree(self, cfdi=None):
        '''Get an objectified tree representing the cfdi.
        If the cfdi is not specified, retrieve it from the attachment.
        :param cfdi: The cfdi as string
        :return: An objectified tree
        '''
        #TODO helper which is not of too much help and should be removed
        self.ensure_one()
        if cfdi is None:
            cfdi = base64.decodestring(self.cfd_mx_cfdi)
        return fromstring(cfdi)

    @api.model
    def _get_tfd_etree(self, cfdi):
        '''Get the TimbreFiscalDigital node from the cfdi.
        :param cfdi: The cfdi as etree
        :return: the TimbreFiscalDigital node
        '''
        if not hasattr(cfdi, 'Complemento'):
            return None
        attribute = 'tfd:TimbreFiscalDigital[1]'
        namespace = {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'}
        node = cfdi.Complemento.xpath(attribute, namespaces=namespace)
        return node[0] if node else None

    @api.model
    def _get_cadena(self):
        self.ensure_one()
        #get the xslt path
        xslt_path = CFDI_XSLT_CADENA_TFD
        #get the cfdi as eTree
        cfdi = base64.decodestring(self.cfd_mx_cfdi)
        cfdi = self._get_xml_etree(cfdi)
        cfdi = self._get_tfd_etree(cfdi)
        #return the cadena
        return self.generate_cadena(xslt_path, cfdi)
