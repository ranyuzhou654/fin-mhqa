from decimal import Decimal, ROUND_HALF_UP
import json
import urllib.request

FACTORS = {'元':Decimal(1),'千元':Decimal(1000),'万元':Decimal(10000),'亿元':Decimal(100000000)}

def decimal(value):
    n = Decimal(str(value).replace(',', ''))
    if not n.is_finite(): raise ValueError('nonfinite value')
    return n

RATIO_OPS = ('ratio', 'growth')

def calculate(args):
    values = [decimal(v['value']) * FACTORS[v['unit']] for v in args['values']]
    if not 1 <= len(values) <= 10: raise ValueError('invalid operand count')
    op=args['operation']
    # ratio/growth (calculator v2) return percentages; amount operations never output '%'.
    if (op in RATIO_OPS) != (args['output_unit'] == '%'): raise ValueError('ratio/growth require output_unit %; amounts cannot')
    if op=='convert' and len(values)==1: result=values[0]
    elif op=='sum': result=sum(values)
    elif op=='difference' and len(values)==2: result=values[0]-values[1]
    elif op=='ratio' and len(values)==2:
        if values[1]==0: raise ValueError('ratio denominator is zero')
        result=values[0]/values[1]*100
    elif op=='growth' and len(values)==2:
        # values = [current, prior]; a non-positive base makes the growth rate meaningless.
        if values[1]<=0: raise ValueError('growth base must be positive')
        result=(values[0]-values[1])/values[1]*100
    else: raise ValueError('invalid operation/arity')
    digits=args['round_digits']
    if type(digits) is not int or not 0<=digits<=6: raise ValueError('invalid precision')
    scale=Decimal(1) if op in RATIO_OPS else FACTORS[args['output_unit']]
    result=(result/scale).quantize(Decimal(1).scaleb(-digits),rounding=ROUND_HALF_UP)
    return {'value':str(result),'unit':args['output_unit'],'operation':op}

def request_json(url,payload=None):
    data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    req=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=180) as response:
        return json.loads(response.read())

def search(base,args,expected_dataset):
    if set(args)!= {'query','company','mode'}: raise ValueError('invalid search fields')
    if args['mode'] not in ('dense','bm25','hybrid'): raise ValueError('invalid search mode')
    result=request_json(base+'/search',dict(args,top_k=5,n_cand=30,expand_parent=True))
    if result['dataset_sha256']!=expected_dataset or result['index_admission']!='ACCEPTED':
        raise ValueError('retrieval dataset/admission changed')
    return result
