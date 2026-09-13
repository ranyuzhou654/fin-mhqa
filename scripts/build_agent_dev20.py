"""Deterministic transcription of assistant-authored dev questions from reviewed facts."""
from copy import deepcopy
import json
from pathlib import Path
from freeze_bridge_pilot import expected


def main():
    out=Path('data/agent_dev20_v1');out.mkdir(parents=True,exist_ok=False)
    previous=[json.loads(l) for l in Path('docs/evidence/bridge_pilot_v1/gold.jsonl').read_text().splitlines()]
    facts={}
    for r in previous:
        for h in r['hops']:
            kind='收入' if h['metric']=='营业收入' else '现金' if '现金' in h['metric'] else '利润'
            facts[h['company'],kind]=h
    rows=[]
    def add(kind,pairs,question,selector=None):
        hops=[]
        for i,pair in enumerate(pairs):
            h=deepcopy(facts[pair]);h['hop_id']=f'h{i+1}';hops.append(h)
        r={'id':f'agent-dev-{len(rows)+1:03}','type':kind,'bridge_kind':'conditional_entity_selection' if kind=='bridge' else None,
           'question':question+'均采用2025年上半年合并口径，金额以亿元表示，四舍五入保留两位小数。',
           'answer_unit':'亿元','round_digits':2,'answer_type':'number','split':'dev_only','generator_model':'root_assistant_authored',
           'provenance':'New manually composed development question from previously reviewed bridge_pilot_v1 facts; not human-authored or held-out test.',
           'hops':hops,'derivation':{'operation':'select_then_read','selector':selector,'selection_hop_ids':['h1','h2'],'target_hop_id':'h3'} if kind=='bridge' else {'operation':'difference' if kind=='comparison' else 'sum','operand_hop_ids':['h1','h2']}}
        r['answer_value'],_=expected(r);r['answer']=r['answer_value']+'亿元';rows.append(r)
    bridges=[('美的集团','宁德时代','argmax','美的集团','利润'),('海尔智家','伊利股份','argmax','海尔智家','现金'),
      ('比亚迪','宁德时代','argmin','宁德时代','利润'),('五粮液','格力电器','argmax','格力电器','现金'),
      ('伊利股份','亿纬锂能','argmin','亿纬锂能','利润'),('贵州茅台','泸州老窖','argmax','贵州茅台','现金'),
      ('比亚迪','海尔智家','argmax','比亚迪','利润'),('五粮液','亿纬锂能','argmax','五粮液','现金')]
    for a,b,sel,target,k in bridges:
        metric='归母净利润' if k=='利润' else '经营活动产生的现金流量净额'
        add('bridge',[(a,'收入'),(b,'收入'),(target,k)],f'根据半年报，比较{a}与{b}营业收入，选择收入较{"高" if sel=="argmax" else "低"}的一家公司，再查询这家公司的{metric}是多少？',sel)
    comparisons=[('宁德时代','美的集团','利润'),('海尔智家','伊利股份','利润'),('比亚迪','亿纬锂能','利润'),
      ('美的集团','格力电器','现金'),('海尔智家','贵州茅台','现金'),('五粮液','泸州老窖','现金'),
      ('宁德时代','海尔智家','收入'),('伊利股份','亿纬锂能','收入')]
    for a,b,k in comparisons:
        metric={'收入':'营业收入','利润':'归母净利润','现金':'经营活动产生的现金流量净额'}[k]
        add('comparison',[(a,k),(b,k)],f'{a}的{metric}减去{b}同一指标，差额是多少？保留正负号。')
    for a,b,k in [('美的集团','伊利股份','利润'),('海尔智家','亿纬锂能','利润'),('美的集团','贵州茅台','现金'),('五粮液','海尔智家','现金')]:
        metric='归母净利润' if k=='利润' else '经营活动产生的现金流量净额'
        add('aggregation',[(a,k),(b,k)],f'{a}与{b}的{metric}合计是多少？')
    (out/'gold.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))

if __name__=='__main__':main()
