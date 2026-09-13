"""Private line-JSON worker: Torch lives here; FAISS is never imported."""
import argparse
from contextlib import redirect_stdout
import json
import sys
import traceback


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True)
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--max-length',type=int,default=1024)
    a=p.parse_args()
    with redirect_stdout(sys.stderr):
        from .models import LocalModels
        models=LocalModels(a.root,batch_size=a.batch_size,max_length=a.max_length)
    print(json.dumps(dict(ok=True,dimension=models.dimension,max_length=models.max_length,bundle_hash=models.bundle_hash)),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            with redirect_stdout(sys.stderr):
                if request['op']=='encode':
                    values=models.encode(request['texts']).tolist();truncated=models.last_embed_truncated
                elif request['op']=='score':
                    values=models.score(request['query'],request['texts']);truncated=models.last_rerank_truncated
                else:raise ValueError('Unknown inference operation')
            response=dict(ok=True,values=values,truncated=truncated)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response=dict(ok=False,error=str(exc))
        print(json.dumps(response,ensure_ascii=False,allow_nan=False),flush=True)

if __name__=='__main__':main()
