# -*- coding: utf-8 -*-

import time
from datetime import date, datetime, timedelta
from pytz import timezone, utc
import threading
import base64, json, requests
from odoo.tools import float_is_zero, float_compare

from lxml import etree
from lxml.objectify import fromstring


from datetime import *; from dateutil.relativedelta import *
import calendar


import odoo
from odoo import api, fields, models, registry, _
from odoo.exceptions import UserError, RedirectWarning, ValidationError

import logging
logging.basicConfig(level=logging.INFO)
CFDI_XSLT_CADENA_TFD = 'cfd_mx/data/xslt/3.3/cadenaoriginal_TFD_1_1.xslt'

class AccountInvoiceBatch(models.Model):
    _name = 'account.invoice.bath'
    _auto = False

    @api.multi
    def _compute_action_invoice_date(self, ids=None):
        with api.Environment.manage():
            new_cr = self.pool.cursor()
            self = self.with_env(self.env(cr=new_cr))
            tz = self.env.user.tz or "UTC"
            hora_factura_utc = datetime.now(timezone("UTC"))
            dtz = hora_factura_utc.astimezone(timezone(tz)).strftime("%Y-%m-%d %H:%M:%S")
            dtz = dtz.replace(" ", "T")
            Invoice = self.sudo().env['account.invoice']
            for inv_id in Invoice.browse(ids):
                if not inv_id.date_invoice_cfdi:
                    logging.info('---DATE %s '%(dtz) )
                    inv_id.write({'date_invoice_cfdi': dtz})
                    new_cr.commit()
            new_cr.close()
        return {}

    def _compute_action_invoice_open(self, ids=None):
        with api.Environment.manage():
            new_cr = self.pool.cursor()
            self = self.with_env(self.env(cr=new_cr))
            Invoice = self.sudo().env['account.invoice']
            for inv_id in Invoice.browse(ids):
                Invoice.with_context(batch=True)._compute_action_invoice_open([inv_id.id], use_new_cursor=new_cr.dbname)
                new_cr.commit()
            new_cr.close()
        return {}

    @api.multi
    def compute_action_invoice_open(self, inv_ids):
        logging.info("Confirm Invoice %s "%(inv_ids,))

        # Escribe fecha
        threaded_calculation = threading.Thread(target=self._compute_action_invoice_date, args=(), kwargs={"ids": inv_ids}, name=inv_ids)
        threaded_calculation.start()
        threaded_calculation.join()

        threaded_calculation = threading.Thread(target=self._compute_action_invoice_open, args=(), kwargs={"ids": inv_ids}, name=inv_ids)
        threaded_calculation.start()
        threaded_calculation.join()

        return True


class AccountInvoiceRefund(models.TransientModel):
    _inherit = "account.invoice.refund"
    _description = "Credit Note"

    tiporelacion_id = fields.Many2one('cfd_mx.tiporelacion', string=u'Tipo de Relacion', copy="False")

    @api.multi
    def compute_refund(self, mode='refund'):
        ctx = dict(self.env.context)
        ctx["tiporelacion_id"] = self.tiporelacion_id.id
        inv_obj = self.env['account.invoice']
        for form in self:
            for inv in inv_obj.browse(ctx.get('active_ids')):
                if not form.tiporelacion_id:
                    if inv.journal_id.id in inv.company_id.cfd_mx_journal_ids.ids:
                        raise UserError(_('Por favor seleccione un tipo de Relacion CFDI.'))

        res = super(AccountInvoiceRefund, self.with_context(**ctx)).compute_refund(mode=mode)
        return res


