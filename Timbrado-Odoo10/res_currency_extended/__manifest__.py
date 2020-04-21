# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name' : 'res_currency_extended',
    'version' : '1.0',
    'summary': 'Extensióon',
    'sequence': 1000,
    'description': """
==================================

    """,
    'category' : 'Accounting & Finance',
    'website': '/',
    'images' : [],
    'author': '',
    'depends' : [
        'base',
        'bias_base_report',
    ],
    'data': [
    ],
    'demo': [],
    'qweb': [],
    'installable': True,
    'application': False,
    'auto_install': False,
}

# No permitir draft de facturas CFDI
# No permitir cancelar si existe un timbre fiscal
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
