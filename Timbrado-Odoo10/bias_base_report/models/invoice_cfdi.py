# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import subprocess#
import tempfile#


import odoo
from odoo import models, fields, tools, api, _
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.tools import DEFAULT_SERVER_TIME_FORMAT
from odoo.tools.misc import DEFAULT_SERVER_DATETIME_FORMAT
from amount_to_text_es_MX import *
import requests
import ssl#
from lxml import etree#
from lxml.objectify import fromstring#
from requests import Request, Session
from zeep import Client
from zeep.transports import Transport
import re
from datetime import date, datetime, timedelta
from pytz import timezone, utc
import textwrap
import base64, json
import logging
#from OpenSSL import crypto
_logger = logging.getLogger(__name__)


try:
    from OpenSSL import crypto
except ImportError:
    _logger.warning('OpenSSL library not found. If you plan to use l10n_mx_edi, please install the library from https://pypi.python.org/pypi/pyOpenSSL')

KEY_TO_PEM_CMD = 'openssl pkcs8 -in %s -inform der -outform pem -out %s -passin file:%s'
CFDI_TEMPLATE = 'bias_base_report.cfdiv33'
CFDI_XSLT_CADENA = 'bias_base_report/data/3.3/cadenaoriginal.xslt'

catcfdi = {
    'formaPago': ['01','02','03','04','05','06','08','12','13','14','15','17','23','24','25','26','27','28','29','30','99'],
    'metodoPago': ['PUE', 'PPD'],
    'impuesto': ['001','002','003'],    
    'regimenFiscal': ['601','603','605','606','608','609','610','611','612','614','616','620','621','622','623','624','628','607','629','630','615'],
    'ingreso': ['I','E','T','N','P'],
    'usoCdfi': ['G01','G02','G03','I01','I02','I03','I04','I05','I06','I07','I08','D01','D02','D03','D04','D05','D06','D07','D08','D09','D10','P01']
}