class AccountInvoiceLine(models.Model):
    _inherit = 'account.invoice.line'

    @api.one
    @api.depends('price_unit', 'discount', 'invoice_line_tax_ids', 'quantity',
        'product_id', 'invoice_id.partner_id', 'invoice_id.currency_id', 'invoice_id.company_id',
        'invoice_id.date_invoice')
    def _compute_price_sat(self):
        currency = self.invoice_id and self.invoice_id.currency_id or None
        # Calculo de Impuestos.
        price_unit = self.price_unit * (1 - (self.discount or 0.0) / 100.0)
        taxes = self.invoice_line_tax_ids.compute_all(price_unit, self.currency_id, self.quantity, self.product_id, self.partner_id)
        base = taxes.get('base', 0.00)
        price_subtotal_sat = taxes.get('total_excluded', 0.00)
        discount =  ((self.discount or 0.0) / 100.0) * base
        perc_discount = (1 - (self.discount / 100) )
        if perc_discount != 0.0:
            subtotal = taxes.get('total_excluded', 0.00) / (1 - (self.discount / 100) )
        else:
            subtotal = 0.0
        self.price_tax_sat = taxes.get('total_included', 0.00) - taxes.get('total_excluded', 0.00)
        self.price_discount_sat = subtotal * (self.discount / 100)
        self.price_subtotal_sat = subtotal # taxes.get('total_excluded', 0.00)  # ( self.price_unit * self.quantity )
        self.price_total_sat = subtotal + self.price_tax_sat - self.price_discount_sat

    price_total_sat = fields.Monetary(string='total (SAT)', readonly=True, compute='_compute_price_sat', default=0.00, digits=(12, 6))
    price_subtotal_sat = fields.Monetary(string='Subtotal (SAT)', readonly=True, compute='_compute_price_sat', default=0.00, digits=(12, 6))
    price_tax_sat = fields.Monetary(string='Tax (SAT)', readonly=True, compute='_compute_price_sat', default=0.00, digits=(12, 6))
    price_discount_sat = fields.Monetary(string='Discount (SAT)', readonly=True, compute='_compute_price_sat', default=0.00, digits=(12, 6))
    numero_pedimento_sat = fields.Char(string='Numero de Pedimento', help="Informacion Aduanera. Numero de Pedimento")



    @api.one
    def get_impuestos_sat(self):
        line = self
        tax_obj = self.env['account.tax']
        dp = self.env['decimal.precision']
        dp_account = dp.precision_get('Account')
        dp_product = dp.precision_get('Product Price')

        res = []
        price_unit = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
        taxes = line.invoice_line_tax_ids.compute_all(price_unit, line.currency_id, line.quantity, line.product_id, line.invoice_id.partner_id)['taxes']
        for tax in taxes:
            tax_id = tax_obj.browse(tax.get('id'))
            tax_group = tax_id.tax_group_id
            importe = tax.get('amount')
            TasaOCuota = '%.6f'%((round(abs(tax_id.amount), dp_account) / 100))
            impuestos = {
                'Name': tax_id.name,
                'Base': round( tax.get('base') , dp_account),
                'Impuesto': tax_group.cfdi_impuestos,
                'TipoFactor': '%s'%(tax_id.cfdi_tipofactor),
                'TasaOCuota': '%s'%(TasaOCuota),
                'Importe': round(importe, dp_account)
            }
            if tax_group.cfdi_retencion:
                impuestos["tipo"] = "ret"
            if tax_group.cfdi_traslado:
                impuestos["tipo"] = "tras"
            res.append(impuestos)
        return res

