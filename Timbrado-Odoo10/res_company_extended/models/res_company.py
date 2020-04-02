# -*- coding: utf-8 -*-
import json, requests
import odoo
from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError, RedirectWarning, ValidationError


# [('zenpar', 'Zenpar (EDICOM)'), ('tralix', 'Tralix'), ('finkok', 'Finkok')]

class company(models.Model):
    _inherit = 'res.company'

    cfd_mx_pac = fields.Selection([('finkok', 'Finkok')], string="PAC", default='')
    cfd_mx_pac_username = fields.Char(string="Usuario de PAC", size=256)
    cfd_mx_pac_password = fields.Char(string="Contrasea de usuario de PAC", size=256)

    cfd_mx_fiscal_regime = fields.Selection(
        [('601', 'General de Ley Personas Morales'),
         ('603', 'Personas Morales con Fines no Lucrativos'),
         ('605', 'Sueldos y Salarios e Ingresos Asimilados a Salarios'),
         ('606', 'Arrendamiento'),
         ('607', 'Régimen de Enajenación o Adquisición de Bienes'),
         ('608', 'Demás ingresos'),
         ('609', 'Consolidación'),
         ('610', 'Residentes en el Extranjero sin Establecimiento Permanente en México'),
         ('611', 'Ingresos por Dividendos (socios y accionistas)'),
         ('612', 'Personas Físicas con Actividades Empresariales y Profesionales'),
         ('614', 'Ingresos por intereses'),
         ('615', 'Régimen de los ingresos por obtención de premios'),
         ('616', 'Sin obligaciones fiscales'),
         ('620', 'Sociedades Cooperativas de Producción que optan por diferir sus ingresos'),
         ('621', 'Incorporación Fiscal'),
         ('622', 'Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras'),
         ('623', 'Opcional para Grupos de Sociedades'),
         ('624', 'Coordinados'),
         ('628', 'Hidrocarburos'),
         ('629', 'De los Regímenes Fiscales Preferentes y de las Empresas Multinacionales'),
         ('630', 'Enajenación de acciones en bolsa de valores')],
        string="Fiscal Regime",
        help="It is used to fill Mexican XML CFDI required field "
        "Comprobante.Emisor.RegimenFiscal.", required=False,)

    @api.multi
    def action_ws_finkok_sat(self, service='', cfdi_params={}):
        self.ensure_one()
        url = "%s/cfdi/%s/%s/%s"%(self.cfd_mx_host, service, self.cfd_mx_db, self.vat)
        headers = {'Content-Type': 'application/json'}
        data = {
            "params": {
                "test": self.cfd_mx_test,
                "pac": self.cfd_mx_pac,
                "version": self.cfd_mx_version,
                "cfdi": cfdi_params
            }
        }
        
        data_json = json.dumps(data)
        res = requests.post(url=url, data=data_json, headers=headers)
        res_datas = res.json()
        dict_error = {}
        if res_datas.get('error') and res_datas['error'].get('data') and res_datas['error']['data'].get('message'):
            dict_error['message'] = res_datas['error']['data']['message']
        if res_datas.get('result') and res_datas['result'].get('error') and res_datas['result']['error'].get('message'):
            dict_error['message'] = res_datas['result']['error']['message']
        if res_datas.get('error'):
            dict_error['message'] = res_datas['error']

        if dict_error.get('message'):
            message = dict_error['message']
            return {'error': message}
            # raise UserError(message)
        else:
            return res_datas.get('result')
        
        return {}

