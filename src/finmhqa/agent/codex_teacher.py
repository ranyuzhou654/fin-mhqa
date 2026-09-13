"""Use an authorized local Codex login. No API credential extraction or fallback."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

SYSTEM = '''你是金融证据问答策略模型。只根据本消息中的问题和真实工具观测回答；不可调用Codex内置工具、访问文件或网络，也不可使用记忆猜数。每步输出一个满足schema的JSON动作，不输出其它内容。
search动作可含1-2个搜索，使用company公司名称过滤并选择dense/bm25/hybrid；query写清公司、2025年上半年、所需指标。仅能通过此动作得到真实财报证据。每题最多6次search。
条件桥接：先查询比较指标，确定中间公司，再针对该公司查询题目要求的新指标。取得新指标后调用calculate进行单位转换或运算，再finish。比较/聚合也要计算工具验证。不能用营业总收入冒充营业收入，区分合并/母公司、本期/上期、归母净利润和净利润。数字读取必须来自工具文本，不要估计。
calculate支持convert/sum/difference，value使用原数字，单位元/千元/万元/亿元；输出单位与小数精度遵从问题。
finish必须提供简明推导、精确answer_value、answer_unit、引用已看到的chunk_id列表，以及条件桥接selected_company。证据不足时status='insufficient'并使用空answer_value，不编造。每步reason简述本次决策。所有未使用分支字段置null。'''

SCHEMA={'type':'object','additionalProperties':False,'required':['action','reason','searches','calculation','final'], 'properties':{
'action':{'type':'string','enum':['search','calculate','finish']},'reason':{'type':'string'},
'searches':{'type':['array','null'],'items':{'type':'object','additionalProperties':False,'required':['query','company','mode'],'properties':{'query':{'type':'string'},'company':{'type':'string'},'mode':{'type':'string','enum':['dense','bm25','hybrid']}}}},
'calculation':{'anyOf':[{'type':'null'},{'type':'object','additionalProperties':False,'required':['operation','values','output_unit','round_digits'],'properties':{'operation':{'type':'string','enum':['convert','sum','difference']},'values':{'type':'array','items':{'type':'object','additionalProperties':False,'required':['value','unit'],'properties':{'value':{'type':'string'},'unit':{'type':'string','enum':['元','千元','万元','亿元']}}}},'output_unit':{'type':'string','enum':['元','千元','万元','亿元']},'round_digits':{'type':'integer'}}}]},
'final':{'anyOf':[{'type':'null'},{'type':'object','additionalProperties':False,'required':['status','answer_value','answer_unit','selected_company','citations','explanation'],'properties':{'status':{'type':'string','enum':['answered','insufficient']},'answer_value':{'type':'string'},'answer_unit':{'type':'string'},'selected_company':{'type':['string','null']},'citations':{'type':'array','items':{'type':'string'}},'explanation':{'type':'string'}}}]}}}

class CodexTeacher:
    def __init__(self,binary,workdir):
        self.binary=str(Path(binary).resolve()); self.workdir=Path(workdir).resolve()
        self.workdir.mkdir(parents=True,exist_ok=True)
        if any(self.workdir.iterdir()): raise ValueError('teacher cwd must be empty')

    def decide(self,question,history,outdir,timeout=180):
        outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=False)
        schema=outdir/'schema.json';schema.write_text(json.dumps(SCHEMA,ensure_ascii=False))
        prompt=SYSTEM+'\n'+json.dumps({'question':question,'history':history},ensure_ascii=False)
        (outdir/'input.txt').write_text(prompt)
        cmd=[self.binary,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','-C',str(self.workdir),'-m','gpt-5.6-sol',
             '--disable','shell_tool','--disable','apps','--disable','multi_agent','--disable','browser_use','--disable','computer_use','--disable','hooks',
             '-c','web_search="disabled"','-c','model_reasoning_effort="low"','--json','--output-schema',str(schema.resolve()),'-']
        # Do not expose project paths or gold in user input. Cwd only contains an empty directory.
        env=os.environ.copy()
        env.pop('OPENAI_API_KEY',None);env.pop('CODEX_API_KEY',None)
        events=[];messages=[];usage=None;failure=None
        with (outdir/'stderr.log').open('w') as stderr, (outdir/'events.jsonl').open('x') as log:
            proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,env=env)
            proc.stdin.write(prompt);proc.stdin.close()
            lines=queue.Queue()
            def read():
                for line in proc.stdout:lines.put(line)
                lines.put(None)
            thread=threading.Thread(target=read,daemon=True);thread.start()
            deadline=time.monotonic()+timeout
            try:
                while time.monotonic()<deadline:
                    try:line=lines.get(timeout=1)
                    except queue.Empty:continue
                    if line is None:break
                    log.write(line);log.flush()
                    event=json.loads(line);events.append(event)
                    if event.get('type') in ('item.started','item.completed'):
                        item=event.get('item',{})
                        if item.get('type') not in ('agent_message','reasoning'):
                            failure='unapproved native item: '+str(item.get('type'));break
                        if event['type']=='item.completed' and item.get('type')=='agent_message':messages.append(item['text'])
                    if event.get('type')=='turn.failed':failure=str(event);break
                    if event.get('type')=='turn.completed':usage=event.get('usage',{});break
                if usage is None and failure is None:failure='teacher incomplete or timeout'
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.wait()
                thread.join(timeout=1)
        if failure:raise RuntimeError(failure)
        if not messages:raise RuntimeError('teacher returned no action')
        action=json.loads(messages[-1])
        (outdir/'decision.json').write_text(json.dumps(action,ensure_ascii=False,indent=2))
        (outdir/'usage.json').write_text(json.dumps(usage))
        return action,usage