class AccountInvoice(models.Model):
    _name = 'account.invoice'
    _inherit = ['account.invoice', 'account.cfdi']

    @api.one
    @api.depends(
        'invoice_line_ids.price_subtotal',
        'invoice_line_ids.price_subtotal_sat',
        'invoice_line_ids.price_tax_sat',
        'invoice_line_ids.price_discount_sat',
        'tax_line_ids.amount',
        'currency_id',
        'company_id',
        'date_invoice',
        'type')
    def _compute_price_sat(self):
        descuento = 0.00
        impuestos = 0.00
        subtotal = 0.00
        total = 0.0
        for line in self.invoice_line_ids:
            impuestos += line.price_tax_sat
            subtotal += line.price_subtotal_sat
            total += line.price_total_sat
            if line.discount:
                descuento += line.price_discount_sat
        self.price_subtotal_sat = subtotal
        self.price_tax_sat = impuestos
        self.price_discount_sat = descuento
        self.price_total_sat = total

    @api.one
    def _default_uso_cfdi_id(self):
        public = self.env.ref('cfd_mx.usocfdi_G01')
        return public

    @api.one
    def _get_parcialidad_pago(self):
        self.pagos = True if len(self.payment_ids) != 0 else False

    @api.one
    def _get_cfdi_required(self):
        required = (
            self.uuid and
            self.cfdi_timbre_id and
            self.state not in ['cancel', 'draft', 'paid'] and
            self.type in ['out_invoice', 'out_refund'] and
            self.company_id.country_id == self.env.ref('base.mx')
        )
        self.cfdi_is_required = required
    
    cfdi_is_required = fields.Boolean(string="CFDI Required", default=False, copy=False, compute='_get_cfdi_required')
    pagos = fields.Boolean(string="Pagos", default=False, copy=False, compute='_get_parcialidad_pago')
    parcialidad_pago = fields.Integer(string="No. Parcialidad Pago", compute='_get_parcialidad_pago')
    uuid_relacionado_id = fields.Many2one('account.invoice', string=u'UUID Relacionado', domain=[("type", "in", ("out_invoice", "out_refund") ), ("timbrada", "=", True), ("uuid", "!=", None)])
    tiporelacion_id = fields.Many2one('cfd_mx.tiporelacion', string=u'Tipo de Relacion', copy="False")

    price_total_sat = fields.Monetary(string='Total (SAT)', readonly=True, compute='_compute_price_sat', digits=(12, 6))
    price_subtotal_sat = fields.Monetary(string='Subtotal (SAT)', readonly=True, compute='_compute_price_sat', digits=(12, 6))
    price_tax_sat = fields.Monetary(string='Tax (SAT)', readonly=True, compute='_compute_price_sat', digits=(12, 6))
    price_discount_sat = fields.Monetary(string='Discount (SAT)', readonly=True, compute='_compute_price_sat', digits=(12, 6))
    xml_cfdi_sinacento = fields.Boolean(related="partner_id.xml_cfdi_sinacento", string='XML CFDI sin acentos')
    internal_number = fields.Char(string='Invoice Number', size=32, readonly=True, copy=False, help="Unique number of the invoice, computed automatically when the invoice is created.")
    usocfdi_id = fields.Many2one('cfd_mx.usocfdi', string="Uso de Comprobante CFDI", required=False)
    metodopago_id = fields.Many2one('cfd_mx.metodopago', string=u'Metodo de Pago')
    reason_cancel = fields.Text(string="Motivo Cancelacion")
    cfdi_timbre_id = fields.Many2one('cfdi.timbres.sat', string=u'Timbre SAT', readonly=True, copy=False)
    cfdi_pending_cancel = fields.Boolean(string="CFDI Pending Cancel", default=False, copy=False)
    cfdi_pending_accept_cancel = fields.Boolean(string="CFDI Pending Accept Cancel", default=False, copy=False)
    cfdi_accept_reject = fields.Selection(
        selection=[
            ('Aceptacion', 'Aceptacion'),
            ('Rechazo', 'Rechazo')
        ],
        string='Aceptar o Rechazar CFDI',
        copy=False,
        track_visibility='onchange',
        default='Rechazo')


    # Quitar en Futuras Versiones
    cuentaBanco = fields.Char(string='Ultimos 4 digitos cuenta', size=4, default='')
    anoAprobacion = fields.Integer(string=u"Año de aprobación")
    noAprobacion = fields.Char(string="No. de aprobación")
    tipopago_id = fields.Many2one('cfd_mx.tipopago', string=u'Forma de Pago')

    @api.onchange('date_invoice')
    def _onchange_date_invoice(self):
        if not self.date_invoice:
            return {}
        field_now = fields.Datetime.now()
        self.date_invoice_cfdi = self.convert_datetime_timezone(field_now, "UTC", self.env.user.tz)

    @api.onchange('uuid_relacionado_id')
    def _onchange_date_invoice(self):
        if not self.uuid_relacionado_id:
            return {}
        self.uuid_egreso = self.uuid_relacionado_id.uuid

    @api.onchange('partner_id', 'formapago_id')
    def onchange_metododepago(self):
        if not self.partner_id:
            self.update({
                'formapago_id': False,
                'cuentaBanco': False,
                'metodopago_id': False,
                'usocfdi_id': False
            })
            return

        if not self.usocfdi_id:
            self.usocfdi_id = self.partner_id.usocfdi_id and self.partner_id.usocfdi_id.id or None 
        if not self.metodopago_id:
            self.metodopago_id = self.partner_id.metodopago_id and self.partner_id.metodopago_id.id or None 
        if not self.formapago_id:
            self.formapago_id = self.partner_id.formapago_id and self.partner_id.formapago_id.id or None
        cuenta = ''
        if self.formapago_id and self.formapago_id.banco:
            if not self.partner_id:
                raise UserError("No se ha definido cliente")
            if self.partner_id.bank_ids:
                for bank in self.partner_id.bank_ids:
                    cuenta = bank.acc_number[-4:]
                    break
            else:
                cuenta = 'xxxx'
        self.cuentaBanco = cuenta
        return {}

    @api.model
    def create(self, vals):
        onchanges = {
            'onchange_metododepago': ['partner_id', 'formapago_id', 'metodopago_id', 'cuentaBanco'],
        }
        for onchange_method, changed_fields in onchanges.items():
            if any(f not in vals for f in changed_fields):
                invoice = self.new(vals)
                getattr(invoice, onchange_method)()
                for field in changed_fields:
                    if field not in vals and invoice[field]:
                        vals[field] = invoice._fields[field].convert_to_write(invoice[field], invoice)
        invoice = super(AccountInvoice, self.with_context(mail_create_nolog=True)).create(vals)
        if invoice.type == 'out_invoice':
            invoice.tipo_comprobante = "I"
            # invoice.usocfdi_id = invoice.partner_id.usocfdi_id and invoice.partner_id.usocfdi_id.id or None
            if not invoice.usocfdi_id:
                invoice.usocfdi_id = invoice.partner_id.usocfdi_id and invoice.partner_id.usocfdi_id.id or None
            if not invoice.formapago_id:
                invoice.formapago_id = invoice.partner_id.formapago_id and invoice.partner_id.formapago_id.id or None
            if not invoice.metodopago_id:
                invoice.metodopago_id = invoice.partner_id.metodopago_id and invoice.partner_id.metodopago_id.id or None
        if invoice.type == 'out_refund':
            invoice.tipo_comprobante = "E"
            
            logging.info("&&&&&&&&&&&entra aqui")
            logging.info(invoice.amount_total)
            logging.info(invoice.residual)
            logging.info(invoice.id)
        return invoice

    @api.multi
    def action_move_create(self):
        res = super(AccountInvoice, self).action_move_create()
        self.write({'internal_number': self.number})
        return True

    @api.model
    def _prepare_refund(self, invoice, date_invoice=None, date=None, description=None, journal_id=None):
        ctx = dict(self.env.context)
        values = super(AccountInvoice, self)._prepare_refund(invoice, date_invoice=date_invoice, date=date, description=description, journal_id=journal_id)
        values['uuid_relacionado_id'] = invoice.id
        values['cuentaBanco'] = invoice.cuentaBanco
        values['formapago_id'] = invoice.formapago_id and invoice.formapago_id.id or None
        values["metodopago_id"] = invoice.metodopago_id and invoice.metodopago_id.id or None
        values["usocfdi_id"] = invoice.usocfdi_id and invoice.usocfdi_id.id or None
        values["tiporelacion_id"] = ctx.get("tiporelacion_id", None) or None
        values['uuid_egreso'] = invoice.uuid
        values['tipo_comprobante'] = 'E'
        values['payment_term_id'] = invoice.payment_term_id.id
        return values

    @api.multi
    def name_get(self):
        TYPES = {
            'out_invoice': _('Invoice'),
            'in_invoice': _('Vendor Bill'),
            'out_refund': _('Refund'),
            'in_refund': _('Vendor Refund'),
        }
        result = []
        for inv in self:
            result.append((inv.id, "%s %s" % (inv.number or inv.internal_number or TYPES[inv.type], inv.name or '')))
        return result

    @api.model
    def name_search(self, name, args=None, operator='ilike', limit=100):
        recs = super(AccountInvoice, self).name_search(name, args=args, operator=operator, limit=limit)
        args = args or []
        recs = self.browse()
        if name:
            recs = self.search([('number', '=', name)] + args, limit=limit)
        if not recs:
            recs = self.search([('internal_number', '=', name)] + args, limit=limit)
        if not recs:
            recs = self.search([('name', operator, name)] + args, limit=limit)
        return recs.name_get()

    ## Creado en batch
    @api.multi
    def _compute_action_invoice_open(self, ids, use_new_cursor=False):
        context = dict(self._context)
        message = ''
        if use_new_cursor:
            cr = registry(self._cr.dbname).cursor()
            self = self.with_env(self.env(cr=cr))
        inv = self.env['account.invoice'].browse(ids)
        inv.action_invoice_open()
        if use_new_cursor:
            cr.commit()
            cr.close()
        return {}

    # Crea xml
    @api.multi
    def invoice_validate(self):
        for invoice in self:
            self.action_write_date_invoice_cfdi(invoice.id)
        for invoice in self:
            invoice.action_create_cfd()
        res = super(AccountInvoice, self).invoice_validate()
        return res

    def action_write_date_invoice_cfdi(self, inv_id):
        dtz = False
        if not self.date_invoice_cfdi:
            tz = self.env.user.tz
            if not tz:
                message = '<li>El usuario no tiene definido Zona Horaria</li>'
                self.action_raise_message(message)
                return message
            cr = self._cr
            hora_factura_utc = datetime.now(timezone("UTC"))
            dtz = hora_factura_utc.astimezone(timezone(tz)).strftime("%Y-%m-%d %H:%M:%S")
            dtz = dtz.replace(" ", "T")
            cr.execute("UPDATE account_invoice SET date_invoice_cfdi='%s' WHERE id=%s "%(dtz, inv_id) )
            cr.commit()
        return dtz

    @api.multi
    def action_validate_cfdi(self):
        context = dict(self._context)
        tz = self.env.user.tz
        message = ''
        # if not self.number:
        #     message += '<li>No se creo un Folio para la Factura</li>'
        if not self.tipo_comprobante:
            message += '<li>No se definio Tipo Comprobante</li>'
        if not self.journal_id.codigo_postal_id:
            message += '<li>No se definio Lugar de Expedicion (C.P.)</li>'
        if not self.payment_term_id:
            message += '<li>No se definio Condiciones de Pago</li>'
        if not self.formapago_id:
            message += '<li>No se definio Forma de Pago</li>'
        if not self.metodopago_id:
            message += '<li>No se definio Metodo de Pago</li>'
        if not self.usocfdi_id:
            message += '<li>No se definio Uso CFDI</li>'
        regimen_id = self.company_id.partner_id.regimen_id
        #if not regimen_id:
        #    message += u'<li>No se definio Regimen Fiscal para la Compañia</li>'
        if not tz:
            message += '<li>El usuario no tiene definido Zona Horaria</li>'
        if not self.partner_id.vat:
            message += '<li>No se especifico el RFC para el Cliente</li>'
        if not self.company_id.partner_id.vat:
            message += '<li>No se especifico el RFC para la Empresa</li>'
        if not self.partner_id.country_id.code_alpha3:
            message += '<li>No se ha configurado el codigo alpha3 del pais del cliente.</li>'
        for line in self.invoice_line_ids:
            if line.price_unit == 0.0:
                message += '<li>El Valor Unitario debe ser mayor a 0.0</li>'
            if not line.uom_id.clave_unidadesmedida_id.clave:
                message += '<li>Favor de Configurar la Clave Unidad SAT "%s"</li>'%(line.uom_id.name)
            for tax in line.invoice_line_tax_ids:
                if not tax.tax_group_id.cfdi_impuestos:
                    message += '<li>El impuesto %s no tiene configurado el campo "CFDI Catalogo de Impuestos"</li>'%(tax.tax_group_id.name)
        self.with_context(**context).action_raise_message(message)
        return message


    @api.one
    def action_create_cfd(self):
        context = dict(self._context)
        tz = self.env.user.tz
        if self.uuid:
            return True
        if not self.journal_id.id in self.company_id.cfd_mx_journal_ids.ids:
            return True
        if self.type.startswith("in"):
            return True
        message = self.action_validate_cfdi()
        try:
            res = self.with_context({'type': 'invoice'}).stamp(self)
            if res.Incidencias:
                message = res.Incidencias.Incidencia[0]
            else:
                self.get_process_data(self, res)
                self.get_process_data_xml(res)####Se crea el archivo 
        except ValueError, e:
            message = str(e)
        except Exception, e:
            message = str(e)
        if message:
            self.message_post(body=message)
            #message = message.replace("(u'", "").replace("', '')", "")
            #self.with_context(**context).action_raise_message("Error al Generar el XML \n\n %s "%( message.upper() ))
            #return False
        return True

    @api.model
    def get_tfd_etree(self, cfdi):
        '''Get the TimbreFiscalDigital node from the cfdi.

        :param cfdi: The cfdi as etree
        :return: the TimbreFiscalDigital node
        '''
        attribute = 'tfd:TimbreFiscalDigital[1]'
        namespace = {'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital'}
        node = cfdi.Complemento.xpath(attribute, namespaces=namespace)
        return node[0] if node else None


    def get_process_data(self, obj, res):
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
        return True

    def get_process_data_xml(self, res): 
        Currency = self.env['res.currency']
        attachment_obj = self.env['ir.attachment']
        Timbre = self.env['cfdi.timbres.sat']
        tree = fromstring(res.xml)
        tfdtree= self.get_tfd_etree(tree)

        cadena_sat = self.generate_cadena(CFDI_XSLT_CADENA_TFD,tfdtree)

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

        if timbre_id:
            context = dict(self._context) or {}
            fname = "cfd_%s.xml"%(self.number or self.name or '')
            if context.get('type') and context.get('type') == 'pagos':
                fname = '%s.xml'% (res.UUID or self.number or self.name or '')
            # Adjuntos
            attachment_obj = self.env['ir.attachment']
            attachment_values = {
                'name': fname,
                'datas': base64.encodestring(res.xml),
                'datas_fname': fname,
                'description': 'Comprobante Fiscal Digital',
                'res_model': self._name,
                'res_id': self.id,
                'mimetype': 'application/xml',
                'type': 'binary'
            }
            attachment_obj.create(attachment_values)
            # Guarda datos:
            tree = fromstring(res.xml)
            values = {
                'cadena': self.cadena,
                'fecha_timbrado': res.Fecha,
                'sello_sat': res.SatSeal,
                'certificado_sat': res.NoCertificadoSAT,
                'sello': tree.attrib['Sello'],
                'noCertificado': tree.attrib['NoCertificado'],
                'uuid': res.UUID,
                #'qrcode': res.qr_img,
                'mensaje_pac': res.CodEstatus,
                'tipo_cambio': tree.attrib['TipoCambio'],
                'cadena_sat': cadena_sat
            }
            self.write(values)
            

            xname = "%s.xml"%tfdtree.get('UUID')
            attachment_values = {
                'name':  xname,
                'datas': res.xml,
                'datas_fname': xname,
                'description': 'Comprobante Fiscal Digital',
                'res_model': 'cfdi.timbres.sat',
                'res_id': timbre_id.id,
                'mimetype': 'application/xml',
                'type': 'binary'
            }
            attachment_obj.create(attachment_values)
            self.write({
                'cfdi_timbre_id': timbre_id.id,
                'uuid': res.UUID,
                'test': self.company_id.cfd_mx_test
            })
            
    @api.multi
    def get_comprobante_addenda(self):
        context = dict(self._context) or {}
        dict_addenda = {}
        if self.type == 'out_invoice':
            Addenda = self.env['cfd_mx.conf_addenda']
            for conf_addenda in Addenda.search([('partner_ids', 'in', self.partner_id.ids), ('company_id', '=', self.company_id.id)]):
                context.update({'model_selection': conf_addenda.model_selection})
                dict_addenda = conf_addenda.with_context(**context).create_addenda(self)
        return dict_addenda

    def cfdi_append_addenda(self, res):
        self.ensure_one()
        context = dict(self._context) or {}
        if self.tipo_comprobante == 'I':
            addenda = self.partner_id.cfdi_addenda
            if addenda:
                xml64 = res.get('xml')
                values = {
                    'record': self,
                }
                tree = fromstring(base64.decodestring(xml64))
                addenda_node = fromstring(addenda.render(values=values))
                if addenda_node.tag != '{http://www.sat.gob.mx/cfd/3}Addenda':
                    node = etree.Element(etree.QName(
                        'http://www.sat.gob.mx/cfd/3', 'Addenda'))
                    node.append(addenda_node)
                    addenda_node = node
                tree.append(addenda_node)
                res['xml'] = base64.encodestring(etree.tostring(tree, xml_declaration=True, encoding='UTF-8'))
                self.message_post(
                    body=_('Addenda has been added in the CFDI with success'),
                    subtype='account.mt_invoice_validated')
        return res

    @api.multi
    def action_test_addenda(self):
        self.ensure_one()
        addenda = self.partner_id.cfdi_addenda
        if addenda:
            values = {
                'record': self,
            }
            add_str = addenda.render(values=values)
            raise UserError(add_str)
        else:
            raise UserError("No existe addenda relacionada")
        return True


    # Cancela xml
    def msg_batch(self, msg):
        context = dict(self._context)
        if not context.get('batch', False):
            self.message_post(body=msg)
        else:
            self.message_post(body='<ul><li> %s </li></ul>'%msg, subtype='account.mt_invoice_validated' )
        return True

    @api.multi
    def action_cancel_pending(self):
        # , ('cfdi_cancel_date_rev', '<=', fields.Date.context_today(self) )

        context = dict(self._context)
        context['batch'] = True
        
        # where = [('cfdi_state', '=', 'Vigente'), ('cfdi_cancel_status_sat', '=', 'En proceso'), ('cfdi_type', 'in', ['I', 'E'])]
        # cfdi_timbre_ids = self.env['cfdi.timbres.sat'].sudo().search(where)
        for inv_id in self.sudo().search([('cfdi_pending_cancel', '=', True), ('state', '=', 'open')]):
            inv_id.sudo().with_context(**context).action_invoice_cancel_cfdi()
        return True


    @api.multi
    def action_cancel_pending_acceptreject(self):
        self.search([('cfdi_pending_accept_cancel', '=', True)]).write({
            'cfdi_pending_accept_cancel': False
        })
        user = self.env.user
        cfdi_params = {
            'RTaxpayer_id': user.company_id.vat
        }
        result = user.company_id.action_ws_finkok_sat('pendingcancel', cfdi_params)
        uuids = result.get('uuids')
        inv_ids = self.search([('uuid', 'in', uuids)])
        inv_ids.write({
            'cfdi_pending_accept_cancel': True
        })
        return True


    @api.multi
    def action_get_status_sat(self):
        self.ensure_one()
        cfdi_params = {
            "uuid": self.uuid,
            "taxpayer_id": self.cfdi_timbre_id.cfdi_supplier_rfc,
            "rtaxpayer_id": self.cfdi_timbre_id.cfdi_customer_rfc,
            "total": self.cfdi_timbre_id.cfdi_amount
        }
        result = self.company_id.action_ws_finkok_sat('getsatstatus', cfdi_params)
        if result.get('error'):
            self.message_post(
                body='<p style="color:red">' + result['error'] + '</p>',
                subtype='account.mt_invoice_validated')


        self.cfdi_timbre_id.write({
            "cfdi_cancel_status_sat": result.get("EstatusCancelacion"),
            "cfdi_cancel_escancelable_sat": result.get("EsCancelable"),
            "cfdi_state": result.get("Estado"),
            "cfdi_code_sat": result.get("CodigoEstatus")
        })
        msg = "<b>Proceso de Cancelacion: </b><br />"
        msg += "<ul>"
        msg += '<li>CodigoEstatus = %s </li>'% result.get('CodigoEstatus', '')
        msg += '<li>Estado = %s </li>'% result.get('Estado', '')
        msg += '<li>EstatusCancelacion = %s </li>'% result.get('EstatusCancelacion', '')
        msg += '<li>EsCancelable = %s </li>'% result.get('EsCancelable', '')
        msg += "</ul>"

        if result.get('Estado') == 'Cancelado':
            msg_tmp = '<p style="color:blue">' + _('The cancel service has been called with success') + '</p>'
            msg_tmp += msg
            self.message_post(
                body=msg_tmp,
                subtype='account.mt_invoice_validated')
        if result.get('Estado') == 'No Encontrado':
            msg_tmp = '<p style="color:red">' + _('The cancel service requested failed') + '</p>'
            msg_tmp += msg
            self.message_post(
                body=msg_tmp,
                subtype='account.mt_invoice_validated')
        if result.get('Estado', '') == 'Vigente' and result.get('EstatusCancelacion', '') == 'En proceso':
            self.cfdi_pending_cancel = result.get('EstatusCancelacion', '')
            self.cfdi_timbre_id.cfdi_cancel_date_rev = (date.today()+relativedelta(days=+3))
            msg_tmp = '<p style="color:red">' + _('The cancel service requested failed') + '</p>'
            msg_tmp += msg
            self.message_post(
                body=msg_tmp,
                subtype='account.mt_invoice_validated')
        return result

    @api.multi
    def action_accept_reject_sat(self):
        if self.cfdi_accept_reject == 'Aceptacion':
            self.action_invoice_cancel()

        cfdi_params = {
            'uuid': self.uuid,
            'respuesta': self.cfdi_accept_reject
        }
        result = self.company_id.action_ws_finkok_sat('acceptreject', cfdi_params)
        return result

    def get_process_cancel_data(self, res):
        self.cfdi_timbre_id.write({
            "cfdi_cancel_date_sat": res.get("Fecha"),
            "cfdi_cancel_status_sat": res.get("EstatusCancelacion"),
            "cfdi_cancel_code_sat": res.get("MsgCancelacion"),
            "cfdi_cancel_state": res.get("Status")
        })
        if res.get("Acuse"):
            Attachment = self.env['ir.attachment']
            fname = "cancelacion_cfd_%s.xml"%(self.cfdi_timbre_id.name or "")
            attachment_values = {
                'name': fname,
                'datas': base64.b64encode( res["Acuse"] ),
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
        return True

    @api.multi
    def action_invoice_cancel_cfdi_sat(self,result, msg):
        self.ensure_one()
        cfdi_params = {
            "uuid": self.cfdi_timbre_id.name,
            'noCertificado': self.cfdi_timbre_id.cfdi_certificate
        }
        #result = self.company_id.action_ws_finkok_sat('cancel', cfdi_params)
        self.cfdi_timbre_id.write({
            "cfdi_cancel_date_sat": getattr(result,'Fecha', None),
            "cfdi_state": 'Cancelado' if hasattr(result, 'Folios') else 'Vigente',
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
        return result



    @api.multi
    def action_invoice_cancel_cfdi(self):
        self.ensure_one()
        message=False
        res= False
        if not self.cfdi_timbre_id:
            return self.action_invoice_cancel()
        try:
            res = self.with_context({'type': 'invoice'}).cancelation(self)
            logging.info(res)
        except ValueError, e:
            message = str(e)
        except Exception, e:
            message = str(e)
        if message:
            self.message_post(body=message)
        if not res:
            return True
        if getattr(res, 'Folios'):
            self.action_invoice_cancel_cfdi_sat(res, message)
            return self.action_invoice_cancel()
        if not self.cfdi_is_required:
            return self.action_invoice_cancel()
        if self.company_id.cfd_mx_test:
            self.message_post(
                body='<p style="color:green">' + _('Invoice canceled in test environment ') + '</p>',
                subtype='account.mt_invoice_validated')
            return self.action_invoice_cancel()

        if self.filtered(lambda inv: inv.state not in ['proforma2', 'draft', 'open']):
            self.message_post(
                body='<p style="color:red">' + _('Invoice must be in draft, Pro-forma or open state in order to be cancelled ') + '</p>',
                subtype='account.mt_invoice_validated')

        if self.payment_move_line_ids:
            self.message_post(
                body='<p style="color:red">' + _('You cannot cancel an invoice which is partially paid. You need to cancel payment entries first ') + '</p>',
                subtype='account.mt_invoice_validated')
        return True

    @api.multi
    def cfdi_cancel_is_required(self):
        self.ensure_one()
        required = (
            self.uuid and
            self.cfdi_timbre_id and
            self.state in ['cancel'] and
            self.type in ['out_invoice', 'out_refund'] and
            self.company_id.country_id == self.env.ref('base.mx')
        )
        return required

    @api.multi
    def action_invoice_draft(self):
        not_allow = self.filtered(lambda inv: inv.cfdi_cancel_is_required())
        not_allow.message_post(
            subject=_('An error occurred while setting to draft.'),
            message_type='comment',
            body='<p style="color:red">' +  _('This invoice does not have a properly cancelled XML and '
                   'it was signed at least once, please cancel properly with '
                   'the SAT.') + '</p>' )
        allow = self - not_allow
        allow.write({'date_invoice_cfdi': False})
        return super(AccountInvoice, self - not_allow).action_invoice_draft()


    @api.multi
    def action_verificacfdi(self):
        if self.cfdi_timbre_id:
            return self.cfdi_timbre_id.action_verificacfdi()
        return True

    def _get_invoice_payment_info_JSON(self):
        if self.payment_move_line_ids:

            info = []
            currency_id = self.currency_id
            for payment in self.payment_move_line_ids:
                payment_currency_id = False
                if self.type in ('out_invoice', 'in_refund'):
                    amount = sum([p.amount for p in payment.matched_debit_ids if p.debit_move_id in self.move_id.line_ids])
                    amount_currency = sum([p.amount_currency for p in payment.matched_debit_ids if p.debit_move_id in self.move_id.line_ids])
                    if payment.matched_debit_ids:
                        payment_currency_id = all([p.currency_id == payment.matched_debit_ids[0].currency_id for p in payment.matched_debit_ids]) and payment.matched_debit_ids[0].currency_id or False
                        
                elif self.type in ('in_invoice', 'out_refund'):
                    raise UserError('Esta in out')
                    amount = sum([p.amount for p in payment.matched_credit_ids if p.credit_move_id in self.move_id.line_ids])
                    amount_currency = sum([p.amount_currency for p in payment.matched_credit_ids if p.credit_move_id in self.move_id.line_ids])
                    if payment.matched_credit_ids:
                        payment_currency_id = all([p.currency_id == payment.matched_credit_ids[0].currency_id for p in payment.matched_credit_ids]) and payment.matched_credit_ids[0].currency_id or False
                if payment_currency_id and payment_currency_id == self.currency_id:
                    amount_to_show = amount_currency
                else:                   
                    amount_to_show = payment.company_id.currency_id.with_context(date=payment.date).compute(amount, self.currency_id)
                if float_is_zero(amount_to_show, precision_rounding=self.currency_id.rounding):
                    continue
                payment_ref = payment.move_id.name
                if payment.move_id.ref:
                    payment_ref += ' (' + payment.move_id.ref + ')'
                info.append({
                    'name': payment.name,
                    'journal_name': payment.journal_id.name,
                    'amount': amount_to_show,
                    'currency': currency_id.symbol,
                    'digits': [69, currency_id.decimal_places],
                    'position': currency_id.position,
                    'date': payment.date,
                    'payment_id': payment.payment_id.id,
                    'move_id': payment.move_id.id,
                    'ref': payment_ref,
                })
            return info

class MailComposeMessage(models.TransientModel):
    _inherit = 'mail.compose.message'

    @api.multi
    def onchange_template_id(self, template_id, composition_mode, model, res_id):
        """ - mass_mailing: we cannot render, so return the template values
            - normal mode: return rendered values
            /!for x2many field, this onchange return command instead of ids
        """
        res = super(MailComposeMessage, self).onchange_template_id(template_id,
                composition_mode, model, res_id)
        if self.env.context.get('active_model', False) == 'account.invoice':
            invoice = self.env["account.invoice"].browse(self.env.context['active_id'])
            if invoice.number:
                xml_name = "cfd_%s.xml"%invoice.number
            else:
                xml_name = "%s.xml"%(invoice.uuid)
            xml_id = self.env["ir.attachment"].search([('name', '=', xml_name)])
            if xml_id:
                res['value'].setdefault('attachment_ids', []).append(xml_id[0].id)
        return res


class report_invoice_mx(models.AbstractModel):
    _name = 'report.report_invoice_mx'
    
    @api.multi
    def render_html(self, data=None):
        report_obj = self.env['report']
        model_obj = self.env['ir.model.data']
        report = report_obj._get_report_from_name('report_invoice_mx')
        docs = self.env[ report.model ].browse(self._ids)
        tipo_cambio = {}
        for invoice in docs:
            tipo_cambio[invoice.id] = 1.0
            if invoice.uuid:
                if invoice.currency_id.name=='MXN':
                    tipo_cambio[invoice.id] = 1.0
                else:
                    date_invoice = invoice.date_invoice or fields.Date.today()
                    mxn_rate = self.env["ir.model.data"].get_object('base', 'MXN').rate
                    tipocambio = (1.0 / invoice.currency_id.with_context(date='%s 06:00:00'%(date_invoice)).rate) * mxn_rate
                    tipo_cambio[invoice.id] = tipocambio
        docargs = {
            'doc_ids': self._ids,
            'doc_model': report.model,
            'docs': docs,
            'tipo_cambio': tipo_cambio
        }
        return report_obj.render('cfd_mx.report_invoice_mx',  docargs)


# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