def get_format(xml):
    xml = xml.replace("<Comprobante", "<cfdi:Comprobante   xsi:schemaLocation=\"http://www.sat.gob.mx/cfd/3  http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv33.xsd\" xmlns:cfdi=\"http://www.sat.gob.mx/cfd/3\" xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"")
    xml = xml.replace("</Comprobante", "</cfdi:Comprobante")
    xml = xml.replace("<Concepto", "<cfdi:Concepto")
    xml = xml.replace("</Concepto", "</cfdi:Concepto")
    xml = xml.replace("<Traslado", "<cfdi:Traslado")
    xml = xml.replace("</Traslado", "</cfdi:Traslado")
    xml = xml.replace("<Impuesto", "<cfdi:Impuesto")
    xml = xml.replace("</Impuesto", "</cfdi:Impuesto")
    xml = xml.replace("<Emisor", "<cfdi:Emisor")
    xml = xml.replace("</Emisor", "</cfdi:Emisor")
    xml = xml.replace("<Receptor", "<cfdi:Receptor")
    xml = xml.replace("</Receptor", "</cfdi:Receptor")
    xml = xml.replace("<CfdiRelacionado", "<cfdi:CfdiRelacionado")
    xml = xml.replace("</CfdiRelacionado", "</cfdi:CfdiRelacionado")
    return xml.replace("\n","")#.replace("  ", "")

def create_list_html(array):
    '''Convert an array of string to a html list.
    :param array: A list of strings
    :return: an empty string if not array, an html list otherwise.
    '''
    if not array:
        return ''
    msg = ''
    for item in array:
        msg += '<li>' + item + '</li>'
    return '<ul>' + msg + '</ul>'

class AccountCfdi(models.Model):
    _name = 'account.cfdi'

    @api.one
    def _compute_cant_letra(self):
        total = 0.0
        # if 'total' in self.env['product.product']._fields:
        if self.currency_id:
            self.cant_letra = self.get_cant_letra(self.currency_id, total)
        else:
            self.cant_letra = ""

    @api.one
    def _get_tipo_cambio(self):
        model_obj = self.env['ir.model.data']
        tipocambio = 1.0
        date_invoice = self.date_invoice or fields.Date.today()
        if date_invoice:
            if self.currency_id.name=='MXN':
                tipocambio = 1.0
            else:
                mxn_rate = self.env["ir.model.data"].get_object('base', 'MXN').with_context(date='%s 06:00:00'%(date_invoice)).rate
                rate = self.currency_id.with_context(date='%s 06:00:00'%(date_invoice)).rate
                if rate != 0.0:
                    tipocambio = (1.0 / rate) * mxn_rate
                else:
                    tipocambio = 1.0

        self.tipo_cambio = tipocambio

    @api.one
    def _get_cfd_mx_pac(self):
        self.cfd_mx_pac = self.env.user.company_id.cfd_mx_pac

    @api.one
    @api.depends("uuid")
    def _get_timbrado(self):
        res = False
        if self.uuid:
            res = True
        self.timbrada = res

    @api.one
    def _get_cadena_sat_wrap(self):
        res = ""
        if self.cadena_sat:
            print "-------------", self.cadena_sat
            # res = self.get_info_sat(self.cadena_sat, 80)
        self.cadena_sat_wrap = res

    @api.one
    def _get_sello_sat_wrap(self):
        res = ""
        if self.sello_sat:
            print "---------", self.sello_sat
            # res = self.get_info_sat(self.sello_sat, 80)
        self.sello_sat_wrap = res

    @api.one
    def _get_es_invoice(self):
        res = False
        if self.journal_id:
            if self.journal_id.id in self.company_id.cfd_mx_journal_ids.ids:
                res = True
        self.es_cfdi = res

    es_cfdi = fields.Boolean(string="Es Invoice", default=False, copy=False, compute='_get_es_invoice')
    timbrada = fields.Boolean(string="Timbrado", default=False, copy=False, compute='_get_timbrado', store=False)

    date_start = fields.Char(string="Fecha inicial", copy=False)
    date_end = fields.Char(string="Fecha final", copy=False)
    serie = fields.Char(string="Serie", size=8, copy=False)

    tipo_cambio = fields.Float(string="Tipo de cambio", digits=(12, 6),  compute='_get_tipo_cambio')
    cfd_mx_pac = fields.Char(string='PAC', compute='_get_cfd_mx_pac')
    test = fields.Boolean(string="Timbrado en modo de prueba", copy=False)
    date_invoice_cfdi = fields.Char(string="Invoice Date", copy=False)
    tipo_comprobante = fields.Selection([
            ('I', 'Ingreso'),
            ('E', 'Egreso'),
            ('T', U'Traslado'),
            ('N', U'Nómina'),
            ('P', 'Pago')
        ], string="Tipo de Comprobante", help="Catálogo de tipos de comprobante.", default="I")
    formapago_id = fields.Many2one('cfd_mx.formapago', string=u'Forma de Pago')
    sello = fields.Char(string="Sello", copy=False)
    cadena = fields.Text(string="Cadena original", copy=False)
    noCertificado = fields.Char(string="No. de serie del certificado", size=64, copy=False)
    #hora = fields.Char(string="Hora", size=8, copy=False)
    
    uuid = fields.Char(string='Timbre fiscal', copy=False)

    uuid_egreso = fields.Char(string='Timbre fiscal Egreso', copy=False)
    hora_factura = fields.Char(string='Hora', size=16)
    qrcode = fields.Binary(string="Codigo QR", copy=False)
    sello_sat = fields.Text(string="Sello del SAT", copy=False)
    sello_sat_wrap = fields.Text(string="Sello del SAT", copy=False, compute="_get_sello_sat_wrap")
    certificado_sat = fields.Text(string="No. Certificado del SAT", size=64, copy=False)
    fecha_timbrado = fields.Char(string="Fecha de Timbrado", size=32, copy=False)
    cadena_sat = fields.Text(string="Cadena SAT", copy=False)
    cadena_sat_wrap = fields.Text(string="Cadena SAT", copy=False, compute="_get_cadena_sat_wrap")
    mensaje_timbrado_pac = fields.Text('Mensaje del PAC', copy=False, default="")
    mensaje_pac = fields.Html(string='Ultimo mensaje del PAC', copy=False, default="")
    mensaje_validar = fields.Text(string='Mensaje Validacion', copy=False, default="")
    cant_letra = fields.Char(string="Cantidad con letra", copy=False, compute='_compute_cant_letra')
    cfdi_xml = fields.Binary(string="Contenido del CFDI", copy=False, readonly=True, help="CFDI en xml codificado en base 64")
    cfdi_name = fields.Char(string='CFDI name', copy=False, readonly=True,help='The attachment name of the CFDI.')
    mandada_cancelar = fields.Boolean('Mandada Cancelar', copy=False)

    @api.model
    def get_payment_method_cfdi(self):
        self.ensure_one()
        if self.cfdi_xml:
            #raise UserError(self.cfdi_xml)
            tree = fromstring(base64.decodestring(self.cfdi_xml))
            return tree.get('MetodoPago')
        else:
            None


    def get_datas(self, obj, cia):
        self.obj = obj
        self.test = cia.cfd_mx_test
        self.pac = cia.cfd_mx_pac
        self.version = cia.cfd_mx_version
        self.host = cia.cfd_mx_host
        self.port = cia.cfd_mx_port
        self.db = cia.cfd_mx_db
        return True

    def cancelation(self, obj):
        obj._check_credentials()
        vals = self._finkok_info(self.company_id, 'cancel')
        return self._finkok_cancel(vals)

    def stamp(self, obj):
        self._check_credentials()
        self.cfdi_name = ('%s-Factura-%s.xml' % ( self.move_id.name, "3-3")).replace('/', '')

        qweb= self.env['ir.qweb']
        ctx = dict(self._context) or {}
        decimal_precision = obj.env['decimal.precision'].precision_get('Account')
        res_datas = None
        cia = obj.company_id
        self.get_datas(obj, cia)
        self.cfdi_datas = {
            'relacionados': None,
            'comprobante': None,
            'emisor': None,
            'receptor': None,
            'conceptos': None,
            'vat': cia.partner_id.vat,
            'cfd': self.get_info_pac(),
            'db': self.db
        }

        datas = json.dumps(self.cfdi_datas, sort_keys=True, indent=4, separators=(',', ': '))
        
        logging.info(datas)
        #raise UserError(datas)
        data= {'record': self}
        cfdi = qweb.render(CFDI_TEMPLATE, values= data)
        cfdi = get_format(cfdi)
        tree = fromstring(cfdi)

        fil= open('/tmp/prueba.xml', 'w')
        fil.write(cfdi)
        fil.close()
        datetime_mx_tz = self._update_hour_timezone()
        time_invoice = datetime.strptime(datetime_mx_tz,
                                         DEFAULT_SERVER_TIME_FORMAT).time()
        tree.attrib['Fecha']= datetime.combine(fields.Datetime.from_string(self.date_invoice), time_invoice).strftime('%Y-%m-%dT%H:%M:%S')
       

        #Se genera el atributo de SELLO
        self.sello= self.get_sello(tree)
        tree.attrib["Sello"] = self.sello
        xml=etree.tostring(tree, pretty_print=True, xml_declaration=True, encoding='UTF-8')
        self.cfdi_xml= base64.encodestring(xml)
        vals = self._finkok_info(self.company_id, 'sign')
        return self._finkok_sign(vals)#xml#cfdi

    def get_code_impuesto(self, name_impuesto):
        if "ISR" in name_impuesto:
            return "001"
        elif "IVA" in name_impuesto:
            return "002"
        else:
            return "003"
 
    def get_info_pac(self):
        cfdi_datas = {
            'test': self.test,
            'pac': self.pac,
            'version': self.version
        }
        return cfdi_datas

    
    def get_process_data(self, obj, res):
        '''Esta permite que el archivo PDF se imprima con los
           valores del SAT una vez timbrada
        '''
        context = dict(self._context) or {}
        
        fname = "cfd_%s.xml"%(obj.number or obj.name or '')
        if context.get('type') and context.get('type') == 'pagos':
            fname = '%s.xml'% (res.UUID or obj.number or obj.name or '')
        # Adjuntos
        attachment_obj = obj.env['ir.attachment']
        attachment_values = {
            'name': fname,
            'datas': base64.encodestring(res.xml),
            'datas_fname': fname,
            'description': 'Comprobante Fiscal Digital',
            'res_model': obj._name,
            'res_id': obj.id,
            'mimetype': 'application/xml',
            'type': 'binary'
        }
        attachment_obj.create(attachment_values)
        # Guarda datos:
        tree = fromstring(res.xml)
        values = {
            'fecha_timbrado': res.Fecha,
            'sello_sat': res.SatSeal,
            'certificado_sat': res.NoCertificadoSAT,
            'sello': tree.attrib['NoCertificado'],
            'noCertificado': tree.attrib['NoCertificado'],
            'uuid': res.UUID,
            'mensaje_pac': res.CodEstatus,
            'tipo_cambio': tree.attrib['TipoCambio'],
        }

        obj.write(values)
        return True

    def get_cant_letra(self, currency, amount):
        if currency.name == 'MXN':
            nombre = currency.nombre_largo or 'pesos'
            siglas = 'M.N.'
        else:
            nombre = currency.nombre_largo or ''
            siglas = currency.name
        return amount_to_text().amount_to_text_cheque(float(amount), nombre,
                                                      siglas).capitalize()

    def action_raise_message(self, message):
        self.ensure_one()
        context = dict(self._context) or {}
        if not self.mensaje_validar:
            self.mensaje_validar = ""
        if not context.get('batch', False):
            if len(message) != 0:
                message = message.replace('<li>', '').replace('</li>', '\n')
                self.message_post(body=message)
                raise UserError(message)
        else:
            self.mensaje_validar += message
        return True
    
    @staticmethod
    def _get_serie_and_folio(number):
        values = {'serie': None, 'folio': None}
        number = (number or '').strip()
        number_matchs = [rn for rn in re.finditer('\d+', number)]
        if number_matchs:
            last_number_match = number_matchs[-1]
            values['serie'] = number[:last_number_match.start()] or None
            values['folio'] = last_number_match.group().lstrip('0') or None
        return values

################################# SECCIÓN DE FINKOK ##########################################

    def _finkok_info(self, company_id, service_type):
        test = company_id.cfd_mx_test
        username = company_id.cfd_mx_pac_username
        password = company_id.cfd_mx_pac_password
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

    def _finkok_sign(self, pac_info):
        '''SIGN for Finkok.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']

        for inv in self:
            cfdi = base64.decodestring(inv.cfdi_xml)
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                response = client.service.stamp(cfdi, username, password)
            except Exception as e:
                self.message_post(e)
                continue
            code = 0
            msg = None
            if response.Incidencias:
                code = getattr(response.Incidencias.Incidencia[0], 'CodigoError', None)
                msg = getattr(response.Incidencias.Incidencia[0], 'MensajeIncidencia', None)
                self.message_post('Código de error: '+code+' - '+msg)
            xml_signed = getattr(response, 'xml', None)

            if xml_signed:
                xml_signed = base64.b64encode(xml_signed.encode('utf-8'))
            return response


    def _finkok_cancel(self, pac_info):
        '''CANCEL for Finkok.
        '''
        url = pac_info['url']
        username = pac_info['username']
        password = pac_info['password']
        for inv in self:
            uuid = inv.uuid
            #certificate = inv.company_id.cer
            #certificate_id = certificate_ids.sudo().get_valid_certificate()
            company_id = self.company_id
            cer_pem = self.get_pem_cer(
                inv.company_id.cfd_mx_cer)
            key_pem = self.get_pem_key(
                inv.company_id.cfd_mx_key, inv.company_id.cfd_mx_key_password)
            cancelled = False
            code = False
            #raise UserError(inv.company_id.cfd_mx_key_password+ ' --- '+ base64.b64encode(cer_pem)+ '---------' +base64.b64encode(key_pem))
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                uuid_type = client.get_type('ns0:stringArray')()
                uuid_type.string = [uuid]
                invoices_list = client.get_type('ns1:UUIDS')(uuid_type)
                response = client.service.cancel(
                    invoices_list, username, password, company_id.vat, cer_pem, key_pem)
                raise UserError(response)
            except Exception as e:
                raise UserError(e)
                #inv.l10n_mx_edi_log_error(str(e))
                #continue
            raise UserError(response)
            if not getattr(response, 'Folios', None):
                code = getattr(response, 'CodEstatus', None)
                msg = _("Cancelling got an error") if code else _('A delay of 2 hours has to be respected before to cancel')
            else:
                code = getattr(response.Folios.Folio[0], 'EstatusUUID', None)
                cancelled = code in ('201', '202')  # cancelled or previously cancelled
                # no show code and response message if cancel was success
                code = '' if cancelled else code
                msg = '' if cancelled else _("Cancelling got an error")
            inv._post_cancel_process(cancelled, code, msg)


    def _post_sign_process(self, xml_signed, code=None, msg=None):
        '''Post process the results of the sign service.

        :param xml_signed: the xml signed datas codified in base64
        :param code: an eventual error code
        :param msg: an eventual error msg
        '''
        self.ensure_one()
        if xml_signed:
            # Post append addenda
            body_msg = _('The sign service has been called with success')
            # Update the pac status
            self.pac_status = 'signed'
            self.cfdi_signed = xml_signed
            # Update the content of the attachment
            attachment_id = self.retrieve_last_attachment()

            ctx = self.env.context.copy()

            ctx.pop('default_type', False)

            attachment_id = self.env['ir.attachment'].with_context(ctx).create({
                'name': self.cfdi_name,
                'res_model': "account.invoice",
                'datas': xml_signed,
                'description': 'FACTURA',
                'mimetype': 'application/xml'
                })
            #raise UserError('Se creo attachment')
            #attachment_id.write({
            #    'datas': xml_signed,
            #    'mimetype': 'application/xml'
            #})
            #xml_signed = self.l10n_mx_edi_append_addenda(xml_signed)
            post_msg = [_('The content of the attachment has been updated')]

        else:
            body_msg = _('The sign service requested failed')
            post_msg = []
        if code:
            raise UserError('error?????CODE')
            post_msg.extend([_('Code: %s') % code])
            
        if msg:
            raise UserError('error?????MENSAJE')
            post_msg.extend([_('Message: %s') % msg])
    

    def _post_cancel_process(self, cancelled, code=None, msg=None):
        '''Post process the results of the cancel service.

        :param cancelled: is the cancel has been done with success
        :param code: an eventual error code
        :param msg: an eventual error msg
        '''

        self.ensure_one()
        if cancelled:
            body_msg = _('The cancel service has been called with success')
            #self.l10n_mx_edi_pac_status = 'cancelled'
        else:
            body_msg = _('The cancel service requested failed')
        post_msg = []
        if code:
            post_msg.extend([_('Code: %s') % code])
        if msg:
            post_msg.extend([_('Message: %s') % msg])
        self.message_post(
            body=body_msg + create_list_html(post_msg),
            subtype='account.mt_invoice_validated')

    ################## AUXILIARES PARA STAMP ##################
    
    @api.model
    def retrieve_attachments(self):
        '''Retrieve all the cfdi attachments generated for this invoice.

        :return: An ir.attachment recordset
        '''
        self.ensure_one()
        if not self.cfdi_name:
            return []
        domain = [
            ('res_id', '=', self.id),
            ('res_model', '=', self._name),
            ('name', '=', self.cfdi_name)]
        
        obj = self.env['ir.attachment'].search(domain)
        #raise UserError(obj)

    @api.model 
    def retrieve_last_attachment(self):
        attachment_ids = self.retrieve_attachments()
        return attachment_ids and attachment_ids[0] or None

    ############### TRATAMIENTO DE CERTIFICADO ############

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


    def get_sello(self,tree):
        #tree= self.get_xml_etree(self.cfdi_xml)
        #raise UserError(fromstring(base64.decodestring(self.cfdi_xml)))
        cadena= self.generate_cadena(CFDI_XSLT_CADENA, tree)
        self.cadena=cadena
        #raise UserError(cadena)
        sello= self.sudo().get_encrypted_cadena(cadena)
        self.sello=sello
        return sello

    def get_encrypted_cadena(self, cadena):
        '''Encrypt the cadena using the private key.
        '''
        key_pem = self.get_pem_key(self.company_id.cfd_mx_key, self.company_id.cfd_mx_key_password)
        
        private_key = crypto.load_privatekey(crypto.FILETYPE_PEM, key_pem)
        #raise UserError(private_key)
        encrypt = 'sha256WithRSAEncryption'
        cadena_crypted = crypto.sign(private_key, cadena, b"sha256")
        #raise UserError(cadena_crypted)
        return base64.b64encode(cadena_crypted)

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

    def _check_credentials(self):
        '''Check the validity of content/key/password and fill the fields
        with the certificate values.
        '''

        mexican_tz = timezone('America/Mexico_City')
        mexican_dt= datetime.now(mexican_tz) 
        
        #mexican_dt = fields.Datetime.context_timestamp(
        #    self.with_context(tz='America/Mexico_City'), fields.Datetime.now())
        date_format = '%Y%m%d%H%M%SZ'

        try:
            cer_pem, certificate = self.get_data()
            before = mexican_tz.localize(
                datetime.strptime(certificate.get_notBefore().decode("utf-8"), date_format))
            after = mexican_tz.localize(
                datetime.strptime(certificate.get_notAfter().decode("utf-8"), date_format))
            serial_number = certificate.get_serial_number()
        #except except_orm as exc_orm:
        #    raise exc_orm
        except Exception:
            raise ValidationError(_('The certificate content is invalid.'))
        # Assign extracted values from the certificate
        self.noCertificado = ('%x' % serial_number)[1::2]
        self.date_start = before.strftime(DEFAULT_SERVER_DATETIME_FORMAT)
        self.date_end = after.strftime(DEFAULT_SERVER_DATETIME_FORMAT)
        if mexican_dt > after:
            raise ValidationError(_('The certificate is expired since %s') % self.date_end)
        # Check the pair key/password
        try:
            key_pem = self.get_pem_key(self.company_id.cfd_mx_key, self.company_id.cfd_mx_key_password)
            crypto.load_privatekey(crypto.FILETYPE_PEM, key_pem)
        except Exception:
            raise ValidationError(_('The certificate key and/or password is/are invalid.'))

    ################################## DATE ##############################################
    def _get_timezone(self, state):
        # northwest area
        if state == 'BCN':
            return timezone('America/Tijuana')
        # Southeast area
        elif state == 'ROO':
            return timezone('America/Cancun')
        # Pacific area
        elif state in ('BCS', 'CHH', 'SIN', 'NAY'):
            return timezone('America/Chihuahua')
        # Sonora
        elif state == 'SON':
            return timezone('America/Hermosillo')
        # By default, takes the central area timezone
        return timezone('America/Mexico_City')

    def _update_hour_timezone(self):
        partner = self.partner_id
        tz = self._get_timezone(partner.state_id.code)
        datetime_mx_tz = datetime.now(tz)
        return datetime_mx_tz.strftime("%H:%M:%S")


    def _get_reconciled_payments(self):
        """Helper used to retrieve the reconciled payments on this journal entry"""
        pay_term_line_ids = self.line_ids.filtered(lambda line: line.account_id.user_type_id.type in ('receivable', 'payable'))
        reconciled_amls = pay_term_line_ids.mapped('matched_debit_ids.debit_move_id') + \
                          pay_term_line_ids.mapped('matched_credit_ids.credit_move_id')
        return reconciled_amls.mapped('payment_id')

    def total_impuestos(self, tipo):
        total_t = 0
        total_w = 0
        for line in self.invoice_line_ids.filtered('price_subtotal'):
            price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0)
            tax_line = {tax['id']: tax for tax in line.invoice_line_tax_ids.compute_all(
                price, line.currency_id, line.quantity, line.product_id, line.partner_id)['taxes']}
            for tax in line.invoice_line_tax_ids.filtered(lambda r: r.cfdi_tax_type != 'Exento'):
                tax_dict = tax_line.get(tax.id, {})
                amount = round(abs(tax_dict.get(
                    'amount', tax.amount / 100 * float("%.2f" % line.price_subtotal))), 2)
                rate = round(abs(tax.amount), 2)
                if tax.amount >= 0 and tipo=='transferred':
                    total_t += amount
                if tax.amount < 0 and tipo=='withhold':
                    total_w += amount
        if tipo=='transferred':
            return total_t
        else:
            return total_w

    def create_taxes_cfdi(self):
        '''Create the taxes values to fill the CFDI template.
        '''
        self.ensure_one()
        values = {
            'withholding': [],
            'transferred': [],
        }
        taxes = {}
        for line in self.invoice_line_ids.filtered('price_subtotal'):
            price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0)
            tax_line = {tax['id']: tax for tax in line.invoice_line_tax_ids.compute_all(
                price, line.currency_id, line.quantity, line.product_id, line.partner_id)['taxes']}
            for tax in line.invoice_line_tax_ids.filtered(lambda r: r.cfdi_tax_type != 'Exento'):
                tax_dict = tax_line.get(tax.id, {})
                amount = round(abs(tax_dict.get(
                    'amount', tax.amount / 100 * float("%.2f" % line.price_subtotal))), 2)
                rate = round(abs(tax.amount), 2)
                if tax.id not in taxes:
                    taxes.update({tax.id: {
                        'name': (tax.tag_ids[0].name
                                 if tax.mapped('tag_ids') else tax.name).upper(),
                        'amount': amount,
                        'rate': rate if tax.amount_type == 'fixed' else rate / 100.0,
                        'type': tax.cfdi_tipofactor,
                        'tax_amount': tax_dict.get('amount', tax.amount),
                    }})
                else:
                    taxes[tax.id].update({
                        'amount': taxes[tax.id]['amount'] + amount
                    })
        values['transferred'] = [tax for tax in taxes.values() if tax['tax_amount'] >= 0]
        values['withholding'] = self._group_withholding(
            [tax for tax in taxes.values() if tax['tax_amount'] < 0])
        return values

    def _group_withholding(self, withholding):
        """In the Taxes node the withholding must be group by tax type"""
        if not withholding:
            return withholding
        new_withholding = {}
        for tax in withholding:
            if tax['name'] not in new_withholding:
                new_withholding.update({tax['name']: tax})
                continue
            new_withholding[tax['name']].update({'amount': new_withholding[
                tax['name']]['amount'] + tax['amount']})
        return list(new_withholding.values())

    """
    def _l10n_mx_edi_create_cfdi_values(self):
        '''Create the values to fill the CFDI template.
        '''
        self.ensure_one()
        partner_id = self.partner_id
        if self.partner_id.type != 'invoice':
            partner_id = self.partner_id.commercial_partner_id
        values = {
            'record': self,
            'currency_name': self.currency_id.name,
            'supplier': self.company_id.partner_id.commercial_partner_id,
            'issued': self.journal_id.l10n_mx_address_issued_id,
            'customer': partner_id,
            'fiscal_regime': self.company_id.l10n_mx_edi_fiscal_regime,
            'payment_method': self.l10n_mx_edi_payment_method_id.code,
            'use_cfdi': self.l10n_mx_edi_usage,
            'conditions': self._get_string_cfdi(
                self.invoice_payment_term_id.name, 1000) if self.invoice_payment_term_id else False,
        }

        values.update(self._l10n_mx_get_serie_and_folio(self.name))
        ctx = dict(company_id=self.company_id.id, date=self.invoice_date)
        mxn = self.env.ref('base.MXN').with_context(ctx)
        invoice_currency = self.currency_id.with_context(ctx)
        values['rate'] = ('%.6f' % (
            invoice_currency._convert(1, mxn, self.company_id, self.invoice_date or fields.Date.today(), round=False))) if self.currency_id.name != 'MXN' else False

        values['document_type'] = 'ingreso' if self.type == 'out_invoice' else 'egreso'
        values['payment_policy'] = self._l10n_mx_edi_get_payment_policy()
        domicile = self.journal_id.l10n_mx_address_issued_id or self.company_id
        values['domicile'] = '%s %s, %s' % (
                domicile.city,
                domicile.state_id.name,
                domicile.country_id.name,
        )

        values['decimal_precision'] = precision_digits
        subtotal_wo_discount = lambda l: float_round(
            l.price_subtotal / (1 - l.discount/100) if l.discount != 100 else
            l.price_unit * l.quantity, int(precision_digits))
        values['subtotal_wo_discount'] = subtotal_wo_discount
        get_discount = lambda l, d: ('%.*f' % (
            int(d), subtotal_wo_discount(l) - l.price_subtotal)) if l.discount else False
        values['total_discount'] = get_discount
        total_discount = sum([float(get_discount(p, precision_digits)) for p in self.invoice_line_ids])
        values['amount_untaxed'] = '%.*f' % (
            precision_digits, sum([subtotal_wo_discount(p) for p in self.invoice_line_ids]))
        values['amount_discount'] = '%.*f' % (precision_digits, total_discount) if total_discount else None

        values['taxes'] = self._l10n_mx_edi_create_taxes_cfdi_values()
        values['amount_total'] = '%0.*f' % (precision_digits,
            float(values['amount_untaxed']) - float(values['amount_discount'] or 0) + (
                values['taxes']['total_transferred'] or 0) - (values['taxes']['total_withhold'] or 0))

        values['tax_name'] = lambda t: {'ISR': '001', 'IVA': '002', 'IEPS': '003'}.get(t, False)

        if self.l10n_mx_edi_partner_bank_id:
            digits = [s for s in self.l10n_mx_edi_partner_bank_id.acc_number if s.isdigit()]
            acc_4number = ''.join(digits)[-4:]
            values['account_4num'] = acc_4number if len(acc_4number) == 4 else None
        else:
            values['account_4num'] = None

        values.update(self._get_external_trade_values(values))
        return values
    """