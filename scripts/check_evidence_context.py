"""Deterministic integration probes against the admitted corpus; no model calls."""
import argparse
import hashlib
import json
from pathlib import Path
from finmhqa.agent.evidence_context import EvidenceContext

# Fixed source identities, independent of model answers. This is a tool
# reachability check, not a new semantic gold label or PDF visual review.
PROBES=[
    {'anchor_id':'a87071b0d5639630b3554bd5','scope':'document_units','required_id':'cdfb0a6aec6b20836ecf91a1'},
    {'anchor_id':'6a78c7f4d9e3115b371fd063','scope':'table','required_id':'6a78c7f4d9e3115b371fd063'},
    {'anchor_id':'ca459b76d4deb2fe6e28697f','scope':'page','required_id':'ca459b76d4deb2fe6e28697f'},
    {'anchor_id':'3625a26ca12e941341a26e0e','scope':'previous_page','required_id':'b80dc40280a60c309e8c606b'},
]


def main():
    p=argparse.ArgumentParser();p.add_argument('--index',type=Path,default=Path('index'));p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise FileExistsError(a.out)
    sha=json.loads((a.index/'manifest.json').read_text())['admission']['dataset_sha256']
    reader=EvidenceContext(a.index,sha);results=[]
    for probe in PROBES:
        cursor=0;responses=[];seen=set()
        while cursor is not None:
            args={k:probe[k] for k in ('anchor_id','scope')};args['cursor']=cursor
            response=reader.read(args)
            for c in response['results']:
                assert c==reader.by_id[c['chunk_id']]
                assert c['chunk_id'] not in seen
                seen.add(c['chunk_id'])
            assert len(response['results'])<=6 and response['text_chars']<=24000
            responses.append({'args':args,'response':response});cursor=response['next_cursor']
        results.append({'probe':probe,'passed':probe['required_id'] in seen,'calls_to_enumerate':len(responses),'responses':responses})
    report={'scope':'real corpus deterministic reachability and unchanged text; not answer quality','dataset_sha256':sha,
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'results':results}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'passed':sum(r['passed'] for r in results),'total':len(results)}))
    if not all(r['passed'] for r in results):raise SystemExit(1)

if __name__=='__main__':main()
