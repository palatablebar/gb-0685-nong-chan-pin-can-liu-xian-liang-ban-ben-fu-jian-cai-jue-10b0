from flask import Blueprint,jsonify
from app.web import api,page
from app import store
bp=Blueprint('m6_reconcile',__name__)

@bp.get('/m6_reconcile')
def screen():return page(6,'m6_reconcile.html')

@bp.route('/api/m6_reconcile',methods=['GET','POST'])
def preview():return api(6)

@bp.get('/api/conclusions')
def list_conclusions():
    return jsonify(store.conclusions())
