# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


import odoo
from odoo import models, fields, api, _
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from amount_to_text_es_MX import *
import requests
from requests import Request, Session
from zeep import Client
from zeep.transports import Transport
import re
from datetime import date, datetime, timedelta
from pytz import timezone, utc
import textwrap
import base64, json
import logging
_logger = logging.getLogger(__name__)

CFDI_TEMPLATE = 'bias_base_report.cfdiv33'


catcfdi = {
    'formaPago': ['01','02','03','04','05','06','08','12','13','14','15','17','23','24','25','26','27','28','29','30','99'],
    'metodoPago': ['PUE', 'PPD'],
    'impuesto': ['001','002','003'],    
    'regimenFiscal': ['601','603','605','606','608','609','610','611','612','614','616','620','621','622','623','624','628','607','629','630','615'],
    'ingreso': ['I','E','T','N','P'],
    'usoCdfi': ['G01','G02','G03','I01','I02','I03','I04','I05','I06','I07','I08','D01','D02','D03','D04','D05','D06','D07','D08','D09','D10','P01']
}

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
    hora = fields.Char(string="Hora", size=8, copy=False)
    
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
    mandada_cancelar = fields.Boolean('Mandada Cancelar', copy=False)


    def get_datas(self, obj, cia):
        self.obj = obj
        self.test = cia.cfd_mx_test
        self.pac = cia.cfd_mx_pac
        self.version = cia.cfd_mx_version
        self.host = cia.cfd_mx_host
        self.port = cia.cfd_mx_port
        self.db = cia.cfd_mx_db
        return True

    def stamp(self, obj):
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
        if hasattr(self, '%s_info_relacionados' % ctx['type']):
            self.cfdi_datas['relacionados'] = getattr(self, '%s_info_relacionados' % ctx['type'])()

        self.cfdi_datas['comprobante'] = getattr(self, '%s_info_comprobante' % ctx['type'])()
        self.cfdi_datas['emisor'] = getattr(self, '%s_info_emisor' % ctx['type'])()
        self.cfdi_datas['receptor'] = getattr(self, '%s_info_receptor' % ctx['type'])()
        self.cfdi_datas['conceptos'] = getattr(self, '%s_info_conceptos' % ctx['type'])()
        if ctx['type'] in ['invoice']:
            self.cfdi_datas['impuestos'] = getattr(self, '%s_info_impuestos' % ctx['type'])(self.cfdi_datas['conceptos'])
            self.cfdi_datas['addenda'] = self.obj.get_comprobante_addenda()
        if ctx['type'] in ['pagos', 'nomina']:
            self.cfdi_datas['complemento'] = getattr(self, '%s_info_complemento' % ctx['type'])()

        if ctx['type'] in ['invoice']:
            Subtotal = float(self.cfdi_datas['comprobante']['SubTotal'])
            Descuento = float(self.cfdi_datas['comprobante']['Descuento'])
            TotalImpuestosRetenidos , TotalImpuestosTrasladados = 0.0, 0.0

            Subtotal = 0.0
            for concepto in self.cfdi_datas.get('conceptos', []):
                Subtotal += float( concepto.get('Importe', '0.0') )
            self.cfdi_datas['comprobante']['Subtotal'] = '%.*f' % (decimal_precision, Subtotal)

            if self.cfdi_datas.get('impuestos'):
                TotalImpuestosRetenidos = float(self.cfdi_datas['impuestos']['TotalImpuestosRetenidos'])
                TotalImpuestosTrasladados = float(self.cfdi_datas['impuestos']['TotalImpuestosTrasladados'])
            Total = Subtotal - Descuento + TotalImpuestosTrasladados - TotalImpuestosRetenidos
            self.cfdi_datas['comprobante']["Total"] = '%.*f' % (decimal_precision, Total)

            if self.cfdi_datas.get('addenda'):
                addenda = self.cfdi_datas['addenda'] and self.cfdi_datas['addenda'].get('addenda') or {}
                if addenda and addenda.get('imploc_attribs'):
                    total = float(self.cfdi_datas['comprobante']["Total"])
                    TotaldeRetenciones = float(addenda['imploc_attribs'].get('TotaldeRetenciones', "0.0"))
                    TotaldeTraslados = float(addenda['imploc_attribs'].get('TotaldeTraslados', "0.0"))
                    total = total + TotaldeTraslados - TotaldeRetenciones
                    self.cfdi_datas['comprobante']["Total"] = '%.*f' % (decimal_precision, total)

        datas = json.dumps(self.cfdi_datas, sort_keys=True, indent=4, separators=(',', ': '))
        
        logging.info(datas)
        #dic_xml = json.loads(self.cfdi_datas)
        #raise ValidationError(dic_xml)
        data= {'record': self}
        cfdi = qweb.render(CFDI_TEMPLATE, values= data)
        cfdi = self.get_format(cfdi)
        fil= open('/tmp/prueba.xml', 'w')
        fil.write(cfdi)
        fil.close()
        self.cfdi_xml= base64.encodestring(cfdi)
        #self.cfdi_sign()
        raise ValidationError("Llego muy lejos")
        #url = '%s/stamp%s/'%(self.host, ctx['type'])
        #if self.port:
        #    url = '%s:%s/stamp%s/'%(self.host, self.port, ctx['type'])
        #params = {"context": {},  "post":  datas }
        #res_datas =  self.action_server(url, self.host, self.db, params) #---> Eliminar 
        #raise ValidationError(res_datas)
        #fil= open('/tmp/otro', 'w')
        #fil.write(json.dumps(res_datas))
        #fil.close()
        #raise ValidationError(json.dumps(res_datas))
        
        return cfdi

    def codigo_impuesto(self, impuesto):
        if "ISR" in impuesto:
            return "001"
        if "IVA" in impuesto:
            return "002"
        else:
            return "003"


    def get_format(self, xml):
        xml = xml.replace("<Comprobante", "<cfdi:Comprobante   xsi:schemaLocation=\"http://www.sat.gob.mx/cfd/3  http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv33.xsd\"xmlns:cfdi=\"http://www.sat.gob.mx/cfd/3\" xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"")
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
        return xml.replace("\n","").replace("  ", "")


    def get_info_pac(self):
        cfdi_datas = {
            'test': self.test,
            'pac': self.pac,
            'version': self.version
        }
        return cfdi_datas


    def action_server(self, url, host, db, params):
        s = Session()
        s.get('%s/web?db=%s'%(host, db))
        headers = {
            'Content-Type':'application/json',
            'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:27.0) Gecko/20100101 Firefox/27.0',
            'Referer' : url
        }
        data = {
            "jsonrpc": "2.0",
            "method": "call",
            "id":0,
            "params": params
        }
        res = s.post(url, data=json.dumps(data), headers=headers)
        res_datas = res.json()
        raise UserError(base64.decodestring(res_datas.get('result').get('xml')))
        msg = res_datas.get('error') and res_datas['error'].get('data') and res_datas['error']['data'].get('message')
        if msg:
            return res_datas['error']['data']
        if res_datas.get('error'):
            return res_datas['error']
        if res_datas.get('result') and res_datas['result'].get('error'):
            return res_datas['result']['error']
        return res_datas

    def get_process_data(self, obj, res):
	raise ValidationError(res.get('xml'))
        context = dict(self._context) or {}
        fname = "cfd_%s.xml"%(obj.number or obj.name or '')
        if context.get('type') and context.get('type') == 'pagos':
            fname = '%s.xml'%(res.get('UUID') or res.get('uuid') or obj.number or obj.name or '')
        # Adjuntos
        attachment_obj = obj.env['ir.attachment']
        attachment_values = {
            'name': fname,
            'datas': res.get('xml'),
            'datas_fname': fname,
            'description': 'Comprobante Fiscal Digital',
            'res_model': obj._name,
            'res_id': obj.id,
            'type': 'binary'
        }
        attachment_obj.create(attachment_values)
        # Guarda datos:
        values = {
            'cadena': res.get('cadenaori', ''),
            'fecha_timbrado': res.get('fecha'),
            'sello_sat': res.get('satSeal'),
            'certificado_sat': res.get('noCertificadoSAT'),
            'sello': res.get('SelloCFD'),
            'noCertificado': res.get('NoCertificado'),
            'uuid': res.get('UUID') or res.get('uuid') or '',
            'qrcode': res.get('qr_img'),
            'mensaje_pac': res.get('Leyenda'),
            'tipo_cambio': res.get('TipoCambio'),
            'cadena_sat': res.get('cadena_sat'),
            'test': res.get('test')
        }
        obj.write(values)
        return True

    def action_raise_message(self, message):
        self.ensure_one()
        context = dict(self._context) or {}
        if not self.mensaje_validar:
            self.mensaje_validar = ""
        if not context.get('batch', False):
            if len(message) != 0:
                message = message.replace('<li>', '').replace('</li>', '\n')
                # self.message_post(body=message)
                raise UserError(message)
        else:
            self.mensaje_validar += message
        return True

    def valida_catcfdi(self, cat, value):
        res = False
        if catcfdi.get(cat, False):
            if value in catcfdi[cat]:
                res = True
        return res

    def _get_folio(self):
        return str(self.id).zfill(6)

    def get_info_sat(self, splitme, swidth):
        pp = textwrap.wrap(splitme, width=int(swidth))
        export_text = ""
        for p in pp:
            export_text += p + '\n'
        return export_text

    def get_cant_letra(self, currency, amount):
        if currency.name == 'MXN':
            nombre = currency.nombre_largo or 'pesos'
            siglas = 'M.N.'
        else:
            nombre = currency.nombre_largo or ''
            siglas = currency.name
        return amount_to_text().amount_to_text_cheque(float(amount), nombre,
                                                      siglas).capitalize()


    def convert_datetime_timezone(self, dt, tz1, tz2):
        tz1 = timezone(tz1)
        tz2 = timezone(tz2)
        dt = datetime.strptime(dt,"%Y-%m-%d %H:%M:%S")
        dt = tz1.localize(dt)
        dt = dt.astimezone(tz2)
        dt = dt.strftime("%Y-%m-%dT%H:%M:%S")
        return dt


    # Cancelar
    def cancel(self, obj):
        cia = obj.company_id
        self.get_datas(obj, cia)

        url = '%s/cancel/'%(self.host)
        if self.port:
            url = '%s:%s/cancel/'%(self.host, self.port)
        cfdi_datas = {
            'db': self.db,
            'uuid': self.obj.uuid,
            'vat': cia.partner_id.vat,
            'test': cia.cfd_mx_test,
            'cfd': self.get_info_pac(),
            'noCertificado': self.obj.noCertificado
        }
        self.datas = json.dumps(cfdi_datas, sort_keys=True, indent=4, separators=(',', ': '))
        params = {"context": {},  "post":  self.datas}
        res_datas =  self.action_server(url, self.host, self.db, params)
        return res_datas


    def validate(self, obj):
        cia = obj.company_id
        host = cia.cfd_mx_host
        url = '%s/validate/'%(host)
        port = cia.cfd_mx_port
        db = cia.cfd_mx_db
        # if port:
        #     url = '%s:%s/validate/'%(host, port)
        cfdi_datas = {
            'db': db,
            'xml': obj.xml,
            'vat': cia.partner_id.vat,
            'test': cia.cfd_mx_test,
            'cfd': {
                'test': cia.cfd_mx_test,
                'pac': cia.cfd_mx_pac,
                'version': cia.cfd_mx_version
            }
        }
        datas = json.dumps(cfdi_datas, sort_keys=True, indent=4, separators=(',', ': '))
        params = {"context": {},  "post":  datas}
        res_datas =  self.action_server(url, host, db, params)
        return res_datas


    def contabilidad(self, obj):
        context = dict(self._context)
        cia = obj.company_id
        host = cia.cfd_mx_host
        url = '%s/contabilidad/'%(host)
        port = cia.cfd_mx_port
        db = cia.cfd_mx_db
        # if port:
        #     url = '%s:%s/validate/'%(host, port)
        cfdi_datas = {
            'db': db,
            'xml': context,
            'vat': cia.partner_id.vat,
            'test': cia.cfd_mx_test,
            'cfd': {
                'test': cia.cfd_mx_test,
                'pac': cia.cfd_mx_pac,
                'version': cia.cfd_mx_version
            }
        }
        datas = json.dumps(cfdi_datas, sort_keys=True, indent=4, separators=(',', ': '))
        params = {"context": {},  "post":  datas}
        res_datas =  self.action_server(url, host, db, params)
        return res_datas


######################################################################################
    def cfdi_sign(self):
        test = self.test
        username = 'cfdi@vauxoo.com' 
        password = 'vAux00__'
        #username = 'cfdi@vauxoo.com' if test else company_id.cfd_finkok_user
        #password = 'vAux00__' if test else company_id.cfd_finkok_key
        url='http://demo-facturacion.finkok.com/servicios/soap/stamp.wsdl'\
                if test else 'http://facturacion.finkok.com/servicios/soap/stamp.wsdl'
            
        for inv in self:
            cfdi = base64.decodestring(inv.cfdi_xml)
            try:
                transport = Transport(timeout=20)
                client = Client(url, transport=transport)
                response = client.service.stamp(cfdi, username, password)
            except Exception as e:
                raise UserError(e)
                continue
            code = 0
            msg = None
            raise UserError(response)
            if response.Incidencias:
                code = getattr(response.Incidencias.Incidencia[0], 'CodigoError', None)
                msg = getattr(response.Incidencias.Incidencia[0], 'MensajeIncidencia', None)
            xml_signed = getattr(response, 'xml', None)
            if xml_signed:
                xml_signed = base64.b64encode(xml_signed.encode('utf-8'))
            inv._post_sign_process(xml_signed, code, msg)

    def cfdi_cancel(self, pac_info):
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

####################################
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
            self.l10n_mx_edi_pac_status = 'signed'
            self.l10n_mx_edi_cfdi = xml_signed
            # Update the content of the attachment
            attachment_id = self.l10n_mx_edi_retrieve_last_attachment()
            attachment_id.write({
                'datas': xml_signed,
                'mimetype': 'application/xml'
            })
            xml_signed = self.l10n_mx_edi_append_addenda(xml_signed)
            post_msg = [_('The content of the attachment has been updated')]
        else:
            body_msg = _('The sign service requested failed')
            post_msg = []
        if code:
            post_msg.extend([_('Code: %s') % code])
        if msg:
            post_msg.extend([_('Message: %s') % msg])
        self.message_post(
            body=body_msg + create_list_html(post_msg),
            subtype='account.mt_invoice_validated')
    

    def _l10n_mx_edi_post_cancel_process(self, cancelled, code=None, msg=None):
        '''Post process the results of the cancel service.

        :param cancelled: is the cancel has been done with success
        :param code: an eventual error code
        :param msg: an eventual error msg
        '''

        self.ensure_one()
        if cancelled:
            body_msg = _('The cancel service has been called with success')
            self.l10n_mx_edi_pac_status = 'cancelled'
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

